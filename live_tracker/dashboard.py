#!/usr/bin/env python3
"""
ECI Live Election Dashboard.

Streamlit + Plotly dashboard showing real-time election results.
Run with: streamlit run dashboard.py --server.port 8501
"""

import os
import time
from datetime import datetime

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
from states_may2026 import PARTY_COLORS, STATES

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ECI Live Election Tracker — May 2026",
    page_icon="🗳️",
    layout="wide",
)

DB_PATH = os.path.join(os.path.dirname(__file__), "live_results.db")

if not os.path.exists(DB_PATH):
    st.error(
        "Database not found. Start the scraper first:\n"
        "```bash\n./scheduler.sh\n```"
    )
    st.stop()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_party_color(party: str) -> str:
    """Get colour for a party, falling back to grey for unknowns."""
    return PARTY_COLORS.get(party, PARTY_COLORS.get("Others", "#ADB5BD"))


def format_ist(utc_iso: str) -> str:
    """Convert UTC ISO timestamp to IST display string."""
    if not utc_iso:
        return "N/A"
    try:
        dt = pd.to_datetime(utc_iso)
        # IST = UTC + 5:30
        ist = dt + pd.Timedelta(hours=5, minutes=30)
        return ist.strftime("%H:%M:%S IST")
    except Exception:
        return utc_iso


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🗳️ ECI Live Tracker")
st.sidebar.caption("General Assembly Elections — May 2026")

# State filter
state_options = ["All States"] + [s["name"] for s in STATES]
selected_state = st.sidebar.selectbox("Filter by State", state_options)

state_code_filter = None
if selected_state != "All States":
    for s in STATES:
        if s["name"] == selected_state:
            state_code_filter = s["code"]
            break

# Auto-refresh
refresh_interval = st.sidebar.slider(
    "Auto-refresh (seconds)", 30, 300, 120
)
st.sidebar.caption(f"Page refreshes every {refresh_interval}s")

