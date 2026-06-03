"""
Streamlit 生产入口：生成 MC SQL、离线 CSV 比对、可选 pyodps 执行。

本地: ./run_web.sh
容器: 见 Dockerfile / docker-compose.yml
"""

from __future__ import annotations

import os
import io

import pandas as pd
import streamlit as st

from app.match_phones import (
    DEFAULT_MC_CIPHER_COLUMN,
    DEFAULT_MC_PARTITION_EXPR,
    DEFAULT_MC_USER_TABLE,
    build_mc_online_sql,
    read_phones_from_excel,
    read_phones_from_text,
)

DEFAULT_ENDPOINT = os.environ.get(
    "ODPS_ENDPOINT",
    "https://service.cn.maxcompute.aliyun.com/api",
)
DEFAULT_PROJECT = os.environ.get("ODPS_PROJECT", "superengineproject")
ENV_ACCESS_ID = os.environ.get("ODPS_ACCESS_ID", "")
ENV_ACCESS_KEY = os.environ.get("ODPS_ACCESS_KEY", "")

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
    将明文手机号转换为 MD5 密文，与数仓 <code>login_name</code> 进行关联匹配 &nbsp;·&nbsp;
    默认表 <code>superengineproject.dim_user_info_df</code>（phone_hex，pt=MAX_PT）
  </p>
  <div style="margin-top:14px; display:flex; gap:8px; flex-wrap:wrap;">
    <span class="metric-chip">MD5 10位 · 去掉最左侧0</span>
    <span class="metric-chip">MD5 11位 · 左填0至11位</span>
    <span class="metric-chip yellow">文件上限 100 MB</span>
    <span class="metric-chip green">支持 TXT / CSV / TSV / Excel</span>
    <span class="metric-chip">保持原始顺序 · 重复数据原样保留</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ─── 手机号输入区 ──────────────────────────────────────────────────────────────
st.session_state.setdefault("last_sql", "")

with st.expander("① 明文手机号（各 Tab 共用）", expanded=True):
    col_up, col_paste = st.columns([1, 1], gap="large")

    with col_up:
        st.markdown('<p class="section-title">上传文件</p>', unsafe_allow_html=True)
        up = st.file_uploader(
            "支持 TXT / CSV / TSV / Excel，单文件 ≤ 100 MB",
            type=["txt", "csv", "tsv", "xlsx", "xls"],
            label_visibility="collapsed",
        )
        col_phone = st.text_input(
            "CSV/TSV 列名",
            value="",
            placeholder="留空：纯文本一行一个；多列无表头取第一列",
            help="当文件含多列时指定手机号所在列名",
        )

    with col_paste:
        st.markdown('<p class="section-title">粘贴明文</p>', unsafe_allow_html=True)
        typed = st.text_area(
            "一行一个，或粘贴表格",
            height=160,
            key="phone_paste",
            label_visibility="collapsed",
            placeholder="13812345678\n08612345678\n...",
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
                if selected_sheets:
                    try:
                        merged: list[str] = []
                        for sh in [s for s in sheet_names if s in set(selected_sheets)]:
                            merged.extend(read_phones_from_excel(
                                raw_bytes,
                                sheet=sh,
                                column=col_phone or None,
                            ))
                        excel_phones = merged
                        sheets_label = "、".join(f"「{s}」" for s in selected_sheets)
                        st.success(f"已从 {sheets_label} 载入 {len(excel_phones):,} 行")
                    except (ValueError, Exception) as e:
                        st.error(f"解析 Excel 失败：{e}")
                        excel_phones = []
                else:
                    excel_phones = []
        else:
            body = up.getvalue().decode("utf-8-sig", errors="replace")
            st.success(f"已载入上传文件（{len(body):,} 字符），覆盖文本框内容")

    upper_md5_global = st.checkbox(
        "密文 MD5 为大写十六进制",
        value=False,
        key="upper_md5_global",
        help="勾选后比对时将 MD5 转大写再比对",
    )

# 解析手机号
phones_err: str | None = None
phones_list: list[str] = []
if excel_phones is not None:
    phones_list = excel_phones
elif body.strip():
    try:
        phones_list = read_phones_from_text(body, col_phone or None)
    except ValueError as e:
        phones_err = str(e)

if phones_err:
    st.error(f"解析失败：{phones_err}")
elif phones_list:
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

    if st.button("生成 SQL", type="primary", key="btn_sql", use_container_width=False):
        if not phones_list:
            st.warning("请先在上方填写或上传明文手机号")
        else:
            try:
                sql = build_mc_online_sql(
                    phones_list,
                    mc_table=mc_table.strip(),
                    login_column=login_col.strip(),
                    cipher_column=cipher_col.strip(),
                    partition_predicate=partition_expr.strip(),
                    extra_where=extra_where.strip() or None,
                )
                st.session_state["last_sql"] = sql
                st.markdown(
                    f'<div class="metric-row">'
                    f'<span class="metric-chip green">✓ SQL 生成成功</span>'
                    f'<span class="metric-chip">{len(phones_list):,} 条手机号</span>'
                    f'<span class="metric-chip">{len(sql):,} 字符</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.code(sql, language="sql")
                st.download_button(
                    "⬇️ 下载 phone_match_odps.sql",
                    sql,
                    file_name="phone_match_odps.sql",
                    mime="text/plain; charset=utf-8",
                    key="dl_sql",
                )
            except ValueError as e:
                st.error(str(e))

# ── Tab 2：MC 云端执行 ────────────────────────────────────────────────────────
with tab_cloud:
    st.info("🚧 功能待开发")
