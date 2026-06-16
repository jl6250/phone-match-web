# MD5 手机号直接匹配 —— 设计文档

日期：2026-06-16

## 背景

现有 phone-match-web 的流程是：**明文手机号 → 计算 MD5（10 位/11 位两种变体）→ 匹配数仓 `phone_hex` → 返回 `login_name`**。Web 端有两个 Tab：

- Tab1「生成 MaxCompute SQL」：用 `phone_hex IN (...)` 过滤大表，生成可在 DataWorks/控制台运行的 SQL。
- Tab2「MC 云端执行」：尚未实现。

输入区（各 Tab 共用）会把所有输入当作明文手机号，调用 `normalize_ph_mobile` 做归一化、去 +63 国家码处理。

## 目标

新增一种能力：用户手上已经是一批 **MD5 密文**（不是明文手机号），希望直接拿这些 MD5 去匹配数仓 `phone_hex`，**跳过「算 MD5」这一步**，返回 `login_name`。

输出形式：**生成 MaxCompute SQL**（与现有 Tab1 同构，拿去 DataWorks/控制台运行）。

不做：离线 CSV 比对、Tab2 云端执行、新文件类型、新的明文 MD5 计算规则。

## 用户决策记录

1. 新功能含义：**直接输入 MD5 密文匹配**（用户已有 MD5 值，跳过计算）。
2. 输出形式：**生成 MaxCompute SQL**。
3. 集成方式：**输入区加「输入类型」开关**（复用现有上传/粘贴控件，改动最小）。

## 设计

### 1. UI —— 输入区「输入类型」开关

在共用输入区顶部新增一个 radio：`● 明文手机号　○ MD5 密文`。

- **明文手机号**（默认）：完全保持现状（`normalize_ph_mobile` 归一化、10/11 位变体规则不变）。
- **MD5 密文**：解析改走 hex 校验路径，**复用同一套上传/粘贴/Excel/列名控件**。
  - 全局「密文 MD5 为大写十六进制」勾选框在此模式下隐藏/忽略——SQL 两侧都 `LOWER(TRIM)`，大小写无关。
  - 解析结果指标改为显示：`✓ N 条有效 MD5` + `去重后 X 条唯一值` + `忽略 Y 条无效（非 32 位 hex）`（Y > 0 时才显示）。

### 2. 解析层（`app/match_phones.py`）

- 新增 `is_md5_hex(s: str) -> bool`：先 `strip()` + `lower()`，再匹配 `^[0-9a-f]{32}$`。
- 将现有 `read_phones_from_text` / `read_phones_from_excel` 泛化：新增可选参数
  `normalizer: Callable[[str], str] = normalize_ph_mobile`，函数内部把
  `normalize_ph_mobile(...)` 调用替换为 `normalizer(...)`。**默认值保证明文行为完全不变。**
- MD5 模式下传入一个「`strip()` + `lower()`、保留非空值」的 normalizer，拿到原始值列表后，
  在 UI 层用 `is_md5_hex` 拆成「有效 / 无效」两组：有效组（已 lower）喂给 SQL 生成函数，
  无效组只用于指标展示。

这样 CSV/TSV/Excel/列名解析逻辑零重复。

### 3. 新 SQL 生成函数

`build_mc_md5_match_sql(md5_rows, *, mc_table, login_column, cipher_column, partition_predicate, extra_where)`

与现有 `build_mc_online_sql` 同构，但**不含任何 `md5()` 计算**，直接拿输入 hex 过滤：

```sql
-- 由 phone-match-web（MD5 直接匹配）生成（MaxCompute / ODPS）
-- 用户表: <表> | 密文列: <cc> | 分区: <分区>
set odps.sql.validate.orderby.limit=false;

WITH raw_input AS (
  SELECT * FROM VALUES (1,'<md5>'), (2,'<md5>'), ... AS t(rn, md5_raw)
),
norm AS (
  SELECT rn, md5_raw, LOWER(TRIM(md5_raw)) AS h FROM raw_input
),
hash_filter AS (
  SELECT DISTINCT h FROM norm
),
u_cipher_map AS (
  SELECT LOWER(TRIM(<cc>)) AS cipher_key, MAX(<lc>) AS login_name
  FROM <表>
  WHERE <分区>
    AND <cc> IS NOT NULL
    AND LOWER(TRIM(<cc>)) IN (SELECT h FROM hash_filter) [AND (<extra>)]
  GROUP BY LOWER(TRIM(<cc>))
)
SELECT
  n.md5_raw,
  u.login_name,
  u.cipher_key AS matched_cipher_in_warehouse,
  CASE WHEN u.login_name IS NOT NULL THEN 'matched' ELSE NULL END AS match_via
FROM norm n
LEFT JOIN u_cipher_map u ON n.h = u.cipher_key
ORDER BY n.rn;
```

复用现有 `sql_escape_literal`、`GROUP BY` 去膨胀（避免 JOIN 膨胀）、`MAX(login_name)` 等模式，
保持按输入顺序（`rn`）输出。空列表抛 `ValueError`（与现有一致）。

### 4. Tab1「生成 SQL」按钮路由

按钮逻辑读取输入类型：

- 明文 → `build_mc_online_sql`（现状）。
- MD5 → `build_mc_md5_match_sql`。

表名 / 密文列 / 分区 / 附加 AND 条件四个输入框两种模式共用；下载文件名、复制按钮、字符数指标全部复用。

### 5. 测试（`tests/`）

- `is_md5_hex`：大小写、长度不足/超长、含非 hex 字符、带前后空白。
- `read_phones_from_text` 传入 MD5 normalizer：纯文本一行一个、CSV 指定列。
- `build_mc_md5_match_sql`：包含全部输入 hex、包含 `phone_hex IN`、**不含 `md5(`**、空列表抛 `ValueError`、按 `rn` 排序。
- 回归：现有明文测试不改动；显式断言 `read_phones_*` 默认行为不变。

## 架构边界

- **解析层**（`match_phones.py` 纯函数）：输入文本/字节 → 值列表；新增的 `is_md5_hex`、
  泛化的 `read_*` 与新 SQL 生成函数都是无副作用纯函数，可独立单测。
- **UI 层**（`streamlit_app.py`）：负责输入类型开关、有效/无效拆分、指标展示与 SQL 函数路由，
  不含业务规则。
- 两层通过明确的函数签名通信，互不内嵌对方细节。
