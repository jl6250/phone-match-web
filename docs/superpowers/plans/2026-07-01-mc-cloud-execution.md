# MC 云端执行（Tab 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Tab 2「MC 云端执行」用 ECS 实例 RAM 角色（完全无 AK）通过 pyodps 直连 MaxCompute 提交匹配 SQL、轮询、取回结果 DataFrame，并在页面内展示 + 下载 CSV。

**Architecture:** 新建 `app/odps_cloud.py` 承载全部云端执行逻辑（凭证、提交、轮询、取数），纯函数以传入的 `ODPS` 对象为参数便于离线测试；`streamlit_app.py` 的 Tab 2 用 `session_state` 状态机驱动提交→轮询→展示，不冻结页面。凭证由 `alibabacloud_credentials` 的 `ecs_ram_role` 类型从 ECS 元数据自动获取临时 STS，AK 不出现在任何位置。

**Tech Stack:** Python 3.12, Streamlit, pyodps（`odps`）, `alibabacloud-credentials`, pandas, pytest。

**规范参考：** `docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md`

**测试运行约定：** 所有命令在 `phone-match-web/` 目录下运行；用 `python -m pytest`（使 `app` 可被 `from app.xxx import` 导入，与现有 `tests/` 一致）。

---

## 文件结构

- **Create** `app/odps_cloud.py` — 云端执行核心：`CloudConfig`、`load_config_from_env`、`_credential_kwargs`、`get_odps`、`submit_sql`、`poll`、`fetch_result`、`logview_url`、`CloudExecError`。
- **Create** `tests/test_odps_cloud.py` — 离线单元测试（fake ODPS，不触云端）。
- **Modify** `app/streamlit_app.py` — Tab 2 状态机（替换 `st.info("🚧 功能待开发")`）；顶部新增 `import time` 与 odps_cloud 导入。
- **Modify** `requirements.txt` — 新增 `alibabacloud-credentials`；`pyodps` 版本下限提升。
- **Modify** `docker-compose.yml` — 删除 `ODPS_ACCESS_ID/KEY`，改为 `ODPS_PROJECT`/`ODPS_ENDPOINT`/`ODPS_RAM_ROLE`。
- **Modify** `app/odps_runner.py` — 文件头标注废弃（由 `odps_cloud.py` 取代）。
- **Modify** `README.md` — 增加「云端执行部署前置（RAM 授权）」说明；修正 AK 相关旧描述。

---

## Task 0: 建分支并纳入 spec + plan

**Files:**
- Commit: `docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md`, `docs/superpowers/plans/2026-07-01-mc-cloud-execution.md`

- [ ] **Step 1: 创建功能分支（当前在 main）**

Run:
```bash
cd phone-match-web && git checkout -b feat/mc-cloud-execution
```
Expected: `Switched to a new branch 'feat/mc-cloud-execution'`

- [ ] **Step 2: 提交设计文档与实现计划**

```bash
cd phone-match-web
git add docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md \
        docs/superpowers/plans/2026-07-01-mc-cloud-execution.md
git commit -m "docs: MC cloud execution (Tab 2) design + plan

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```
Expected: 一个提交，包含两个文档。

---

## Task 1: `odps_cloud.py` 配置与凭证种子（无 AK 契约）

