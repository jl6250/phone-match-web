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
