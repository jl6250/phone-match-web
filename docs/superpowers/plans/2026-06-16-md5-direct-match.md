# MD5 手机号直接匹配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户直接粘贴/上传一批已是 MD5 的值，生成「`phone_hex IN (这些 MD5)` 直接过滤大表」的 MaxCompute SQL，返回 `login_name`，跳过算 MD5 这步。

**Architecture:** 解析层（`app/match_phones.py` 纯函数）新增 MD5 校验与一个不含 `md5()` 计算的 SQL 生成函数，并把现有 `read_*` 函数泛化出 `normalizer` 参数；UI 层（`app/streamlit_app.py`）加「输入类型」开关，按类型路由解析与 SQL 生成。两层通过明确函数签名通信。

**Tech Stack:** Python 3、Streamlit、pandas、pytest。所有命令在 `phone-match-web/` 目录下运行；测试用 `PYTHONPATH=. python3 -m pytest`。

参考 spec：`docs/superpowers/specs/2026-06-16-md5-direct-match-design.md`

---

### Task 1: `is_md5_hex` 校验函数

**Files:**
- Modify: `app/match_phones.py`（在 `normalize_hex` 附近新增函数）
- Test: `tests/test_md5_direct_match.py`（新建）

- [ ] **Step 1: Write the failing test**

新建 `tests/test_md5_direct_match.py`：

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_md5_hex'`

- [ ] **Step 3: Write minimal implementation**

在 `app/match_phones.py` 顶部 import 区已存在 `import re`？没有则添加 `import re`（放在 `import hashlib` 下方）。在 `normalize_hex` 函数之后新增：

```python
_MD5_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


def is_md5_hex(s: str) -> bool:
    """判断 s 是否为 32 位十六进制 MD5（忽略前后空白与大小写）。"""
    return bool(_MD5_HEX_RE.match(s.strip().lower()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add app/match_phones.py tests/test_md5_direct_match.py
git commit -m "feat: add is_md5_hex validator"
```

---

### Task 2: 泛化 `read_phones_from_text` / `read_phones_from_excel` 的 normalizer 参数

**Files:**
- Modify: `app/match_phones.py`（`read_phones_from_text`、`read_phones_from_excel` 签名与函数体）
- Test: `tests/test_md5_direct_match.py`

**说明：** 默认 `normalizer=normalize_ph_mobile`，保证现有明文行为完全不变；MD5 模式传入一个「strip+lower、保留非空」的 normalizer。

- [ ] **Step 1: Write the failing test**

在 `tests/test_md5_direct_match.py` 末尾追加：

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py -k normalizer -v`
Expected: FAIL with `TypeError: read_phones_from_text() got an unexpected keyword argument 'normalizer'`

- [ ] **Step 3: Write minimal implementation**

在 `app/match_phones.py` 中修改两个函数。

`read_phones_from_text` —— 改签名并把两处 `normalize_ph_mobile(r)` 替换为 `normalizer(r)`：

```python
def read_phones_from_text(
    text: str,
    column: Optional[str],
    normalizer: Callable[[str], str] = normalize_ph_mobile,
) -> List[str]:
    """从已解码文本解析值列表；列名找不到时抛出 ValueError。

    normalizer: 对每个原始值做归一化，返回空串表示丢弃（默认按明文手机号处理）。
    """
    rows: List[str] = []
    lines_split = text.splitlines()
    if not lines_split:
        return []
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
    start = 0
    col_idx = 0
    if column:
        header = [h.strip() for h in lines[0]]
        if column not in header:
            raise ValueError(f"列 {column!r} 不在表头中: {header}")
        col_idx = header.index(column)
        start = 1
    else:
        col_idx = 0

    for row in lines[start:]:
        if not row:
            continue
        if col_idx < len(row):
            v = row[col_idx].strip()
            if v:
                rows.append(v)
    return [n for r in rows if (n := normalizer(r))]
```

`read_phones_from_file` 同步加参数并透传：

```python
def read_phones_from_file(
    path: Path,
    column: Optional[str],
    encoding: str,
    normalizer: Callable[[str], str] = normalize_ph_mobile,
) -> List[str]:
    return read_phones_from_text(path.read_text(encoding=encoding), column, normalizer)
```

`read_phones_from_excel` —— 改签名并把末尾推导式的 `normalize_ph_mobile(s)` 替换为 `normalizer(s)`：

```python
def read_phones_from_excel(
    data: bytes,
    sheet: "str | int",
    column: "Optional[str]",
    normalizer: "Callable[[str], str]" = normalize_ph_mobile,
) -> "List[str]":
    """从 Excel bytes 中解析值列表。

    sheet: Sheet 名称或索引（0-based）。
    column: 列名；为 None 时取第一列。
    normalizer: 对每个原始值做归一化，返回空串表示丢弃（默认按明文手机号处理）。
    抛出 ValueError 当指定列不存在。
    """
    import io as _io
    df = pd.read_excel(_io.BytesIO(data), sheet_name=sheet, dtype=str)
    df = df.dropna(how="all")

    if column is not None:
        if column not in df.columns:
            raise ValueError(f"列 {column!r} 不在表头中: {list(df.columns)}")
        series = df[column]
    else:
        series = df.iloc[:, 0]

    return [
        n for x in series
        if (s := str(x).strip().replace("_x000D_", "").replace("\r", "")) and s not in ("nan", "None", "NaT")
        if (n := normalizer(s))
    ]
