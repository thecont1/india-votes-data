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
    get_party_seat_tally_won_leading,
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


def minutes_since_last_update() -> int:
    """Returns minutes since last successful data update."""
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


def compute_party_round_series(
    db_path: str, state_code: str = None, metric: str = "cumulative_votes"
) -> pd.DataFrame:
    """
    Transform raw round snapshots into a round-based series for plotting.

    For each constituency, uses the ECI round_no as the progression axis.
    For each round r, uses the latest snapshot where round_no <= r.
    Aggregates across all constituencies by party.

    Returns DataFrame with columns: party, round_num, round_label, value
    """
    conn = _connect(db_path)
    try:
        where = "WHERE state_code = ?" if state_code else ""
        params = (state_code,) if state_code else ()
        query = f"""
            SELECT state_code, ac_no, round_no, party, votes, scraped_at
            FROM rounds
            {where}
            ORDER BY state_code, ac_no, scraped_at
        """
        raw = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()

    if raw.empty:
        return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])

    # Convert votes to numeric
    raw["votes"] = pd.to_numeric(raw["votes"], errors="coerce").fillna(0).astype(int)
    raw["scraped_at"] = pd.to_datetime(raw["scraped_at"])

    # Get max round across all constituencies
    max_round = int(raw["round_no"].max())

    # For each constituency, get the latest snapshot per round
    # First, rank snapshots within each (state_code, ac_no) by scraped_at
    raw = raw.sort_values(["state_code", "ac_no", "scraped_at"])
    raw["snap_rank"] = raw.groupby(["state_code", "ac_no"]).cumcount() + 1

    # For each round r, find the latest snapshot where round_no <= r
    # We'll build a mapping: for each (state_code, ac_no, round_no), keep only the latest scraped_at
    latest_per_round = (
        raw.groupby(["state_code", "ac_no", "round_no"])["scraped_at"]
        .max()
        .reset_index()
    )
    # Merge back to get only the latest snapshot per (ac, round)
    raw_latest = raw.merge(
        latest_per_round,
        on=["state_code", "ac_no", "round_no", "scraped_at"],
        how="inner",
    )

    # Now for each round r, for each constituency, use the latest snapshot where round_no <= r
    # This means forward-filling: if an AC has data at R4 and R7, then R5 and R6 use R4's data
    results = []
    for r in range(1, max_round + 1):
        # For each constituency, get the latest snapshot with round_no <= r
        mask = raw_latest["round_no"] <= r
        eligible = raw_latest[mask]
        if eligible.empty:
            continue

        # For each constituency, keep only the latest snapshot (highest round_no <= r)
        latest_ac = (
            eligible.groupby(["state_code", "ac_no"])["round_no"]
            .max()
            .reset_index()
        )
        snapshot = eligible.merge(latest_ac, on=["state_code", "ac_no", "round_no"], how="inner")

        # Aggregate by party
        party_totals = snapshot.groupby("party")["votes"].sum().reset_index()
        party_totals["round_num"] = r
        party_totals["round_label"] = f"R{r}"
        results.append(party_totals)

    if not results:
        return pd.DataFrame(columns=["party", "round_num", "round_label", "value"])

    series_df = pd.concat(results, ignore_index=True)
    series_df.rename(columns={"votes": "value"}, inplace=True)

    # Add R0 baseline for all parties
    all_parties = series_df["party"].unique()
    r0_rows = pd.DataFrame({
        "party": all_parties,
        "round_num": 0,
        "round_label": "R0",
        "value": 0,
    })
    series_df = pd.concat([r0_rows, series_df], ignore_index=True)

    # Forward-fill: for each party, ensure all rounds from 0 to max are present
    full_index = pd.MultiIndex.from_product(
        [all_parties, range(0, max_round + 1)],
        names=["party", "round_num"],
    )
    series_df = (
        series_df.set_index(["party", "round_num"])
        .reindex(full_index)
        .reset_index()
    )
    series_df["round_label"] = series_df["round_num"].apply(lambda x: f"R{x}")
    series_df["value"] = series_df.groupby("party")["value"].ffill().fillna(0)

    # Compute vote share % if requested
    if metric == "vote_share_pct":
        totals_per_round = series_df.groupby("round_num")["value"].sum()
        series_df = series_df.merge(totals_per_round, on="round_num", suffixes=("", "_total"))
        series_df["value"] = series_df.apply(
            lambda r: (r["value"] / r["value_total"] * 100) if r["value_total"] > 0 else 0,
            axis=1,
        )

    return series_df


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