**Files:**
- Create: `app/odps_cloud.py`
- Test: `tests/test_odps_cloud.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_odps_cloud.py`:
```python
"""odps_cloud 单元测试：全部离线，不连接真实 MaxCompute。"""
from __future__ import annotations

import pytest

from app.odps_cloud import CloudConfig, _credential_kwargs, load_config_from_env


def test_credential_kwargs_uses_ecs_ram_role_and_no_ak():
    cfg = CloudConfig(project="P", endpoint="http://e", ram_role=None)
    kw = _credential_kwargs(cfg)
    assert kw["type"] == "ecs_ram_role"
    assert kw["role_name"] is None
    # 绝不出现任何 AK 字段
    assert "access_key_id" not in kw
    assert "access_key_secret" not in kw


def test_credential_kwargs_passes_role_name():
    cfg = CloudConfig(project="P", endpoint="http://e", ram_role="my-role")
    assert _credential_kwargs(cfg)["role_name"] == "my-role"


def test_load_config_defaults(monkeypatch):
    for k in ("ODPS_PROJECT", "ODPS_ENDPOINT", "ODPS_RAM_ROLE"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config_from_env()
    assert cfg.project == "SuperEngineProject"
    assert cfg.endpoint == "http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api"
    assert cfg.ram_role is None


def test_load_config_env_override(monkeypatch):
    monkeypatch.setenv("ODPS_PROJECT", "proj2")
    monkeypatch.setenv("ODPS_ENDPOINT", "http://other")
    monkeypatch.setenv("ODPS_RAM_ROLE", "role2")
    cfg = load_config_from_env()
    assert (cfg.project, cfg.endpoint, cfg.ram_role) == ("proj2", "http://other", "role2")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd phone-match-web && python -m pytest tests/test_odps_cloud.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.odps_cloud'`

- [ ] **Step 3: Write minimal implementation**

Create `app/odps_cloud.py`:
```python
"""Tab 2「MC 云端执行」核心：ECS RAM 角色（无 AK）+ pyodps 直连。

设计见 docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md。
凭证由 alibabacloud_credentials 的 ecs_ram_role 类型从 ECS 元数据自动获取
临时 STS，AK 不出现在代码/UI/镜像/环境变量/配置文件中。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PROJECT = "SuperEngineProject"
DEFAULT_ENDPOINT = "http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api"


class CloudExecError(RuntimeError):
    """云端执行相关错误（依赖缺失、无权限、执行失败等）。"""


@dataclass
class CloudConfig:
    project: str
    endpoint: str
    ram_role: str | None = None  # None = 自动探测 ECS 绑定角色


def load_config_from_env() -> CloudConfig:
    return CloudConfig(
        project=os.environ.get("ODPS_PROJECT", DEFAULT_PROJECT),
        endpoint=os.environ.get("ODPS_ENDPOINT", DEFAULT_ENDPOINT),
        ram_role=os.environ.get("ODPS_RAM_ROLE") or None,
    )


def _credential_kwargs(cfg: CloudConfig) -> dict:
    """构造 alibabacloud_credentials Config 的参数：仅用 ecs_ram_role，绝不含 AK。"""
    return {"type": "ecs_ram_role", "role_name": cfg.ram_role}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd phone-match-web && python -m pytest tests/test_odps_cloud.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
cd phone-match-web
git add app/odps_cloud.py tests/test_odps_cloud.py
git commit -m "feat(cloud): odps_cloud config + ecs_ram_role credential seam

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 执行函数 submit / poll / fetch / logview（离线可测）

**Files:**
- Modify: `app/odps_cloud.py`
- Test: `tests/test_odps_cloud.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_odps_cloud.py`:
```python
from app.odps_cloud import submit_sql, poll, fetch_result, logview_url


class _FakeReader:
    def __init__(self, rows, columns):
        self._rows, self._columns = rows, columns

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def to_pandas(self):
        import pandas as pd
        return pd.DataFrame(self._rows, columns=self._columns)


class _FakeInstance:
    def __init__(self, instance_id="inst-1", terminated=True, successful=True,
                 rows=None, columns=None, logview="http://logview/x"):
        self.id = instance_id
        self._terminated, self._successful = terminated, successful
        self._rows, self._columns = rows or [], columns or []
        self._logview = logview
        self.open_reader_kwargs = None

    def is_terminated(self):
        return self._terminated

    def is_successful(self):
        return self._successful

    def get_logview_address(self):
        return self._logview

    def open_reader(self, **kwargs):
        self.open_reader_kwargs = kwargs
        return _FakeReader(self._rows, self._columns)


class _FakeODPS:
    def __init__(self, instance):
        self._instance = instance
        self.run_sql_arg = None
        self.get_instance_arg = None

    def run_sql(self, sql):
        self.run_sql_arg = sql
        return self._instance

    def get_instance(self, instance_id):
        self.get_instance_arg = instance_id
        return self._instance