```

确认文件顶部已 `from typing import Callable, ...`（现有已有 `Callable`，无需改动）。

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py tests/test_excel_parsing.py -v`
Expected: PASS（含 Task1/Task2 新测试 + 原 excel 回归测试全过）

- [ ] **Step 5: Commit**

```bash
git add app/match_phones.py tests/test_md5_direct_match.py
git commit -m "refactor: parametrize read_phones_* with normalizer (default unchanged)"
```

---

### Task 3: `build_mc_md5_match_sql` SQL 生成函数

**Files:**
- Modify: `app/match_phones.py`（在 `build_mc_online_sql` 之后新增）
- Test: `tests/test_md5_direct_match.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_md5_direct_match.py` 末尾追加：

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py -k build_md5 -v`
Expected: FAIL with `ImportError: cannot import name 'build_mc_md5_match_sql'`

- [ ] **Step 3: Write minimal implementation**

在 `app/match_phones.py` 的 `build_mc_online_sql` 函数之后新增（复用现有 `sql_escape_literal`）：

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python3 -m pytest tests/test_md5_direct_match.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: Commit**

```bash
git add app/match_phones.py tests/test_md5_direct_match.py
git commit -m "feat: add build_mc_md5_match_sql for direct MD5 matching"
```

---

### Task 4: UI —— 输入类型开关 + 解析路由 + SQL 路由

**Files:**
- Modify: `app/streamlit_app.py`（import 区、输入区开关与解析、Tab1 SQL 生成分支）

**说明：** Streamlit UI 无自动化测试，本任务以手动验证为准（见 Step 5）。改动集中在三处。

- [ ] **Step 1: 扩展 import**

把 `app/streamlit_app.py` 顶部的 import 块改为加入新函数与校验：

```python
from app.match_phones import (
    DEFAULT_MC_CIPHER_COLUMN,
    DEFAULT_MC_PARTITION_EXPR,
    DEFAULT_MC_USER_TABLE,
    build_mc_md5_match_sql,
    build_mc_online_sql,
    is_md5_hex,
    read_phones_from_excel,
    read_phones_from_text,
)
```

- [ ] **Step 2: 输入区加「输入类型」开关并按类型解析**

在输入区 expander 内、`col_up`/`col_paste` 两列**之前**（即 `with st.expander("① 明文手机号（各 Tab 共用）", expanded=True):` 块的第一行）加入 radio：

```python
    input_kind = st.radio(
        "输入类型",
        options=["明文手机号", "MD5 密文"],
        horizontal=True,
        key="input_kind",
        help="选「MD5 密文」时，输入将按 32 位十六进制校验，直接用于匹配数仓密文列",
    )
    is_md5_mode = input_kind == "MD5 密文"
```

在文件解析与文本解析处按模式切换 normalizer。

MD5 normalizer 定义（放在解析逻辑之前，例如紧跟 `is_md5_mode = ...` 之后）：

```python
    def _md5_norm(s: str) -> str:
        return s.strip().lower()

    _normalizer = _md5_norm if is_md5_mode else None
```

Excel 解析调用处（`read_phones_from_excel(raw_bytes, sheet=sh, column=col_phone or None)`）改为：传入 normalizer 时用它，否则用默认。由于默认参数语义不同（默认=明文），用关键字按需传：

```python
                        for sh in [s for s in sheet_names if s in set(selected_sheets)]:
                            if _normalizer is not None:
                                merged.extend(read_phones_from_excel(
                                    raw_bytes, sheet=sh, column=col_phone or None,
                                    normalizer=_normalizer,
                                ))
                            else:
                                merged.extend(read_phones_from_excel(
                                    raw_bytes, sheet=sh, column=col_phone or None,
                                ))
```

文本解析处（`phones_list = read_phones_from_text(body, col_phone or None)`）改为：

```python
        try:
            if _normalizer is not None:
                phones_list = read_phones_from_text(body, col_phone or None, normalizer=_normalizer)
            else:
                phones_list = read_phones_from_text(body, col_phone or None)
        except ValueError as e:
            phones_err = str(e)
```

- [ ] **Step 3: MD5 模式下拆分有效/无效并改指标**

在「解析手机号」段之后、展示指标处，按模式区分。把现有 `elif phones_list:` 指标块替换为按模式分支：

```python
if phones_err:
    st.error(f"解析失败：{phones_err}")
