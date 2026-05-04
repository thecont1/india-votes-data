#!/usr/bin/env python3
"""
ECI Live Election Dashboard — Single Page View
"""

import os
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
    PARTY_SHORT,
    STATES,
    STATUS_COLORS,
    get_url,
    short,
    state_code_for,
)

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = os.path.join(os.path.dirname(__file__), "live_results.db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_ist(utc_iso, fmt="%H:%M IST"):
    if not utc_iso:
        return "N/A"
    try:
        dt = datetime.fromisoformat(utc_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime(fmt)
    except Exception:
        return str(utc_iso)


def get_party_color(party):
    return PARTY_COLORS.get(party, PARTY_COLORS.get("Others", "#ADB5BD"))


def collapse_others(df, party_col, value_col, top_n=10):
    df_sorted = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    top = df_sorted.head(top_n).copy()
    rest = df_sorted.iloc[top_n:]
    if not rest.empty:
        others_row = pd.DataFrame([{
            party_col: f"Others ({len(rest)} parties)",
            value_col: rest[value_col].sum(),
        }])
        top = pd.concat([top, others_row], ignore_index=True)
    return top


def candidate_display_name(name):
    if name.upper() == "NOTA":
        return "NOTA"
    return name.title()


# ---------------------------------------------------------------------------
# Page config — no sidebar
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ECI Live Election Tracker — May 2026",
    page_icon="🗳️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide sidebar, constrain width, position settings gear
st.markdown("""
<style>
    [data-testid="stSidebar"] {display: none !important;}
    [data-testid="collapsedControl"] {display: none !important;}
    .block-container {
        padding-top: 1rem;
        padding-bottom: 1rem;
        max-width: 60%;
        margin: 0 auto;
    }
    /* Settings gear button — position top-right */
    .settings-gear {position: fixed; top: 0.5rem; right: 5rem; z-index: 999;}
</style>
""", unsafe_allow_html=True)

if not os.path.exists(DB_PATH):
    st.error("Database not found. Run `./scheduler.sh` first.")
    st.stop()

# ---------------------------------------------------------------------------
# Settings panel (top-right gear)
# ---------------------------------------------------------------------------

# Settings gear — fixed top-right (next to Deploy/⋮)
st.markdown(
    '<div class="settings-gear">'
    '<form action="" method="get">'
    '<input type="hidden" name="settings" value="1">'
    '<button type="submit" style="background:none;border:none;font-size:1.5rem;cursor:pointer;" title="Settings & System Monitor">⚙️</button>'
    '</form></div>',
    unsafe_allow_html=True,
)

st.markdown("# 🗳️ ECI Live Election Tracker")

# Check if settings was clicked (via query param)
show_settings = params.get("settings") == "1"
if show_settings:
    # Add a "Back" link
    st.markdown('[← Back to dashboard](?)')
    st.divider()

# ---------------------------------------------------------------------------
# State selector — one-click pills
# ---------------------------------------------------------------------------

params = st.query_params
state_options = ["Overall"] + [s["name"] for s in STATES]
default_state = params.get("state", "Overall")
if default_state not in state_options:
    default_state = "Overall"

selected_state = st.pills(
    "State",
    state_options,
    default=default_state,
    selection_mode="single",
)
# Only write to query_params if changed (avoids double-rerun)
if selected_state and selected_state != params.get("state"):
    st.query_params["state"] = selected_state
    st.rerun()

state_code_filter = None
if selected_state != "Overall":
    state_code_filter = state_code_for(selected_state)

# Majority info
if selected_state != "Overall":
    maj = MAJORITIES.get(state_code_filter, 0)
    st.caption(f"Majority: {maj} seats")

# ---------------------------------------------------------------------------
# Settings / System Monitor panel (conditional)
# ---------------------------------------------------------------------------

if show_settings:
    st.divider()
    st.subheader("⚙️ Settings & System Monitor")

    # Status summary
    status_summary = get_status_summary(DB_PATH, state_code_filter)
    total_acs = sum(status_summary.values())
    reporting = status_summary.get("LIVE", 0) + status_summary.get("DONE", 0)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total ACs", total_acs)
    with col2:
        st.metric("Reporting", f"{reporting} / {total_acs}")
    with col3:
        st.metric("All Rounds Counted", status_summary.get("DONE", 0))
    with col4:
        st.metric("Errors", status_summary.get("ERROR", 0))

    # Auto-refresh
    refresh_interval = st.slider("Auto-refresh (seconds)", 30, 300, 120, key="refresh_slider")
    if st.button("🔄 Refresh Now"):
        st.rerun()

    # Last update
    last_update = get_last_scrape_time(DB_PATH)
    if last_update:
        st.caption(f"Last update: {fmt_ist(last_update, '%H:%M:%S IST')}")

    st.divider()

    # Update cycles
    st.subheader("Update Cycles")
    cycles_df = get_scrape_cycles(DB_PATH)
    if not cycles_df.empty:
        display_cycles = cycles_df.copy()
        for col in ["started_at", "finished_at"]:
            if col in display_cycles.columns:
                display_cycles[col] = display_cycles[col].apply(
                    lambda x: fmt_ist(x, "%H:%M:%S IST") if pd.notna(x) else ""
                )
        display_cols = [c for c in [
            "started_at", "finished_at", "pages_attempted",
            "pages_success", "pages_skipped", "pages_error", "cycle_duration_sec",
        ] if c in display_cycles.columns]
        st.dataframe(display_cycles[display_cols].head(10), width="stretch", hide_index=True)

    st.divider()

    # Error URLs
    ac_statuses = get_all_constituency_statuses(DB_PATH)
    error_acs = ac_statuses[ac_statuses["status"] == "ERROR"] if not ac_statuses.empty else pd.DataFrame()
    if not error_acs.empty:
        st.subheader(f"⚠️ Failed Updates ({len(error_acs)})")
        for _, row in error_acs.iterrows():
            ac_name = row.get("ac_name") or f"AC-{row['ac_no']}"
            url = get_url(row["state_code"], row["ac_no"])
            st.markdown(f"- **{ac_name}** ({row['state_name']}) — {row['error_count']} errors — [View page]({url})")
        st.divider()

    # Constituency table with search
    st.subheader("All Constituencies")
    if not ac_statuses.empty:
        search_col, status_col = st.columns([2, 1])
        with search_col:
            search = st.text_input("Search", "", placeholder="e.g. Madavaram, Kochi", key="search_ac")
        with status_col:
            status_filter = st.selectbox("Status", ["All", "LIVE", "DONE", "PENDING", "ERROR"], key="filter_status")

        filtered = ac_statuses.copy()
        if search:
            filtered = filtered[
                filtered["ac_name"].str.upper().str.contains(search.upper(), na=False)
                | filtered["state_name"].str.upper().str.contains(search.upper(), na=False)
            ]
        if status_filter != "All":
            filtered = filtered[filtered["status"] == status_filter]

        if "last_scraped" in filtered.columns:
            filtered["last_updated"] = filtered["last_scraped"].apply(
                lambda x: fmt_ist(x, "%d %b %H:%M") if pd.notna(x) and x else ""
            )

        display_cols = [c for c in [
            "state_name", "ac_no", "ac_name", "status",
            "current_round", "total_rounds", "last_updated",
        ] if c in filtered.columns]

        def color_status(val):
            bg = STATUS_COLORS.get(val, "#6B7280")
            return f"background-color: {bg}; color: white; font-weight: bold"

        styled = filtered[display_cols].style.map(color_status, subset=["status"])
        st.dataframe(styled, width="stretch", height=400, hide_index=True)

    # State-wise quick stats
    st.subheader("Quick Stats")
    summary = get_state_status_summary(DB_PATH)
    if summary:
        df_sum = pd.DataFrame(summary)
        for state in STATES:
            state_data = df_sum[df_sum["state_name"] == state["name"]]
            if not state_data.empty:
                counts = dict(zip(state_data["status"], state_data["cnt"]))
                total = sum(counts.values())
                done = counts.get("DONE", 0)
                live = counts.get("LIVE", 0)
                pend = counts.get("PENDING", 0)
                err = counts.get("ERROR", 0)
                st.markdown(
                    f"**{state['name']}**: "
                    f"🟢 {done} done  🟡 {live} live  ⚪ {pend} pending  🔴 {err} error  "
                    f"({done + live}/{total} reporting)"
                )

    st.stop()  # Don't show main content when settings is open


# ===========================================================================
# MAIN CONTENT — single page, no tabs
# ===========================================================================

# --- SECTION 1: SEAT TALLY ---
st.subheader("Seat Tally")

wl_tally = get_party_seat_tally_won_leading(DB_PATH, state_code_filter)
if wl_tally.empty:
    st.info("No data yet.")
else:
    wl_display = collapse_others(wl_tally, "party", "total", top_n=10)
    wl_display["short"] = wl_display["party"].apply(short)
    for col in ["won", "leading", "total"]:
        wl_display[col] = wl_display[col].fillna(0).astype(int)

    fig = go.Figure()

    # Won — solid, text inside
    fig.add_trace(go.Bar(
        y=wl_display["short"],
        x=wl_display["won"],
        orientation="h",
        name="Won",
        marker_color=[get_party_color(p) for p in wl_display["party"]],
        text=wl_display.apply(lambda r: str(int(r["won"])) if r["won"] > 0 else "", axis=1),
        textposition="inside",
        textfont=dict(color="white", size=13),
        hovertext=wl_display.apply(lambda r: f"{r['party']}: {int(r['won'])} won", axis=1),
    ))

    # Leading — hatched, text outside
    fig.add_trace(go.Bar(
        y=wl_display["short"],
        x=wl_display["leading"],
        orientation="h",
        name="Leading",
        marker_color=[get_party_color(p) for p in wl_display["party"]],
        marker_pattern=dict(shape="/", solidity=0.6),
        text=wl_display.apply(lambda r: str(int(r["leading"])) if r["leading"] > 0 else "", axis=1),
        textposition="outside",
        textfont=dict(size=12),
        hovertext=wl_display.apply(lambda r: f"{r['party']}: {int(r['leading'])} leading", axis=1),
    ))

    # Majority line
    if state_code_filter and state_code_filter in MAJORITIES:
        maj = MAJORITIES[state_code_filter]
        fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
        fig.add_annotation(x=maj, y=1.05, text=f"Majority ({maj})",
                           showarrow=False, font=dict(color="red", size=11))

    fig.update_layout(
        barmode="stack",
        height=max(300, len(wl_display) * 40),
        xaxis_title="Seats",
        yaxis_title="",
        yaxis=dict(autorange="reversed"),
        margin=dict(l=0, r=0, t=20, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width="stretch")

    # Per-state panels (only in Overall view)
    if selected_state == "Overall":
        for state in STATES:
            sc = state["code"]
            maj = MAJORITIES.get(sc, 0)
            with st.expander(f"{state['name']}  (majority: {maj} seats)", expanded=False):
                st_wl = get_party_seat_tally_won_leading(DB_PATH, sc)
                if st_wl.empty:
                    st.info("No data yet.")
                    continue
                st_display = collapse_others(st_wl, "party", "total", top_n=8)
                st_display["short"] = st_display["party"].apply(short)
                for col in ["won", "leading", "total"]:
                    st_display[col] = st_display[col].fillna(0).astype(int)

                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=st_display["short"], x=st_display["won"], orientation="h",
                    name="Won", marker_color=[get_party_color(p) for p in st_display["party"]],
                    text=st_display.apply(lambda r: str(int(r["won"])) if r["won"] > 0 else "", axis=1),
                    textposition="inside", textfont=dict(color="white", size=12),
                ))
                fig.add_trace(go.Bar(
                    y=st_display["short"], x=st_display["leading"], orientation="h",
                    name="Leading", marker_color=[get_party_color(p) for p in st_display["party"]],
                    marker_pattern=dict(shape="/", solidity=0.6),
                    text=st_display.apply(lambda r: str(int(r["leading"])) if r["leading"] > 0 else "", axis=1),
                    textposition="outside", textfont=dict(size=11),
                ))
                fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
                fig.add_annotation(x=maj, y=1.05, text=f"Majority ({maj})",
                                   showarrow=False, font=dict(color="red", size=11))
                fig.update_layout(
                    barmode="stack", height=max(250, len(st_display) * 35),
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=0, r=0, t=20, b=0), showlegend=False,
                )
                st.plotly_chart(fig, width="stretch")


