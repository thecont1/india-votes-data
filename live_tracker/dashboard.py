#!/usr/bin/env python3
"""
ECI Live Election Dashboard — Single-Page Template
Reusable across all state pages and Overall.
"""

import os
import sqlite3 as _sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db_utils import (
    get_all_constituency_statuses,
    get_constituency_rounds,
    get_last_scrape_time,
    get_party_seat_tally_won_leading,
    get_scrape_cycles,
    get_state_status_summary,
    get_status_summary,
)
from states_may2026 import (
    MAJORITIES,
    PARTY_COLORS,
    STATES,
    STATUS_COLORS,
    get_url,
    normalise_party,
    short,
    state_code_for,
)

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = os.path.join(os.path.dirname(__file__), "live_results.db")

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
# Global CSS: hide sidebar, hide anchors, hide Plotly toolbars, 65% width
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    [data-testid="stSidebar"] {display: none !important;}
    [data-testid="collapsedControl"] {display: none !important;}
    /* Hide Streamlit header and heading anchors */
    header[data-testid="stHeader"] {display: none !important;}
    [data-testid="stHeaderActionElements"] {display: none !important;}
    h1 a, h2 a, h3 a {display: none !important;}
    /* Constrain width and tighter top padding */
    .block-container {
        padding-top: 0.2rem !important;
        padding-bottom: 1rem !important;
        max-width: 65%;
        margin: 0 auto;
    }
    /* Header bar */
    .header-bar {
        display: flex; align-items: center; justify-content: space-between;
        padding: 0.6rem 1rem; margin-bottom: 1rem;
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        border-radius: 10px; color: white;
    }
    .header-bar h2 { margin: 0; font-size: 1.15rem; color: white; }
    .header-bar .ts { font-size: 1.0rem; font-weight: 600; letter-spacing: 0.03em; opacity: 1; }
    /* Gear button: no border, scaled up */
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) [data-testid="stElementContainer"] {
        width: auto !important;
        display: flex !important;
        justify-content: flex-end !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) [data-testid="stButton"] {
        width: auto !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) [data-testid="stButton"] button {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        font-size: 1rem !important;
        padding: 0.2rem 0.4rem !important;
        min-height: 0 !important;
        line-height: 1 !important;
        width: auto !important;
        height: auto !important;
        zoom: 2.0 !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) [data-testid="stButton"] button:hover {
        background: transparent !important;
        box-shadow: none !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) [data-testid="stButton"] button:active {
        background: transparent !important;
        box-shadow: none !important;
    }
    /* Right-align ALL headers and numeric data in tables */
    [data-testid="stDataFrame"] thead th { text-align: right !important; }
    [data-testid="stDataFrame"] tbody td:not(:first-child) { text-align: right !important; }
    /* Reduce State Overview table width */
    [data-testid="stDialog"] [data-testid="stDataFrame"] { max-width: 90% !important; }
    /* Equal-width metric cells in Status (dialog only) */
    [data-testid="stDialog"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        flex: 1 1 0% !important;
        min-width: 0 !important;
        max-width: none !important;
    }
    /* Metric labels right-aligned in dialog */
    [data-testid="stDialog"] [data-testid="stMetric"] [data-testid="stMetricLabel"] {
        text-align: right !important;
    }
