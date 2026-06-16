# phone-match-web

手机号明文 MD5 对照数仓 `login_name`：生成 MaxCompute SQL、离线 CSV 比对、可选 pyodps 直连执行。

## 本地运行

```bash
cd phone-match-web
python3 -m pip install -r requirements.txt
chmod +x run_web.sh
./run_web.sh --server.address=127.0.0.1
```

浏览器访问终端提示的地址（默认端口见 `.streamlit/config.toml`）。

## Docker 生产部署

```bash
cd phone-match-web
docker compose build
# 按需传入 ODPS_*（仅「MC 执行 SQL」需要 AK 时）
export ODPS_ACCESS_ID=... ODPS_ACCESS_KEY=...
docker compose up -d
```

也可使用 `docker compose --env-file .env up -d`（自行从 `.env.example` 复制 `.env`，勿提交 Git）。

默认映射端口 `8501`，可通过环境变量 `PHONE_MATCH_PORT` 修改。

- 镜像内监听 `0.0.0.0:8501`，**请勿直接暴露公网**；建议前置 Nginx / 阿里云 ALB，开启 HTTPS 与访问控制，可参考 `nginx.example.conf`。
- **不要**把 AccessKey 打进镜像层；通过运行环境注入 `ODPS_ACCESS_ID`、`ODPS_ACCESS_KEY` 等。

## 生产发布（基于 git）

在**生产服务器**的仓库根目录运行：

```bash
./scripts/deploy.sh            # 发布 main 最新代码
./scripts/deploy.sh <分支/ref> # 发布指定分支或提交
```

脚本流程：预检（工作区须干净）→ 记录当前提交作为回滚点 → `git fetch` 并重置到 `origin/<分支>` → `docker compose build` → `up -d` → 轮询容器 `HEALTHCHECK`。**任一步失败会自动 `git reset` 回滚到发布前的提交并重建恢复旧版本**，并以非零码退出。

可选环境变量：`DEPLOY_BRANCH`（默认 `main`）、`COMPOSE_SERVICE`（默认 `phone-match-web`）、`HEALTH_TIMEOUT`（默认 `90` 秒）、`HEALTH_INTERVAL`（默认 `3` 秒）。

## 与仓库内 `tools/phone_login_match` 的关系

命令行 `match_phones.py` 已改为从本目录 `app/match_phones.py` 加载；`tools/phone_login_match/run_web.sh` 会跳转到本项目的 `run_web.sh`。
