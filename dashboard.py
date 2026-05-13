#!/usr/bin/env python3
"""
ECI Live Election Dashboard — Single-Page Template
Reusable across all state pages and Overall.
"""

import os
from db_utils import DATABASE_URL, IS_PG

if IS_PG:
    import psycopg2
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db_utils import (
    get_all_constituency_statuses,
    get_constituency_rounds,
    get_party_seat_tally_won_leading,
    get_state_status_summary,
    get_status_summary,
    get_state_name,
)
from config import get_election_id, get_tracked_states
from core.scraper import build_roundwise_url

IST = ZoneInfo("Asia/Kolkata")

TRACKED_STATES = get_tracked_states()
ELECTION_ID = get_election_id()

# ---------------------------------------------------------------------------
# Dashboard visual config
# ---------------------------------------------------------------------------

MAJORITIES = {
    "S03": 64,   # Assam: 126/2 + 1
    "S11": 71,   # Kerala: 140/2 + 1
    "U07": 16,   # Puducherry: 30/2 + 1
    "S22": 118,  # Tamil Nadu: 234/2 + 1
    "S25": 148,  # West Bengal: 294/2 + 1
}

PARTY_COLORS = {
    "Bharatiya Janata Party": "#FF6600",
    "Indian National Congress": "#00ADEF",
    "All India Trinamool Congress": "#20C997",
    "Dravida Munnetra Kazhagam": "#E63946",
    "All India Anna Dravida Munnetra Kazhagam": "#F4A261",
    "Communist Party of India (Marxist)": "#DC2626",
    "Communist Party of India": "#B91C1C",
    "Indian Union Muslim League": "#2D6A4F",
    "Kerala Congress (M)": "#F4D03F",
    "Tamilaga Vettri Kazhagam": "#FFD700",
    "Aam Aadmi Party": "#0A2463",
    "Asom Gana Parishad": "#8B5CF6",
    "All India United Democratic Front": "#059669",
    "Bodoland People's Front": "#D97706",
    "Independent": "#6B7280",
    "NOTA": "#374151",
    "Others": "#ADB5BD",
}

PARTY_SHORT = {
    "Bharatiya Janata Party": "BJP",
    "Indian National Congress": "INC",
    "All India Trinamool Congress": "AITC",
    "Dravida Munnetra Kazhagam": "DMK",
    "All India Anna Dravida Munnetra Kazhagam": "AIADMK",
    "Tamilaga Vettri Kazhagam": "TVK",
    "Communist Party of India (Marxist)": "CPM",
    "Communist Party of India": "CPI",
    "Indian Union Muslim League": "IUML",
    "Bodoland People's Front": "BPF",
    "Bodoland Peoples Front": "BPF",
    "Asom Gana Parishad": "AGP",
    "All India United Democratic Front": "AIUDF",
    "All India N.R. Congress": "AINRC",
    "Kerala Congress": "KC",
    "Kerala Congress (M)": "KC(M)",
    "Revolutionary Socialist Party": "RSP",
    "Viduthalai Chiruthaigal Katchi": "VCK",
    "Pattali Makkal Katchi": "PMK",
    "Aam Aadmi Party": "AAP",
    "Aam Janata Unnayan party": "AJUP",
    "All India Secular Front": "AISF",
    "NOTA": "NOTA",
    "Independent": "IND",
    "Others": "Others",
}

STATUS_COLORS = {
    "DONE": "#16A34A",
    "LIVE": "#F59E0B",
    "PENDING": "#6B7280",
    "ERROR": "#DC2626",
}