</style>
""", unsafe_allow_html=True)

if not os.path.exists(DB_PATH):
    st.error("Database not found. Run `./scheduler.sh` first.")
    st.stop()

# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

@st.dialog("⚙️ Settings & System Monitor", width="large")
def settings_dialog():
    st.subheader("Refresh")
    col_refresh, _ = st.columns([1, 3])
    with col_refresh:
        refresh_interval = st.slider("Auto-refresh (seconds)", 30, 300, 120, key="dlg_refresh")
        if st.button("🔄 Refresh Now", key="dlg_refresh_btn"):
            st.rerun()
    last_update = get_last_scrape_time(DB_PATH)
    if last_update:
        st.caption(f"Last update: {fmt_ist(last_update)}")

    st.divider()
    st.subheader("Status")
    ss = get_status_summary(DB_PATH)
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
    summary = get_state_status_summary(DB_PATH)
    if summary:
        df_sum = pd.DataFrame(summary)
        so_rows = []
        for state in STATES:
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
            st.dataframe(so_df, width="stretch", hide_index=True,
                column_config={
                    "State": st.column_config.TextColumn("State", width="small"),
                    "🟢 Counted": st.column_config.NumberColumn("🟢 Counted", width="small"),
                    "🟡 Counting": st.column_config.NumberColumn("🟡 Counting", width="small"),
                    "⚪ Pending": st.column_config.NumberColumn("⚪ Pending", width="small"),
                    "🔴 Errors": st.column_config.NumberColumn("🔴 Errors", width="small"),
                    "Reporting": st.column_config.TextColumn("Reporting", width="small"),
                })

    st.divider()
    st.subheader("Update Cycles")
    cycles_df = get_scrape_cycles(DB_PATH)
    if not cycles_df.empty:
        dc = cycles_df.copy()
        for col in ["started_at", "finished_at"]:
            if col in dc.columns:
                dc[col] = dc[col].apply(lambda x: fmt_ist(x, "%H:%M:%S IST") if pd.notna(x) else "")
        show_cols = [c for c in ["started_at","finished_at","pages_attempted","pages_success","pages_skipped","pages_error","cycle_duration_sec"] if c in dc.columns]
        st.dataframe(dc[show_cols], width="stretch", hide_index=True, height=300)

    st.divider()
    ac_statuses = get_all_constituency_statuses(DB_PATH)
    errs = ac_statuses[ac_statuses["status"] == "ERROR"] if not ac_statuses.empty else pd.DataFrame()
    if not errs.empty:
        st.subheader(f"⚠️ Failed ({len(errs)})")
        for _, row in errs.iterrows():
            name = row.get("ac_name") or f"AC-{row['ac_no']}"
            url = get_url(row["state_code"], row["ac_no"])
            st.markdown(f"- **{name}** ({row['state_name']}) — [View]({url})")


# ---------------------------------------------------------------------------
# State selector
# ---------------------------------------------------------------------------

def _get_state_dots():
    """Return {state_name: dot_emoji} for per-state coloured status dots."""
    summary = get_state_status_summary(DB_PATH)
    dots = {}
    if not summary:
        return {s["name"]: "⚪" for s in STATES}
    df_sum = pd.DataFrame(summary)
    for state in STATES:
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
state_options = ["Overall"] + [f"{state_dots.get(s['name'], '⚪')} {s['name']}" for s in STATES]
default_state = st.session_state.get("selected_state", "Overall")
if default_state not in state_options:
    default_state = "Overall"

# Pills + gear button on same row
col_pills, col_gear = st.columns([8, 1])
with col_pills:
    selected_state = st.pills("State", state_options, default=default_state, selection_mode="single", label_visibility="collapsed")
    # Strip leading dot emoji for state lookup
    _clean_state = selected_state.lstrip("🟢🟡⚪🔴 ") if selected_state else selected_state
    if _clean_state and _clean_state != st.session_state.get("selected_state"):
        st.session_state["selected_state"] = _clean_state
        st.rerun()
with col_gear:
    if st.button("⚙️", key="gear_top"):
        settings_dialog()

state_code_filter = None
_display_state = st.session_state.get("selected_state", "Overall")
if _display_state and _display_state != "Overall":
    state_code_filter = state_code_for(_display_state)

# ---------------------------------------------------------------------------
# Header bar
# ---------------------------------------------------------------------------

last_update = get_last_scrape_time(DB_PATH)
last_ts = fmt_ist(last_update) if last_update else "No data yet"

st.markdown(
    f"""<div class="header-bar">
    <h2>🗳️ ECI Live Election Tracker — {_display_state}</h2>
    <span class="ts">📅 {last_ts}</span>
