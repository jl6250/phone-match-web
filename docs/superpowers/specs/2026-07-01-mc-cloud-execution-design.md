# MC 云端执行功能设计（Tab 2）

- 日期：2026-07-01
- 状态：待评审
- 作者：King + Claude

## 1. 背景与目标

`phone-match-web` 的 Tab 1 已能生成 MaxCompute 匹配 SQL，供用户手动复制到
DataWorks / MaxCompute 控制台运行。Tab 2「MC 云端执行」当前是占位符
（`st.info("🚧 功能待开发")`）。

目标：让用户在 Tab 2 **一键把匹配 SQL 提交到云端 MaxCompute 执行**，并在页面内
**展示结果表格 + 下载 CSV**。硬约束：**完全不使用 AccessKey**——AK 不出现在代码、
UI、镜像、环境变量或任何配置文件中。

## 2. 凭证方案：ECS 实例 RAM 角色（完全无 AK）

app 以 Docker 部署在**阿里云 ECS（ap-southeast-1 新加坡）**。因此采用阿里云
计算环境的标准免 AK 方案：

- 给运行 app 的 **ECS 实例绑定一个 RAM 角色**（需具备访问 MaxCompute
  SuperEngineProject 的权限，见 §7 部署前置）。
- 容器内用 `alibabacloud_credentials` 库，凭证类型 `ecs_ram_role`：程序访问实例
  元数据服务（`100.100.100.200`）**自动获取临时 STS 凭证并自动轮转**。
- **AK 在任何地方都不存在**：不在代码、UI、镜像层、环境变量、配置文件。

对比曾考虑并放弃的方案：明文 AK 环境变量 / aliyun CLI + profile 挂载（AK 落宿主机
文件）/ DataWorks OpenAPI + 日志解析——均因"仍存在 AK"或"实现更脆弱"被否决。

## 3. 执行方案：pyodps 直连

用 pyodps（已在 `requirements.txt`）以上述 STS 凭证直连 MaxCompute：

- `run_sql()` 异步提交 → 拿 instance_id（不阻塞 UI）。
- 轮询 instance 状态 / 进度。
- `open_reader(tunnel=True, limit=False)` 读**全量**结果 → `pandas.DataFrame`。

相比 DataWorks OpenAPI + 解析运行日志文本表格的方案，pyodps 直连：
- 结果直接是 DataFrame，**无需解析、无 ~1 万行日志上限**（Tunnel 读全量）；
- 代码更少、更健壮；
- 不需要在容器安装 aliyun CLI。

### 调查背景（经 MCP 实测确认，供参考）

调查阶段用阿里云 OpenAPI MCP 验证过：MaxCompute OpenAPI 无执行 SQL 接口；
DataWorks `ExecuteAdhocWorkflowInstance` 可提交 SQL 且结果会打印进运行日志
（实测 `SELECT 1` 成功、日志含结果表格）。该路径可行但更脆弱；在 ECS + RAM 角色
条件下，pyodps 直连是更优解，故本设计采用 pyodps。

## 4. 架构

```
Tab 2 UI (streamlit_app.py)
   │ 复用已解析 phones_list → 现有 build_mc_online_sql / build_mc_md5_match_sql 生成匹配 SQL
   │ 用户点「☁️ 云端执行」
   ▼
app/odps_cloud.py  (新模块)
   ├ get_odps() -> ODPS                     # CredentialProviderAccount(ecs_ram_role)，带缓存
   ├ submit_sql(odps, sql) -> str           # run_sql，返回 instance_id
   ├ poll(odps, instance_id) -> Status      # 轮询状态 + 进度
   └ fetch_result(odps, instance_id) -> DataFrame   # open_reader(tunnel=True).to_pandas()
   ▼
页面内 DataFrame + 「下载 CSV」+ MaxCompute Logview 链接
```

`odps_cloud.py` 取代未接线的 `app/odps_runner.py`（旧 pyodps AK 骨架）；后者删除或
在文件头标注废弃。各函数职责单一、可独立测试。

## 5. 模块设计：`app/odps_cloud.py`

```python
@dataclass
class CloudConfig:
    project: str      # SuperEngineProject
    endpoint: str     # VPC 内网 endpoint
    ram_role: str | None = None   # None=自动探测 ECS 绑定角色

def get_odps(cfg: CloudConfig) -> ODPS:
    """用 ECS RAM 角色 STS 构造 ODPS 客户端（无 AK）。"""
    from odps import ODPS
    from odps.accounts import CredentialProviderAccount
    from alibabacloud_credentials.client import Client as CredClient
    from alibabacloud_credentials.models import Config as CredConfig
    cred = CredClient(CredConfig(type="ecs_ram_role", role_name=cfg.ram_role))
    account = CredentialProviderAccount(cred)   # STS 自动轮转
    return ODPS(account=account, project=cfg.project, endpoint=cfg.endpoint)

def submit_sql(odps, sql: str) -> str: ...        # odps.run_sql(sql).id
def poll(odps, instance_id: str) -> str: ...      # get_instance().status / is_terminated()
def fetch_result(odps, instance_id: str) -> pd.DataFrame: ...  # open_reader(tunnel=True, limit=False).to_pandas()
def logview_url(odps, instance_id: str) -> str: ...            # instance.get_logview_address()
```

