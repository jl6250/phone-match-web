#!/usr/bin/env bash
#
# 生产发布脚本（基于 git）—— 在生产服务器的仓库根目录运行。
#
#   ./scripts/deploy.sh [分支或ref]      # 默认 main
#
# 流程：预检 → 记录回滚点 → 拉取最新代码 → docker compose build → up -d
#       → 健康检查；失败则自动回滚到发布前的提交并恢复旧版本。
#
# 环境变量（可选）：
#   DEPLOY_BRANCH      默认 main（也可用第一个位置参数覆盖）
#   COMPOSE_SERVICE    compose 服务名，默认 phone-match-web
#   HEALTH_TIMEOUT     健康检查超时秒数，默认 90
#   HEALTH_INTERVAL    健康检查轮询间隔秒数，默认 3
#
# Git 认证（免交互拉取私有仓库）：
#   把令牌放进 scripts/deploy.env（已被 .gitignore 忽略，不会被提交、也不会被
#   脚本的 git reset 覆盖），脚本会在拉取前用它设置带令牌的 origin 远程地址。
#   见 scripts/deploy.env.example。变量：
#     GIT_REMOTE_USER   Codeup 用户名
#     GIT_REMOTE_TOKEN  个人访问令牌（pt- 开头）
#
set -euo pipefail

# ── 路径：脚本在 scripts/ 下，仓库根目录是其上一级 ─────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
cd "$REPO_ROOT"

# 加载 git 认证等本地配置（不纳入版本控制）
if [[ -f "$SCRIPT_DIR/deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/deploy.env"
fi

BRANCH="${1:-${DEPLOY_BRANCH:-main}}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-phone-match-web}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"

# ── 日志辅助 ──────────────────────────────────────────────────────────────────
log()  { printf '\033[0;36m[deploy %s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
ok()   { printf '\033[0;32m[deploy %s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*"; }
err()  { printf '\033[0;31m[deploy %s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$*" >&2; }

# ── 选择 docker compose 命令（v2 优先，回退 v1）──────────────────────────────
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  err "未找到 docker compose（v2）或 docker-compose（v1），请先安装 Docker。"
  exit 1
fi

# ── 预检 ──────────────────────────────────────────────────────────────────────
preflight() {
  command -v git >/dev/null 2>&1    || { err "未安装 git"; exit 1; }
  command -v docker >/dev/null 2>&1 || { err "未安装 docker"; exit 1; }
  git rev-parse --git-dir >/dev/null 2>&1 || { err "当前目录不是 git 仓库：$REPO_ROOT"; exit 1; }

  # 工作区必须干净，否则 git reset 回滚会丢失未提交改动
  if [[ -n "$(git status --porcelain)" ]]; then
    err "工作区存在未提交改动，发布中止（请先提交或清理，以保证回滚安全）："
    git status --short >&2
    exit 1
  fi
}

# ── 配置带令牌的 origin 远程（免交互拉取私有仓库）────────────────────────────
# 若设置了 GIT_REMOTE_TOKEN，则把 origin 重写为 https://user:token@host/path。
# 令牌只写入 .git/config（不纳入版本控制，git reset 也不影响），不会打印到日志。
configure_remote() {
  [[ -n "${GIT_REMOTE_TOKEN:-}" ]] || return 0
  local cur host_path
  cur="$(git remote get-url origin)"
  host_path="${cur#*://}"     # 去掉 scheme
  host_path="${host_path#*@}" # 去掉可能已存在的 user:token@
  git remote set-url origin \
    "https://${GIT_REMOTE_USER:-}:${GIT_REMOTE_TOKEN}@${host_path}"
  log "已配置带令牌的 origin 远程（凭证仅存于 .git/config）"
}

# ── 健康检查：轮询容器 HEALTHCHECK 状态 ──────────────────────────────────────
# 返回 0=healthy；非 0=超时/unhealthy。若镜像无 HEALTHCHECK 则退化为「容器在运行即视为健康」。
wait_healthy() {
  local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
  local cid status
  while (( SECONDS < deadline )); do
    cid="$("${COMPOSE[@]}" ps -q "$COMPOSE_SERVICE" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      # 容器是否在运行
      if [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" == "true" ]]; then
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo none)"
        case "$status" in
          healthy) ok "容器健康检查通过（healthy）"; return 0 ;;
          none)    ok "镜像未定义 HEALTHCHECK，容器已在运行（视为健康）"; return 0 ;;
          unhealthy) err "容器健康状态 unhealthy"; return 1 ;;
          *) log "等待健康检查… 当前状态：$status" ;;
        esac
      else
        log "等待容器启动…"
      fi
    else
      log "等待容器创建…"
    fi
    sleep "$HEALTH_INTERVAL"
  done
  err "健康检查超时（${HEALTH_TIMEOUT}s）"
  return 1
}

# ── 构建 + 启动 ───────────────────────────────────────────────────────────────
build_and_up() {
  log "构建镜像：${COMPOSE[*]} build"
  "${COMPOSE[@]}" build || return 1
  log "启动服务：${COMPOSE[*]} up -d"
  "${COMPOSE[@]}" up -d || return 1
}

# ── 回滚到指定提交并恢复旧版本 ───────────────────────────────────────────────
rollback() {
  local target="$1"
  err "发布失败，开始回滚到 ${target:0:12} …"
  if ! git reset --hard "$target"; then
    err "git reset 回滚失败！请手动处理。当前 HEAD：$(git rev-parse --short HEAD)"
    exit 1
  fi
  if build_and_up && wait_healthy; then
    ok "已回滚并恢复到旧版本 ${target:0:12}"
  else
    err "回滚后服务仍异常！请立即人工介入。当前 HEAD：$(git rev-parse --short HEAD)"
  fi
  exit 1
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
main() {
  preflight
  configure_remote

  local prev_sha new_sha
  prev_sha="$(git rev-parse HEAD)"
  log "发布前提交（回滚点）：${prev_sha:0:12}"

  log "拉取最新代码：git fetch origin $BRANCH"
  git fetch origin "$BRANCH"

  # 快进到远端分支（工作区已确认干净，reset 安全）
  log "切换并重置到 origin/$BRANCH"
  git checkout -q "$BRANCH" 2>/dev/null || git checkout -q -b "$BRANCH" "origin/$BRANCH"
  git reset --hard "origin/$BRANCH"

  new_sha="$(git rev-parse HEAD)"
  if [[ "$new_sha" == "$prev_sha" ]]; then
    log "代码无更新（仍为 ${new_sha:0:12}），继续重建以应用可能的配置变更。"
  else
    log "更新：${prev_sha:0:12} → ${new_sha:0:12}"
    git --no-pager log --oneline "${prev_sha}..${new_sha}" | sed 's/^/    /' || true
  fi

  # 从这里起任何失败都触发回滚
  if ! build_and_up; then
    rollback "$prev_sha"
  fi
  if ! wait_healthy; then
    rollback "$prev_sha"
  fi

  ok "发布成功 ✅  当前版本：${new_sha:0:12}（分支 $BRANCH）"
  "${COMPOSE[@]}" ps
}

main "$@"
