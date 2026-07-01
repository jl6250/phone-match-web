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
