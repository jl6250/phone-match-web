# Design: Excel 源文件解析功能（手机号输入）

**Date:** 2026-05-18  
**Scope:** phone-match-web — 手机号输入区域（① 明文手机号）增加 .xlsx/.xls 上传支持

---

## 目标

允许用户直接上传 Excel 文件作为手机号来源，无需先将 Excel 转成 CSV/TXT。

**不在范围内：** 对照表（Tab 2）的 Excel 支持、列名下拉选择 UI。

---

## 架构

改动涉及两个文件：

### `app/match_phones.py`

新增一个函数：

```python
def read_phones_from_excel(
    data: bytes,
    sheet: str | int,
    column: str | None,
) -> list[str]:
```

- 用 `pandas.read_excel(BytesIO(data), sheet_name=sheet, dtype=str)` 读取
- `column` 为 `None` 时取第一列
- `column` 非空时按列名查找；列不存在时抛出 `ValueError`
- 返回值与 `read_phones_from_text()` 格式一致（字符串列表，strip 空值）

依赖：`openpyxl`（pandas read_excel 的 xlsx 后端，需加入 requirements.txt）

### `app/streamlit_app.py`

① **文件上传组件**：`type` 列表增加 `"xlsx"`, `"xls"`；提示文字更新为「支持 TXT / CSV / TSV / Excel」

② **两步交互（仅 Excel 触发）**：

```
上传 Excel 文件
    ↓
读取 sheet_names（不加载数据）
    ↓
展示 Sheet 下拉选择框（st.selectbox，存入 session_state）
    ↓
用现有「CSV/TSV 列名」输入框指定列（留空=第一列）
    ↓
调用 read_phones_from_excel()
```

③ **文本/CSV 路径不变**：非 Excel 文件走现有 `body = up.getvalue().decode(...)` 路径。

---

## 数据流

```
用户上传 .xlsx
    → streamlit_app 检测后缀
    → pd.ExcelFile 读 sheet_names（仅元数据）
    → 用户选择 Sheet
    → read_phones_from_excel(bytes, sheet, column)
        → pd.read_excel → 提取列 → strip → 过滤空值
        → 返回 List[str]
    → phones_list（与现有路径汇合）
    → 后续 SQL 生成 / 离线比对 / 云端执行不变
```

---

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 指定列名不存在 | `ValueError`，UI 显示 `st.error` |
| Excel 文件损坏 | `Exception` 冒泡，UI 显示 `st.error` |
| Sheet 为空 | 返回空列表，UI 显示「未解析到手机号」 |
| 文件过大（>100 MB） | Streamlit 自带限制，无需额外处理 |

---

## 依赖变更

`requirements.txt` 增加：
```
openpyxl>=3.1
```

（`xlrd` 仅处理旧 `.xls` 格式，若需支持 `.xls` 可选加；pandas 对 `.xlsx` 走 openpyxl）

---

## 测试要点

1. 上传含单列手机号的 xlsx，留空列名 → 正确解析
2. 上传多列 xlsx，填写列名 → 提取正确列
3. 上传多列 xlsx，填写不存在的列名 → 显示错误
4. 多 Sheet 文件 → 下拉框出现，选择非默认 Sheet → 正确解析
5. 上传后切回粘贴框 → 粘贴内容正常覆盖 Excel 数据（现有逻辑不变）
