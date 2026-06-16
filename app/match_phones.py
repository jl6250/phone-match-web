#!/usr/bin/env python3
"""
根据明文手机号计算 MD5，与数仓导出的「密文」列比对，输出 login_name。

默认规则（可用 --ten-variant 切换）：
  - 11 位：对纯数字串（默认取连续数字）做 MD5，32 位小写 hex。
  - 10 位：对「去掉左侧所有 0」后的数字串做 MD5（若业务是去首位 1，请用 --ten-variant drop_first_one）。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# column 选择：None=首列/整行；str=单列；Sequence[str]=多列
ColumnSpec = Union[str, Sequence[str], None]

import pandas as pd

# 数仓默认（可用命令行覆盖）
DEFAULT_MC_USER_TABLE = "superengineproject.dim_user_info_df"
DEFAULT_MC_CIPHER_COLUMN = "phone_hex"
DEFAULT_MC_PARTITION_EXPR = f"pt = MAX_PT('{DEFAULT_MC_USER_TABLE}')"


def digits_only(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def normalize_ph_mobile(raw: str) -> str:
    """Strip +63/63 country code; return 10-digit or 0-prefixed 11-digit PH mobile."""
    d = digits_only(raw)
    # +639XXXXXXXXX → 9XXXXXXXXX (12→10), +6309XXXXXXXXX → 09XXXXXXXXX (13→11)
    if d.startswith("63") and len(d) in (12, 13):
        d = d[2:]
    return d


def md5_hex_lower(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def normalize_hex(s: str) -> str:
    return s.strip().lower()


_MD5_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def is_md5_hex(s: str) -> bool:
    """判断 s 是否为 32 位十六进制 MD5（忽略前后空白与大小写）。"""
    return bool(_MD5_HEX_RE.match(s.strip().lower()))


def compute_key10(digits: str) -> str:
    """去掉最左侧连续 0 → 10 位密钥。"""
    return digits.lstrip("0")


def compute_key11(key10: str) -> str:
    """最左侧加 0 补至 11 位 → 11 位密钥。"""
    return key10.zfill(11)


def sql_escape_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def mc_key10_expr(digits_col: str = "digits_only") -> str:
    """MaxCompute：去掉最左侧 0（对应 Python compute_key10）。"""
    return f"regexp_replace({digits_col}, '^0+', '')"


def mc_key11_expr(digits_col: str = "digits_only") -> str:
    """MaxCompute：去掉最左侧 0 后左填 0 至 11 位（对应 Python compute_key11）。"""
    return f"LPAD(regexp_replace({digits_col}, '^0+', ''), 11, '0')"


def build_mc_online_sql(
    plain_rows: List[str],
    *,
    mc_table: str,
    login_column: str,
    cipher_column: str,
    partition_predicate: str,
    extra_where: Optional[str],
) -> str:
    """生成在 MaxCompute 在线执行的关联 SQL（小批量明文驱动，先算 MD5 再 IN 过滤大表）。"""
    if not plain_rows:
        raise ValueError("无手机号，无法生成 SQL")

    values_sql = ",\n    ".join(
        f"({i + 1}, {sql_escape_literal(p)})" for i, p in enumerate(plain_rows)
    )
    key10_expr = mc_key10_expr("digits_only")
    key11_expr = mc_key11_expr("digits_only")

    extra = ""
    if extra_where and extra_where.strip():
        extra = f"\n    AND ({extra_where.strip()})"

    lc, cc = login_column.strip(), cipher_column.strip()

    return f"""-- 由 phone-match-web（match_phones）--emit-sql 生成（MaxCompute / ODPS）
-- 用户表: {mc_table} | 密文列: {cc} | 分区: {partition_predicate}
-- 10位规则：去掉最左侧0；11位规则：10位结果左填0至11位；结果按输入顺序排列
-- 明文过多时若超 SQL 长度，请拆成多批或先写入临时表再 UNION
set odps.sql.validate.orderby.limit=false;

