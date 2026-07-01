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


def _build_odps(cfg: CloudConfig):
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


_odps_client_cache: dict = {}


def get_odps(cfg: CloudConfig):
    """返回缓存的 ODPS 客户端（按配置缓存，避免每次 rerun 重建）；无 AK。"""
    key = (cfg.project, cfg.endpoint, cfg.ram_role)
    if key not in _odps_client_cache:
        _odps_client_cache[key] = _build_odps(cfg)
    return _odps_client_cache[key]