# --- SECTION 2: PARTY FORTUNES BY ROUND ---
st.divider()
st.subheader("Party Fortunes by Counting Round")

trend_mode = st.radio("Display mode", ["Cumulative Votes", "Vote Share %"], horizontal=True, key="trend_mode")
metric = "vote_share_pct" if trend_mode == "Vote Share %" else "cumulative_votes"

# Compute round-based series
import sqlite3 as _sqlite3

def compute_party_round_series(db_path, state_code=None, metric="cumulative_votes"):
    where = "WHERE state_code = ?" if state_code else ""
    params = (state_code,) if state_code else ()
    conn = _sqlite3.connect(db_path, timeout=30)
    conn.row_factory = _sqlite3.Row
    raw = pd.read_sql_query(
        f"SELECT state_code, ac_no, round_no, party, votes, scraped_at FROM rounds {where} ORDER BY state_code, ac_no, scraped_at",
        conn, params=params,
    )
    conn.close()
    if raw.empty:
        return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])

    raw["votes"] = pd.to_numeric(raw["votes"], errors="coerce").fillna(0).astype(int)
    raw["scraped_at"] = pd.to_datetime(raw["scraped_at"])
    max_round = int(raw["round_no"].max())

    latest_per_round = raw.groupby(["state_code", "ac_no", "round_no"])["scraped_at"].max().reset_index()
    raw_latest = raw.merge(latest_per_round, on=["state_code", "ac_no", "round_no", "scraped_at"], how="inner")

    results = []
    for r in range(1, max_round + 1):
        eligible = raw_latest[raw_latest["round_no"] <= r]
        if eligible.empty:
            continue
        latest_ac = eligible.groupby(["state_code", "ac_no"])["round_no"].max().reset_index()
        snapshot = eligible.merge(latest_ac, on=["state_code", "ac_no", "round_no"], how="inner")
        party_totals = snapshot.groupby("party")["votes"].sum().reset_index()
        party_totals["round_num"] = r
        party_totals["round_label"] = f"R{r}"
        results.append(party_totals)

    if not results:
        return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])

    series_df = pd.concat(results, ignore_index=True)
    series_df.rename(columns={"votes": "value"}, inplace=True)

    all_parties = series_df["party"].unique()
    r0 = pd.DataFrame({"party": all_parties, "round_num": 0, "round_label": "R0", "value": 0})
    series_df = pd.concat([r0, series_df], ignore_index=True)

    full_idx = pd.MultiIndex.from_product([all_parties, range(0, max_round + 1)], names=["party", "round_num"])
    series_df = series_df.set_index(["party", "round_num"]).reindex(full_idx).reset_index()
    series_df["round_label"] = series_df["round_num"].apply(lambda x: f"R{x}")
    series_df["value"] = series_df.groupby("party")["value"].ffill().fillna(0)

    if metric == "vote_share_pct":
        final_total = series_df[series_df["round_num"] == max_round]["value"].sum()
        series_df["value"] = series_df.apply(
            lambda r: (r["value"] / final_total * 100) if final_total > 0 else 0, axis=1
        )
    return series_df