elif phones_list:
    if is_md5_mode:
        valid_md5 = [h for h in phones_list if is_md5_hex(h)]
        invalid_n = len(phones_list) - len(valid_md5)
        phones_list = valid_md5  # 仅有效 MD5 进入后续 SQL 生成
        uniq_count = len(set(valid_md5))
        dup_count = len(valid_md5) - uniq_count
        dup_chip = f'<span class="metric-chip yellow">含重复 {dup_count:,} 条</span>' if dup_count else ""
        invalid_chip = f'<span class="metric-chip yellow">忽略 {invalid_n:,} 条无效（非 32 位 hex）</span>' if invalid_n else ""
        st.markdown(
            f'<div class="metric-row">'
            f'<span class="metric-chip green">✓ 已解析 {len(valid_md5):,} 条有效 MD5</span>'
            f'<span class="metric-chip">去重后 {uniq_count:,} 条唯一值</span>'
            f'{dup_chip}{invalid_chip}'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        uniq_count = len(set(phones_list))
        dup_count  = len(phones_list) - uniq_count
        dup_chip   = f'<span class="metric-chip yellow">含重复 {dup_count:,} 条</span>' if dup_count else ""
        st.markdown(
            f'<div class="metric-row">'
            f'<span class="metric-chip green">✓ 已解析 {len(phones_list):,} 条手机号</span>'
            f'<span class="metric-chip">去重后 {uniq_count:,} 条唯一值</span>'
            f'{dup_chip}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with st.expander("查看解析明细（排查行数异常用）", expanded=False):
        _df = pd.DataFrame({"#": range(1, len(phones_list)+1), "解析结果": phones_list})
        st.dataframe(_df, use_container_width=True, height=220)
```

同时，把全局「密文 MD5 为大写十六进制」勾选框在 MD5 模式下隐藏（它只对明文算 MD5 有意义）。将现有：

```python
    upper_md5_global = st.checkbox(
        "密文 MD5 为大写十六进制",
        value=False,
        key="upper_md5_global",
        help="勾选后比对时将 MD5 转大写再比对",
    )
```

改为：

```python
    if not is_md5_mode:
        upper_md5_global = st.checkbox(
            "密文 MD5 为大写十六进制",
            value=False,
            key="upper_md5_global",
            help="勾选后比对时将 MD5 转大写再比对",
        )
    else:
        upper_md5_global = False
```

- [ ] **Step 4: Tab1「生成 SQL」按钮按模式路由**

在 Tab1 的 `if st.button("生成 SQL", ...)` 块内，把 `sql = build_mc_online_sql(...)` 调用改为按模式分支：

```python
            try:
                if is_md5_mode:
                    sql = build_mc_md5_match_sql(
                        phones_list,
                        mc_table=mc_table.strip(),
                        login_column=login_col.strip(),
                        cipher_column=cipher_col.strip(),
                        partition_predicate=partition_expr.strip(),
                        extra_where=extra_where.strip() or None,
                    )
                else:
                    sql = build_mc_online_sql(
                        phones_list,
                        mc_table=mc_table.strip(),
                        login_column=login_col.strip(),
                        cipher_column=cipher_col.strip(),
                        partition_predicate=partition_expr.strip(),
                        extra_where=extra_where.strip() or None,
                    )
                st.session_state["last_sql"] = sql
```

其余（指标、`st.code`、复制按钮、下载按钮）保持不变。

- [ ] **Step 5: 手动验证**

Run: `PYTHONPATH=. python3 -c "import ast; ast.parse(open('app/streamlit_app.py').read()); print('syntax ok')"`
Expected: 输出 `syntax ok`

Run: `./run_web.sh --server.address=127.0.0.1`
手动检查：
1. 默认「明文手机号」：粘贴 `13812345678` → 生成 SQL，内容含 `md5(`（与改动前一致）。
2. 切到「MD5 密文」：粘贴一行有效 32 位 hex + 一行 `abc`（无效）→ 指标显示「1 条有效 MD5」「忽略 1 条无效」；生成的 SQL 含 `IN (SELECT h FROM hash_filter)` 且**不含** `md5(`；「大写十六进制」勾选框消失。

- [ ] **Step 6: Commit**

```bash
git add app/streamlit_app.py
git commit -m "feat: add MD5 direct-match input mode to web UI"
```

---

## Self-Review

**Spec coverage:**
- UI「输入类型」开关 → Task 4 Step 2 ✓
- MD5 模式隐藏大写勾选框 → Task 4 Step 3 ✓
- 指标显示有效/无效/去重 → Task 4 Step 3 ✓
- `is_md5_hex` → Task 1 ✓
- `read_*` 泛化 normalizer（默认不变）→ Task 2 ✓
- `build_mc_md5_match_sql`（无 `md5()`、`IN` 过滤、按 rn 排序、空抛错）→ Task 3 ✓
- Tab1 按模式路由 → Task 4 Step 4 ✓
- 测试覆盖 → Task 1/2/3 ✓；回归断言 read 默认行为 → Task 2 ✓
- 范围外（离线 CSV、Tab2、新文件类型）→ 计划未涉及，符合 spec ✓

**Placeholder scan:** 无 TBD/TODO；所有代码步骤含完整代码。

**Type consistency:** `build_mc_md5_match_sql` 签名在 Task 3 定义、Task 4 调用一致；`normalizer` 参数在 Task 2 定义、Task 4 以关键字传入一致；`is_md5_hex` 在 Task 1 定义、Task 4 使用一致。