WITH raw_input AS (
  SELECT * FROM VALUES
    {values_sql}
  AS t(rn, plain_raw)
),
prep AS (
  SELECT
    rn,
    plain_raw,
    regexp_replace(plain_raw, '[^0-9]', '') AS digits_only
  FROM raw_input
),
keys AS (
  SELECT
    rn,
    plain_raw,
    digits_only,
    md5({key11_expr}) AS md5_hex_11,
    md5({key10_expr}) AS md5_hex_10
  FROM prep
),
hash_filter AS (
  SELECT LOWER(TRIM(md5_hex_11)) AS h FROM keys
  UNION
  SELECT LOWER(TRIM(md5_hex_10)) AS h FROM keys
),
-- 仓库侧：每个 cipher 取一个 login_name（GROUP BY 消除重复，避免 JOIN 膨胀）
u_cipher_map AS (
  SELECT
    LOWER(TRIM({cc})) AS cipher_key,
    MAX({lc})         AS login_name
  FROM {mc_table}
  WHERE {partition_predicate}
    AND {cc} IS NOT NULL
    AND LOWER(TRIM({cc})) IN (SELECT h FROM hash_filter){extra}
  GROUP BY LOWER(TRIM({cc}))
)
SELECT
  k.plain_raw,
  k.digits_only,
  k.md5_hex_11,
  k.md5_hex_10,
  COALESCE(u11.login_name, u10.login_name) AS login_name,
  COALESCE(u11.cipher_key, u10.cipher_key) AS matched_cipher_in_warehouse,
  CASE
    WHEN u11.login_name IS NOT NULL THEN 'md5_11'
    WHEN u10.login_name IS NOT NULL THEN 'md5_10'
    ELSE NULL
  END AS match_via
FROM keys k
LEFT JOIN u_cipher_map u11 ON LOWER(TRIM(k.md5_hex_11)) = u11.cipher_key
LEFT JOIN u_cipher_map u10 ON LOWER(TRIM(k.md5_hex_10)) = u10.cipher_key
ORDER BY k.rn;
"""


def build_mc_md5_match_sql(
    md5_rows: List[str],
    *,
    mc_table: str,
    login_column: str,
    cipher_column: str,
    partition_predicate: str,
    extra_where: Optional[str],
) -> str:
    """生成 MaxCompute 直接匹配 SQL：输入已是 MD5 hex，直接 IN 过滤密文列。"""
    if not md5_rows:
        raise ValueError("无 MD5，无法生成 SQL")

    values_sql = ",\n    ".join(
        f"({i + 1}, {sql_escape_literal(h)})" for i, h in enumerate(md5_rows)
    )

    extra = ""
    if extra_where and extra_where.strip():
        extra = f"\n    AND ({extra_where.strip()})"

    lc, cc = login_column.strip(), cipher_column.strip()

    return f"""-- 由 phone-match-web（MD5 直接匹配）生成（MaxCompute / ODPS）
-- 用户表: {mc_table} | 密文列: {cc} | 分区: {partition_predicate}
-- 输入已是 MD5 密文，直接以 {cc} IN (...) 过滤大表，结果按输入顺序排列
set odps.sql.validate.orderby.limit=false;

WITH raw_input AS (
  SELECT * FROM VALUES
    {values_sql}
  AS t(rn, md5_raw)
),
norm AS (
  SELECT rn, md5_raw, LOWER(TRIM(md5_raw)) AS h FROM raw_input
),
hash_filter AS (
  SELECT DISTINCT h FROM norm
),
-- 仓库侧：每个 cipher 取一个 login_name（GROUP BY 消除重复，避免 JOIN 膨胀）
u_cipher_map AS (
  SELECT
    LOWER(TRIM({cc})) AS cipher_key,
    MAX({lc})         AS login_name
  FROM {mc_table}
  WHERE {partition_predicate}
    AND {cc} IS NOT NULL
    AND LOWER(TRIM({cc})) IN (SELECT h FROM hash_filter){extra}
  GROUP BY LOWER(TRIM({cc}))
)
SELECT
  n.md5_raw,
  u.login_name,
  u.cipher_key AS matched_cipher_in_warehouse,
  CASE WHEN u.login_name IS NOT NULL THEN 'matched' ELSE NULL END AS match_via