# State filter — persisted via query params (survives meta-refresh)
state_options = [s["name"] for s in STATES] + ["All States"]
params = st.query_params
default_state = params.get("state", "All States")
if default_state not in state_options:
    default_state = "All States"

selected_state = st.sidebar.selectbox(
    "Filter by State",
    state_options,
    index=state_options.index(default_state),
    key="sidebar_state_select",
)
st.query_params["state"] = selected_state

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

    # Progress bar — % of fully declared constituencies
    done_pct = status_summary.get("DONE", 0) / max(total_acs, 1)
    st.progress(done_pct, text=f"Declared: {done_pct:.0%} ACs")

    st.divider()

    # --- Combined seat tally: Won (solid) + Leading (hatched) ---
    st.subheader("Seat Tally")

    wl_tally = get_party_seat_tally_won_leading(DB_PATH, state_code_filter)
    if wl_tally.empty:
        st.info("No data yet.")
    else:
        # Collapse minor parties (by total)
        wl_display = collapse_others(wl_tally, "party", "total", top_n=10)
        wl_display["short"] = wl_display["party"].apply(short)

        fig = go.Figure()

        # Won seats — solid blocks
        fig.add_trace(go.Bar(
            y=wl_display["short"],
            x=wl_display["won"],
            orientation="h",
            name="Won",
            marker_color=[get_party_color(p) for p in wl_display["party"]],
            text=wl_display.apply(lambda r: f"{r['won']}" if r["won"] > 0 else "", axis=1),
            textposition="inside",
            hovertext=wl_display.apply(lambda r: f"{r['party']}: {r['won']} won", axis=1),
        ))

        # Leading seats — same colour but hatched/striped
        fig.add_trace(go.Bar(
            y=wl_display["short"],
            x=wl_display["leading"],
            orientation="h",
            name="Leading",
            marker_color=[get_party_color(p) for p in wl_display["party"]],
            marker_pattern=dict(shape="/", solidity=0.6),
            text=wl_display.apply(lambda r: f"{r['leading']}" if r["leading"] > 0 else "", axis=1),
            textposition="inside",
            hovertext=wl_display.apply(lambda r: f"{r['party']}: {r['leading']} leading", axis=1),
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

        # Show all parties expander
        with st.expander("Show all parties"):
            all_tally = get_party_seat_tally_won_leading(DB_PATH, state_code_filter)
            all_tally_display = all_tally.rename(columns={"party": "Party", "won": "Won", "leading": "Leading", "total": "Total"})
            st.dataframe(all_tally_display, width="stretch", height=400)

    # --- Per-state breakdowns (only when "All States" selected) ---
    if selected_state == "All States":
        st.divider()
        st.subheader("State-by-State Breakdown")

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

                fig = go.Figure()
                # Won — solid
                fig.add_trace(go.Bar(
                    y=st_display["short"],
                    x=st_display["won"],
                    orientation="h",
                    name="Won",
                    marker_color=[get_party_color(p) for p in st_display["party"]],
                    text=st_display.apply(lambda r: f"{r['won']}" if r["won"] > 0 else "", axis=1),
                    textposition="inside",
                ))
                # Leading — hatched
                fig.add_trace(go.Bar(
                    y=st_display["short"],
                    x=st_display["leading"],
                    orientation="h",
                    name="Leading",
                    marker_color=[get_party_color(p) for p in st_display["party"]],
                    marker_pattern=dict(shape="/", solidity=0.6),
                    text=st_display.apply(lambda r: f"{r['leading']}" if r["leading"] > 0 else "", axis=1),
                    textposition="inside",
                ))
                fig.add_vline(x=maj, line_dash="dash", line_color="red", line_width=1.5)
                fig.add_annotation(x=maj, y=1.05, text=f"Majority ({maj})",
                                   showarrow=False, font=dict(color="red", size=11))
                fig.update_layout(
                    barmode="stack",
                    height=max(250, len(st_display) * 35),
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
        pivot["Total"] = pivot.sum(axis=1)
        for col in ["DONE", "LIVE", "PENDING", "ERROR"]:
            if col not in pivot.columns:
                pivot[col] = 0
        pivot["% Reporting"] = ((pivot["DONE"] + pivot["LIVE"]) / pivot["Total"] * 100).round(0).astype(int)
        pivot = pivot[["Total", "LIVE", "DONE", "PENDING", "ERROR", "% Reporting"]]
        pivot.index.name = "State"
        st.dataframe(pivot, width="stretch")


# ===========================================================================
# TAB 2: PARTY FORTUNES BY ROUND
# ===========================================================================
with tab2:
    st.header("Party Fortunes by Counting Round")

    trend_mode = st.radio(
        "Display mode",
        ["Cumulative Votes", "Vote Share %"],
        horizontal=True,
    )

    metric = "vote_share_pct" if trend_mode == "Vote Share %" else "cumulative_votes"
    series_df = compute_party_round_series(DB_PATH, state_code_filter, metric=metric)

    if series_df.empty:
        st.info("No data yet.")
    else:
        max_round = int(series_df["round_num"].max())
        if max_round < 1:
            st.info("Waiting for counting data to appear...")
        else:
            # Get top parties by latest round value
            latest_round = series_df[series_df["round_num"] == max_round]
            top_parties = latest_round.nlargest(10, "value")["party"].tolist()

            # Group others
            series_df["party_group"] = series_df["party"].apply(
                lambda p: p if p in top_parties else "Others"
            )
            # Re-aggregate with party_group
            plot_df = (
                series_df.groupby(["party_group", "round_num", "round_label"])["value"]
                .sum()
                .reset_index()
            )

            # Build ordered round labels
            round_labels = [f"R{i}" for i in range(0, max_round + 1)]

            fig = go.Figure()
            for party in top_parties + ["Others"]:
                party_df = plot_df[plot_df["party_group"] == party].sort_values("round_num")
                if party_df.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=party_df["round_label"],
                    y=party_df["value"],
                    mode="lines+markers",
                    name=short(party),
                    line=dict(color=get_party_color(party), width=2),
                    marker=dict(size=5),
                    hovertemplate=(
                        f"<b>{short(party)}</b><br>"
                        "Round: %{x}<br>"
                        f"{trend_mode}: " + "%{y:,.0f}<extra></extra>"
                    ),
                ))

            y_label = "Vote Share (%)" if metric == "vote_share_pct" else "Cumulative Votes"
            fig.update_layout(
                xaxis_title="Counting Round",
                yaxis_title=y_label,
                height=500,
                hovermode="x unified",
                xaxis=dict(categoryorder="array", categoryarray=round_labels),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig, width="stretch")

            st.caption(
                "Each line shows how a party's fortunes change as counting rounds progress. "
                "Flat segments mean no new votes in that round. "
                "Vote share = % of total votes counted so far across all reporting constituencies."
            )


# ===========================================================================
# TAB 3: CONSTITUENCY DRILL-DOWN
# ===========================================================================
with tab3:
    st.header("Constituency Drill-Down")

    # --- Persist via query params (survives meta-refresh) ---
    state_for_ac_options = [s["name"] for s in STATES]
    default_drill_state = params.get("drill_state", state_for_ac_options[0])
    if default_drill_state not in state_for_ac_options:
        default_drill_state = state_for_ac_options[0]

    state_for_ac = st.selectbox(
        "Select State",
        state_for_ac_options,
        index=state_for_ac_options.index(default_drill_state),
        key="drill_state_select",
    )
    st.query_params["drill_state"] = state_for_ac

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
        # Persist AC selection via query params
        default_ac = params.get("drill_ac", ac_options[0])
        if default_ac not in ac_options:
            default_ac = ac_options[0]

        selected_ac = st.selectbox(
            "Select Constituency",
            ac_options,
            index=ac_options.index(default_ac),
            key="drill_ac_select",
        )
        st.query_params["drill_ac"] = selected_ac
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
            st.error("⚠️ Error fetching data for this constituency")
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

            # --- Bar chart with party colours + title-case names ---
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

    # --- Update cycle duration chart ---
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
            filtered["last_updated"] = filtered["last_scraped"].apply(
                lambda x: fmt_ist(x, "%d %b %H:%M") if pd.notna(x) and x else ""
            )

        display_cols = [c for c in [
            "state_name", "ac_no", "ac_name", "status",
            "current_round", "total_rounds", "last_updated",
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
