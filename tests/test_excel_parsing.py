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


from app.match_phones import read_phones_from_excel


def test_single_column_no_column_arg():
    """留空 column 时取第一列。"""
    data = _make_xlsx({"Sheet1": [["phone"], ["13812345678"], ["08612345678"], [""]]})
    result = read_phones_from_excel(data, sheet="Sheet1", column=None)
    assert result == ["13812345678", "08612345678"]


def test_multi_column_with_column_arg():
    """指定列名时提取正确列。"""
    data = _make_xlsx({
        "Sheet1": [
            ["name", "phone", "city"],
            ["Alice", "13812345678", "Beijing"],
            ["Bob", "08612345678", "Shanghai"],
        ]
    })
    result = read_phones_from_excel(data, sheet="Sheet1", column="phone")
    assert result == ["13812345678", "08612345678"]


def test_column_not_found_raises():
    """列名不存在时抛出 ValueError。"""
    data = _make_xlsx({"Sheet1": [["name", "phone"], ["Alice", "13800000000"]]})
    with pytest.raises(ValueError, match="列 'mobile' 不在表头中"):
        read_phones_from_excel(data, sheet="Sheet1", column="mobile")


def test_select_second_sheet():
    """能正确读取非第一个 Sheet。"""
    data = _make_xlsx({
        "Ignore": [["phone"], ["00000000000"]],
        "Data": [["phone"], ["13912345678"]],
    })
    result = read_phones_from_excel(data, sheet="Data", column=None)
    assert result == ["13912345678"]


def test_empty_sheet_returns_empty_list():
    """Sheet 无数据行时返回空列表。"""
    data = _make_xlsx({"Sheet1": [["phone"]]})
    result = read_phones_from_excel(data, sheet="Sheet1", column=None)
    assert result == []