FROM norm n
LEFT JOIN u_cipher_map u ON n.h = u.cipher_key
ORDER BY n.rn;
"""


def build_sql_batches(
    rows: List[str],
    build_one: Callable[[List[str]], str],
    max_bytes: int = 120_000,
) -> List[str]:
    """把 rows 拆成多批，使每批 build_one(batch) 的 UTF-8 字节数 ≤ max_bytes。

    用于绕过 DataWorks/编辑器对单段 SQL 体积的限制（如 130KB）。每批都是
    build_one 生成的独立完整 SQL（行号 rn 在批内从 1 重新计数）。

    返回 SQL 字符串列表（至少一项）。空 rows 透传 build_one 的 ValueError。
    若单行即超 max_bytes，则该行单独成批（仍会超限，由调用方告警）。
    """
    if not rows:
        return [build_one(rows)]  # 触发与 build_one 一致的空输入错误

    n = len(rows)
    batches: List[str] = []
    i = 0
    while i < n:
        # 二分找最大的 j（i < j ≤ n）使 build_one(rows[i:j]) 字节数 ≤ max_bytes
        lo, hi, best = i + 1, n, i + 1
        while lo <= hi:
            mid = (lo + hi) // 2
            size = len(build_one(rows[i:mid]).encode("utf-8"))
            if size <= max_bytes:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        batches.append(build_one(rows[i:best]))  # best ≥ i+1，保证推进、不死循环
        i = best
    return batches


def read_phones_from_text(
    text: str,
    column: ColumnSpec,
    normalizer: Callable[[str], str] = normalize_ph_mobile,
) -> List[str]:
    """从已解码文本解析手机号（或其他值）列表。

    column:
      - None：纯文本一行一个；含分隔符时取第一列。
      - str：单列（列名找不到时抛出 ValueError）。
      - Sequence[str]：多列，按「行优先」展开；缺失的列跳过（不报错）。
    normalizer: 对每个原始字符串做规范化；返回空串则丢弃该值。
    默认为 normalize_ph_mobile（明文 PH 手机号去国家码逻辑）。
    """
    rows: List[str] = []
    lines_split = text.splitlines()
    if not lines_split:
        return []

    multi = isinstance(column, (list, tuple))
    single = isinstance(column, str)

    first = lines_split[0]
    if column is None and "," not in first and "\t" not in first:
        for line in lines_split:
            line = line.strip()
            if line:
                rows.append(line)
        return [n for r in rows if (n := normalizer(r))]

    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    lines = list(reader)
    if not lines:
        return []

    if multi:
        header = [h.strip() for h in lines[0]]
        idxs = [header.index(c) for c in column if c in header]  # 缺失列跳过
        out: List[str] = []
        for row in lines[1:]:
            if not row:
                continue
            for ci in idxs:  # 行优先：同一行内按所选列顺序
                if ci < len(row):
                    v = row[ci].strip()
                    if v and (n := normalizer(v)):
                        out.append(n)
        return out

    start = 0
    col_idx = 0
    if single:
        header = [h.strip() for h in lines[0]]
        if column not in header:
            raise ValueError(f"列 {column!r} 不在表头中: {header}")
        col_idx = header.index(column)
        start = 1

    for row in lines[start:]:
        if not row:
            continue
        if col_idx < len(row):
            v = row[col_idx].strip()
            if v:
                rows.append(v)
    return [n for r in rows if (n := normalizer(r))]


def list_columns_from_text(text: str) -> List[str]:
    """解析 CSV/TSV 文本表头，返回列名列表（首行）。纯文本/空返回 []。"""
    lines = text.splitlines()
    if not lines:
        return []
    first = lines[0]
    if "," not in first and "\t" not in first:
        return []  # 纯文本一行一个，无表头
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    header = next(csv.reader([first], delimiter=delimiter), [])
    return [h.strip() for h in header]


def read_phones_from_file(
    path: Path,
    column: Optional[str],
    encoding: str,
    normalizer: Callable[[str], str] = normalize_ph_mobile,
) -> List[str]:
    return read_phones_from_text(path.read_text(encoding=encoding), column, normalizer)


def _clean_excel_cell(x: object) -> str:
    """清洗单元格字符串：去空白与 _x000D_/\\r 伪影；空占位返回空串。"""
    s = str(x).strip().replace("_x000D_", "").replace("\r", "")
    return "" if s in ("nan", "None", "NaT") else s


def read_phones_from_excel(
    data: bytes,
    sheet: "str | int",
    column: ColumnSpec,
    normalizer: "Callable[[str], str]" = normalize_ph_mobile,
) -> "List[str]":
    """从 Excel bytes 中解析手机号（或其他值）列表。

    sheet: Sheet 名称或索引（0-based）。
    column:
      - None：取第一列。
      - str：单列（不存在时抛出 ValueError）。
      - Sequence[str]：多列，按「行优先」展开；缺失的列跳过（不报错）。
    normalizer: 对每个原始字符串做规范化；返回空串则丢弃该值。
    默认为 normalize_ph_mobile（明文 PH 手机号去国家码逻辑）。
    """
    import io as _io
    df = pd.read_excel(_io.BytesIO(data), sheet_name=sheet, dtype=str)
    df = df.dropna(how="all")

    if isinstance(column, (list, tuple)):
        cols = [c for c in column if c in df.columns]  # 缺失列跳过
        out: List[str] = []
        for _, row in df.iterrows():
            for c in cols:  # 行优先：同一行内按所选列顺序
                s = _clean_excel_cell(row[c])
                if s and (n := normalizer(s)):
                    out.append(n)
        return out

    if column is not None:
        if column not in df.columns:
            raise ValueError(f"列 {column!r} 不在表头中: {list(df.columns)}")
        series = df[column]
    else:
        series = df.iloc[:, 0]

    return [
        n for x in series
        if (s := _clean_excel_cell(x))
        if (n := normalizer(s))
    ]


def list_columns_from_excel(data: bytes, sheet: "str | int") -> List[str]:
    """返回指定 Sheet 的列名列表（仅读表头，不读数据）。"""
    import io as _io
    df = pd.read_excel(_io.BytesIO(data), sheet_name=sheet, dtype=str, nrows=0)
    return [str(c) for c in df.columns]


def load_warehouse_map_from_text(text: str, login_col: str, cipher_col: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """cipher(hex) -> [login_name, ...]，保留重复以便告警。"""
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("对照表无法解析表头")
    fields = [f.strip() for f in reader.fieldnames]
    if login_col not in fields or cipher_col not in fields:
        raise ValueError(f"对照表需要列 {login_col!r} 与 {cipher_col!r}，当前表头: {fields}")

    cipher_to_logins: Dict[str, List[str]] = {}
    dup_warnings: List[str] = []

    for row in reader:
        ln = (row.get(login_col) or "").strip()
        cp = normalize_hex((row.get(cipher_col) or "").strip())
        if not ln or not cp:
            continue
        if cp not in cipher_to_logins:
            cipher_to_logins[cp] = []
        cipher_to_logins[cp].append(ln)

    for cp, names in cipher_to_logins.items():
        if len(names) > 1:
            uniq = sorted(set(names))
            if len(uniq) > 1:
                dup_warnings.append(f"密文 {cp[:12]}… 对应多个 login_name: {uniq}")

    return cipher_to_logins, dup_warnings


def load_warehouse_map(
    path: Path,
    login_col: str,
    cipher_col: str,
    encoding: str,
) -> Tuple[Dict[str, List[str]], List[str]]:
    return load_warehouse_map_from_text(path.read_text(encoding=encoding), login_col, cipher_col)


MATCH_RESULT_HEADER = [
    "plain_input",
    "digits_only",
    "md5_key_11",
    "md5_hex_11",
    "md5_key_10",
    "md5_hex_10",
    "match_via",
    "matched_cipher",
    "login_name",
    "note",
]


def compute_match_rows(
    phones: List[str],
    cipher_map: Dict[str, List[str]],
    uppercase_hex: bool,
) -> List[List[str]]:
    out_rows: List[List[str]] = []
    for raw in phones:
        d, key11, key10, h11, h10 = match_one(raw, uppercase_hex)
        h11_lo = normalize_hex(h11)
        h10_lo = normalize_hex(h10)

        match_via = ""
        matched_cipher = ""
        login_name = ""
        note = ""

        if h11_lo in cipher_map:
            match_via = "md5_11"
            matched_cipher = h11
            names = cipher_map[h11_lo]
            login_name = names[0]
            if len(set(names)) > 1:
                note = f"ambiguous login: {names}"
        elif h10_lo in cipher_map:
            match_via = "md5_10"
            matched_cipher = h10
            names = cipher_map[h10_lo]
            login_name = names[0]
            if len(set(names)) > 1:
                note = f"ambiguous login: {names}"
        else:
            note = "no_match"

        out_rows.append(
            [
                raw,
                d,
                key11,
                h11,
                key10,
                h10,
                match_via,
                matched_cipher,
                login_name,
                note,
            ]
        )
    return out_rows


def match_one(
    plain_raw: str,
    uppercase_hex: bool,
) -> Tuple[str, str, str, str, str]:
    """返回 digits_only, md5_key_11, md5_key_10, md5_hex_11, md5_hex_10。

    规则：key10 = 去掉最左侧0；key11 = key10 左填0至11位。
    """
    d = digits_only(plain_raw)
    key10 = compute_key10(d)
    key11 = compute_key11(key10)
    md10 = md5_hex_lower(key10)
    md11 = md5_hex_lower(key11)

    def fmt(h: str) -> str:
        return h.upper() if uppercase_hex else h

    return d, key11, key10, fmt(md11), fmt(md10)


MC_EXPORT_SQL_EPILOG = f"""
离线比对：从 MC 导出 login_name + phone_hex 为 CSV，使用 --warehouse。