- 依赖延迟 import（`odps`、`alibabacloud_credentials`），缺失时给可读安装提示。
- `CredentialProviderAccount`（pyodps ≥ 0.11.3）传入 credentials provider，自动处理
  STS 过期轮转。实现时确认该 API；备选：手动 `StsAccount` + 到期前刷新。
- `get_odps` 结果按配置缓存（避免每次 rerun 重建）。

## 6. 配置（环境变量，无凭证项）

`docker-compose.yml` **删除** `ODPS_ACCESS_ID` / `ODPS_ACCESS_KEY`，改为：

| 变量 | 默认值 |
|---|---|
| `ODPS_PROJECT` | `SuperEngineProject` |
| `ODPS_ENDPOINT` | `http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api`（VPC 内网） |
| `ODPS_RAM_ROLE` | 空（自动探测 ECS 绑定角色） |

内网 endpoint（同 region VPC）免公网流量、更快；实测该 project 位于 ap-southeast-1。

## 7. 部署前置（RAM 授权，一次性）

1. 运行 app 的 ECS 实例**绑定 RAM 角色**（如无则新建）。
2. 该 RAM 角色授予 MaxCompute 访问权限（RAM 策略含 `odps:*` 或最小化的
   SuperEngineProject 读权限 + Tunnel 下载）。
3. 在 MaxCompute 项目内把该角色对应身份**加为项目成员**并授予对
   `dim_user_info_df` 的 `SELECT` 与 Tunnel `Download` 权限。

上述为运维配置项，写入 README 部署说明；app 代码不涉及。

## 8. SQL 构造：返回全部对照行

云端执行**直接复用现有** `build_mc_online_sql`（明文）/ `build_mc_md5_match_sql`
（MD5），不新增 SQL 逻辑。二者 LEFT JOIN 保留每个输入一行、`ORDER BY rn`。
Tunnel reader 读全量结果，无行数上限，故**返回全部对照行**（含未命中）。

单条完整 SQL（不复用 Tab 1 的 `build_sql_batches` 分批——分批只为绕过 DataWorks
编辑器 ~130KB 单段限制，pyodps 提交无此限制）。

## 9. UI 状态机（不冻结、可恢复）

`st.session_state["cloud_stage"]`：

```
idle → submitting → running → done   (DataFrame + 下载 CSV + Logview)
                          └──→ failed (错误 + Logview)
```

- `submitting`：调 `submit_sql`，存 `instance_id` 到 session_state，`st.rerun()`。
- `running`：`st.status()` 显示轮询；每次 rerun 调 `poll`；未结束 `time.sleep(2)`
  后 `st.rerun()`；`Terminated+成功`→`fetch_result`→`done`；失败→`failed`。
- 执行态存 session_state（instance_id/stage），刷新可从 instance_id 继续轮询，
  作业不丢。
- 前置：`phones_list` 为空则提示先在①输入；按当前输入类型（明文/MD5）选 SQL builder。

结果展示：DataFrame（大结果分页 / 限制预览行数），CSV 下载提供全量。

## 10. Dockerfile / 依赖

- **不安装 aliyun CLI。**
- `requirements.txt` 增加 `alibabacloud-credentials`；`pyodps`、`pandas` 已在。
- Dockerfile 无需额外系统包（`ca-certificates` 已有）。

## 11. 错误处理

| 场景 | 处理 |
|---|---|
| 无 RAM 角色 / 取不到 STS | 提示检查 ECS 是否绑定 RAM 角色（§7） |
| MaxCompute 无权限 / 无项目成员 | 提示检查角色在 MC 项目的授权（§7） |
| SQL 执行失败 | 展示 instance 报错 + Logview 链接 |
| Tunnel 读取失败 | 提示检查 Tunnel Download 权限 |
| 轮询超时（可配置上限，默认 5 分钟） | 停止轮询，提示可凭 Logview 去控制台查看 |
| 缺 pyodps / credentials 依赖 | 提示 `pip install` |

## 12. 测试

- `submit_sql` / `poll` / `fetch_result`：monkeypatch 一个 fake ODPS 对象，验证
  调用参数与状态判断（`is_terminated` / `is_successful` / reader→DataFrame），
  **不触真实云端**。
- `get_odps`：monkeypatch credentials/account 构造，断言用的是 `ecs_ram_role`
  类型、未传任何 AK。
- 复用现有 `tests/` pytest 结构与 conftest。
- **局限（诚实标注）**：ECS 元数据 → STS → pyodps 的真实链路无法在本地
  （非 ECS）验证，需在生产 ECS 上做一次冒烟测试（跑一条小 SQL 确认能连、能取数）。

## 13. YAGNI 明确排除

- 明文 AK / aliyun CLI / profile 挂载 / DataWorks OpenAPI + 日志解析（全部放弃）。
- 多批并发执行（单条 SQL 足够）。
- 结果写回 MC 表 / CREATE TABLE AS（本期直接取数展示）。
- ACK RRSA 凭证（本期部署在 ECS 用实例角色；RRSA 作为未来 K8s 迁移的替换点，
  仅需改 `get_odps` 的 credential 类型）。
- Management API 成本诊断（CU / 扫描量）。

## 14. 风险与未决

- **`CredentialProviderAccount` 的确切 API 与最低 pyodps 版本**：实现时确认；
  必要时回退到「credentials 取 STS + `StsAccount` + 到期刷新」。
- **本地不可验证 ECS 元数据链**：见 §12，靠生产冒烟测试兜底。
- **临时工作/资源消耗**：pyodps 执行按量计费，简单查询消耗极小。
