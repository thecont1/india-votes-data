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
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) button[data-testid="stBaseButton-secondary"] {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        font-size: 1.8rem !important;
        padding: 0.2rem 0.5rem !important;
        min-height: 0 !important;
        line-height: 1 !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) button[data-testid="stBaseButton-secondary"]:hover {
        background: transparent !important;
        box-shadow: none !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:nth-of-type(2) button[data-testid="stBaseButton-secondary"]:active {
        background: transparent !important;
        box-shadow: none !important;
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
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Counted", done, help="Counting complete — all rounds scraped (DONE)")
    c2.metric("Counting", live, help="Counting in progress — rounds still coming in (LIVE)")
    c3.metric("Pending", pending, help="Page not yet live on ECI — counting hasn't started (PENDING)")
    c4.metric("Errors", errors, help="Scraping failed after multiple retries (ERROR)")
    c5.metric("Total", total, help="All constituencies being tracked across all states")

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
            st.dataframe(pd.DataFrame(so_rows), width="stretch", hide_index=True)

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

params = st.query_params
state_options = ["Overall"] + [s["name"] for s in STATES]
default_state = params.get("state", "Overall")
if default_state not in state_options:
    default_state = "Overall"

# Pills + gear button on same row
col_pills, col_gear = st.columns([9, 1])
with col_pills:
    selected_state = st.pills("State", state_options, default=default_state, selection_mode="single", label_visibility="collapsed")
    if selected_state and selected_state != params.get("state"):
        st.query_params["state"] = selected_state
        st.rerun()
with col_gear:
    if st.button("⚙️", key="gear_top"):
        settings_dialog()

state_code_filter = None
if selected_state != "Overall":
    state_code_filter = state_code_for(selected_state)

# ---------------------------------------------------------------------------
# Header bar
# ---------------------------------------------------------------------------

last_update = get_last_scrape_time(DB_PATH)
last_ts = fmt_ist(last_update) if last_update else "No data yet"

st.markdown(
    f"""<div class="header-bar">
    <h2>🗳️ ECI Live Election Tracker — {selected_state}</h2>
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

    # Per-state expanders (Overall only)
    if selected_state == "Overall":
        for state in STATES:
            sc = state["code"]
            maj = MAJORITIES.get(sc, 0)
            with st.expander(f"{state['name']}  (majority: {maj})"):
                sw = get_party_seat_tally_won_leading(DB_PATH, sc)
                if sw.empty:
                    st.info("No data yet.")
                    continue
                sd = collapse_others(sw, "party", "total", top_n=8)
                sd["short"] = sd["party"].apply(short)
                for c in ["won", "leading", "total"]:
                    sd[c] = sd[c].fillna(0).astype(int)
                f2 = go.Figure()
                f2.add_trace(go.Bar(y=sd["short"], x=sd["won"], orientation="h", name="Won",
                    marker_color=[get_pc(p) for p in sd["party"]],
                    text=sd.apply(lambda r: str(int(r["won"])) if r["won"]>0 else "", axis=1),
                    textposition="inside", textfont=dict(color="white", size=12)))
                f2.add_trace(go.Bar(y=sd["short"], x=sd["leading"], orientation="h", name="Leading",
                    marker_color=[get_pc(p) for p in sd["party"]],
                    marker_pattern=dict(shape="/", solidity=0.6),
                    text=sd.apply(lambda r: str(int(r["leading"])) if r["leading"]>0 else "", axis=1),
                    textposition="outside", textfont=dict(size=11)))
                f2.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
                f2.add_annotation(x=maj, y=len(sd) - 2, text=f"Majority ({maj})",
                    showarrow=False, font=dict(color="red", size=13),
                    xanchor="left", xshift=8, yanchor="middle")
                f2.update_layout(barmode="stack", height=max(250, len(sd)*35),
                    yaxis=dict(autorange="reversed"), margin=dict(l=0,r=50,t=20,b=30), showlegend=False)
                st.plotly_chart(f2, width="stretch", config=CHART_CFG)


# ===========================================================================
# SECTION 2: PARTY FORTUNES BY ROUND
# ===========================================================================

with st.container(border=True):
    st.markdown("**📈 Party Fortunes by Counting Round**")

    trend_mode = st.radio("Display mode", ["Cumulative Votes", "Vote Share %"], horizontal=True, key="trend_mode")
    metric = "vote_share_pct" if trend_mode == "Vote Share %" else "cumulative_votes"

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
                    hovertemplate=f"<b>{short(p)}</b><br>Round: %{{x}}<br>{trend_mode}: %{{y:,.0f}}<extra></extra>",
                ))
            yl = "Vote Share (%)" if metric == "vote_share_pct" else "Cumulative Votes"
            fig.update_layout(
                xaxis_title="Counting Round", yaxis_title=yl, height=500,
                hovermode="x unified",
                xaxis=dict(categoryorder="array", categoryarray=rl),
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
        drill_state_name = selected_state
        dsc = state_code_filter
    else:
        drill_state_opts = [s["name"] for s in STATES]
        drill_state_name = st.selectbox("State", drill_state_opts, key="drill_state_select")
        dsc = state_code_for(drill_state_name)

    ac_statuses = get_all_constituency_statuses(DB_PATH)
    acl = ac_statuses[ac_statuses["state_code"] == dsc]
    ac_opts = []
    for _, row in acl.iterrows():
        name = row.get("ac_name") or f"AC-{row['ac_no']}"
        ac_opts.append(f"{row['ac_no']}. {name}")

    if not ac_opts:
        st.info("No data.")
    else:
        da = params.get("drill_ac", ac_opts[0])
        if da not in ac_opts:
            da = ac_opts[0]
        # Dynamic key so widget resets when state changes
        sel_ac = st.selectbox("Constituency", ac_opts, index=ac_opts.index(da), key=f"drill_ac_{dsc}")
        if sel_ac and sel_ac != params.get("drill_ac"):
            st.query_params["drill_ac"] = sel_ac
            st.rerun()

        ac_no = int(sel_ac.split(".")[0])
        arow = acl[acl["ac_no"] == ac_no].iloc[0]
        status = arow["status"]
        cr = int(arow.get("current_round", 0) or 0)
        tr = int(arow.get("total_rounds", 0) or 0)

        if status == "DONE":
            st.success(f"✅ Complete — Round {cr}/{tr}")
        elif status == "LIVE":
            pct = (cr / tr * 100) if tr > 0 else 0
            st.warning(f"🔴 Live — Round {cr}/{tr} (~{pct:.0f}% counted)")
        elif status == "ERROR":
            st.error("⚠️ Error fetching data")
        else:
            st.info("⏳ No data yet")

        rdf = get_constituency_rounds(DB_PATH, dsc, ac_no)
        if not rdf.empty:
            lt = rdf["scraped_at"].max()
            latest = rdf[rdf["scraped_at"] == lt].copy().sort_values("votes", ascending=True)

            if len(latest) >= 2:
                ds = latest.sort_values("votes", ascending=False)
                leader = ds.iloc[0]
                runner = ds.iloc[1]
                margin = int(leader["votes"]) - int(runner["votes"])
                ln = cdn(leader["candidate"])
                rn = cdn(runner["candidate"])
                msg = f"**{ln}** ({short(leader['party'])}) leading by **{margin:,} votes** over {rn} ({short(runner['party'])})"
                if cr > 0 and tr > 0:
                    msg += f" — ~{cr/tr*100:.0f}% counted"
                if margin < 100:
                    st.error(f"🔥 {msg}")
                elif margin < 500:
                    st.warning(f"⚠️ {msg}")
                else:
                    st.success(f"🏆 {msg}")

            latest["dn"] = latest["candidate"].apply(cdn)
            latest["sp"] = latest["party"].apply(short)
            latest["ht"] = latest.apply(lambda r: f"{r['candidate']} ({r['party']})", axis=1)
            colors = ["#374151" if row["party"] == "NOTA" else get_pc(row["party"]) for _, row in latest.iterrows()]

            fig = go.Figure()
            fig.add_trace(go.Bar(y=latest["dn"], x=latest["votes"], orientation="h",
                marker_color=colors,
                text=latest.apply(lambda r: f"{r['sp']} ({r['votes']:,})", axis=1),
                textposition="auto", hovertext=latest["ht"]))
            rnd = latest["round_no"].iloc[0] if "round_no" in latest.columns else cr
            fig.update_layout(title=f"Round {rnd} Snapshot", height=max(300, len(latest)*35),
                xaxis_title="Votes", yaxis_title="", margin=dict(l=0,r=0,t=40,b=0))
            st.plotly_chart(fig, width="stretch", config=CHART_CFG)

            if len(rdf["scraped_at"].unique()) > 1:
                rdf["time_ist"] = pd.to_datetime(rdf["scraped_at"]).dt.tz_convert(IST)
                tc = rdf[rdf["scraped_at"] == lt].nlargest(4, "votes")["candidate"].tolist()
                nc = rdf[rdf["party"] == "NOTA"]["candidate"].unique().tolist()
                sc = set(tc + nc)
                fl = go.Figure()
                for c in sc:
                    cf = rdf[rdf["candidate"] == c]
                    if cf.empty:
                        continue
                    party = cf["party"].iloc[0]
                    color = "#374151" if party == "NOTA" else get_pc(party)
                    fl.add_trace(go.Scatter(x=cf["time_ist"], y=cf["votes"], mode="lines+markers",
                        name=f"{cdn(c)} ({short(party)})", line=dict(color=color, width=2)))
                fl.update_layout(xaxis_title="Time (IST)", yaxis_title="Cumulative Votes",
                    height=400, hovermode="x unified", margin=dict(l=0,r=0,t=10,b=0))
                st.plotly_chart(fl, width="stretch", config=CHART_CFG)


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
st.markdown("<meta http-equiv='refresh' content='120'>", unsafe_allow_html=True)