在线 SQL（大表不落本地）：--emit-sql，默认表 {DEFAULT_MC_USER_TABLE}、密文列
{DEFAULT_MC_CIPHER_COLUMN}、分区 {DEFAULT_MC_PARTITION_EXPR}；可用
--mc-table / --cipher-column / --partition-expr 覆盖。
"""


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="手机号 MD5 与数仓密文比对，关联 login_name",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=MC_EXPORT_SQL_EPILOG,
    )
    p.add_argument("--phones", type=Path, required=True, help="明文手机号：纯文本一行一个，或 CSV/TSV（默认取第一列）")
    p.add_argument("--phones-column", default=None, help="CSV/TSV 中手机号所在列名")
    p.add_argument(
        "--warehouse",
        type=Path,
        default=None,
        help="【离线模式】从 MC 导出的 CSV/TSV：含 login_name 与密文列（与 --emit-sql 二选一）",
    )
    p.add_argument("--login-column", default="login_name", help="MC 表或导出 CSV 中的登录名列名")
    p.add_argument(
        "--cipher-column",
        default=DEFAULT_MC_CIPHER_COLUMN,
        help=f"MC 表或导出 CSV 中的手机 MD5 列名（默认 {DEFAULT_MC_CIPHER_COLUMN}）",
    )
    p.add_argument("--uppercase-md5", action="store_true", help="输出大写 MD5（与仓里存放大写时一致）")
    p.add_argument("--encoding", default="utf-8-sig", help="文件编码，Excel 导出常用 utf-8-sig")
    p.add_argument("-o", "--output", type=Path, default=None, help="输出路径：离线模式为 CSV；--emit-sql 时为 .sql（默认 stdout）")
    p.add_argument(
        "--emit-sql",
        action="store_true",
        help="生成 MaxCompute 在线关联 SQL（无需 --warehouse），写入 -o 或 stdout",
    )
    p.add_argument(
        "--mc-table",
        default=DEFAULT_MC_USER_TABLE,
        help=f"【在线 SQL】用户表全名（默认 {DEFAULT_MC_USER_TABLE}）",
    )
    p.add_argument(
        "--partition-expr",
        default=DEFAULT_MC_PARTITION_EXPR,
        help=f"【在线 SQL】分区谓词（默认使用 MAX_PT 取最新分区）",
    )
    p.add_argument(
        "--extra-where",
        default=None,
        help="【在线 SQL】附加 AND 条件（可选），如 business_line = 'bp'",
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    if not args.emit_sql and not args.warehouse:
        p.error("离线比对请指定 --warehouse，或增加 --emit-sql 生成在线 SQL")

    try:
        phones = read_phones_from_file(args.phones, args.phones_column, args.encoding)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.emit_sql:
        sql_text = build_mc_online_sql(
            phones,
            mc_table=args.mc_table or "",
            login_column=args.login_column,
            cipher_column=args.cipher_column,
            partition_predicate=args.partition_expr or "",
            extra_where=args.extra_where,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(sql_text, encoding="utf-8")
        else:
            sys.stdout.write(sql_text)
        return 0

    assert args.warehouse is not None
    cipher_map, dup_warnings = load_warehouse_map(
        args.warehouse,
        args.login_column,
        args.cipher_column,
        args.encoding,
    )

    for w in dup_warnings:
        print(w, file=sys.stderr)

    out_rows = compute_match_rows(phones, cipher_map, args.uppercase_md5)
    header = MATCH_RESULT_HEADER

    def write_output(stream) -> None:
        w = csv.writer(stream)
        w.writerow(header)
        w.writerows(out_rows)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8-sig", newline="") as f:
            write_output(f)
    else:
        write_output(sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
