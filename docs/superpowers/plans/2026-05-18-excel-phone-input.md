# Excel 手机号输入解析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在手机号输入区域（① 明文手机号）增加 `.xlsx`/`.xls` 文件上传支持，上传后让用户选择 Sheet，复用现有列名输入框指定列。

**Architecture:** 在 `match_phones.py` 新增 `read_phones_from_excel(data, sheet, column)` 纯函数，与现有 `read_phones_from_text` 并列；`streamlit_app.py` 检测文件后缀，Excel 文件走新路径（两步交互：选 Sheet → 解析），非 Excel 文件走现有路径不变。

**Tech Stack:** Python 3.10+, pandas (已有), openpyxl>=3.1 (新增), Streamlit (已有)

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `requirements.txt` | 新增 openpyxl 依赖 |
| Modify | `app/match_phones.py` | 新增 `read_phones_from_excel()` |
| Modify | `app/streamlit_app.py` | 文件上传扩展 + Sheet 选择 UI |
| Create | `tests/test_excel_parsing.py` | 单元测试 |

---

## Task 1: 安装依赖 & 创建测试文件骨架

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/test_excel_parsing.py`

- [ ] **Step 1: 在 requirements.txt 末尾追加 openpyxl**

  `requirements.txt` 最终内容：
  ```
  streamlit>=1.28
  pandas>=2.0
  pyodps>=0.11
  openpyxl>=3.1
  ```

- [ ] **Step 2: 安装依赖**

  ```bash
  pip install openpyxl>=3.1
  ```
  预期：Successfully installed openpyxl-...

- [ ] **Step 3: 创建 tests/__init__.py（空文件）**

  ```bash
  mkdir -p tests && touch tests/__init__.py
  ```

- [ ] **Step 4: 创建测试文件骨架**

  `tests/test_excel_parsing.py`:
  ```python
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
  ```

- [ ] **Step 5: 验证测试文件可导入**

  ```bash
  cd /Users/king/Documents/ClaudeWorkspace/phone-match-web
  python -m pytest tests/test_excel_parsing.py --collect-only
  ```
  预期：`no tests ran`（无报错）

- [ ] **Step 6: Commit**

  ```bash
  git add requirements.txt tests/__init__.py tests/test_excel_parsing.py
  git commit -m "chore: add openpyxl dep and test scaffold for Excel parsing"
  ```

---

## Task 2: 实现 read_phones_from_excel（TDD）

**Files:**
- Modify: `app/match_phones.py`（在 `read_phones_from_text` 之后插入新函数）
- Modify: `tests/test_excel_parsing.py`

- [ ] **Step 1: 写失败测试——单列无表头情况（取第一列）**

  在 `tests/test_excel_parsing.py` 末尾追加：
  ```python
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
  ```

- [ ] **Step 2: 运行测试，确认失败**

  ```bash
  cd /Users/king/Documents/ClaudeWorkspace/phone-match-web
  python -m pytest tests/test_excel_parsing.py -v
  ```
  预期：`ImportError` 或 `AttributeError: module 'app.match_phones' has no attribute 'read_phones_from_excel'`

- [ ] **Step 3: 在 match_phones.py 顶部 import 区（第 16 行之后）追加 pandas 导入**

  在现有 `from typing import ...` 行之后添加：
  ```python
  import pandas as pd
  ```

- [ ] **Step 4: 在 match_phones.py 中实现函数**

  在 `read_phones_from_file` 函数（第 189 行）之后插入：

  ```python
  def read_phones_from_excel(
      data: bytes,
      sheet: "str | int",
      column: "Optional[str]",
  ) -> "List[str]":
      """从 Excel bytes 中解析手机号列表。

      sheet: Sheet 名称或索引（0-based）。
      column: 列名；为 None 时取第一列。
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

      return [v.strip() for v in series.astype(str).str.strip() if v.strip() and v.strip() != "nan"]
  ```

- [ ] **Step 5: 运行测试，确认全部通过**

  ```bash
  python -m pytest tests/test_excel_parsing.py -v
  ```
  预期：5 个测试全部 PASS

- [ ] **Step 6: Commit**

  ```bash
  git add app/match_phones.py tests/test_excel_parsing.py
  git commit -m "feat: add read_phones_from_excel to match_phones"
  ```

---

## Task 3: 更新 Streamlit UI

**Files:**
- Modify: `app/streamlit_app.py`

**目标：**
1. `file_uploader` 支持 `xlsx`/`xls` 类型
2. 上传 Excel 文件后，展示 Sheet 下拉框（`st.selectbox`）
3. 用户选好 Sheet 后，调用 `read_phones_from_excel`；非 Excel 走现有路径

- [ ] **Step 1: 在 streamlit_app.py 顶部增加 `import io`（现有的是 `from io import StringIO`，需额外加一行）**

  在 `from io import StringIO` 行之后（约第 11 行）插入：
  ```python
  import io
  ```

- [ ] **Step 2: 在 streamlit_app.py 顶部的 import 区更新**

  在 `from app.match_phones import (` 块中，增加 `read_phones_from_excel`：

  ```python
  from app.match_phones import (
      DEFAULT_MC_CIPHER_COLUMN,
      DEFAULT_MC_PARTITION_EXPR,
      DEFAULT_MC_USER_TABLE,
      MATCH_RESULT_HEADER,
      build_mc_online_sql,
      compute_match_rows,
      load_warehouse_map_from_text,
      read_phones_from_excel,
      read_phones_from_text,
  )
  ```

- [ ] **Step 3: 更新文件上传组件的 type 列表和提示文字**

  找到（约第 296-300 行）：
  ```python
  up = st.file_uploader(
      "支持 TXT / CSV / TSV，单文件 ≤ 100 MB",
      type=["txt", "csv", "tsv"],
      label_visibility="collapsed",
  )
  ```
  替换为：
  ```python
  up = st.file_uploader(
      "支持 TXT / CSV / TSV / Excel，单文件 ≤ 100 MB",
      type=["txt", "csv", "tsv", "xlsx", "xls"],
      label_visibility="collapsed",
  )
  ```

- [ ] **Step 4: 更新 Hero 区域的格式标签**

  找到（约第 231 行）：
  ```python
  <span class="metric-chip green">支持 TXT / CSV / TSV</span>
  ```
  替换为：
  ```python
  <span class="metric-chip green">支持 TXT / CSV / TSV / Excel</span>
  ```

- [ ] **Step 5: 替换文件解析逻辑块（约第 318-321 行）**

  找到：
  ```python
      body = typed
      if up is not None:
          body = up.getvalue().decode("utf-8-sig", errors="replace")
          st.success(f"已载入上传文件（{len(body):,} 字符），覆盖文本框内容")
  ```
  替换为：
  ```python
      body = typed
      excel_phones: list[str] | None = None  # 非 None 时直接用，跳过 body 解析

      if up is not None:
          suffix = up.name.rsplit(".", 1)[-1].lower()
          if suffix in ("xlsx", "xls"):
              raw_bytes = up.getvalue()
              try:
                  xf = pd.ExcelFile(io.BytesIO(raw_bytes))
                  sheet_names = xf.sheet_names
              except Exception as e:
                  st.error(f"无法读取 Excel 文件：{e}")
                  sheet_names = []
              if sheet_names:
                  selected_sheet = st.selectbox(
                      "选择 Sheet",
                      options=sheet_names,
                      key="excel_sheet_select",
                  )
                  try:
                      excel_phones = read_phones_from_excel(
                          raw_bytes,
                          sheet=selected_sheet,
                          column=col_phone or None,
                      )
                      st.success(f"已从 Excel「{selected_sheet}」载入 {len(excel_phones):,} 行")
                  except (ValueError, Exception) as e:
                      st.error(f"解析 Excel 失败：{e}")
                      excel_phones = []
          else:
              body = up.getvalue().decode("utf-8-sig", errors="replace")
              st.success(f"已载入上传文件（{len(body):,} 字符），覆盖文本框内容")
  ```

- [ ] **Step 6: 在手机号解析块使用 excel_phones**

  找到（约第 330 行）：
  ```python
  phones_err: str | None = None
  phones_list: list[str] = []
  if body.strip():
      try:
          phones_list = read_phones_from_text(body, col_phone or None)
      except ValueError as e:
          phones_err = str(e)
  ```
  替换为：
  ```python
  phones_err: str | None = None
  phones_list: list[str] = []
  if excel_phones is not None:
      phones_list = excel_phones
  elif body.strip():
      try:
          phones_list = read_phones_from_text(body, col_phone or None)
      except ValueError as e:
          phones_err = str(e)
  ```

- [ ] **Step 7: 手动验证 UI（启动 Streamlit）**

  ```bash
  cd /Users/king/Documents/ClaudeWorkspace/phone-match-web
  streamlit run app/streamlit_app.py
  ```

  验证清单：
  - [ ] 文件上传区提示文字变为「支持 TXT / CSV / TSV / Excel」
  - [ ] 上传 xlsx 文件 → 出现 Sheet 下拉框
  - [ ] 选择 Sheet 后 → 显示「已从 Excel 载入 N 行」
  - [ ] 上传 CSV/TXT → 不出现 Sheet 下拉框，走原有路径
  - [ ] 粘贴框内容在无文件上传时仍正常解析

- [ ] **Step 8: Commit**

  ```bash
  git add app/streamlit_app.py
  git commit -m "feat: support Excel file upload for phone number input"
  ```

---

## Task 4: 边界处理与最终收尾

**Files:**
- Modify: `tests/test_excel_parsing.py`（补充 NaN 处理测试）

- [ ] **Step 1: 追加 NaN/空字符串过滤测试**

  在 `tests/test_excel_parsing.py` 末尾追加：
  ```python
  def test_nan_and_blank_cells_filtered():
      """NaN 单元格和空字符串不出现在结果中。"""
      import io
      import openpyxl
      wb = openpyxl.Workbook()
      ws = wb.active
      ws.title = "Sheet1"
      ws.append(["phone"])
      ws.append(["13812345678"])
      ws.append([None])          # NaN
      ws.append([""])            # 空字符串
      ws.append(["  "])          # 纯空格
      ws.append(["13900000000"])
      buf = io.BytesIO()
      wb.save(buf)
      result = read_phones_from_excel(buf.getvalue(), sheet="Sheet1", column=None)
      assert result == ["13812345678", "13900000000"]
  ```

- [ ] **Step 2: 运行全部测试**

  ```bash
  cd /Users/king/Documents/ClaudeWorkspace/phone-match-web
  python -m pytest tests/test_excel_parsing.py -v
  ```
  预期：6 个测试全部 PASS

- [ ] **Step 3: Commit**

  ```bash
  git add tests/test_excel_parsing.py
  git commit -m "test: add NaN/blank cell filter test for read_phones_from_excel"
  ```