series_df = compute_party_round_series(DB_PATH, state_code_filter, metric)

if series_df.empty:
    st.info("No data yet.")
else:
    max_round = int(series_df["round_num"].max())
    if max_round < 1:
        st.info("Waiting for counting data...")
    else:
        latest_round = series_df[series_df["round_num"] == max_round]
        top_parties = latest_round.nlargest(10, "value")["party"].tolist()

        series_df["party_group"] = series_df["party"].apply(lambda p: p if p in top_parties else "Others")
        plot_df = series_df.groupby(["party_group", "round_num", "round_label"])["value"].sum().reset_index()
        round_labels = [f"R{i}" for i in range(0, max_round + 1)]

        fig = go.Figure()
        for party in top_parties + ["Others"]:
            party_df = plot_df[plot_df["party_group"] == party].sort_values("round_num")
            if party_df.empty:
                continue
            fig.add_trace(go.Scatter(
                x=party_df["round_label"], y=party_df["value"],
                mode="lines+markers", name=short(party),
                line=dict(color=get_party_color(party), width=2),
                marker=dict(size=5),
                hovertemplate=f"<b>{short(party)}</b><br>Round: %{{x}}<br>{trend_mode}: %{{y:,.0f}}<extra></extra>",
            ))

        y_label = "Vote Share (%)" if metric == "vote_share_pct" else "Cumulative Votes"
        fig.update_layout(
            xaxis_title="Counting Round", yaxis_title=y_label, height=500,
            hovermode="x unified",
            xaxis=dict(categoryorder="array", categoryarray=round_labels),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig, width="stretch")

        st.caption(
            "Each line shows a party's cumulative share of the final total across counting rounds. "
            "Lines converge to the final declared share. "
            "Flat segments mean no new votes in that round."
        )


