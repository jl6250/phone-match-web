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
