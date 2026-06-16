"""测试 MD5 直接匹配：is_md5_hex / read_* normalizer / build_mc_md5_match_sql。"""
from __future__ import annotations

import pytest

from app.match_phones import is_md5_hex


def test_is_md5_hex_valid_lowercase():
    assert is_md5_hex("d41d8cd98f00b204e9800998ecf8427e") is True


def test_is_md5_hex_valid_uppercase():
    assert is_md5_hex("D41D8CD98F00B204E9800998ECF8427E") is True


def test_is_md5_hex_with_surrounding_whitespace():
    assert is_md5_hex("  d41d8cd98f00b204e9800998ecf8427e  ") is True


def test_is_md5_hex_too_short():
    assert is_md5_hex("d41d8cd98f00b204e9800998ecf8427") is False


def test_is_md5_hex_too_long():
    assert is_md5_hex("d41d8cd98f00b204e9800998ecf8427ee") is False


def test_is_md5_hex_non_hex_char():
    assert is_md5_hex("z41d8cd98f00b204e9800998ecf8427e") is False


def test_is_md5_hex_empty():
    assert is_md5_hex("") is False


from app.match_phones import read_phones_from_text


def _md5_keep(s: str) -> str:
    """MD5 模式 normalizer：strip+lower，保留非空（不在此处校验 hex）。"""
    return s.strip().lower()


def test_read_text_with_md5_normalizer_plain_lines():
    text = "D41D8CD98F00B204E9800998ECF8427E\n900150983cd24fb0d6963f7d28e17f72\n"
    result = read_phones_from_text(text, None, normalizer=_md5_keep)
    assert result == [
        "d41d8cd98f00b204e9800998ecf8427e",
        "900150983cd24fb0d6963f7d28e17f72",
    ]


def test_read_text_with_md5_normalizer_csv_column():
    text = "name,hash\nAlice,D41D8CD98F00B204E9800998ECF8427E\nBob,900150983cd24fb0d6963f7d28e17f72\n"
    result = read_phones_from_text(text, "hash", normalizer=_md5_keep)
    assert result == [
        "d41d8cd98f00b204e9800998ecf8427e",
        "900150983cd24fb0d6963f7d28e17f72",
    ]


def test_read_text_default_normalizer_unchanged():
    """回归：默认 normalizer 仍按明文手机号处理（去 +63 国家码）。"""
    text = "+639171234567\n13812345678\n"
    result = read_phones_from_text(text, None)
    assert result == ["9171234567", "13812345678"]


from app.match_phones import build_mc_md5_match_sql

_H1 = "d41d8cd98f00b204e9800998ecf8427e"
_H2 = "900150983cd24fb0d6963f7d28e17f72"


def test_build_md5_sql_contains_all_hashes():
    sql = build_mc_md5_match_sql(
        [_H1, _H2],
        mc_table="proj.tbl",
        login_column="login_name",
        cipher_column="phone_hex",
        partition_predicate="pt = '20260101'",
        extra_where=None,
    )
    assert _H1 in sql
    assert _H2 in sql


def test_build_md5_sql_has_in_filter_and_no_md5_compute():
    sql = build_mc_md5_match_sql(
        [_H1],
        mc_table="proj.tbl",
        login_column="login_name",
        cipher_column="phone_hex",
        partition_predicate="pt = '20260101'",
        extra_where=None,
    )
    assert "IN (SELECT h FROM hash_filter)" in sql
    assert "md5(" not in sql.lower()


def test_build_md5_sql_orders_by_rn():
    sql = build_mc_md5_match_sql(
        [_H1, _H2],
        mc_table="proj.tbl",
        login_column="login_name",
        cipher_column="phone_hex",
        partition_predicate="pt = '20260101'",
        extra_where=None,
    )
    assert "ORDER BY n.rn" in sql


def test_build_md5_sql_extra_where_injected():
    sql = build_mc_md5_match_sql(
        [_H1],
        mc_table="proj.tbl",
        login_column="login_name",
        cipher_column="phone_hex",
        partition_predicate="pt = '20260101'",
        extra_where="business_line = 'bp'",
    )
    assert "business_line = 'bp'" in sql


def test_build_md5_sql_empty_raises():
    with pytest.raises(ValueError):
        build_mc_md5_match_sql(
            [],
            mc_table="proj.tbl",
            login_column="login_name",
            cipher_column="phone_hex",
            partition_predicate="pt = '20260101'",
            extra_where=None,
        )