# Last update time
last_scrape = get_last_scrape_time(DB_PATH)
if last_scrape:
    st.sidebar.metric("Last Data", format_ist(last_scrape))


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

    # Summary metrics
    status_summary = get_status_summary(DB_PATH)
    col1, col2, col3, col4 = st.columns(4)

    total_acs = sum(status_summary.values())
    with col1:
        st.metric("Total ACs", total_acs or 824)
    with col2:
        reporting = total_acs - status_summary.get("PENDING", 0)
        st.metric("Reporting", reporting)
    with col3:
        st.metric("Complete", status_summary.get("DONE", 0))
    with col4:
        st.metric("Errors", status_summary.get("ERROR", 0))

    # Progress bar
    done_pct = status_summary.get("DONE", 0) / max(total_acs, 1)
    st.progress(done_pct, text=f"Counting progress: {done_pct:.0%}")

    st.divider()

    # Seat tally
    st.subheader("Seat Tally (Leading/Won)")

    seat_tally = get_party_seat_tally(DB_PATH, state_code_filter)
    if seat_tally.empty:
        st.info("No data yet. Scraping begins when counting starts at 8 AM IST.")
    else:
        # Build horizontal bar chart
        colors = [get_party_color(p) for p in seat_tally["party"]]
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                y=seat_tally["party"],
                x=seat_tally["seats_leading"],
                orientation="h",
                marker_color=colors,
                text=seat_tally["seats_leading"],
                textposition="auto",
            )
        )
        fig.update_layout(
            height=max(300, len(seat_tally) * 35),
            xaxis_title="Seats Leading",
            yaxis_title="",
            yaxis=dict(autorange="reversed"),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Per-state breakdown
    if selected_state == "All States":
        st.subheader("State-wise Breakdown")
        state_summary = get_state_status_summary(DB_PATH)
        if state_summary:
            df_states = pd.DataFrame(state_summary)
            pivot = df_states.pivot_table(
                index="state_name", columns="status", values="cnt", fill_value=0
            )
            st.dataframe(pivot, use_container_width=True)


# ===========================================================================
# TAB 2: PARTY TRENDS
# ===========================================================================
with tab2:
    st.header("Party Trends Over Time")

    trend_mode = st.radio(
        "Display mode",
        ["Cumulative Votes", "Vote Share %"],
        horizontal=True,
    )

    trends_df = get_party_totals_over_time(DB_PATH, state_code_filter)

    if trends_df.empty:
        st.info("No trend data yet.")
    else:
        # Parse timestamps
        trends_df["scraped_at"] = pd.to_datetime(trends_df["scraped_at"])
        # Convert to IST
        trends_df["time_ist"] = trends_df["scraped_at"] + pd.Timedelta(
            hours=5, minutes=30
        )

        # Get top N parties by latest total
        latest_time = trends_df["scraped_at"].max()
        latest_totals = (
            trends_df[trends_df["scraped_at"] == latest_time]
            .nlargest(8, "total_votes")
        )
        top_parties = latest_totals["party"].tolist()

        # Filter to top parties + "Others"
        trends_df["party_group"] = trends_df["party"].apply(
            lambda p: p if p in top_parties else "Others"
        )
        if trend_mode == "Vote Share %":
            # Calculate percentage per timestamp
            totals_per_time = trends_df.groupby("scraped_at")["total_votes"].sum()
            trends_df = trends_df.merge(
                totals_per_time, on="scraped_at", suffixes=("", "_total")
            )
            trends_df["vote_pct"] = (
                trends_df["total_votes"] / trends_df["total_votes_total"] * 100
            )
            y_col = "vote_pct"
            y_label = "Vote Share (%)"
        else:
            y_col = "total_votes"
            y_label = "Cumulative Votes"

        # Aggregate Others
        plot_df = (
            trends_df.groupby(["time_ist", "party_group"])[y_col]
            .sum()
            .reset_index()
        )

        # Build line chart
        fig = go.Figure()
        for party in top_parties + ["Others"]:
            party_df = plot_df[plot_df["party_group"] == party]
            if party_df.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=party_df["time_ist"],
                    y=party_df[y_col],
                    mode="lines+markers",
                    name=party,
                    line=dict(color=get_party_color(party), width=2),
                    marker=dict(size=4),
                )
            )

        fig.update_layout(
            xaxis_title="Time (IST)",
            yaxis_title=y_label,
            height=500,
            hovermode="x unified",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# TAB 3: CONSTITUENCY DRILL-DOWN
# ===========================================================================
with tab3:
    st.header("Constituency Drill-Down")

    # State dropdown
    state_for_ac = st.selectbox(
        "Select State",
        [s["name"] for s in STATES],
        key="ac_state",
    )
    selected_state_code = None
    for s in STATES:
        if s["name"] == state_for_ac:
            selected_state_code = s["code"]
            break

    # Constituency dropdown
    ac_statuses = get_all_constituency_statuses(DB_PATH)
    if selected_state_code:
        ac_list = ac_statuses[ac_statuses["state_code"] == selected_state_code]
    else:
        ac_list = ac_statuses

    ac_options = []
    for _, row in ac_list.iterrows():
        name = row.get("ac_name") or f"AC-{row['ac_no']}"
        ac_options.append(f"{row['ac_no']}. {name}")
    if not ac_options:
        st.info("No constituency data available.")
    else:
        selected_ac = st.selectbox("Select Constituency", ac_options, key="ac_select")
        ac_no = int(selected_ac.split(".")[0])

        # Get status
        ac_row = ac_list[ac_list["ac_no"] == ac_no].iloc[0]
        status = ac_row["status"]
        current_round = ac_row.get("current_round", 0)
        total_rounds = ac_row.get("total_rounds", 0)

        # Status badge
        if status == "DONE":
            st.success(f"✅ Counting complete — Round {current_round}/{total_rounds}")
        elif status == "LIVE":
            st.warning(f"🔴 Live — Round {current_round}/{total_rounds}")
        elif status == "ERROR":
            st.error("⚠️ Error scraping this constituency")
        else:
            st.info("⏳ Pending — counting not yet started")

        # Get round data
        rounds_df = get_constituency_rounds(DB_PATH, selected_state_code, ac_no)

        if rounds_df.empty:
            st.info("No round data scraped yet for this constituency.")
        else:
            # Latest snapshot — bar chart
            latest_time = rounds_df["scraped_at"].max()
            latest = rounds_df[rounds_df["scraped_at"] == latest_time].copy()
            latest = latest.sort_values("votes", ascending=True)

            colors = [get_party_color(p) for p in latest["party"]]
            fig_bar = go.Figure()
            fig_bar.add_trace(
                go.Bar(
                    y=latest["candidate"],
                    x=latest["votes"],
                    orientation="h",
                    marker_color=colors,
                    text=latest.apply(
                        lambda r: f"{r['party']} ({r['votes']:,})", axis=1
                    ),
                    textposition="auto",
                )
            )
            fig_bar.update_layout(
                title=f"Latest Snapshot — Round {latest['round_no'].iloc[0]}",
                height=max(300, len(latest) * 35),
                xaxis_title="Votes",
                yaxis_title="",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            # Scrape timeline (votes over time)
            if len(rounds_df["scraped_at"].unique()) > 1:
                st.subheader("Vote Trajectory Over Scrapes")
                rounds_df["time_ist"] = pd.to_datetime(
                    rounds_df["scraped_at"]
                ) + pd.Timedelta(hours=5, minutes=30)

                fig_line = go.Figure()
                for candidate in rounds_df["candidate"].unique():
                    cand_df = rounds_df[rounds_df["candidate"] == candidate]
                    party = cand_df["party"].iloc[0]
                    fig_line.add_trace(
                        go.Scatter(
                            x=cand_df["time_ist"],
                            y=cand_df["votes"],
                            mode="lines+markers",
                            name=f"{candidate} ({party})",
                            line=dict(color=get_party_color(party), width=2),
                        )
                    )
                fig_line.update_layout(
                    xaxis_title="Time (IST)",
                    yaxis_title="Cumulative Votes",
                    height=400,
                    hovermode="x unified",
                    margin=dict(l=0, r=0, t=30, b=0),
                )
                st.plotly_chart(fig_line, use_container_width=True)


# ===========================================================================
# TAB 4: SYSTEM MONITOR
# ===========================================================================
with tab4:
    st.header("System Monitor")

    # Scrape cycle log
    st.subheader("Scrape Cycles")
    cycles_df = get_scrape_cycles(DB_PATH)
    if cycles_df.empty:
        st.info("No scrape cycles recorded yet.")
    else:
        # Format for display
        display_cycles = cycles_df.copy()
        for col in ["started_at", "finished_at"]:
            if col in display_cycles.columns:
                display_cycles[col] = display_cycles[col].apply(format_ist)
        display_cols = [
            c for c in [
                "started_at", "finished_at", "pages_attempted",
                "pages_success", "pages_skipped", "pages_error",
                "cycle_duration_sec",
            ]
            if c in display_cycles.columns
        ]
        st.dataframe(
            display_cycles[display_cols].head(20),
            use_container_width=True,
        )

    st.divider()

    # Constituency status table
    st.subheader("All Constituencies")
    all_statuses = get_all_constituency_statuses(DB_PATH)
    if not all_statuses.empty:
        # Colour-coded status
        def color_status(val):
            colors = {
                "PENDING": "background-color: #FEF3C7",
                "LIVE": "background-color: #FEE2E2",
                "DONE": "background-color: #D1FAE5",
                "ERROR": "background-color: #FECACA",
            }
            return colors.get(val, "")

        styled = all_statuses.style.applymap(
            color_status, subset=["status"]
        )
        st.dataframe(styled, use_container_width=True, height=400)

    # Stats summary
    st.subheader("Quick Stats")
    summary = get_state_status_summary(DB_PATH)
    if summary:
        df_sum = pd.DataFrame(summary)
        for state in STATES:
            state_data = df_sum[df_sum["state_name"] == state["name"]]
            if not state_data.empty:
                counts = dict(
                    zip(state_data["status"], state_data["cnt"])
                )
                total = sum(counts.values())
                done = counts.get("DONE", 0)
                live = counts.get("LIVE", 0)
                st.write(
                    f"**{state['name']}**: {done}/{total} done, "
                    f"{live} live, "
                    f"{counts.get('PENDING', 0)} pending"
                )


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.caption(
    "Built by Mahesh Shantaram | "
    "[GitHub](https://github.com/thecont1/india-votes-data)"
)

# Auto-refresh using time.sleep + st.rerun
time.sleep(refresh_interval)
st.rerun()