def test_submit_sql_returns_instance_id_and_passes_sql():
    odps = _FakeODPS(_FakeInstance(instance_id="20260701abc"))
    assert submit_sql(odps, "SELECT 1") == "20260701abc"
    assert odps.run_sql_arg == "SELECT 1"


def test_poll_running():
    odps = _FakeODPS(_FakeInstance(terminated=False))
    assert poll(odps, "inst-1") == "Running"


def test_poll_success():
    odps = _FakeODPS(_FakeInstance(terminated=True, successful=True))
    assert poll(odps, "inst-1") == "Success"


def test_poll_failure():
    odps = _FakeODPS(_FakeInstance(terminated=True, successful=False))
    assert poll(odps, "inst-1") == "Failure"


def test_fetch_result_uses_tunnel_full_and_returns_df():
    inst = _FakeInstance(rows=[["a", "x"], ["b", "y"]], columns=["login_name", "col"])
    odps = _FakeODPS(inst)
    df = fetch_result(odps, "inst-1")
    assert list(df.columns) == ["login_name", "col"]
    assert len(df) == 2
    # 必须用 tunnel 读全量（不受 ~1万行日志上限）
    assert inst.open_reader_kwargs == {"tunnel": True, "limit": False}


def test_logview_url():
    odps = _FakeODPS(_FakeInstance(logview="http://logview/abc"))
    assert logview_url(odps, "inst-1") == "http://logview/abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd phone-match-web && python -m pytest tests/test_odps_cloud.py -v`
Expected: FAIL — `ImportError: cannot import name 'submit_sql'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/odps_cloud.py`:
```python
def submit_sql(odps, sql: str) -> str:
    """异步提交 SQL，返回 instance_id（不阻塞）。"""
    return odps.run_sql(sql).id


def poll(odps, instance_id: str) -> str:
    """返回 'Running' | 'Success' | 'Failure'。"""
    inst = odps.get_instance(instance_id)
    if not inst.is_terminated():
        return "Running"
    return "Success" if inst.is_successful() else "Failure"


def fetch_result(odps, instance_id: str):
    """用 Tunnel 读全量结果，返回 pandas.DataFrame。"""
    inst = odps.get_instance(instance_id)
    with inst.open_reader(tunnel=True, limit=False) as reader:
        try:
            return reader.to_pandas()
        except AttributeError:
            import pandas as pd
            cols = [c.name for c in reader.schema.columns] if getattr(reader, "schema", None) else []
            rows = [list(rec.values) for rec in reader]
            return pd.DataFrame(rows, columns=cols or None)


def logview_url(odps, instance_id: str) -> str:
    """返回 MaxCompute Logview 地址（排查用）。"""
    return odps.get_instance(instance_id).get_logview_address()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd phone-match-web && python -m pytest tests/test_odps_cloud.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
cd phone-match-web
git add app/odps_cloud.py tests/test_odps_cloud.py
git commit -m "feat(cloud): submit/poll/fetch_result/logview via pyodps instance

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `get_odps` 真实凭证接线（懒加载，无 AK）

**Files:**
- Modify: `app/odps_cloud.py`

说明：`get_odps` 依赖 pyodps 与 alibabacloud_credentials 的真实导入，且 ECS 元数据→STS 链路只能在生产 ECS 验证（见 spec §12）。因此本任务不写连接真实云端的自动化测试；契约（ecs_ram_role、无 AK）已由 Task 1 的 `_credential_kwargs` 测试覆盖。本任务用「依赖缺失时给可读错误」+ `py_compile` 保证可导入。

- [ ] **Step 1: 追加实现**

Append to `app/odps_cloud.py`:
```python
def get_odps(cfg: CloudConfig):
    """用 ECS RAM 角色 STS 构造 ODPS 客户端（无 AK）。

    STS 由 alibabacloud_credentials 从 ECS 元数据自动获取并轮转。
    仅在阿里云 ECS（绑定了具备 MaxCompute 权限的 RAM 角色）上可用。
    """
    try:
        from odps import ODPS
        from odps.accounts import CredentialProviderAccount
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_credentials.models import Config as CredConfig
    except ImportError as e:  # 依赖缺失
        raise CloudExecError(
            "缺少依赖，请安装: pip install pyodps alibabacloud-credentials pandas"
        ) from e

    cred = CredClient(CredConfig(**_credential_kwargs(cfg)))
    account = CredentialProviderAccount(cred)  # STS 自动轮转
    return ODPS(account=account, project=cfg.project, endpoint=cfg.endpoint)
