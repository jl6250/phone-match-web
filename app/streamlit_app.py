"""
Streamlit 生产入口：生成 MC SQL、离线 CSV 比对、可选 pyodps 执行。

本地: ./run_web.sh
容器: 见 Dockerfile / docker-compose.yml
"""

from __future__ import annotations

import json
import os
import io
import zipfile
import time

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from app.match_phones import (
    DEFAULT_MC_CIPHER_COLUMN,
    DEFAULT_MC_PARTITION_EXPR,
    DEFAULT_MC_USER_TABLE,
    build_mc_md5_match_sql,
    build_mc_online_sql,
    build_sql_batches,
    is_md5_hex,
    list_columns_from_excel,
    list_columns_from_text,
    read_phones_from_excel,
    read_phones_from_text,
    validate_ph_phone,
)
import traceback

from app.odps_cloud import (
    CloudExecError,
    fetch_result,
    get_odps,
    load_config_from_env,
    logview_url,
    poll,
    sql_for_cloud,
    submit_sql,
)

def _md5_norm(s: str) -> str:
    """MD5 模式 normalizer：strip + lower，保留非空。"""
    return s.strip().lower()


def _keep_strip(s: str) -> str:
    """明文模式 normalizer：仅 strip，保留原始候选，稍后做严格校验。"""
    return s.strip()


def _copy_button(text: str, label: str = "📋 复制") -> None:
    payload = json.dumps(text)
    components.html(
        f"""<!DOCTYPE html><html><head><style>
          button{{background:#7B2FBE;color:#fff;border:none;border-radius:6px;
                  padding:6px 18px;font-size:14px;cursor:pointer;font-family:sans-serif;}}
          button:hover{{background:#6a26a8;}}
        </style></head><body>
        <button id="cb" onclick="doCopy()">📋 复制 SQL</button>
        <script>
          var _TEXT = {payload};
          function doCopy() {{
            var t = document.createElement('textarea');
            t.value = _TEXT;
            t.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
            document.body.appendChild(t); t.focus(); t.select();
            var ok = document.execCommand('copy');
            document.body.removeChild(t);
            var b = document.getElementById('cb');
            if (ok) {{
              b.textContent = '✓ 已复制'; b.style.background = '#2d8a4e';
            }} else {{
              b.textContent = '✗ 失败'; b.style.background = '#c0392b';
            }}
            setTimeout(function() {{
              b.textContent = '📋 复制 SQL'; b.style.background = '#7B2FBE';
            }}, 2000);
          }}
        </script>
        </body></html>""",
        height=44,
    )