</div>""",
    unsafe_allow_html=True,
)

CHART_CFG = dict(displayModeBar=False, responsive=True)

# ===========================================================================
# SECTION 1: SEAT TALLY
# ===========================================================================

with st.container(border=True):
    st.markdown("**📊 Seat Tally**")

    wl_tally = get_party_seat_tally_won_leading(DB_PATH, state_code_filter)
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
        ))
        fig.add_trace(go.Bar(
            y=wl["short"], x=wl["leading"], orientation="h", name="Leading",
            marker_color=[get_pc(p) for p in wl["party"]],
            marker_pattern=dict(shape="/", solidity=0.6),
            text=wl.apply(lambda r: str(int(r["leading"])) if r["leading"] > 0 else "", axis=1),
            textposition="outside", textfont=dict(size=12),
            hovertext=wl.apply(lambda r: f"{r['party']}: {int(r['leading'])} leading", axis=1),
        ))
        if state_code_filter and state_code_filter in MAJORITIES:
            maj = MAJORITIES[state_code_filter]
            fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
            fig.add_annotation(x=maj, y=len(wl) - 2, text=f"Majority ({maj})",
                               showarrow=False, font=dict(color="red", size=14),
                               xanchor="left", xshift=8, yanchor="middle")
        fig.update_layout(
            barmode="stack", height=max(300, len(wl) * 40),
            xaxis_title="Seats", yaxis_title="",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=0, r=50, t=20, b=30),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, width="stretch", config=CHART_CFG)


# ===========================================================================
# SECTION 2: PARTY FORTUNES BY ROUND
# ===========================================================================

with st.container(border=True):
    col_title, col_toggle = st.columns([5, 1])
    with col_title:
        st.markdown("**📈 Party Fortunes by Counting Round**")
    with col_toggle:
        share_pct = st.toggle("Vote Share %", value=False, key="vote_share_toggle")
    metric = "vote_share_pct" if share_pct else "cumulative_votes"

    def compute_party_round_series(db_path, state_code=None, metric="cumulative_votes"):
        where = "WHERE state_code = ?" if state_code else ""
        params_q = (state_code,) if state_code else ()
        conn = _sqlite3.connect(db_path, timeout=30)
        conn.row_factory = _sqlite3.Row
        raw = pd.read_sql_query(
            f"SELECT state_code, ac_no, round_no, party, votes, scraped_at FROM rounds {where} ORDER BY state_code, ac_no, scraped_at",
            conn, params=params_q,
        )
        conn.close()
        if raw.empty:
            return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])
        raw["votes"] = pd.to_numeric(raw["votes"], errors="coerce").fillna(0).astype(int)
        raw["scraped_at"] = pd.to_datetime(raw["scraped_at"])
        max_round = int(raw["round_no"].max())
        lpr = raw.groupby(["state_code", "ac_no", "round_no"])["scraped_at"].max().reset_index()
        rl = raw.merge(lpr, on=["state_code", "ac_no", "round_no", "scraped_at"], how="inner")
        results = []
        for r in range(1, max_round + 1):
            el = rl[rl["round_no"] <= r]
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

    series_df = compute_party_round_series(DB_PATH, state_code_filter, metric)

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
                    x=pf["round_label"], y=pf["value"], mode="lines+markers", name=short(p),
                    line=dict(color=get_pc(p), width=2), marker=dict(size=5),
                    hovertemplate=f"<b>{short(p)}</b><br>Round: %{{x}}<br>{'Vote Share' if metric == 'vote_share_pct' else 'Votes'}: %{{y:,.1f}}{'%' if metric == 'vote_share_pct' else ''}<extra></extra>",
                ))
            yl = "Vote Share (%)" if metric == "vote_share_pct" else "Cumulative Votes"
            fig.update_layout(
                xaxis_title="Counting Round", yaxis_title=yl, height=500,
                hovermode="x unified",
                xaxis=dict(categoryorder="array", categoryarray=rl),
                yaxis=dict(ticksuffix="%" if metric == "vote_share_pct" else ""),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch", config=CHART_CFG)
            st.caption("Lines converge to the final declared share. Flat = no new votes in that round.")


# ===========================================================================
# SECTION 3: CONSTITUENCY DRILL-DOWN
# ===========================================================================

with st.container(border=True):
    st.markdown("**🔍 Constituency Drill-Down**")

    # When a specific state is selected, drill-down follows it.
    # When "Overall" is selected, show a state dropdown so user can pick one.
    if state_code_filter:
        drill_state_name = _display_state
        dsc = state_code_filter
    else:
        drill_state_opts = [s["name"] for s in STATES]
        drill_state_name = st.selectbox("State", drill_state_opts, key="drill_state_select")
        dsc = state_code_for(drill_state_name)

    ac_statuses = get_all_constituency_statuses(DB_PATH)
    acl = ac_statuses[ac_statuses["state_code"] == dsc]
    ac_opts = []
    margin_map = {}
    for _, row in acl.iterrows():
        name = row.get("ac_name") or f"AC-{row['ac_no']}"
        ac_opts.append(f"{row['ac_no']}. {name}")
        # Compute margin for default selection
        rdf_tmp = get_constituency_rounds(DB_PATH, dsc, int(row["ac_no"]))
        if not rdf_tmp.empty:
            lt_tmp = rdf_tmp["scraped_at"].max()
            latest_tmp = rdf_tmp[rdf_tmp["scraped_at"] == lt_tmp]
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

        rdf = get_constituency_rounds(DB_PATH, dsc, ac_no)
        if not rdf.empty:
            lt = rdf["scraped_at"].max()
            latest = rdf[rdf["scraped_at"] == lt].copy().sort_values("votes", ascending=False)

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

            # Margin bracket between winner and runner-up
            if len(latest) >= 2:
                w_votes = int(latest.iloc[0]["votes"])
                r_votes = int(latest.iloc[1]["votes"])
                margin = w_votes - r_votes
                if margin > 0:
                    # Draw a bracket: vertical line at winner votes, horizontal connectors
                    mid_y = 0.5  # Between winner (y=0) and runner-up (y=1)
                    bracket_color = "#9CA3AF"
                    # Horizontal line from runner-up votes to winner votes at mid_y
                    fig.add_shape(type="line",
                        x0=r_votes, x1=w_votes, y0=mid_y, y1=mid_y,
                        line=dict(color=bracket_color, width=1.5), layer="above")
                    # Vertical tick at runner-up end
                    fig.add_shape(type="line",
                        x0=r_votes, x1=r_votes, y0=mid_y - 0.15, y1=mid_y + 0.15,
                        line=dict(color=bracket_color, width=1.5), layer="above")
                    # Vertical tick at winner end
                    fig.add_shape(type="line",
                        x0=w_votes, x1=w_votes, y0=mid_y - 0.15, y1=mid_y + 0.15,
                        line=dict(color=bracket_color, width=1.5), layer="above")
                    # Margin label
                    fig.add_annotation(
                        x=(w_votes + r_votes) / 2, y=mid_y,
                        text=f"+{margin:,}", showarrow=False,
                        font=dict(color="#6B7280", size=10),
                        bgcolor="white", borderpad=1)

            rnd = latest["round_no"].iloc[0] if "round_no" in latest.columns else cr
            _legend_text = "  🏆 Winner  ·  🥈 Runner-up  ·  🦆 Lost deposit"
            fig.update_layout(
                title=dict(text=f"{_status_dot} Round {rnd} Snapshot{_legend_text}",
                           font=dict(size=16)),
                height=max(300, len(latest)*35),
                xaxis_title="Votes", yaxis_title="",
                margin=dict(l=0,r=0,t=50,b=0))
            st.plotly_chart(fig, width="stretch", config=CHART_CFG)

            if len(rdf["scraped_at"].unique()) > 1:
                # Get top candidates and NOTA
                tc = rdf[rdf["scraped_at"] == lt].nlargest(4, "votes")["candidate"].tolist()
                nc = rdf[rdf["party"] == "NOTA"]["candidate"].unique().tolist()
                sc = set(tc + nc)

                # For each candidate, get the latest vote count per round_no
                rdf_sorted = rdf.sort_values("scraped_at")
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
                fl.update_layout(
                    xaxis_title="Counting Round", yaxis_title="Cumulative Votes",
                    height=400, hovermode="x unified", showlegend=False,
                    xaxis=dict(categoryorder="array", categoryarray=rl),
                    margin=dict(l=0, r=80, t=10, b=0),
                )
                st.plotly_chart(fl, width="stretch", config=CHART_CFG)


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
st.markdown("<meta http-equiv='refresh' content='120'>", unsafe_allow_html=True)
