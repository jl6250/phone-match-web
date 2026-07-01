"""odps_cloud 单元测试：全部离线，不连接真实 MaxCompute。"""
from __future__ import annotations

import pytest

from app.odps_cloud import CloudConfig, load_config_from_env


class _Resp:
    def __init__(self, body: str):
        self._b = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _make_fake_urlopen(calls, *, token="tok-123", role="my-role", creds=None,
                       token_fails=False):
    import json as _json
    creds = creds or {
        "AccessKeyId": "STS.ak", "AccessKeySecret": "sk",
        "SecurityToken": "tok", "Code": "Success",
    }

    def fake_urlopen(req, timeout=3):
        url = req.full_url
        calls.append((req.get_method(), url))
        if url.endswith("/api/token"):
            if token_fails:
                raise OSError("token endpoint unavailable")
            return _Resp(token)
        if url.endswith("/security-credentials/"):
            return _Resp(role)
        if url.endswith("/security-credentials/" + role):
            return _Resp(_json.dumps(creds))
        raise AssertionError(f"unexpected url {url}")

    return fake_urlopen


def test_fetch_ecs_ram_sts_hardened_and_autodiscover(monkeypatch):
    import urllib.request
    from app.odps_cloud import _fetch_ecs_ram_sts
    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(calls))
    ak, sk, tok = _fetch_ecs_ram_sts(None)  # 自动探测角色
    assert (ak, sk, tok) == ("STS.ak", "sk", "tok")
    methods_urls = calls
    # 加固模式：先 PUT token
    assert methods_urls[0] == ("PUT", "http://100.100.100.200/latest/api/token")
    # 未指定角色 → 先列角色，再取该角色凭证
    assert any(u.endswith("/security-credentials/") for _, u in methods_urls)
    assert any(u.endswith("/security-credentials/my-role") for _, u in methods_urls)


def test_fetch_ecs_ram_sts_token_fallback_and_explicit_role(monkeypatch):
    import urllib.request
    from app.odps_cloud import _fetch_ecs_ram_sts
    calls = []
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _make_fake_urlopen(calls, role="explicit-role", token_fails=True),
    )
    ak, sk, tok = _fetch_ecs_ram_sts("explicit-role")  # 指定角色 → 不列举
    assert (ak, sk, tok) == ("STS.ak", "sk", "tok")
    # token PUT 失败后仍能取凭证；指定角色时不请求角色列表
    assert not any(u.endswith("/security-credentials/") for _, u in calls)
    assert any(u.endswith("/security-credentials/explicit-role") for _, u in calls)


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

    def run_sql(self, sql, hints=None):
        self.run_sql_arg = sql
        self.run_sql_hints = hints
        return self._instance

    def get_instance(self, instance_id):
        self.get_instance_arg = instance_id
        return self._instance


def test_submit_sql_returns_instance_id_and_passes_sql():
    odps = _FakeODPS(_FakeInstance(instance_id="20260701abc"))
    assert submit_sql(odps, "SELECT 1") == "20260701abc"
    assert odps.run_sql_arg == "SELECT 1"


def test_submit_sql_passes_hints():
    odps = _FakeODPS(_FakeInstance(instance_id="i-9"))
    assert submit_sql(odps, "SELECT 1", hints={"k": "v"}) == "i-9"
    assert odps.run_sql_hints == {"k": "v"}


def test_split_sql_hints_extracts_set_lines():
    from app.odps_cloud import split_sql_hints
    sql = (
        "set odps.sql.validate.orderby.limit=false;\n"
        "WITH raw_input AS (SELECT 1) SELECT * FROM raw_input ORDER BY 1;"
    )
    body, hints = split_sql_hints(sql)
    assert hints == {"odps.sql.validate.orderby.limit": "false"}
    assert body.startswith("WITH raw_input")
    assert "set odps.sql" not in body           # set 行已剥离 → 单语句
    assert body.count(";") == 1                   # 仅剩一条语句


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


def test_get_odps_caches_within_ttl_and_rebuilds_after(monkeypatch):
    import app.odps_cloud as oc
    oc._odps_cache.clear()
    calls = {"n": 0}

    def fake_build(cfg):
        calls["n"] += 1
        return f"client:{cfg.project}:{calls['n']}"

    monkeypatch.setattr(oc, "_build_odps", fake_build)
    cfg = CloudConfig(project="P", endpoint="http://e", ram_role=None)
    a = oc.get_odps(cfg, _now=1000)
    b = oc.get_odps(cfg, _now=1000 + oc._CACHE_TTL - 1)  # TTL 内 → 命中缓存
    assert a is b
    assert calls["n"] == 1
    c = oc.get_odps(cfg, _now=1000 + oc._CACHE_TTL + 1)  # 超 TTL → 重取新 STS 重建
    assert calls["n"] == 2
    assert c != a
    # 不同配置 → 独立构建
    oc.get_odps(CloudConfig(project="Q", endpoint="http://e", ram_role=None), _now=1000)
    assert calls["n"] == 3
    oc._odps_cache.clear()


def test_sql_for_cloud_strips_comment_lines():
    from app.odps_cloud import sql_for_cloud
    sql = (
        "-- 由 phone-match-web 生成（MaxCompute / ODPS）\n"
        "-- 用户表: proj.tbl | 密文列: phone_hex\n"
        "set odps.sql.validate.orderby.limit=false;\n"
        "SELECT login_name FROM proj.tbl WHERE phone_hex IN ('abc');\n"
        "  -- 缩进注释也要去掉\n"
    )
    out = sql_for_cloud(sql)
    assert "--" not in out                      # 注释行全部剥离
    assert "由 phone-match-web" not in out       # 中文注释已去除
    assert "set odps.sql.validate.orderby.limit=false;" in out
    assert "SELECT login_name FROM proj.tbl" in out
