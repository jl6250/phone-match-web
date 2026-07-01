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