```

- [ ] **Step 2: 语法与导入自检**

Run: `cd phone-match-web && python -m py_compile app/odps_cloud.py && echo OK`
Expected: `OK`（无语法错误）

- [ ] **Step 3: 确认全部离线测试仍通过**

Run: `cd phone-match-web && python -m pytest tests/test_odps_cloud.py -v`
Expected: PASS（10 passed，get_odps 未触发真实连接）

- [ ] **Step 4: 回退方案备注（仅当 Task 4 生产冒烟发现 `CredentialProviderAccount` 不可用时启用）**

若 pyodps 版本无 `CredentialProviderAccount`，将 `get_odps` 内改为手动取 STS + `StsAccount`：
```python
    from odps import ODPS
    from odps.accounts import StsAccount
    from alibabacloud_credentials.client import Client as CredClient
    from alibabacloud_credentials.models import Config as CredConfig
    cred = CredClient(CredConfig(**_credential_kwargs(cfg)))
    c = cred.get_credential()
    account = StsAccount(c.access_key_id, c.access_key_secret, c.security_token)
    return ODPS(account=account, project=cfg.project, endpoint=cfg.endpoint)
```
注意：`StsAccount` 不自动轮转，长时运行需在每次执行前重建 `odps`（本 app 每次执行都新建，可接受）。

- [ ] **Step 5: Commit**

```bash
cd phone-match-web
git add app/odps_cloud.py
git commit -m "feat(cloud): get_odps wiring with ecs_ram_role STS (no AK)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Streamlit Tab 2 状态机（提交→轮询→展示）

**Files:**
- Modify: `app/streamlit_app.py`（顶部导入；Tab 2 代码块，当前 `with tab_cloud:` 下的 `st.info("🚧 功能待开发")`）

- [ ] **Step 1: 顶部新增导入**

在 `app/streamlit_app.py` 现有 `import zipfile` 之后新增一行 `import time`；并在 `from app.match_phones import (...)` 之后新增：
```python
from app.odps_cloud import (
    CloudExecError,
    fetch_result,
    get_odps,
    load_config_from_env,
    logview_url,
    poll,
    submit_sql,
)
```

- [ ] **Step 2: 替换 Tab 2 代码块**