def short(party_name: str) -> str:
    """Get short abbreviation for a party name."""
    return PARTY_SHORT.get(party_name, party_name[:20] if len(party_name) > 20 else party_name)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_ist(utc_iso, fmt="%d %b %H:%M IST"):
    if not utc_iso:
        return "N/A"
    try:
        dt = datetime.fromisoformat(utc_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime(fmt)
    except Exception:
        return str(utc_iso)


def get_pc(party):
    return PARTY_COLORS.get(party, "#ADB5BD")


def collapse_others(df, party_col, val_col, top_n=10):
    s = df.sort_values(val_col, ascending=False).reset_index(drop=True)
    top = s.head(top_n).copy()
    rest = s.iloc[top_n:]
    if not rest.empty:
        other = pd.DataFrame([{party_col: f"Others ({len(rest)} parties)", val_col: rest[val_col].sum()}])
        top = pd.concat([top, other], ignore_index=True)
    return top


def cdn(name):
    if name.upper() == "NOTA":
        return "NOTA"
    return name.title()


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ECI Live Election Tracker — May 2026",
    page_icon="🗳️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

is_dark = st.session_state.get("dark_mode", False)

_dark_css = ""
_light_css = ""
if is_dark:
    _dark_css = """
    body, .stApp, [data-testid="stAppViewContainer"], .main, .block-container {
        background: #0e1117 !important; color: #fafafa !important;
    }
    [data-testid="stHeader"] { background: #0e1117 !important; }
    .stMarkdown p, .stMarkdown li, .stCaption, span { color: #fafafa !important; }
    h1, h2, h3, h4, h5, h6 { color: #fafafa !important; }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #333 !important; background: #1a1a2e !important;
    }
    [data-testid="stPlotlyChart"], [data-testid="stPlotlyChart"] > div {
        background: #0e1117 !important;
    }
    [data-testid="stBaseButton-pills"], [data-testid="stBaseButton-pillsActive"] {
        background: #1e293b !important; color: #fafafa !important;
        border-color: #475569 !important;
    }
    input, select, textarea {
        background: #1a1a2e !important; color: #fafafa !important; border-color: #444 !important;
    }
    """
else:
    _light_css = """
    body, .stApp, [data-testid="stAppViewContainer"], .main, .block-container {
        background: #ffffff !important; color: #262730 !important;
    }
    [data-testid="stHeader"] { background: #ffffff !important; }
    .stMarkdown p, .stMarkdown li, .stCaption, span { color: #262730 !important; }
    h1, h2, h3, h4, h5, h6 { color: #262730 !important; }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #e0e0e0 !important; background: #ffffff !important;
    }
    [data-testid="stPlotlyChart"], [data-testid="stPlotlyChart"] > div {
        background: #ffffff !important;
    }
    [data-testid="stBaseButton-pills"], [data-testid="stBaseButton-pillsActive"] {
        background: #f0f2f6 !important; color: #262730 !important;
        border-color: #d0d5dd !important;
    }
    [data-testid="stBaseButton-pillsActive"] {
        background: #e8eaf0 !important; border-color: #6c757d !important;
    }
    input, select, textarea {
        background: #ffffff !important; color: #262730 !important; border-color: #cccccc !important;
    }
    [data-testid="stCheckbox"] label, [data-testid="stToggle"] label {
        color: #262730 !important;
    }
    [data-testid="stMetric"] [data-testid="stMarkdownContainer"] p {
        color: #262730 !important;
    }
    [data-testid="stSlider"] label, [data-testid="stSelectbox"] label {
        color: #262730 !important;
    }
    """

st.markdown(f"""
<style>
    [data-testid="stSidebar"] {{display: none !important;}}
    [data-testid="collapsedControl"] {{display: none !important;}}
    /* Hide Streamlit header and heading anchors */
    header[data-testid="stHeader"] {{display: none !important;}}
    [data-testid="stHeaderActionElements"] {{display: none !important;}}
    h1 a, h2 a, h3 a {{display: none !important;}}
    /* Constrain width */
    .block-container {{
        padding-top: 0.2rem !important;
        padding-bottom: 1rem !important;
        max-width: 72rem;
        margin: 0 auto;
    }}
    @media (max-width: 900px) {{
        .block-container {{
            max-width: 100%;
        }}
    }}
    /* Header bar */
    .header-bar {{
        display: flex;
        flex-wrap: nowrap;
        align-items: center;
        gap: 0.75rem;
        padding: 0.6rem 1rem;
        margin-bottom: 1rem;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 10px;
        color: white;
    }}
    .header-brand {{
        white-space: nowrap;
        flex-shrink: 1;
        margin: 0;
        font-size: 1.15rem;
        color: white;
    }}
    .header-bar .ts {{
        font-size: 0.95rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        opacity: 1;
        flex-shrink: 0;
    }}
    @media (max-width: 600px) {{
        .header-brand {{
            font-size: 1rem;
        }}
        .header-bar .ts {{
            font-size: 0.85rem;
        }}
    }}
    /* State pills and gear button */
    [data-testid="stHorizontalBlock"]:has([data-testid="stButtonGroup"]) {{
        display: flex;
        align-items: center;
    }}
    [data-testid="stHorizontalBlock"]:has([data-testid="stButtonGroup"]) > [data-testid="stColumn"]:first-child {{
        flex: 1 1 0%;
        overflow-x: auto;
    }}
    [data-testid="stHorizontalBlock"]:has([data-testid="stButtonGroup"]) > [data-testid="stColumn"]:last-child {{
        flex: 0 0 auto;
        display: flex;
        justify-content: flex-end;
    }}
    [data-testid="stButtonGroup"] {{
        display: flex;
        flex-wrap: nowrap;
        overflow-x: auto;
        white-space: nowrap;
        gap: 0.25rem;
    }}
    [data-testid="stButtonGroup"]::-webkit-scrollbar {{
        display: none;
    }}
    [data-testid="stButtonGroup"] {{
        -ms-overflow-style: none;
        scrollbar-width: none;
    }}
    @media (min-width: 601px) {{
        [data-testid="stBaseButton-pills"],
        [data-testid="stBaseButton-pillsActive"] {{
            padding: 0.25rem 0.6rem !important;
            font-size: 0.8rem !important;
            flex-shrink: 0 !important;
        }}
    }}
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {{
        border: none !important;
        border-radius: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
        outline: none !important;
        min-height: 44px !important;
        min-width: 44px !important;
        font-size: 2rem !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }}
    /* Two-column grid */
    .main-grid {{
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 0.75rem;
    }}
    @media (min-width: 1024px) {{
        .main-grid {{
            grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
        }}
    }}
    .main-grid > * {{
        min-width: 0;
        width: 100%;
    }}
    /* Metric cells — scoped to metrics row only */
    [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > [data-testid="stColumn"] {{
        flex: 1 1 0% !important;
        min-width: 0 !important;
    }}
    [data-testid="stMetricLabel"] {{
        justify-content: end !important;
        justify-items: end !important;
    }}
    [data-testid="stMetricLabel"] > * {{
        text-align: right !important;
    }}
    [data-testid="stMetricValue"] {{
        text-align: left !important;
        justify-content: start !important;
        justify-items: start !important;
    }}
    [data-testid="stMetricValue"] > * {{
        text-align: left !important;
    }}
    @media (max-width: 600px) {{
        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
            flex: 1 1 45% !important;
        }}
    }}
    /* Tables */
    [data-testid="stDataFrame"] thead th,
    [data-testid="stDataFrame"] [role="columnheader"] {{
        text-align: right !important;
    }}
    [data-testid="stDataFrame"] tbody td:not(:first-child),
    [data-testid="stDataFrame"] [role="gridcell"]:not(:first-child) {{
        text-align: right !important;
    }}
    /* Dialog-specific table overrides */
    [data-testid="stDialog"] [data-testid="stDataFrame"] thead th,
    [data-testid="stDialog"] [data-testid="stDataFrame"] [role="columnheader"] {{
        text-align: right !important;
    }}
    [data-testid="stDialog"] [data-testid="stDataFrame"] tbody td,
    [data-testid="stDialog"] [data-testid="stDataFrame"] [role="gridcell"] {{
        text-align: right !important;
    }}
    /* Overflow protection — charts & bordered containers only */
    [data-testid="stVerticalBlockBorderWrapper"] {{
        min-width: 0;
        width: 100%;
    }}
    /* WCAG touch targets — exclude pill buttons */
    [data-testid="stButton"] button,
    [data-testid="stDialog"] button {{
        min-height: 44px !important;
        min-width: 44px !important;
    }}
    select,
    [data-testid="stSelectbox"] select {{
        min-height: 44px !important;
    }}
    /* WCAG focus-visible */
    [data-testid="stButton"] button:focus-visible,
    [data-testid="stSelectbox"] select:focus-visible,
    [data-testid="stBaseButton-pills"]:focus-visible,
    [data-testid="stBaseButton-pillsActive"]:focus-visible {{
        outline: 2px solid #1f77d2 !important;
        outline-offset: 2px !important;
    }}
    /* WCAG sr-only utility */
    .sr-only {{
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
    }}
    /* WCAG skip link */
    .skip-link {{
        position: absolute;
        top: -9999px;
        left: 50%;
        transform: translateX(-50%);
        background: #1f77d2;
        color: white;
        padding: 1rem;
        z-index: 9999;
        text-decoration: none;
    }}
    .skip-link:focus {{
        top: 0;
    }}
    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {{
        * {{
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
        }}
    }}
    /* Dark mode */
    {_dark_css}
    /* Light mode — overrides Streamlit's OS-driven dark theme */
    {_light_css}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<a href="#main-content" class="skip-link">Skip to main content</a>
<div aria-live="polite" aria-atomic="true" class="sr-only" id="data-status"></div>
<div id="main-content" tabindex="-1" style="outline:none;"></div>
""", unsafe_allow_html=True)

try:
    from db_utils import _connect as _test_connect
    _test_conn = _test_connect()
    _test_conn.close()
except Exception:
    st.error(f"Cannot connect to database: {DATABASE_URL}")
    st.stop()

# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

@st.dialog("⚙️ Settings & System Monitor", width="large")
def settings_dialog():
    # Dark mode toggle at the start
    dark_mode = st.toggle("🌙 Dark mode", value=st.session_state.get("dark_mode", False), key="dark_toggle")
    st.session_state["dark_mode"] = dark_mode
    
    st.subheader("Refresh")
    col_refresh, _ = st.columns([1, 3])
    with col_refresh:
        refresh_interval = st.slider("Auto-refresh (seconds)", 30, 300, 120, key="dlg_refresh")
        if st.button("🔄 Refresh Now", key="dlg_refresh_btn"):
            st.rerun()
    last_update = None
    if last_update:
        st.caption(f"Last update: {fmt_ist(last_update)}")

    st.divider()
    st.subheader("Status")
    ss = get_status_summary()
    total = sum(ss.values())
    done = ss.get("DONE", 0)
    live = ss.get("LIVE", 0)
    pending = ss.get("PENDING", 0)
    errors = ss.get("ERROR", 0)
    c1, c2, c3, c4, c5 = st.columns(5, gap="small")
    c1.metric("Counted", done)
    c2.metric("Counting", live)
    c3.metric("Pending", pending)
    c4.metric("Errors", errors)
    c5.metric("Total", total)

    st.divider()
    st.subheader("State Overview")
    summary = get_state_status_summary()
    if summary:
        df_sum = pd.DataFrame(summary)
        so_rows = []
        for state in TRACKED_STATES:
            sd = df_sum[df_sum["state_name"] == state["name"]]
            if not sd.empty:
                c = dict(zip(sd["status"], sd["cnt"]))
                so_rows.append({
                    "State": state["name"],
                    "🟢 Counted": c.get("DONE", 0),
                    "🟡 Counting": c.get("LIVE", 0),
                    "⚪ Pending": c.get("PENDING", 0),
                    "🔴 Errors": c.get("ERROR", 0),
                    "Reporting": f"{c.get('DONE', 0) + c.get('LIVE', 0)}/{sum(c.values())}",
                })
        if so_rows:
            so_df = pd.DataFrame(so_rows)
            # Generate HTML table with right-aligned columns
            html_table = '<table style="width:100%; border-collapse: collapse;">'
            html_table += '<thead><tr>'
            for col in so_df.columns:
                html_table += f'<th style="text-align: right; padding: 8px; border-bottom: 1px solid #ddd;">{col}</th>'
            html_table += '</tr></thead><tbody>'
            for _, row in so_df.iterrows():
                html_table += '<tr>'
                for col in so_df.columns:
                    val = row[col]
                    if isinstance(val, (int, float)):
                        val = f'{val:,}'
                    html_table += f'<td style="text-align: right; padding: 8px; border-bottom: 1px solid #ddd;">{val}</td>'
                html_table += '</tr>'
            html_table += '</tbody></table>'
            st.markdown(html_table, unsafe_allow_html=True)

    st.divider()
    ac_statuses = get_all_constituency_statuses()
    errs = ac_statuses[ac_statuses["status"] == "ERROR"] if not ac_statuses.empty else pd.DataFrame()
    if not errs.empty:
        st.subheader(f"⚠️ Failed ({len(errs)})")
        for _, row in errs.iterrows():
            name = row.get("ac_name") or f"AC-{row['ac_no']}"
            url = build_roundwise_url(ELECTION_ID, row["state_code"], row["ac_no"])
            st.markdown(f"- **{name}** ({row['state_name']}) — [View]({url})")

    st.divider()
    st.subheader("Update Cycles")
    cycles_df = None
    if cycles_df is not None and not cycles_df.empty:
        dc = cycles_df.copy()
        for col in ["started_at", "finished_at"]:
            if col in dc.columns:
                dc[col] = dc[col].apply(lambda x: fmt_ist(x, "%H:%M:%S IST") if pd.notna(x) else "")
        show_cols = [c for c in ["started_at","finished_at","pages_attempted","pages_success","pages_skipped","pages_error","cycle_duration_sec"] if c in dc.columns]
        # Generate HTML table with right-aligned columns
        html_table = '<div style="max-height: 300px; overflow-y: auto;"><table style="width:100%; border-collapse: collapse;">'
        html_table += '<thead><tr>'
        for col in show_cols:
            html_table += f'<th style="text-align: right; padding: 8px; border-bottom: 1px solid #ddd;">{col}</th>'
        html_table += '</tr></thead><tbody>'
        for _, row in dc[show_cols].iterrows():
            html_table += '<tr>'
            for col in show_cols:
                val = row[col]
                if isinstance(val, (int, float)):
                    val = f'{val:,}'
                html_table += f'<td style="text-align: right; padding: 8px; border-bottom: 1px solid #ddd;">{val}</td>'
            html_table += '</tr>'
        html_table += '</tbody></table></div>'
        st.markdown(html_table, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# State selector
# ---------------------------------------------------------------------------

def _get_state_dots():
    """Return {state_name: dot_emoji} for per-state coloured status dots."""
    summary = get_state_status_summary()
    dots = {}
    if not summary:
        return {s["name"]: "⚪" for s in TRACKED_STATES}
    df_sum = pd.DataFrame(summary)
    for state in TRACKED_STATES:
        sd = df_sum[df_sum["state_name"] == state["name"]]
        if sd.empty:
            dots[state["name"]] = "⚪"
            continue
        c = dict(zip(sd["status"], sd["cnt"]))
        total_s = sum(c.values())
        done = c.get("DONE", 0)
        live = c.get("LIVE", 0)
        errors = c.get("ERROR", 0)
        if errors > 0:
            dots[state["name"]] = "🔴"
        elif done == total_s and total_s > 0:
            dots[state["name"]] = "🟢"
        elif live > 0:
            dots[state["name"]] = "🟡"
        else:
            dots[state["name"]] = "⚪"
    return dots

state_dots = _get_state_dots()
state_options = ["Overall"] + [f"{state_dots.get(s['name'], '⚪')} {s['name']}" for s in TRACKED_STATES]
default_state = st.session_state.get("selected_state", "Overall")
if default_state not in state_options:
    default_state = "Overall"

# Define _display_state before header
_display_state = st.session_state.get("selected_state", "Overall")

# Header bar HTML BEFORE pills row
last_update = None
last_ts = fmt_ist(last_update) if last_update else "No data yet"

st.markdown(
    f"""<div class="header-bar">
    <h2 class="header-brand">🗳️ ECI Live Election Tracker — {_display_state}</h2>
    <span class="ts">📅 {last_ts}</span>
</div>""",
    unsafe_allow_html=True,
)

# Pills + gear button on same row
col_pills, col_gear = st.columns([12, 1])
with col_pills:
    selected_state = st.pills("State", state_options, default=default_state, selection_mode="single", label_visibility="collapsed")
    # Strip leading dot emoji for state lookup (use lstrip, not strip)
    _clean_state = selected_state.lstrip("🟢🟡⚪🔴 ") if selected_state else selected_state
    if _clean_state and _clean_state != st.session_state.get("selected_state"):
        st.session_state["selected_state"] = _clean_state
        st.rerun()
with col_gear:
    if st.button("⚙️", key="gear_top"):
        settings_dialog()

state_code_filter = None
if _display_state and _display_state != "Overall":
    state_code_filter = next((s["code"] for s in TRACKED_STATES if s["name"] == _display_state), "")

CHART_CFG = dict(displayModeBar=False, responsive=True)

# Set Plotly chart backgrounds to match theme
_chart_bg = "#0e1117" if is_dark else "#ffffff"
_chart_paper = "#0e1117" if is_dark else "#ffffff"
_chart_font = "#fafafa" if is_dark else "#262730"
CHART_LAYOUT = dict(
    paper_bgcolor=_chart_paper,
    plot_bgcolor=_chart_bg,
    font=dict(color=_chart_font),
    xaxis=dict(tickfont=dict(color=_chart_font), title_font=dict(color=_chart_font), gridcolor="#e0e0e0" if not is_dark else "#333"),
    yaxis=dict(tickfont=dict(color=_chart_font), title_font=dict(color=_chart_font)),
)

def _apply_chart_theme(fig):
    """Apply theme-aware layout to a Plotly figure, merging with existing layout."""
    fig.update_layout(
        paper_bgcolor=_chart_paper,
        plot_bgcolor=_chart_bg,
        font=dict(color=_chart_font),
    )
    for ax in ("xaxis", "yaxis"):
        if ax in fig.layout and fig.layout[ax] is not None:
            fig.layout[ax].tickfont = dict(color=_chart_font)
            fig.layout[ax].title = dict(font=dict(color=_chart_font))
    return fig

# ===========================================================================
# SECTION 1: SEAT TALLY
# ===========================================================================

st.markdown('<div class="main-grid">', unsafe_allow_html=True)

with st.container(border=True):
    st.subheader("📊 Seat Tally")

    wl_tally = get_party_seat_tally_won_leading(state_code_filter)
    if wl_tally.empty:
        st.info("No data yet.")
    else:
        wl = collapse_others(wl_tally, "party", "total", top_n=10)
        wl["short"] = wl["party"].apply(short)
        for c in ["won", "leading", "total"]:
            wl[c] = wl[c].fillna(0).astype(int)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=wl["short"], x=wl["won"], orientation="h", name="Won",
            marker_color=[get_pc(p) for p in wl["party"]],
            text=wl.apply(lambda r: str(int(r["won"])) if r["won"] > 0 else "", axis=1),
            textposition="inside", textfont=dict(color="white", size=13),
            hovertext=wl.apply(lambda r: f"{r['party']}: {int(r['won'])} won", axis=1),
            showlegend=False,
        ))
        fig.add_trace(go.Bar(
            y=wl["short"], x=wl["leading"], orientation="h", name="Leading",
            marker_color=[get_pc(p) for p in wl["party"]],
            marker_pattern=dict(shape="/", solidity=0.6),
            text=wl.apply(lambda r: str(int(r["leading"])) if r["leading"] > 0 else "", axis=1),
            textposition="outside", textfont=dict(size=12),
            hovertext=wl.apply(lambda r: f"{r['party']}: {int(r['leading'])} leading", axis=1),
            showlegend=False,
        ))
        if state_code_filter and state_code_filter in MAJORITIES:
            maj = MAJORITIES[state_code_filter]
            fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
            fig.add_annotation(x=maj, y=len(wl) - 2, text=f"Majority ({maj})",
                               showarrow=False, font=dict(color="red", size=14),
                               xanchor="left", xshift=8, yanchor="middle")
        _apply_chart_theme(fig)
        fig.update_layout(
            barmode="stack", height=max(300, len(wl) * 40),
            xaxis_title="Seats", yaxis_title="",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=0, r=50, t=20, b=30),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, width="stretch", config=CHART_CFG)
        st.markdown('<p class="sr-only">Stacked horizontal bar chart showing won and leading seats by party</p>', unsafe_allow_html=True)