# --- SECTION 3: CONSTITUENCY DRILL-DOWN ---
st.divider()
st.subheader("Constituency Drill-Down")

state_for_ac_options = [s["name"] for s in STATES]
default_drill_state = params.get("drill_state", state_for_ac_options[0])
if default_drill_state not in state_for_ac_options:
    default_drill_state = state_for_ac_options[0]

col_state, col_ac = st.columns(2)
with col_state:
    state_for_ac = st.selectbox("State", state_for_ac_options,
                                 index=state_for_ac_options.index(default_drill_state),
                                 key="drill_state_select")
if state_for_ac and state_for_ac != params.get("drill_state"):
    st.query_params["drill_state"] = state_for_ac
    st.rerun()

selected_state_code = state_code_for(state_for_ac)
ac_statuses = get_all_constituency_statuses(DB_PATH)
ac_list = ac_statuses[ac_statuses["state_code"] == selected_state_code]

ac_options = []
for _, row in ac_list.iterrows():
    name = row.get("ac_name") or f"AC-{row['ac_no']}"
    ac_options.append(f"{row['ac_no']}. {name}")

if not ac_options:
    st.info("No constituency data available.")
else:
    default_ac = params.get("drill_ac", ac_options[0])
    if default_ac not in ac_options:
        default_ac = ac_options[0]

    with col_ac:
        selected_ac = st.selectbox("Constituency", ac_options,
                                    index=ac_options.index(default_ac),
                                    key="drill_ac_select")
    if selected_ac and selected_ac != params.get("drill_ac"):
        st.query_params["drill_ac"] = selected_ac
        st.rerun()
    ac_no = int(selected_ac.split(".")[0])

    ac_row = ac_list[ac_list["ac_no"] == ac_no].iloc[0]
    status = ac_row["status"]
    current_round = int(ac_row.get("current_round", 0) or 0)
    total_rounds = int(ac_row.get("total_rounds", 0) or 0)

    if status == "DONE":
        st.success(f"✅ Counting complete — Round {current_round}/{total_rounds}")
    elif status == "LIVE":
        pct_est = (current_round / total_rounds * 100) if total_rounds > 0 else 0
        st.warning(f"🔴 Live — Round {current_round}/{total_rounds} (approx {pct_est:.0f}% of EVM votes counted)")
    elif status == "ERROR":
        st.error("⚠️ Error fetching data")
    else:
        st.info("⏳ No data yet")

    rounds_df = get_constituency_rounds(DB_PATH, selected_state_code, ac_no)

    if rounds_df.empty:
        if status == "PENDING":
            st.info("No data yet. It will appear after the next update cycle.")
    else:
        latest_time = rounds_df["scraped_at"].max()
        latest = rounds_df[rounds_df["scraped_at"] == latest_time].copy()
        latest = latest.sort_values("votes", ascending=True)

        # Leading candidate callout
        if len(latest) >= 2:
            df_sorted = latest.sort_values("votes", ascending=False)
            leader = df_sorted.iloc[0]
            runner = df_sorted.iloc[1]
            margin = int(leader["votes"]) - int(runner["votes"])
            leader_name = candidate_display_name(leader["candidate"])
            runner_name = candidate_display_name(runner["candidate"])
            msg = f"**{leader_name}** ({short(leader['party'])}) leading by **{margin:,} votes** over {runner_name} ({short(runner['party'])})"
            if current_round > 0 and total_rounds > 0:
                msg += f" — approx {current_round / total_rounds * 100:.0f}% counted"
            if margin < 100:
                st.error(f"🔥 Extremely close! {msg}")
            elif margin < 500:
                st.warning(f"⚠️ Too close to call. {msg}")
            else:
                st.success(f"🏆 {msg}")

        # Bar chart with party colours
        latest["display_name"] = latest["candidate"].apply(candidate_display_name)
        latest["short_party"] = latest["party"].apply(short)
        latest["hover"] = latest.apply(lambda r: f"{r['candidate']} ({r['party']})", axis=1)
        colors = ["#374151" if row["party"] == "NOTA" else get_party_color(row["party"]) for _, row in latest.iterrows()]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=latest["display_name"], x=latest["votes"], orientation="h",
            marker_color=colors,
            text=latest.apply(lambda r: f"{r['short_party']} ({r['votes']:,})", axis=1),
            textposition="auto", hovertext=latest["hover"],
        ))
        round_no = latest["round_no"].iloc[0] if "round_no" in latest.columns else current_round
        fig.update_layout(
            title=f"Round {round_no} Snapshot",
            height=max(300, len(latest) * 35),
            xaxis_title="Votes", yaxis_title="",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig, width="stretch")

        # Round-by-round history
        if len(rounds_df["scraped_at"].unique()) > 1:
            st.subheader("Vote Trajectory Over Time")
            rounds_df["time_ist"] = pd.to_datetime(rounds_df["scraped_at"]).dt.tz_convert(IST)
            top_cands = rounds_df[rounds_df["scraped_at"] == latest_time].nlargest(4, "votes")["candidate"].tolist()
            nota_candidates = rounds_df[rounds_df["party"] == "NOTA"]["candidate"].unique().tolist()
            show_cands = set(top_cands + nota_candidates)

            fig_line = go.Figure()
            for cand in show_cands:
                cand_df = rounds_df[rounds_df["candidate"] == cand]
                if cand_df.empty:
                    continue
                party = cand_df["party"].iloc[0]
                color = "#374151" if party == "NOTA" else get_party_color(party)
                fig_line.add_trace(go.Scatter(
                    x=cand_df["time_ist"], y=cand_df["votes"],
                    mode="lines+markers",
                    name=f"{candidate_display_name(cand)} ({short(party)})",
                    line=dict(color=color, width=2),
                ))
            fig_line.update_layout(
                xaxis_title="Time (IST)", yaxis_title="Cumulative Votes",
                height=400, hovermode="x unified",
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig_line, width="stretch")


# ---------------------------------------------------------------------------
# Auto-refresh via meta tag
# ---------------------------------------------------------------------------

# Use default refresh if settings not opened
refresh_interval = 120  # default
st.markdown(
    f"<meta http-equiv='refresh' content='{refresh_interval}'>",
    unsafe_allow_html=True,
)
