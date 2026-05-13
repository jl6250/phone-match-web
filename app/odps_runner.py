"""可选：通过 pyodps 在 MaxCompute 执行脚本生成的 SQL（需 AK，勿写入代码仓库）。"""

from __future__ import annotations

from typing import Any


def execute_odps_sql(
    sql: str,
    *,
    access_id: str,
    access_key: str,
    project: str,
    endpoint: str,
) -> Any:
    """
    执行查询类 SQL，返回 pandas.DataFrame（需安装 pandas、pyodps）。
    """
    try:
        import pandas as pd  # noqa: F401
        from odps import ODPS
    except ImportError as e:
        raise RuntimeError(
            "缺少依赖，请安装: pip install pyodps pandas"
        ) from e

    odps = ODPS(access_id, access_key, project=project, endpoint=endpoint)
    instance = odps.execute_sql(sql)
    instance.wait_for_success()

    with instance.open_reader(tunnel=True, limit=False) as reader:
        try:
            return reader.to_pandas()
        except AttributeError:
            rows = []
            cols = [c.name for c in reader.schema.columns] if reader.schema else []
            for record in reader:
                rows.append(list(record.values))
            import pandas as pd

            return pd.DataFrame(rows, columns=cols or None)