from app.match_phones import build_sql_batches


def _md5_builder(rows):
    return build_mc_md5_match_sql(
        rows,
        mc_table="proj.tbl",
        login_column="login_name",
        cipher_column="phone_hex",
        partition_predicate="pt = '20260101'",
        extra_where=None,
    )


def _gen_hashes(n):
    # 生成 n 个互不相同的 32 位 hex（用十进制零填充再补足到 32 位）
    return [str(i).zfill(32) for i in range(n)]


def test_batches_single_when_small():
    rows = _gen_hashes(3)
    batches = build_sql_batches(rows, _md5_builder, max_bytes=120_000)
    assert len(batches) == 1
    assert batches[0] == _md5_builder(rows)


def test_batches_split_respects_byte_limit():
    rows = _gen_hashes(200)
    limit = 4_000
    batches = build_sql_batches(rows, _md5_builder, max_bytes=limit)
    assert len(batches) > 1
    for sql in batches:
        assert len(sql.encode("utf-8")) <= limit


def test_batches_cover_all_rows_in_order():
    rows = _gen_hashes(200)
    batches = build_sql_batches(rows, _md5_builder, max_bytes=4_000)
    # 每个输入 hash 恰好出现在某一批中，且整体顺序保持
    seen = []
    for h in rows:
        hits = [k for k, sql in enumerate(batches) if h in sql]
        assert len(hits) == 1, f"{h} 应恰好出现在一批中，实际 {hits}"
        seen.append((h, hits[0]))
    # 批次索引随输入顺序单调不减
    batch_idx = [b for _, b in seen]
    assert batch_idx == sorted(batch_idx)


def test_batches_oversize_single_row_no_infinite_loop():
    rows = _gen_hashes(3)
    # 阈值小到连一行都放不下：仍应每行单独成批，不死循环
    batches = build_sql_batches(rows, _md5_builder, max_bytes=10)
    assert len(batches) == 3


def test_batches_empty_raises():
    with pytest.raises(ValueError):
        build_sql_batches([], _md5_builder, max_bytes=120_000)


# ── 多列手机号 ────────────────────────────────────────────────────────────────
from app.match_phones import (
    list_columns_from_text,
    list_columns_from_excel,
    read_phones_from_excel,
)


def test_read_text_multi_columns_row_major():
    text = "a,b\n13800000001,13800000002\n13800000003,13800000004\n"
    result = read_phones_from_text(text, ["a", "b"])
    # 行优先：第一行的 a,b 再第二行的 a,b
    assert result == ["13800000001", "13800000002", "13800000003", "13800000004"]


def test_read_text_multi_columns_skips_missing():
    text = "a,b\n13800000001,13800000002\n"
    # c 不存在 → 跳过，不报错
    result = read_phones_from_text(text, ["a", "c"])
    assert result == ["13800000001"]


def test_read_text_single_column_still_raises_on_missing():
    text = "a,b\n13800000001,13800000002\n"
    with pytest.raises(ValueError):
        read_phones_from_text(text, "missing")


def test_list_columns_from_text_csv():
    text = "phone1,phone2,name\n13800000001,13800000002,alice\n"
    assert list_columns_from_text(text) == ["phone1", "phone2", "name"]


def test_list_columns_from_text_plain_returns_empty():
    text = "13800000001\n13800000002\n"
    assert list_columns_from_text(text) == []


def _make_xlsx_multi(rows, cols, sheet="Sheet1"):
    import io as _io
    import pandas as _pd
    buf = _io.BytesIO()
    with _pd.ExcelWriter(buf, engine="openpyxl") as w:
        _pd.DataFrame(rows, columns=cols).to_excel(w, sheet_name=sheet, index=False)
    return buf.getvalue()


def test_read_excel_multi_columns_row_major():
    data = _make_xlsx_multi(
        [["13800000001", "13800000002"], ["13800000003", "13800000004"]],
        ["a", "b"],
    )
    result = read_phones_from_excel(data, sheet="Sheet1", column=["a", "b"])
    assert result == ["13800000001", "13800000002", "13800000003", "13800000004"]


def test_list_columns_from_excel():
    data = _make_xlsx_multi([["13800000001", "x"]], ["phone", "note"])
    assert list_columns_from_excel(data, sheet="Sheet1") == ["phone", "note"]
