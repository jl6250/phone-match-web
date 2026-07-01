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
docker compose up -d
```

默认映射端口 `8501`，可通过环境变量 `PHONE_MATCH_PORT` 修改。

- 镜像内监听 `0.0.0.0:8501`，**请勿直接暴露公网**；建议前置 Nginx / 阿里云 ALB，开启 HTTPS 与访问控制，可参考 `nginx.example.conf`。
- 「MC 云端执行」**不使用 AccessKey**：凭证由运行 app 的**阿里云 ECS 实例 RAM 角色**自动提供（临时 STS）。相关环境变量：
  - `ODPS_PROJECT`（默认 `SuperEngineProject`）
  - `ODPS_ENDPOINT`（默认新加坡 VPC 内网 `http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api`）
  - `ODPS_RAM_ROLE`（默认空＝自动探测 ECS 绑定角色）

### 云端执行部署前置（RAM 授权，一次性）

1. 给运行 app 的 ECS 实例**绑定一个 RAM 角色**（如无则新建）。
2. 该 RAM 角色授予 MaxCompute 访问权限（如 `AliyunODPSFullAccess`，或最小化的 SuperEngineProject 读 + Tunnel 下载权限）。
3. 在 MaxCompute 项目内把该角色对应身份**加为项目成员**，并授予对目标表（如 `dim_user_info_df`）的 `SELECT` 与 Tunnel `Download` 权限。
4. 首次上线后在 ECS 上做一次冒烟：在 Tab 2 用少量手机号执行一次，确认能连通并取回结果。

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