将结尾处：
```python
# ── Tab 2：MC 云端执行 ────────────────────────────────────────────────────────
with tab_cloud:
    st.info("🚧 功能待开发")
```
整体替换为：
```python
# ── Tab 2：MC 云端执行 ────────────────────────────────────────────────────────
with tab_cloud:
    st.markdown(
        "用 ECS 实例 RAM 角色（无 AccessKey）直连 MaxCompute 执行匹配 SQL，"
        "结果在页面内展示并可下载 CSV。仅在已绑定 RAM 角色的阿里云 ECS 上可用。"
    )

    _cfg = load_config_from_env()
    st.markdown(
        f'<div class="metric-row">'
        f'<span class="metric-chip">项目 {_cfg.project}</span>'
        f'<span class="metric-chip">Endpoint {_cfg.endpoint}</span>'
        f'<span class="metric-chip green">凭证：ECS RAM 角色（无 AK）</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2, gap="large")
    with c1:
        cloud_table = st.text_input("用户表", value=DEFAULT_MC_USER_TABLE, key="cloud_mc_table")
        cloud_cipher = st.text_input("密文列", value=DEFAULT_MC_CIPHER_COLUMN, key="cloud_cipher")
    with c2:
        cloud_pt = st.text_input("分区条件", value=DEFAULT_MC_PARTITION_EXPR, key="cloud_pt")
        cloud_login = st.text_input("登录名列", value="login_name", key="cloud_login")
    cloud_extra = st.text_input(
        "附加 AND 条件（可空）", value="", key="cloud_extra",
        placeholder="例：business_line = 'bp'",
    )

    st.session_state.setdefault("cloud_stage", "idle")

    def _build_cloud_sql() -> str:
        if is_md5_mode:
            return build_mc_md5_match_sql(
                phones_list,
                mc_table=cloud_table.strip(), login_column=cloud_login.strip(),
                cipher_column=cloud_cipher.strip(), partition_predicate=cloud_pt.strip(),
                extra_where=cloud_extra.strip() or None,
            )
        return build_mc_online_sql(
            phones_list,
            mc_table=cloud_table.strip(), login_column=cloud_login.strip(),
            cipher_column=cloud_cipher.strip(), partition_predicate=cloud_pt.strip(),
            extra_where=cloud_extra.strip() or None,
        )

    if st.button("☁️ 云端执行", type="primary", key="btn_cloud"):
        if not phones_list:
            st.warning("请先在上方填写或上传数据（明文手机号或 MD5 密文）")
        else:
            try:
                sql = _build_cloud_sql()
                odps = get_odps(_cfg)
                st.session_state["cloud_instance_id"] = submit_sql(odps, sql)
                st.session_state["cloud_stage"] = "running"
                st.session_state["cloud_started_at"] = time.time()
                st.session_state.pop("cloud_error", None)
                st.rerun()
            except (CloudExecError, Exception) as e:
                st.session_state["cloud_stage"] = "failed"
                st.session_state["cloud_error"] = str(e)

    stage = st.session_state.get("cloud_stage", "idle")
    inst_id = st.session_state.get("cloud_instance_id")

    if stage == "running" and inst_id:
        with st.status(f"云端执行中… instance={inst_id}", expanded=True) as status:
            try:
                elapsed = time.time() - st.session_state.get("cloud_started_at", time.time())
                if elapsed > 300:  # 轮询超时上限 5 分钟
                    odps = get_odps(_cfg)
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "failed"
                    st.session_state["cloud_error"] = (
                        "轮询超过 5 分钟未完成，已停止等待；可凭 Logview 去控制台查看作业。"
                    )
                    st.rerun()
                odps = get_odps(_cfg)
                state = poll(odps, inst_id)
                st.write(f"状态：{state}（已等待 {int(elapsed)}s）")
                if state == "Running":
                    time.sleep(2)
                    st.rerun()
                elif state == "Success":
                    df = fetch_result(odps, inst_id)
                    st.session_state["cloud_df"] = df
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "done"
                    status.update(label="执行完成", state="complete")
                    st.rerun()
                else:  # Failure
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "failed"
                    st.session_state["cloud_error"] = "SQL 执行失败，请查看 Logview"
                    st.rerun()
            except Exception as e:
                st.session_state["cloud_stage"] = "failed"
                st.session_state["cloud_error"] = str(e)
                st.rerun()

    if stage == "done":
        df = st.session_state.get("cloud_df")
        n = 0 if df is None else len(df)
        st.markdown(
            f'<div class="metric-row">'
            f'<span class="metric-chip green">✓ 执行成功</span>'
            f'<span class="metric-chip">{n:,} 行结果</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.get("cloud_logview"):
            st.markdown(f"[🔗 MaxCompute Logview]({st.session_state['cloud_logview']})")
        if df is not None:
            st.dataframe(df, use_container_width=True, height=420)
            st.download_button(
                "⬇️ 下载结果 CSV",
                df.to_csv(index=False).encode("utf-8-sig"),
                file_name="phone_match_result.csv",
                mime="text/csv; charset=utf-8",
                key="dl_cloud_csv",
            )

    if stage == "failed":
        st.error(f"云端执行失败：{st.session_state.get('cloud_error', '未知错误')}")
        if st.session_state.get("cloud_logview"):
            st.markdown(f"[🔗 MaxCompute Logview]({st.session_state['cloud_logview']})")
        st.caption(
            "排查：① ECS 是否绑定了具备 MaxCompute 权限的 RAM 角色；"
            "② 该角色是否为 MC 项目成员并有表 SELECT + Tunnel Download 权限。"
        )
```

- [ ] **Step 3: 语法自检**

Run: `cd phone-match-web && python -m py_compile app/streamlit_app.py && echo OK`
Expected: `OK`