st.set_page_config(
    page_title="手机号 MD5 对照工具",
    page_icon="📱",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── 全局 CSS ─────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
/* ===== 全局 ===== */
html, body, [class*="css"] { font-family: "PingFang SC", "Helvetica Neue", sans-serif; }

/* ===== 主标题卡 ===== */
.hero-card {
    background: linear-gradient(135deg, #1a1f3c 0%, #2d3561 50%, #1a1f3c 100%);
    border-radius: 16px;
    padding: 28px 36px;
    margin-bottom: 24px;
    border: 1px solid rgba(99,102,241,0.3);
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
}
.hero-title {
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(90deg, #818cf8, #c4b5fd, #60a5fa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin: 0 0 6px 0;
}
.hero-sub {
    color: #94a3b8;
    font-size: 0.88rem;
    margin: 0;
}

/* ===== 规则说明框 ===== */
.rule-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(99,102,241,0.12);
    border: 1px solid rgba(99,102,241,0.35);
    border-radius: 8px;
    padding: 8px 14px;
    margin: 4px 4px 4px 0;
    font-size: 0.82rem;
    color: #c4b5fd;
}
.rule-badge strong { color: #a5b4fc; }

/* ===== 段落卡片 ===== */
.section-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
}
.section-title {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #64748b;
    margin-bottom: 12px;
}

/* ===== 指标行 ===== */
.metric-row {
    display: flex;
    gap: 12px;
    margin: 12px 0 4px;
    flex-wrap: wrap;
}
.metric-chip {
    background: rgba(99,102,241,0.15);
    border: 1px solid rgba(99,102,241,0.3);
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 0.82rem;
    color: #a5b4fc;
    font-weight: 500;
}
.metric-chip.green  { background:rgba(16,185,129,0.12); border-color:rgba(16,185,129,0.3); color:#6ee7b7; }
.metric-chip.yellow { background:rgba(245,158,11,0.12); border-color:rgba(245,158,11,0.3); color:#fcd34d; }
.metric-chip.red    { background:rgba(239,68,68,0.12);  border-color:rgba(239,68,68,0.3);  color:#fca5a5; }

/* ===== 按钮优化 ===== */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    transition: all .2s ease !important;
    padding: 0.45rem 1.4rem !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    border: none !important;
    color: #fff !important;
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(99,102,241,0.45) !important;
}

/* ===== Tab 样式 ===== */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background: rgba(255,255,255,0.04);
    border-radius: 10px;
    padding: 4px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    font-weight: 500 !important;
    color: #94a3b8 !important;
    padding: 6px 18px !important;
}
.stTabs [aria-selected="true"] {
    background: rgba(99,102,241,0.25) !important;
    color: #a5b4fc !important;
}

/* ===== 文件上传区 ===== */
[data-testid="stFileUploader"] {
    border: 2px dashed rgba(99,102,241,0.35) !important;
    border-radius: 10px !important;
    background: rgba(99,102,241,0.05) !important;
    padding: 8px !important;
}

/* ===== Expander ===== */
.streamlit-expanderHeader {
    background: rgba(255,255,255,0.04) !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
}

/* ===== Sidebar ===== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%) !important;
    border-right: 1px solid rgba(255,255,255,0.07) !important;
}
.sidebar-section {
    background: rgba(255,255,255,0.04);
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
    border: 1px solid rgba(255,255,255,0.07);
}
.sidebar-section h4 {
    color: #94a3b8;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin: 0 0 10px 0;
}

/* ===== 下载按钮 ===== */
[data-testid="stDownloadButton"] > button {
    border-radius: 8px !important;
    border: 1px solid rgba(16,185,129,0.4) !important;
    color: #6ee7b7 !important;
    background: rgba(16,185,129,0.1) !important;
    font-weight: 600 !important;
}
[data-testid="stDownloadButton"] > button:hover {
    background: rgba(16,185,129,0.2) !important;
}

/* ===== 代码块 ===== */
.stCodeBlock { border-radius: 10px !important; }

/* ===== 分割线 ===== */
hr { border-color: rgba(255,255,255,0.08) !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ─── Hero 标题 ────────────────────────────────────────────────────────────────
st.markdown(
    """
<div class="hero-card">
  <p class="hero-title">📱 手机号 MD5 对照工具</p>
  <p class="hero-sub">
    支持明文手机号（自动转 MD5）或直接输入 MD5 密文，与数仓 <code>login_name</code> 关联匹配 &nbsp;·&nbsp;
    默认表 <code>superengineproject.dim_user_info_df</code>（phone_hex，pt=MAX_PT）
  </p>
  <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap;">
    <span class="metric-chip">明文：MD5 10位去0 / 11位补0</span>
    <span class="metric-chip">明文严格校验 · 纯数字 10–13 位</span>
    <span class="metric-chip">MD5 密文：直接匹配</span>
    <span class="metric-chip yellow">文件上限 100 MB</span>
    <span class="metric-chip green">支持 TXT / CSV / TSV / Excel</span>
    <span class="metric-chip">多 Sheet · 多列手机号</span>
    <span class="metric-chip">超大输入自动分批 SQL</span>
    <span class="metric-chip">保持原始顺序 · 重复数据原样保留</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ─── 手机号输入区 ──────────────────────────────────────────────────────────────
st.session_state.setdefault("last_sql", "")

with st.expander("① 输入数据：明文手机号 / MD5 密文（各 Tab 共用）", expanded=True):
    input_kind = st.radio(
        "输入类型",
        options=["明文手机号", "MD5 密文"],
        horizontal=True,
        key="input_kind",
        help="选「MD5 密文」时，输入将按 32 位十六进制校验，直接用于匹配数仓密文列",
    )
    is_md5_mode = input_kind == "MD5 密文"

    _normalizer = _md5_norm if is_md5_mode else _keep_strip

    col_up, col_paste = st.columns([1, 1], gap="large")

    with col_up:
        st.markdown('<p class="section-title">上传文件</p>', unsafe_allow_html=True)
        up = st.file_uploader(
            "支持 TXT / CSV / TSV / Excel，单文件 ≤ 100 MB",
            type=["txt", "csv", "tsv", "xlsx", "xls"],
            label_visibility="collapsed",
        )
        col_phone = st.text_input(
            "CSV/TSV 列名（粘贴文本用；上传文件改用下方多选）",
            value="",
            placeholder="留空：纯文本一行一个；多列无表头取第一列",
            help="粘贴文本时指定手机号所在列名；上传含表头的文件时用「选择手机号列」多选框",
        )

    with col_paste:
        st.markdown('<p class="section-title">粘贴数据</p>', unsafe_allow_html=True)
        _paste_ph = (
            "d41d8cd98f00b204e9800998ecf8427e\n900150983cd24fb0d6963f7d28e17f72\n..."
            if is_md5_mode
            else "13812345678\n08612345678\n..."
        )
        typed = st.text_area(
            "一行一个，或粘贴表格",
            height=160,
            key="phone_paste",
            label_visibility="collapsed",
            placeholder=_paste_ph,
        )

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
                selected_sheets = st.multiselect(
                    "选择 Sheet（可多选）",
                    options=sheet_names,
                    default=sheet_names[:1],
                    key="excel_sheet_select",
                )
                ordered_sheets = [s for s in sheet_names if s in set(selected_sheets)]
                # 选中 Sheet 的列名并集（保持出现顺序）
                union_cols: list[str] = []
                for sh in ordered_sheets:
                    try:
                        for c in list_columns_from_excel(raw_bytes, sheet=sh):
                            if c not in union_cols:
                                union_cols.append(c)
                    except Exception:
                        pass
                chosen_cols = st.multiselect(
                    "选择手机号列（可多选，留空=各表第一列）",
                    options=union_cols,
                    default=[],
                    key="excel_col_select",
                    help="跨多个 Sheet 时为列名并集；某 Sheet 没有的列会自动跳过",
                )
                if selected_sheets:
                    col_arg = chosen_cols if chosen_cols else (col_phone or None)
                    try:
                        merged: list[str] = []
                        for sh in ordered_sheets:
                            merged.extend(read_phones_from_excel(
                                raw_bytes, sheet=sh, column=col_arg,
                                normalizer=_normalizer,
                            ))
                        excel_phones = merged
                        sheets_label = "、".join(f"「{s}」" for s in selected_sheets)
                        cols_label = ("列 " + "、".join(chosen_cols)) if chosen_cols else "第一列"
                        st.success(f"已从 {sheets_label} 的 {cols_label} 载入 {len(excel_phones):,} 行")
                    except (ValueError, Exception) as e:
                        st.error(f"解析 Excel 失败：{e}")
                        excel_phones = []
                else:
                    excel_phones = []
        elif suffix in ("csv", "tsv"):
            text = up.getvalue().decode("utf-8-sig", errors="replace")
            cols = list_columns_from_text(text)
            if len(cols) > 1:
                chosen_cols = st.multiselect(
                    "选择手机号列（可多选，留空=第一列）",
                    options=cols,
                    default=[],
                    key="csv_col_select",
                )
                col_arg = chosen_cols if chosen_cols else (col_phone or None)
                try:
                    excel_phones = read_phones_from_text(text, col_arg, normalizer=_normalizer)
                    cols_label = ("列 " + "、".join(chosen_cols)) if chosen_cols else "第一列"
                    st.success(f"已从 {cols_label} 载入 {len(excel_phones):,} 行")
                except ValueError as e:
                    st.error(f"解析失败：{e}")
                    excel_phones = []
            else:
                body = text
                st.success(f"已载入上传文件（{len(body):,} 字符），覆盖文本框内容")
        else:
            body = up.getvalue().decode("utf-8-sig", errors="replace")
            st.success(f"已载入上传文件（{len(body):,} 字符），覆盖文本框内容")

    if not is_md5_mode:
        upper_md5_global = st.checkbox(
            "密文 MD5 为大写十六进制",
            value=False,
            key="upper_md5_global",
            help="勾选后比对时将 MD5 转大写再比对",
        )
    else:
        upper_md5_global = False

# 解析手机号
phones_err: str | None = None
phones_list: list[str] = []
if excel_phones is not None:
    phones_list = excel_phones
elif body.strip():
    try:
        phones_list = read_phones_from_text(body, col_phone or None, normalizer=_normalizer)
    except ValueError as e:
        phones_err = str(e)

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
        # 明文严格校验：仅 ASCII 数字、去 63 国家码后 10–13 位；非法行丢弃并计数
        checked = [(raw, validate_ph_phone(raw)) for raw in phones_list]
        valid_phones = [n for _, n in checked if n is not None]
        invalid_raws = [raw for raw, n in checked if n is None]
        invalid_n = len(invalid_raws)
        phones_list = valid_phones  # 仅合法手机号进入后续 SQL 生成
        uniq_count = len(set(valid_phones))
        dup_count = len(valid_phones) - uniq_count
        dup_chip = f'<span class="metric-chip yellow">含重复 {dup_count:,} 条</span>' if dup_count else ""
        invalid_chip = f'<span class="metric-chip yellow">忽略 {invalid_n:,} 条非法（非纯数字/位数不符）</span>' if invalid_n else ""
        st.markdown(
            f'<div class="metric-row">'
            f'<span class="metric-chip green">✓ 已解析 {len(valid_phones):,} 条合法手机号</span>'
            f'<span class="metric-chip">去重后 {uniq_count:,} 条唯一值</span>'
            f'{dup_chip}{invalid_chip}'
            f'</div>',
            unsafe_allow_html=True,
        )
        if invalid_raws:
            with st.expander(f"查看被忽略的 {invalid_n:,} 条非法行", expanded=False):
                _idf = pd.DataFrame({"原始值": invalid_raws[:1000]})
                st.dataframe(_idf, use_container_width=True, height=200)
                if invalid_n > 1000:
                    st.caption(f"仅展示前 1000 条，共 {invalid_n:,} 条")
    with st.expander("查看解析明细（排查行数异常用）", expanded=False):
        _df = pd.DataFrame({"#": range(1, len(phones_list)+1), "解析结果": phones_list})
        st.dataframe(_df, use_container_width=True, height=220)

st.markdown("<br>", unsafe_allow_html=True)

# ─── 功能 Tabs ────────────────────────────────────────────────────────────────
tab_sql, tab_cloud = st.tabs(
    ["🗄️ 生成 MaxCompute SQL", "☁️ MC 云端执行"]
)

# ── Tab 1：生成 SQL ──────────────────────────────────────────────────────────
with tab_sql:
    st.markdown(
        "对大表用 `phone_hex IN (...)` 过滤，无需下载全表。"
        "生成的 SQL 可在 DataWorks / MaxCompute 控制台直接运行。",
    )

    col1, col2 = st.columns(2, gap="large")
    with col1:
        mc_table = st.text_input("用户表", value=DEFAULT_MC_USER_TABLE, key="sql_mc_table")
        cipher_col = st.text_input("密文列", value=DEFAULT_MC_CIPHER_COLUMN, key="sql_cipher")
    with col2:
        partition_expr = st.text_input("分区条件", value=DEFAULT_MC_PARTITION_EXPR, key="sql_pt")
        login_col = st.text_input("登录名列", value="login_name", key="sql_login")

    extra_where = st.text_input(
        "附加 AND 条件（可空）",
        value="",
        key="sql_extra",
        placeholder="例：business_line = 'bp'",
    )

    max_kb = st.number_input(
        "单批最大体积 (KB)",
        min_value=10, max_value=500, value=90, step=10,
        key="sql_max_kb",
        help="超过该体积自动拆成多批，每批为独立可运行的 SQL。默认 90KB：DataWorks「数据分析」保存草稿按请求体校验（换行转义/编码会膨胀 ~1.3x），90KB 原始 SQL 膨胀后仍稳在 130KB 限制内；粘贴仍报超限可再调小。",
    )

    if st.button("生成 SQL", type="primary", key="btn_sql", use_container_width=False):
        if not phones_list:
            st.warning("请先在上方填写或上传数据（明文手机号或 MD5 密文）")
        else:
            def _builder(rows: list[str]) -> str:
                if is_md5_mode:
                    return build_mc_md5_match_sql(
                        rows,
                        mc_table=mc_table.strip(),
                        login_column=login_col.strip(),
                        cipher_column=cipher_col.strip(),
                        partition_predicate=partition_expr.strip(),
                        extra_where=extra_where.strip() or None,
                    )
                return build_mc_online_sql(
                    rows,
                    mc_table=mc_table.strip(),
                    login_column=login_col.strip(),
                    cipher_column=cipher_col.strip(),
                    partition_predicate=partition_expr.strip(),
                    extra_where=extra_where.strip() or None,
                )

            max_bytes = int(max_kb) * 1000
            try:
                batches = build_sql_batches(phones_list, _builder, max_bytes=max_bytes)
            except ValueError as e:
                st.error(str(e))
                batches = []

            if batches:
                total_chars = sum(len(b) for b in batches)
                unit = "条 MD5" if is_md5_mode else "条手机号"
                st.session_state["last_sql"] = batches[0]
                st.markdown(
                    f'<div class="metric-row">'
                    f'<span class="metric-chip green">✓ SQL 生成成功</span>'
                    f'<span class="metric-chip">{len(phones_list):,} {unit}</span>'
                    f'<span class="metric-chip">共 {len(batches)} 批</span>'
                    f'<span class="metric-chip">{total_chars:,} 字符</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                oversize = [k for k, b in enumerate(batches, 1)
                            if len(b.encode("utf-8")) > max_bytes]
                if oversize:
                    st.warning(
                        f"第 {oversize} 批单批仍超过 {int(max_kb)}KB（可能单行过长），"
                        f"请调高阈值或检查输入。"
                    )

                if len(batches) == 1:
                    sql = batches[0]
                    st.code(sql, language="sql")
                    _copy_button(sql, "📋 复制 SQL")
                    st.download_button(
                        "⬇️ 下载 phone_match_odps.sql",
                        sql,
                        file_name="phone_match_odps.sql",
                        mime="text/plain; charset=utf-8",
                        key="dl_sql",
                    )
                else:
                    st.info(f"已拆成 {len(batches)} 批，请逐批在 DataWorks 运行（结果可直接合并）。")
                    zip_buf = io.BytesIO()
                    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        for k, b in enumerate(batches, 1):
                            zf.writestr(f"phone_match_odps_part{k:02d}.sql", b)
                    st.download_button(
                        f"⬇️ 打包下载全部 {len(batches)} 批 (.zip)",
                        zip_buf.getvalue(),
                        file_name="phone_match_odps_batches.zip",
                        mime="application/zip",
                        key="dl_zip",
                    )
                    for k, b in enumerate(batches, 1):
                        kb = len(b.encode("utf-8")) / 1000
                        with st.expander(f"批次 {k} / {len(batches)}　（约 {kb:,.0f} KB）",
                                         expanded=(k == 1)):
                            st.code(b, language="sql")
                            _copy_button(b, "📋 复制 SQL")
                            st.download_button(
                                f"⬇️ 下载 part{k:02d}.sql",
                                b,
                                file_name=f"phone_match_odps_part{k:02d}.sql",
                                mime="text/plain; charset=utf-8",
                                key=f"dl_sql_{k}",
                            )

# ── Tab 2：MC 云端执行 ────────────────────────────────────────────────────────
with tab_cloud:
    st.markdown(
        "用 ECS 实例 RAM 角色（无 AccessKey）直连 MaxCompute 执行匹配 SQL，"
        "结果在页面内展示并可下载 CSV。仅在已绑定 RAM 角色的阿里云 ECS 上可用。"
    )

    _cfg = load_config_from_env()
    st.markdown(
        f'<div class="metric-row">'
        f'<span class="metric-chip">项目 {_cfg.project}</span>'
        f'<span class="metric-chip">Endpoint {_cfg.endpoint}</span>'
        f'<span class="metric-chip green">凭证：ECS RAM 角色（无 AK）</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2, gap="large")
    with c1:
        cloud_table = st.text_input("用户表", value=DEFAULT_MC_USER_TABLE, key="cloud_mc_table")
        cloud_cipher = st.text_input("密文列", value=DEFAULT_MC_CIPHER_COLUMN, key="cloud_cipher")
    with c2:
        cloud_pt = st.text_input("分区条件", value=DEFAULT_MC_PARTITION_EXPR, key="cloud_pt")
        cloud_login = st.text_input("登录名列", value="login_name", key="cloud_login")
    cloud_extra = st.text_input(
        "附加 AND 条件（可空）", value="", key="cloud_extra",
        placeholder="例：business_line = 'bp'",
    )

    st.session_state.setdefault("cloud_stage", "idle")

    def _build_cloud_sql() -> str:
        if is_md5_mode:
            return build_mc_md5_match_sql(
                phones_list,
                mc_table=cloud_table.strip(), login_column=cloud_login.strip(),
                cipher_column=cloud_cipher.strip(), partition_predicate=cloud_pt.strip(),
                extra_where=cloud_extra.strip() or None,
            )
        return build_mc_online_sql(
            phones_list,
            mc_table=cloud_table.strip(), login_column=cloud_login.strip(),
            cipher_column=cloud_cipher.strip(), partition_predicate=cloud_pt.strip(),
            extra_where=cloud_extra.strip() or None,
        )

    if st.button("☁️ 云端执行", type="primary", key="btn_cloud"):
        if not phones_list:
            st.warning("请先在上方填写或上传数据（明文手机号或 MD5 密文）")
        else:
            try:
                sql = sql_for_cloud(_build_cloud_sql())
                odps = get_odps(_cfg)
                st.session_state["cloud_instance_id"] = submit_sql(odps, sql)
                st.session_state["cloud_stage"] = "running"
                st.session_state["cloud_started_at"] = time.time()
                st.session_state.pop("cloud_error", None)
                st.session_state.pop("cloud_trace", None)
                st.rerun()
            except Exception as e:
                st.session_state["cloud_stage"] = "failed"
                st.session_state["cloud_error"] = str(e)
                st.session_state["cloud_trace"] = traceback.format_exc()

    stage = st.session_state.get("cloud_stage", "idle")
    inst_id = st.session_state.get("cloud_instance_id")

    if stage == "running" and inst_id:
        with st.status(f"云端执行中… instance={inst_id}", expanded=True) as status:
            try:
                elapsed = time.time() - st.session_state.get("cloud_started_at", time.time())
                if elapsed > 300:  # 轮询超时上限 5 分钟
                    odps = get_odps(_cfg)
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "failed"
                    st.session_state["cloud_error"] = (
                        "轮询超过 5 分钟未完成，已停止等待；可凭 Logview 去控制台查看作业。"
                    )
                    st.rerun()
                odps = get_odps(_cfg)
                state = poll(odps, inst_id)
                st.write(f"状态：{state}（已等待 {int(elapsed)}s）")
                if state == "Running":
                    time.sleep(2)
                    st.rerun()
                elif state == "Success":
                    df = fetch_result(odps, inst_id)
                    st.session_state["cloud_df"] = df
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "done"
                    status.update(label="执行完成", state="complete")
                    st.rerun()
                else:  # Failure
                    st.session_state["cloud_logview"] = logview_url(odps, inst_id)
                    st.session_state["cloud_stage"] = "failed"
                    st.session_state["cloud_error"] = "SQL 执行失败，请查看 Logview"
                    st.rerun()
            except Exception as e:
                st.session_state["cloud_stage"] = "failed"
                st.session_state["cloud_error"] = str(e)
                st.session_state["cloud_trace"] = traceback.format_exc()
                st.rerun()

    if stage == "done":
        df = st.session_state.get("cloud_df")
        n = 0 if df is None else len(df)
        st.markdown(
            f'<div class="metric-row">'
            f'<span class="metric-chip green">✓ 执行成功</span>'
            f'<span class="metric-chip">{n:,} 行结果</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.get("cloud_logview"):
            st.markdown(f"[🔗 MaxCompute Logview]({st.session_state['cloud_logview']})")
        if df is not None:
            st.dataframe(df, use_container_width=True, height=420)
            st.download_button(
                "⬇️ 下载结果 CSV",
                df.to_csv(index=False).encode("utf-8-sig"),
                file_name="phone_match_result.csv",
                mime="text/csv; charset=utf-8",
                key="dl_cloud_csv",
            )

    if stage == "failed":
        st.error(f"云端执行失败：{st.session_state.get('cloud_error', '未知错误')}")
        if st.session_state.get("cloud_logview"):
            st.markdown(f"[🔗 MaxCompute Logview]({st.session_state['cloud_logview']})")
        if st.session_state.get("cloud_trace"):
            with st.expander("查看完整错误堆栈（排查用）", expanded=False):
                st.code(st.session_state["cloud_trace"], language="text")
        st.caption(
            "排查：① ECS 是否绑定了具备 MaxCompute 权限的 RAM 角色；"
            "② 该角色是否为 MC 项目成员并有表 SELECT + Tunnel Download 权限。"
        )
