"""Tab 2「MC 云端执行」核心：ECS 实例 RAM 角色（无 AK）+ pyodps 直连。

设计见 docs/superpowers/specs/2026-07-01-mc-cloud-execution-design.md。
临时 STS 直接从 ECS 实例元数据服务（100.100.100.200）获取，喂给 pyodps
StsAccount。不依赖 alibabacloud_credentials↔pyodps 的版本互调（该互调在部分
版本组合下不兼容），AK 不出现在代码/UI/镜像/环境变量/配置文件中。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

DEFAULT_PROJECT = "SuperEngineProject"
DEFAULT_ENDPOINT = "http://service.ap-southeast-1.maxcompute.aliyun-inc.com/api"

# ECS 实例元数据服务（IMDS）
_METADATA_BASE = "http://100.100.100.200/latest/meta-data/ram/security-credentials/"
_METADATA_TOKEN_URL = "http://100.100.100.200/latest/api/token"

# ODPS 客户端缓存 TTL（秒）。ECS 元数据 STS 有效期通常 ≥6h 且自动轮转，
# 30 分钟 TTL 既避免每次 rerun/轮询重取，又能远早于过期前刷新。
_CACHE_TTL = 1800


class CloudExecError(RuntimeError):
    """云端执行相关错误（依赖缺失、无权限、无 RAM 角色、执行失败等）。"""


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


def sql_for_cloud(sql: str) -> str:
    """去掉 `--` 注释行（含中文说明），返回纯可执行 SQL。

    云端由 pyodps 直接执行，无需人类可读注释；剥离非 ASCII 注释也可规避个别
    环境下多字节字符在请求/响应链路引发的编码问题。仅删除整行注释；行内值里的
    单引号字符串（MD5/手机号）不含 `--`，不受影响。
    """
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()


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


def _fetch_ecs_ram_sts(role_name: str | None) -> tuple[str, str, str]:
    """从 ECS 实例元数据服务获取 RAM 角色临时 STS。

    返回 (access_key_id, access_key_secret, security_token)。兼容加固模式
    （IMDSv2）：先尝试取 metadata token 并在后续请求带上；取不到 token 则退回
    普通模式。全程无 AK。
    """
    import json
    import urllib.request

    def _http(url: str, method: str = "GET", headers: dict | None = None,
              timeout: int = 3) -> str:
        req = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")

    # 加固模式：先取 metadata token；失败则普通模式（headers 为空）
    headers: dict = {}
    try:
        token = _http(
            _METADATA_TOKEN_URL,
            method="PUT",
            headers={"X-aliyun-ecs-metadata-token-ttl-seconds": "21600"},
        ).strip()
        if token:
            headers = {"X-aliyun-ecs-metadata-token": token}
    except Exception:
        headers = {}

    try:
        rn = role_name or _http(_METADATA_BASE, headers=headers).strip()
        if not rn:
            raise CloudExecError("未找到 ECS 绑定的 RAM 角色，请确认实例已绑定 RAM 角色。")
        doc = _http(_METADATA_BASE + rn, headers=headers)
    except CloudExecError:
        raise
    except Exception as e:
        raise CloudExecError(
            f"无法从 ECS 元数据获取 RAM 角色 STS（{e}）。"
            "请确认在阿里云 ECS 上运行且已绑定 RAM 角色。"
        ) from e

    try:
        data = json.loads(doc)
    except ValueError as e:
        raise CloudExecError(f"ECS 元数据返回非 JSON：{doc[:120]!r}") from e

    if data.get("Code") not in (None, "Success"):
        raise CloudExecError(f"ECS 元数据返回异常：Code={data.get('Code')}")
    try:
        return data["AccessKeyId"], data["AccessKeySecret"], data["SecurityToken"]
    except KeyError as e:
        raise CloudExecError(f"ECS 元数据凭证字段缺失：{e}") from e


def _build_odps(cfg: CloudConfig):
    """用 ECS 实例元数据的 RAM 角色 STS 构造 ODPS 客户端（无 AK）。

    仅在阿里云 ECS（绑定了具备 MaxCompute 权限的 RAM 角色）上可用。
    """
    try:
        from odps import ODPS
        from odps.accounts import StsAccount
    except ImportError as e:  # 依赖缺失
        raise CloudExecError("缺少依赖，请安装: pip install pyodps pandas") from e

    ak_id, ak_secret, sts_token = _fetch_ecs_ram_sts(cfg.ram_role)
    account = StsAccount(ak_id, ak_secret, sts_token)
    return ODPS(account=account, project=cfg.project, endpoint=cfg.endpoint)


# 缓存条目：key -> (built_at_epoch, odps_client)
_odps_cache: dict = {}


def get_odps(cfg: CloudConfig, _now: float | None = None):
    """返回 ODPS 客户端（按配置缓存，TTL 内复用；过期后重取新 STS）。无 AK。

    因 StsAccount 持有固定 STS 令牌会过期，缓存带 _CACHE_TTL；超时后重新从 ECS
    元数据取新 STS 重建，避免长时运行后令牌失效。_now 仅供测试注入。
    """
    now = _now if _now is not None else time.time()
    key = (cfg.project, cfg.endpoint, cfg.ram_role)
    entry = _odps_cache.get(key)
    if entry is not None and (now - entry[0]) < _CACHE_TTL:
        return entry[1]
    client = _build_odps(cfg)
    _odps_cache[key] = (now, client)
    return client