- [ ] **Step 4: 手动冒烟（本地，无凭证也应不崩）**

Run: `cd phone-match-web && ./run_web.sh --server.address=127.0.0.1 --server.headless=true`
预期：页面加载，切到「☁️ MC 云端执行」Tab 显示项目/Endpoint/凭证 chips 与输入框；未输入数据点执行 → 黄色提示「请先填写数据」；本地无 ECS 角色时点执行 → 红色错误（缺依赖或取不到 STS），页面不崩溃。确认后 Ctrl-C 退出。

- [ ] **Step 5: Commit**

```bash
cd phone-match-web
git add app/streamlit_app.py
git commit -m "feat(ui): Tab 2 MC cloud execution state machine (submit/poll/render)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 依赖、compose、废弃 odps_runner

**Files:**
- Modify: `requirements.txt`, `docker-compose.yml`, `app/odps_runner.py`

- [ ] **Step 1: 更新 requirements.txt**

将 `requirements.txt` 全文替换为：
```
streamlit>=1.28
pandas>=2.0
pyodps>=0.11.6
openpyxl>=3.1
alibabacloud-credentials>=0.3.4
```

- [ ] **Step 2: 更新 docker-compose.yml（移除 AK，改用无凭证配置）**

将 `docker-compose.yml` 的 `environment:` 块替换为：
```yaml
    environment:
      ODPS_PROJECT: ${ODPS_PROJECT:-SuperEngineProject}
      ODPS_ENDPOINT: ${ODPS_ENDPOINT:-http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api}
      ODPS_RAM_ROLE: ${ODPS_RAM_ROLE:-}
```
（删除 `ODPS_ACCESS_ID`、`ODPS_ACCESS_KEY`；凭证走 ECS 实例 RAM 角色。）

- [ ] **Step 3: 标注 odps_runner.py 废弃**

将 `app/odps_runner.py` 的模块 docstring（第 1 行）替换为：
```python
"""[已废弃] 旧的 pyodps + AK 执行骨架。

云端执行改由 app/odps_cloud.py（ECS RAM 角色，无 AK）实现，见
docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md。保留此文件仅为
历史参考，勿在新代码中使用。
"""
```

- [ ] **Step 4: 确认全部测试通过**

Run: `cd phone-match-web && python -m pytest -v`
Expected: PASS（既有测试 + `tests/test_odps_cloud.py` 全绿）

- [ ] **Step 5: Commit**

```bash
cd phone-match-web
git add requirements.txt docker-compose.yml app/odps_runner.py
git commit -m "chore(cloud): add alibabacloud-credentials, drop AK env, deprecate odps_runner

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: README 部署前置文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 修正 Docker 部署段并新增 RAM 授权前置**

将 `README.md` 中「## Docker 生产部署」整节（从该标题到「## 生产发布（基于 git）」之前）替换为：
````markdown
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
````

- [ ] **Step 2: 提交**

```bash
cd phone-match-web
git add README.md
git commit -m "docs: cloud execution deploy prereq (ECS RAM role, no AK)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 全量验证与收尾

- [ ] **Step 1: 全量测试**

Run: `cd phone-match-web && python -m pytest -v`
Expected: 全绿（含 `test_odps_cloud.py` 10 项 + 既有测试）。

- [ ] **Step 2: 全量语法自检**

Run: `cd phone-match-web && python -m py_compile app/odps_cloud.py app/streamlit_app.py && echo OK`
Expected: `OK`

- [ ] **Step 3: 生产 ECS 冒烟（人工，部署后）**

在生产 ECS 上部署本分支镜像，打开 Tab 2，用 2–3 个已知能命中的手机号执行一次：
- 预期：状态 running→done，页面出结果表格（含 `login_name` 列），可下载 CSV，Logview 链接可打开。
- 若失败按 Tab 2 的排查提示核对 RAM 角色与 MC 项目授权（README 部署前置）。

此步验证 spec §12 标注的「ECS 元数据→STS→pyodps」真实链路，本地无法替代。

- [ ] **Step 4: 完成分支**

REQUIRED SUB-SKILL: 使用 superpowers:finishing-a-development-branch 决定合并 / PR / 清理。
