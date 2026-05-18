"""测试 read_phones_from_excel。"""
from __future__ import annotations

import io
import pytest
import pandas as pd


def _make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    """辅助：构建内存中的 xlsx bytes。sheets = {sheet_name: [[row], ...]}"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            df = pd.DataFrame(rows[1:], columns=rows[0])
            df.to_excel(writer, sheet_name=name, index=False)
    return buf.getvalue()