# ===========================================================================
# SECTION 2: PARTY FORTUNES BY ROUND
# ===========================================================================

with st.container(border=True):
    col_title, col_toggle = st.columns([5, 1])
    with col_title:
        st.subheader("📈 Party Fortunes by Counting Round")
    with col_toggle:
        share_pct = st.toggle("Vote Share %", value=False, key="vote_share_toggle")
    metric = "vote_share_pct" if share_pct else "cumulative_votes"

    def compute_party_round_series(state_code=None, metric="cumulative_votes"):
        from db_utils import _connect, _placeholder
        p = _placeholder()
        where = f"WHERE r.state_code = {p}" if state_code else ""
        params_q = (state_code,) if state_code else None
        conn = _connect()
        raw = pd.read_sql_query(
            f"""SELECT r.state_code, r.ac_no, r.round_no, r.party_abv AS party, r.votes
                FROM rounds_ac r
                {where} ORDER BY r.state_code, r.ac_no, r.round_no""",
            conn, params=params_q,
        )
        conn.close()
        if raw.empty:
            return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])
        raw["votes"] = pd.to_numeric(raw["votes"], errors="coerce").fillna(0).astype(int)
        max_round = int(raw["round_no"].max())
        results = []
        for r in range(1, max_round + 1):
            # For each round, take the latest data per AC (highest round_no <= r)
            el = raw[raw["round_no"] <= r]
            if el.empty:
                continue
            la = el.groupby(["state_code", "ac_no"])["round_no"].max().reset_index()
            sn = el.merge(la, on=["state_code", "ac_no", "round_no"], how="inner")
            pt = sn.groupby("party")["votes"].sum().reset_index()
            pt["round_num"] = r
            pt["round_label"] = f"R{r}"
            results.append(pt)
        if not results:
            return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])
        s = pd.concat(results, ignore_index=True)
        s.rename(columns={"votes": "value"}, inplace=True)
        ap = s["party"].unique()
        r0 = pd.DataFrame({"party": ap, "round_num": 0, "round_label": "R0", "value": 0})
        s = pd.concat([r0, s], ignore_index=True)
        fi = pd.MultiIndex.from_product([ap, range(0, max_round + 1)], names=["party", "round_num"])
        s = s.set_index(["party", "round_num"]).reindex(fi).reset_index()
        s["round_label"] = s["round_num"].apply(lambda x: f"R{x}")
        s["value"] = s.groupby("party")["value"].ffill().fillna(0)
        if metric == "vote_share_pct":
            ft = s[s["round_num"] == max_round]["value"].sum()
            s["value"] = s.apply(lambda r: (r["value"] / ft * 100) if ft > 0 else 0, axis=1)
        return s

    series_df = compute_party_round_series( state_code_filter, metric)

    if series_df.empty:
        st.info("No data yet.")
    else:
        max_round = int(series_df["round_num"].max())
        if max_round < 1:
            st.info("Waiting for counting data...")
        else:
            lr = series_df[series_df["round_num"] == max_round]
            tp = lr.nlargest(10, "value")["party"].tolist()
            series_df["pg"] = series_df["party"].apply(lambda p: p if p in tp else "Others")
            pdf = series_df.groupby(["pg", "round_num", "round_label"])["value"].sum().reset_index()
            rl = [f"R{i}" for i in range(0, max_round + 1)]
            fig = go.Figure()
            for p in tp + ["Others"]:
                pf = pdf[pdf["pg"] == p].sort_values("round_num")
                if pf.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=pf["round_label"], y=pf["value"], mode="lines", name=short(p),
                    line=dict(color=get_pc(p), width=2),
                    hovertemplate=f"<b>{short(p)}</b><br>Round: %{{x}}<br>{'Vote Share' if metric == 'vote_share_pct' else 'Votes'}: %{{y:,.1f}}{'%' if metric == 'vote_share_pct' else ''}<extra></extra>",
                ))
            yl = "Vote Share (%)" if metric == "vote_share_pct" else "Cumulative Votes"
            _apply_chart_theme(fig)
            fig.update_layout(
                xaxis_title="Counting Round", yaxis_title=yl, height=500,
                hovermode="x unified",
                xaxis=dict(categoryorder="array", categoryarray=rl),
                yaxis=dict(ticksuffix="%" if metric == "vote_share_pct" else ""),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch", config=CHART_CFG)
            st.markdown('<p class="sr-only">Line chart showing cumulative votes or vote share by counting round for each party</p>', unsafe_allow_html=True)
            st.caption("Lines converge to the final declared share. Flat = no new votes in that round.")


# ===========================================================================
# SECTION 3: CONSTITUENCY DRILL-DOWN
# ===========================================================================

# Hide from Overall view
if state_code_filter:
    with st.container(border=True):
        st.subheader("🔍 Constituency Drill-Down")

        # Since state_code_filter is always truthy in this block, use directly
        drill_state_name = _display_state
        dsc = state_code_filter

        ac_statuses = get_all_constituency_statuses()
        acl = ac_statuses[ac_statuses["state_code"] == dsc]
        ac_opts = []
        margin_map = {}
        for _, row in acl.iterrows():
            name = row.get("ac_name") or f"AC-{row['ac_no']}"
            ac_opts.append(f"{row['ac_no']}. {name}")
            # Compute margin for default selection
            rdf_tmp = get_constituency_rounds( dsc, int(row["ac_no"]))
            if not rdf_tmp.empty:
                lt_tmp = rdf_tmp["round_no"].max()
                latest_tmp = rdf_tmp[rdf_tmp["round_no"] == lt_tmp]
                if len(latest_tmp) >= 2:
                    sv = latest_tmp.sort_values("votes", ascending=False)
                    margin_map[f"{row['ac_no']}. {name}"] = int(sv.iloc[0]["votes"]) - int(sv.iloc[1]["votes"])
                else:
                    margin_map[f"{row['ac_no']}. {name}"] = 0
            else:
                margin_map[f"{row['ac_no']}. {name}"] = 0

        if not ac_opts:
            st.info("No data.")
        else:
            da = st.session_state.get("drill_ac", ac_opts[0])
            if da not in ac_opts:
                # Default to constituency with largest margin
                da = max(margin_map, key=margin_map.get) if margin_map else ac_opts[0]
            # Dynamic key so widget resets when state changes
            st.markdown('<style>[data-testid="stVerticalBlock"] label:has(+ div [data-testid="stSelectbox"]) { text-align: left !important; }</style>', unsafe_allow_html=True)
            sel_ac = st.selectbox("Constituency", ac_opts, index=ac_opts.index(da), key=f"drill_ac_{dsc}")
            if sel_ac and sel_ac != st.session_state.get("drill_ac"):
                st.session_state["drill_ac"] = sel_ac
                st.rerun()

            ac_no = int(sel_ac.split(".")[0])
            arow = acl[acl["ac_no"] == ac_no].iloc[0]
            status = arow["status"]
            cr = int(arow.get("current_round", 0) or 0)
            tr = int(arow.get("total_rounds", 0) or 0)

            # Status dot for chart title
            _status_dot = {"DONE": "🟢", "LIVE": "🟡", "ERROR": "🔴"}.get(status, "⚪")

            rdf = get_constituency_rounds( dsc, ac_no)
            if not rdf.empty:
                lt = rdf["round_no"].max()
                latest = rdf[rdf["round_no"] == lt].copy().sort_values("votes", ascending=False)

                latest["dn"] = latest["candidate"].apply(cdn)
                latest["sp"] = latest["party"].apply(short)
                latest["ht"] = latest.apply(lambda r: f"{r['candidate']} ({r['party']})", axis=1)
                colors = ["#374151" if row["party"] == "NOTA" else get_pc(row["party"]) for _, row in latest.iterrows()]

                # Determine winner, runner-up, and deposit-lost
                total_valid = int(latest["votes"].sum())
                deposit_threshold = total_valid / 6

                def _badge(row, idx):
                    if idx == 0:
                        return "🏆 " + row["dn"]
                    elif idx == 1:
                        return "🥈 " + row["dn"]
                    elif row["votes"] < deposit_threshold and row["party"] != "NOTA":
                        return "🦆 " + row["dn"]
                    return row["dn"]

                latest["label"] = [_badge(r, i) for i, (_, r) in enumerate(latest.iterrows())]
                # Keep sorted ascending for horizontal bar (highest at top when reversed)
                chart_data = latest.sort_values("votes", ascending=True)

                fig = go.Figure()
                fig.add_trace(go.Bar(y=chart_data["label"], x=chart_data["votes"], orientation="h",
                    marker_color=[colors[latest.index.get_loc(idx)] for idx in chart_data.index],
                    text=chart_data.apply(lambda r: f"{r['sp']} ({r['votes']:,})", axis=1),
                    textposition="auto", hovertext=chart_data["ht"]))

                # Margin bar: anchored at runner-up bar end
                if len(latest) >= 2:
                    w_votes = int(latest.iloc[0]["votes"])
                    r_votes = int(latest.iloc[1]["votes"])
                    margin = w_votes - r_votes
                    if margin > 0:
                        r_label = chart_data.loc[chart_data["votes"] == r_votes, "label"].iloc[0]
                        fig.add_trace(go.Bar(
                            y=[r_label], x=[margin], base=[r_votes],
                            orientation="h",
                            marker=dict(color="#D1D5DB", pattern=dict(shape="/", fillmode="replace", solidity=0.4)),
                            text=[f"+{margin:,}"], textposition="inside", insidetextanchor="middle",
                            textfont=dict(color="#374151", size=10),
                            showlegend=False, hoverinfo="skip"))
                        _apply_chart_theme(fig)
                        fig.update_layout(barmode="overlay")

                rnd = latest["round_no"].iloc[0] if "round_no" in latest.columns else cr
                _legend_text = "  🏆 Winner  ·  🥈 Runner-up  ·  🦆 Lost deposit"
                _apply_chart_theme(fig)
                fig.update_layout(
                    title=dict(text=f"{_status_dot} Round {rnd} Snapshot{_legend_text}",
                               font=dict(size=16)),
                    height=max(300, len(latest)*35),
                    xaxis_title="Votes", yaxis_title="",
                    margin=dict(l=0,r=0,t=50,b=0))
                st.plotly_chart(fig, width="stretch", config=CHART_CFG)
                st.markdown('<p class="sr-only">Horizontal bar chart showing candidate vote counts with winner and runner-up highlighted</p>', unsafe_allow_html=True)

                if len(rdf["round_no"].unique()) > 1:
                    # Get top candidates and NOTA
                    tc = rdf[rdf["round_no"] == lt].nlargest(4, "votes")["candidate"].tolist()
                    nc = rdf[rdf["party"] == "NOTA"]["candidate"].unique().tolist()
                    sc = set(tc + nc)

                    # For each candidate, get the latest vote count per round_no
                    rdf_sorted = rdf.sort_values("round_no")
                    latest_per_round = rdf_sorted.groupby(["candidate", "round_no"]).tail(1)

                    fl = go.Figure()
                    for c in sc:
                        cf = latest_per_round[latest_per_round["candidate"] == c].sort_values("round_no")
                        if cf.empty:
                            continue
                        party = cf["party"].iloc[0]
                        color = "#374151" if party == "NOTA" else get_pc(party)
                        label = f"{cdn(c)} ({short(party)})"
                        # Show name only on the last point
                        text_vals = [""] * (len(cf) - 1) + [label]
                        fl.add_trace(go.Scatter(
                            x=cf["round_no"].apply(lambda r: f"R{r}"),
                            y=cf["votes"], mode="lines+markers+text",
                            text=text_vals, textposition="middle right",
                            textfont=dict(size=11, color=color),
                            name=label, showlegend=False,
                            line=dict(color=color, width=2),
                        ))
                    max_r = int(latest_per_round["round_no"].max()) if not latest_per_round.empty else 1
                    rl = [f"R{i}" for i in range(1, max_r + 1)]
                    _apply_chart_theme(fl)
                    fl.update_layout(
                        xaxis_title="Counting Round", yaxis_title="Cumulative Votes",
                        height=400, hovermode="x unified", showlegend=False,
                        xaxis=dict(categoryorder="array", categoryarray=rl),
                        margin=dict(l=0, r=80, t=10, b=0),
                    )
                    st.plotly_chart(fl, width="stretch", config=CHART_CFG)
                    st.markdown('<p class="sr-only">Line chart showing vote progression by counting round for top candidates and NOTA</p>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Auto-refresh + WCAG Announcement
# ---------------------------------------------------------------------------

st.markdown("<meta http-equiv='refresh' content='120'>", unsafe_allow_html=True)

_ss = get_status_summary()
_status_msg = f"Election tracker loaded. {_ss.get('DONE', 0)} constituencies counted, {_ss.get('LIVE', 0)} counting, {_ss.get('PENDING', 0)} pending, {_ss.get('ERROR', 0)} errors"
st.markdown(f"<script>var el=document.getElementById('data-status');if(el)el.textContent='{_status_msg}';</script>", unsafe_allow_html=True)
