#!/usr/bin/env python3
"""
ECI Live Election Dashboard.

Streamlit + Plotly dashboard showing real-time election results.
Run with: streamlit run dashboard.py --server.port 8501
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db_utils import (
    get_all_constituency_statuses,
    get_constituency_rounds,
    get_last_scrape_time,
    get_leading_seats,
    get_party_seat_tally,
    get_party_totals_over_time,
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
    short,
    state_code_for,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = os.path.join(os.path.dirname(__file__), "live_results.db")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_ist(utc_iso: str, fmt: str = "%H:%M IST") -> str:
    """Convert UTC ISO timestamp to IST display string."""
    if not utc_iso:
        return "N/A"
    try:
        dt = datetime.fromisoformat(utc_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime(fmt)
    except Exception:
        return utc_iso


def get_party_color(party: str) -> str:
    return PARTY_COLORS.get(party, PARTY_COLORS.get("Others", "#ADB5BD"))


def minutes_since_last_scrape() -> int:
    """Returns minutes since last successful scrape."""
    ts = get_last_scrape_time(DB_PATH)
    if not ts:
        return 999
    try:
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - last).total_seconds() / 60)
    except Exception:
        return 999


def collapse_others(df: pd.DataFrame, party_col: str, value_col: str, top_n: int = 10) -> pd.DataFrame:
    """Keep top N parties; collapse rest into 'Others (N parties)'."""
    df_sorted = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    top = df_sorted.head(top_n).copy()
    rest = df_sorted.iloc[top_n:]
    if not rest.empty:
        others_total = rest[value_col].sum()
        others_count = len(rest)
        others_row = pd.DataFrame([{party_col: f"Others ({others_count} parties)", value_col: others_total}])
        top = pd.concat([top, others_row], ignore_index=True)
    return top


def candidate_display_name(name: str) -> str:
    """Title-case candidate name, preserving NOTA."""
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

if not os.path.exists(DB_PATH):
    st.error("Database not found. Start the data collector first: `./scheduler.sh`")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🗳️ ECI Live Tracker")
st.sidebar.caption("General Assembly Elections — May 2026")

# State filter — persisted across refreshes
state_options = [s["name"] for s in STATES] + ["All States"]
if "sidebar_state" not in st.session_state:
    st.session_state["sidebar_state"] = "All States"

selected_state = st.sidebar.selectbox(
    "Filter by State",
    state_options,
    index=state_options.index(st.session_state["sidebar_state"]),
    key="sidebar_state_select",
)
st.session_state["sidebar_state"] = selected_state

state_code_filter = None
if selected_state != "All States":
    state_code_filter = state_code_for(selected_state)

# Refresh
last_update = get_last_scrape_time(DB_PATH)
if last_update:
    st.sidebar.metric("Last Update", fmt_ist(last_update, "%H:%M:%S IST"))

refresh_interval = st.sidebar.slider("Auto-refresh (seconds)", 30, 300, 120)

if st.sidebar.button("🔄 Refresh Now"):
    st.rerun()

# Majority info in sidebar
if selected_state != "All States":
    maj = MAJORITIES.get(state_code_filter, 0)
    st.sidebar.info(f"Majority in {selected_state}: **{maj}** seats")

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs(
    ["📊 Overview", "📈 Party Trends", "🔍 Constituency", "⚙️ System Monitor"]
)


# ===========================================================================
# TAB 1: OVERVIEW
# ===========================================================================
with tab1:
    st.header("Election Overview")

    # Summary metrics — filtered by selected state
    status_summary = get_status_summary(DB_PATH, state_code_filter)
    total_acs = sum(status_summary.values())
    reporting = status_summary.get("LIVE", 0) + status_summary.get("DONE", 0)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total ACs", total_acs or 824)
    with col2:
        st.metric("Reporting", f"{reporting} / {total_acs or 824}")
    with col3:
        st.metric(
            "All Rounds Counted",
            status_summary.get("DONE", 0),
            help="Constituencies where all EVM counting rounds are done. "
                 "Official declaration by ECI may follow shortly after.",
        )
    with col4:
        st.metric("Errors", status_summary.get("ERROR", 0))

    # Progress bar
    reporting_pct = reporting / max(total_acs, 1)
    st.progress(
        reporting_pct,
        text=f"Reporting: {reporting_pct:.0%} ACs",
    )

    st.divider()

    # --- Combined seat tally ---
    st.subheader("Seat Tally (Leading/Won)")

    seat_tally = get_party_seat_tally(DB_PATH, state_code_filter)
    if seat_tally.empty:
        st.info("No data yet.")
    else:
        # Collapse minor parties
        seat_tally_display = collapse_others(seat_tally, "party", "seats_leading", top_n=10)
        # Add short names
        seat_tally_display["short"] = seat_tally_display["party"].apply(short)
        colors = [get_party_color(p) for p in seat_tally_display["party"]]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=seat_tally_display["short"],
            x=seat_tally_display["seats_leading"],
            orientation="h",
            marker_color=colors,
            text=seat_tally_display["seats_leading"],
            textposition="auto",
            hovertext=seat_tally_display["party"],
        ))

        # Majority line (for filtered state or combined)
        if state_code_filter and state_code_filter in MAJORITIES:
            maj = MAJORITIES[state_code_filter]
            fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
            fig.add_annotation(x=maj, y=1.05, text=f"Majority ({maj})",
                               showarrow=False, font=dict(color="red", size=11))

        fig.update_layout(
            height=max(300, len(seat_tally_display) * 35),
            xaxis_title="Seats Leading",
            yaxis_title="",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig, width="stretch")

        # Show all parties expander
        with st.expander("Show all parties"):
            st.dataframe(
                seat_tally.rename(columns={"party": "Party", "seats_leading": "Seats"})
                .sort_values("Seats", ascending=False)
                .reset_index(drop=True),
                width="stretch",
                height=400,
            )

    # --- Per-state breakdowns ---
    if selected_state == "All States":
        st.divider()
        st.subheader("State-by-State Breakdown")

        for state in STATES:
            sc = state["code"]
            maj = MAJORITIES.get(sc, 0)
            with st.expander(f"{state['name']}  (majority: {maj} seats)", expanded=False):
                state_tally = get_party_seat_tally(DB_PATH, sc)
                if state_tally.empty:
                    st.info("No data yet.")
                    continue
                state_tally_display = collapse_others(state_tally, "party", "seats_leading", top_n=8)
                state_tally_display["short"] = state_tally_display["party"].apply(short)
                colors = [get_party_color(p) for p in state_tally_display["party"]]

                fig = go.Figure()
                fig.add_trace(go.Bar(
                    y=state_tally_display["short"],
                    x=state_tally_display["seats_leading"],
                    orientation="h",
                    marker_color=colors,
                    text=state_tally_display["seats_leading"],
                    textposition="auto",
                    hovertext=state_tally_display["party"],
                ))
                fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
                fig.add_annotation(x=maj, y=1.05, text=f"Majority ({maj})",
                                   showarrow=False, font=dict(color="red", size=11))
                fig.update_layout(
                    height=max(250, len(state_tally_display) * 30),
                    yaxis=dict(autorange="reversed"),
                    margin=dict(l=0, r=0, t=20, b=0),
                    showlegend=False,
                )
                st.plotly_chart(fig, width="stretch")

    # --- State-wise status table ---
    st.divider()
    st.subheader("Counting Status by State")
    state_summary = get_state_status_summary(DB_PATH)
    if state_summary:
        df_states = pd.DataFrame(state_summary)
        pivot = df_states.pivot_table(
            index="state_name", columns="status", values="cnt", fill_value=0
        ).astype(int)
        # Add total
        pivot["Total"] = pivot.sum(axis=1)
        # Add progress
        for col in ["DONE", "LIVE", "PENDING", "ERROR"]:
            if col not in pivot.columns:
                pivot[col] = 0
        pivot["% Reporting"] = ((pivot["DONE"] + pivot["LIVE"]) / pivot["Total"] * 100).round(0).astype(int)
        # Reorder
        pivot = pivot[["Total", "LIVE", "DONE", "PENDING", "ERROR", "% Reporting"]]
        pivot.index.name = "State"
        st.dataframe(pivot, width="stretch")


# ===========================================================================
# TAB 2: PARTY TRENDS
# ===========================================================================
with tab2:
    st.header("Party Vote Trends")

    trend_mode = st.radio(
        "Display mode",
        ["Cumulative Votes", "Vote Share %"],
        horizontal=True,
    )

    trends_df = get_party_totals_over_time(DB_PATH, state_code_filter)

    if trends_df.empty:
        st.info("No trend data yet.")
    else:
        trends_df["scraped_at"] = pd.to_datetime(trends_df["scraped_at"])
        trends_df["time_ist"] = trends_df["scraped_at"].dt.tz_convert(IST)

        n_cycles = trends_df["scraped_at"].nunique()

        # Get top parties by latest totals
        latest_time = trends_df["scraped_at"].max()
        latest_totals = (
            trends_df[trends_df["scraped_at"] == latest_time]
            .nlargest(10, "total_votes")
        )
        top_parties = latest_totals["party"].tolist()

        trends_df["party_group"] = trends_df["party"].apply(
            lambda p: p if p in top_parties else "Others"
        )

        if trend_mode == "Vote Share %":
            totals_per_time = trends_df.groupby("scraped_at")["total_votes"].sum()
            trends_df = trends_df.merge(totals_per_time, on="scraped_at", suffixes=("", "_total"))
            trends_df["vote_pct"] = trends_df["total_votes"] / trends_df["total_votes_total"] * 100
            y_col = "vote_pct"
            y_label = "Vote Share (% of counted votes)"
        else:
            y_col = "total_votes"
            y_label = "Cumulative Votes"

        if n_cycles == 1:
            st.caption(
                f"Snapshot from {trends_df['time_ist'].iloc[0].strftime('%H:%M IST')} — "
                "trend lines will appear after the 2nd update cycle (~15 min)"
            )
            # Show bar chart differentiated from overview (vote share)
            bar_df = (
                trends_df.groupby("party_group")[y_col]
                .sum()
                .reset_index()
                .sort_values(y_col, ascending=True)
            )
            bar_df["short"] = bar_df["party_group"].apply(short)
            colors = [get_party_color(p) for p in bar_df["party_group"]]

            fig = go.Figure(go.Bar(
                y=bar_df["short"],
                x=bar_df[y_col],
                orientation="h",
                marker_color=colors,
                text=bar_df[y_col].apply(lambda x: f"{x:,.0f}"),
                textposition="auto",
                hovertext=bar_df["party_group"],
            ))
            fig.update_layout(
                xaxis_title=y_label,
                yaxis_title="",
                height=max(400, len(bar_df) * 32),
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig, width="stretch")

            st.caption("Vote share = % of total votes counted so far across all reporting constituencies")
        else:
            # Multiple cycles — trend line chart
            plot_df = (
                trends_df.groupby(["time_ist", "party_group"])[y_col]
                .sum()
                .reset_index()
            )

            fig = go.Figure()
            for party in top_parties + ["Others"]:
                party_df = plot_df[plot_df["party_group"] == party]
                if party_df.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=party_df["time_ist"],
                    y=party_df[y_col],
                    mode="lines+markers",
                    name=short(party),
                    line=dict(color=get_party_color(party), width=2),
                    marker=dict(size=4),
                    hovertext=party,
                ))

            fig.update_layout(
                xaxis_title="Time (IST)",
                yaxis_title=y_label,
                height=500,
                hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch")

            st.caption("Vote share = % of total votes counted so far across all reporting constituencies")


# ===========================================================================
# TAB 3: CONSTITUENCY DRILL-DOWN
# ===========================================================================
with tab3:
    st.header("Constituency Drill-Down")

    # --- Session state for persistence across refreshes ---
    if "drill_state" not in st.session_state:
        st.session_state["drill_state"] = STATES[0]["name"]
    if "drill_ac" not in st.session_state:
        st.session_state["drill_ac"] = 0

    state_for_ac = st.selectbox(
        "Select State",
        [s["name"] for s in STATES],
        index=[s["name"] for s in STATES].index(st.session_state["drill_state"]),
        key="drill_state_select",
    )
    st.session_state["drill_state"] = state_for_ac

    selected_state_code = state_code_for(state_for_ac)

    # Constituency dropdown
    ac_statuses = get_all_constituency_statuses(DB_PATH)
    ac_list = ac_statuses[ac_statuses["state_code"] == selected_state_code]

    ac_options = []
    for _, row in ac_list.iterrows():
        name = row.get("ac_name") or f"AC-{row['ac_no']}"
        ac_options.append(f"{row['ac_no']}. {name}")

    if not ac_options:
        st.info("No constituency data available.")
    else:
        # Persist selection
        ac_index = min(st.session_state["drill_ac"], len(ac_options) - 1)
        selected_ac = st.selectbox(
            "Select Constituency",
            ac_options,
            index=ac_index,
            key="drill_ac_select",
        )
        st.session_state["drill_ac"] = ac_options.index(selected_ac)
        ac_no = int(selected_ac.split(".")[0])

        # Get status
        ac_row = ac_list[ac_list["ac_no"] == ac_no].iloc[0]
        status = ac_row["status"]
        current_round = int(ac_row.get("current_round", 0) or 0)
        total_rounds = int(ac_row.get("total_rounds", 0) or 0)

        # Status badge
        if status == "DONE":
            st.success(f"✅ Counting complete — Round {current_round}/{total_rounds}")
        elif status == "LIVE":
            pct_est = (current_round / total_rounds * 100) if total_rounds > 0 else 0
            st.warning(
                f"🔴 Live — Round {current_round}/{total_rounds} "
                f"(approx {pct_est:.0f}% of EVM votes counted)"
            )
        elif status == "ERROR":
            st.error("⚠️ Error scraping this constituency")
        else:
            st.info("⏳ No data yet — counting hasn't started or data available yet")

        # Get round data
        rounds_df = get_constituency_rounds(DB_PATH, selected_state_code, ac_no)

        if rounds_df.empty:
            if status == "PENDING":
                st.info("No data yet. It will appear after the next update cycle.")
        else:
            # Latest snapshot
            latest_time = rounds_df["scraped_at"].max()
            latest = rounds_df[rounds_df["scraped_at"] == latest_time].copy()
            latest = latest.sort_values("votes", ascending=True)

            # --- Leading candidate callout ---
            if len(latest) >= 2:
                df_sorted = latest.sort_values("votes", ascending=False)
                leader = df_sorted.iloc[0]
                runner = df_sorted.iloc[1]
                margin = int(leader["votes"]) - int(runner["votes"])

                leader_name = candidate_display_name(leader["candidate"])
                runner_name = candidate_display_name(runner["candidate"])

                msg = (
                    f"**{leader_name}** ({short(leader['party'])}) leading by "
                    f"**{margin:,} votes** over {runner_name} ({short(runner['party'])})"
                )
                if current_round > 0 and total_rounds > 0:
                    msg += f" — approx {current_round / total_rounds * 100:.0f}% counted"

                if margin < 100:
                    st.error(f"🔥 Extremely close! {msg}")
                elif margin < 500:
                    st.warning(f"⚠️ Too close to call. {msg}")
                else:
                    st.success(f"🏆 {msg}")

            # --- Bar chart with party colours ---
            latest["display_name"] = latest["candidate"].apply(candidate_display_name)
            latest["short_party"] = latest["party"].apply(short)
            latest["hover"] = latest.apply(
                lambda r: f"{r['candidate']} ({r['party']})", axis=1
            )
            colors = []
            for _, row in latest.iterrows():
                if row["party"] == "NOTA":
                    colors.append("#374151")
                else:
                    colors.append(get_party_color(row["party"]))

            fig = go.Figure()
            fig.add_trace(go.Bar(
                y=latest["display_name"],
                x=latest["votes"],
                orientation="h",
                marker_color=colors,
                text=latest.apply(lambda r: f"{r['short_party']} ({r['votes']:,})", axis=1),
                textposition="auto",
                hovertext=latest["hover"],
            ))
            round_no = latest["round_no"].iloc[0] if "round_no" in latest.columns else current_round
            fig.update_layout(
                title=f"Round {round_no} Snapshot",
                height=max(300, len(latest) * 35),
                xaxis_title="Votes",
                yaxis_title="",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch")

            # --- Round-by-round history chart ---
            if len(rounds_df["scraped_at"].unique()) > 1:
                st.subheader("Vote Trajectory Over Time")
                rounds_df["time_ist"] = pd.to_datetime(
                    rounds_df["scraped_at"]
                ).dt.tz_convert(IST)

                # Top 4 candidates + NOTA by latest total
                top_cands = (
                    rounds_df[rounds_df["scraped_at"] == latest_time]
                    .nlargest(4, "votes")["candidate"]
                    .tolist()
                )
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
                        x=cand_df["time_ist"],
                        y=cand_df["votes"],
                        mode="lines+markers",
                        name=f"{candidate_display_name(cand)} ({short(party)})",
                        line=dict(color=color, width=2),
                    ))
                fig_line.update_layout(
                    xaxis_title="Time (IST)",
                    yaxis_title="Cumulative Votes",
                    height=400,
                    hovermode="x unified",
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_line, width="stretch")


# ===========================================================================
# TAB 4: SYSTEM MONITOR
# ===========================================================================
with tab4:
    st.header("System Monitor")

    # --- Scrape cycle duration chart ---
    st.subheader("Update Cycles")
    cycles_df = get_scrape_cycles(DB_PATH)
    if cycles_df.empty:
        st.info("No update cycles recorded yet.")
    else:
        # Duration trend chart
        if len(cycles_df) > 1:
            cycles_df["cycle_num"] = range(1, len(cycles_df) + 1)
            fig_cycles = go.Figure()
            fig_cycles.add_trace(go.Bar(
                x=cycles_df["cycle_num"],
                y=cycles_df["cycle_duration_sec"],
                marker_color="#3B82F6",
                text=cycles_df["cycle_duration_sec"].apply(lambda x: f"{x:.0f}s"),
                textposition="auto",
            ))
            fig_cycles.update_layout(
                xaxis_title="Cycle",
                yaxis_title="Duration (seconds)",
                height=250,
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig_cycles, width="stretch")

        # Cycles table (most recent first)
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
        st.dataframe(display_cycles[display_cols].head(10), width="stretch")

    st.divider()

    # --- Error URLs ---
    error_acs = ac_statuses[ac_statuses["status"] == "ERROR"] if not ac_statuses.empty else pd.DataFrame()
    if not error_acs.empty:
        st.subheader(f"⚠️ Failed Updates ({len(error_acs)})")
        from states_may2026 import get_url
        for _, row in error_acs.iterrows():
            ac_name = row.get("ac_name") or f"AC-{row['ac_no']}"
            url = get_url(row["state_code"], row["ac_no"])
            st.markdown(
                f"- **{ac_name}** "
                f"({row['state_name']}) — {row['error_count']} errors — "
                f"[View page]({url})"
            )
        st.divider()

    # --- Constituency status table with search ---
    st.subheader("All Constituencies")

    if not ac_statuses.empty:
        # Search + filter
        search_col, status_col = st.columns([2, 1])
        with search_col:
            search = st.text_input("Search constituency", "", placeholder="e.g. Madavaram, Kochi")
        with status_col:
            status_filter = st.selectbox("Status", ["All", "LIVE", "DONE", "PENDING", "ERROR"])

        filtered = ac_statuses.copy()
        if search:
            filtered = filtered[
                filtered["ac_name"].str.upper().str.contains(search.upper(), na=False)
                | filtered["state_name"].str.upper().str.contains(search.upper(), na=False)
            ]
        if status_filter != "All":
            filtered = filtered[filtered["status"] == status_filter]

        # Format timestamps to IST
        if "last_scraped" in filtered.columns:
            filtered["last_scraped_ist"] = filtered["last_scraped"].apply(
                lambda x: fmt_ist(x, "%d %b %H:%M") if pd.notna(x) and x else ""
            )

        display_cols = [c for c in [
            "state_name", "ac_no", "ac_name", "status",
            "current_round", "total_rounds", "last_scraped_ist",
        ] if c in filtered.columns]

        # Style with status colours
        def color_status(val):
            bg = STATUS_COLORS.get(val, "#6B7280")
            return f"background-color: {bg}; color: white; font-weight: bold"

        styled = filtered[display_cols].style.map(color_status, subset=["status"])
        st.dataframe(styled, width="stretch", height=400)

    # --- Quick stats ---
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
                    f"🟢 {done} done  "
                    f"🟡 {live} live  "
                    f"⚪ {pend} pending  "
                    f"🔴 {err} error  "
                    f"({done + live}/{total} reporting)"
                )


# ---------------------------------------------------------------------------
# Auto-refresh via browser meta tag
# ---------------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.caption(
    "Built by Mahesh Shantaram | "
    "[GitHub](https://github.com/thecont1/india-votes-data)"
)

st.markdown(
    f"<meta http-equiv='refresh' content='{refresh_interval}'>",
    unsafe_allow_html=True,
)
