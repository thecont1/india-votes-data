"""
SQLite database layer for ECI Live Election Tracker.

Tables:
  - rounds: time-series vote snapshots per scrape cycle
  - constituency_status: per-AC lifecycle tracking (PENDING/LIVE/DONE/ERROR)
  - scrape_cycles: audit log of each scrape cycle

Uses Python's built-in sqlite3 — no external DB server needed.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from states_may2026 import STATES, TOTAL_ACS

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    state_code      TEXT    NOT NULL,
    state_name      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    total_rounds    INTEGER,
    candidate       TEXT    NOT NULL,
    party           TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    scraped_at      TEXT    NOT NULL   -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_rounds_ac_scraped
    ON rounds (state_code, ac_no, scraped_at);

CREATE INDEX IF NOT EXISTS idx_rounds_party_scraped
    ON rounds (party, scraped_at);

CREATE INDEX IF NOT EXISTS idx_rounds_state_scraped
    ON rounds (state_code, scraped_at);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL,
    state_name      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    total_rounds    INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    last_scraped    TEXT,
    PRIMARY KEY (state_code, ac_no)
);

CREATE TABLE IF NOT EXISTS scrape_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    pages_attempted INTEGER DEFAULT 0,
    pages_success   INTEGER DEFAULT 0,
    pages_skipped   INTEGER DEFAULT 0,
    pages_error     INTEGER DEFAULT 0,
    cycle_duration_sec REAL
);
"""

_INSERT_CONSTITUENCY_STATUS = """
INSERT OR IGNORE INTO constituency_status
    (state_code, state_name, ac_no, status)
VALUES (?, ?, ?, 'PENDING')
"""

_UPSERT_CONSTITUENCY_STATUS = """
INSERT INTO constituency_status
    (state_code, state_name, ac_no, ac_name, status,
     current_round, total_rounds, error_count, last_scraped)
VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
ON CONFLICT(state_code, ac_no) DO UPDATE SET
    ac_name      = excluded.ac_name,
    status       = excluded.status,
    current_round = excluded.current_round,
    total_rounds  = excluded.total_rounds,
    error_count   = CASE
        WHEN excluded.status = 'ERROR'
        THEN constituency_status.error_count + 1
        ELSE 0
    END,
    last_scraped  = excluded.last_scraped
"""

_INSERT_ROUND = """
INSERT INTO rounds
    (state_code, state_name, ac_no, ac_name, round_no, total_rounds,
     candidate, party, votes, scraped_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_CYCLE = """
INSERT INTO scrape_cycles
    (started_at, finished_at, pages_attempted, pages_success,
     pages_skipped, pages_error, cycle_duration_sec)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# Connection helper (WAL mode for concurrent read/write)
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> None:
    """Create tables and seed constituency_status for all ACs (idempotent)."""
    conn = _connect(db_path)
    try:
        conn.executescript(_CREATE_TABLES)
        for state in STATES:
            for ac_no in range(1, state["ac_count"] + 1):
                conn.execute(
                    _INSERT_CONSTITUENCY_STATUS,
                    (state["code"], state["name"], ac_no),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Work queue
# ---------------------------------------------------------------------------

def get_work_queue(db_path: str) -> list[dict]:
    """
    Return constituencies that still need scraping.
    LIVE (mid-count) first, then PENDING.
    Excludes DONE and ERROR (when error_count >= 3).
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT cs.state_code, cs.state_name, cs.ac_no, cs.ac_name,
                   cs.status, cs.current_round, cs.total_rounds
            FROM constituency_status cs
            WHERE cs.status NOT IN ('DONE', 'ERROR')
            ORDER BY CASE cs.status WHEN 'LIVE' THEN 0 ELSE 1 END,
                     cs.state_code, cs.ac_no
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_error_constituencies(db_path: str) -> list[dict]:
    """Return constituencies marked ERROR for potential retry."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT state_code, state_name, ac_no, ac_name, error_count
            FROM constituency_status
            WHERE status = 'ERROR'
            ORDER BY state_code, ac_no
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_constituency_status(
    db_path: str,
    state_code: str,
    ac_no: int,
    ac_name: Optional[str],
    status: str,
    current_round: int,
    total_rounds: int,
    state_name: str = "",
) -> None:
    """Update constituency lifecycle status."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        if not state_name:
            row = conn.execute(
                "SELECT state_name FROM constituency_status WHERE state_code=? AND ac_no=?",
                (state_code, ac_no),
            ).fetchone()
            state_name = row["state_name"] if row else ""
        conn.execute(
            _UPSERT_CONSTITUENCY_STATUS,
            (state_code, state_name, ac_no, ac_name, status,
             current_round, total_rounds, now),
        )
        conn.commit()
    finally:
        conn.close()


def insert_round_snapshot(
    db_path: str,
    state_code: str,
    state_name: str,
    ac_no: int,
    ac_name: str,
    round_no: int,
    total_rounds: int,
    candidates: list[dict],
    scraped_at: str,
) -> None:
    """
    Bulk-insert one round snapshot for all candidates in a constituency.
    candidates = [{"candidate": str, "party": str, "votes": int}, ...]
    """
    if not candidates:
        return
    conn = _connect(db_path)
    try:
        conn.executemany(
            _INSERT_ROUND,
            [
                (
                    state_code, state_name, ac_no, ac_name,
                    round_no, total_rounds,
                    c["candidate"], c["party"], c["votes"],
                    scraped_at,
                )
                for c in candidates
            ],
        )
        conn.commit()
    finally:
        conn.close()


def record_cycle(
    db_path: str,
    started_at: str,
    finished_at: str,
    pages_attempted: int,
    pages_success: int,
    pages_skipped: int,
    pages_error: int,
    duration_sec: float,
) -> None:
    """Record one scrape cycle in the audit log."""
    conn = _connect(db_path)
    try:
        conn.execute(
            _INSERT_CYCLE,
            (started_at, finished_at, pages_attempted, pages_success,
             pages_skipped, pages_error, duration_sec),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads (for dashboard)
# ---------------------------------------------------------------------------

def get_status_summary(db_path: str, state_code: str = None) -> dict:
    """Get counts by status: {'PENDING': N, 'LIVE': N, 'DONE': N, 'ERROR': N}."""
    conn = _connect(db_path)
    try:
        if state_code:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM constituency_status "
                "WHERE state_code = ? GROUP BY status",
                (state_code,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM constituency_status GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
    finally:
        conn.close()


def get_state_status_summary(db_path: str) -> list[dict]:
    """Get status counts broken down by state."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT state_name, status, COUNT(*) as cnt
            FROM constituency_status
            GROUP BY state_name, status
            ORDER BY state_name, status
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_leading_seats(db_path: str, state_code: Optional[str] = None) -> pd.DataFrame:
    """
    For each AC, find the candidate with highest votes in the latest snapshot.
    Returns DataFrame with columns: state_code, state_name, ac_no, ac_name,
    leading_candidate, leading_party, leading_votes.
    """
    conn = _connect(db_path)
    try:
        where = "AND r.state_code = ?" if state_code else ""
        params = (state_code,) if state_code else ()
        query = f"""
            SELECT r.state_code, r.state_name, r.ac_no, r.ac_name,
                   r.candidate AS leading_candidate,
                   r.party     AS leading_party,
                   r.votes     AS leading_votes
            FROM rounds r
            INNER JOIN (
                SELECT state_code, ac_no, MAX(scraped_at) as latest
                FROM rounds
                GROUP BY state_code, ac_no
            ) latest ON r.state_code = latest.state_code
                AND r.ac_no = latest.ac_no
                AND r.scraped_at = latest.latest
            WHERE r.votes = (
                SELECT MAX(r2.votes)
                FROM rounds r2
                WHERE r2.state_code = r.state_code
                  AND r2.ac_no = r.ac_no
                  AND r2.scraped_at = r.scraped_at
            )
            {where}
            GROUP BY r.state_code, r.ac_no
        """
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_party_seat_tally(db_path: str, state_code: Optional[str] = None) -> pd.DataFrame:
    """
    Count how many ACs each party is leading in (based on latest scrape).
    Returns DataFrame: party, seats_leading.
    """
    df = get_leading_seats(db_path, state_code)
    if df.empty:
        return pd.DataFrame(columns=["party", "seats_leading"])
    tally = df.groupby("leading_party").size().reset_index(name="seats_leading")
    tally = tally.sort_values("seats_leading", ascending=False).reset_index(drop=True)
    tally.rename(columns={"leading_party": "party"}, inplace=True)
    return tally


def get_party_seat_tally_won_leading(db_path: str, state_code: Optional[str] = None) -> pd.DataFrame:
    """
    Count won (DONE) vs leading (LIVE) seats per party.
    Returns DataFrame: party, won, leading, total.
    """
    df = get_leading_seats(db_path, state_code)
    if df.empty:
        return pd.DataFrame(columns=["party", "won", "leading", "total"])

    # Join with constituency_status to get DONE vs LIVE
    conn = _connect(db_path)
    try:
        where = "AND cs.state_code = ?" if state_code else ""
        params = (state_code,) if state_code else ()
        query = f"""
            SELECT r.party,
                   SUM(CASE WHEN cs.status = 'DONE' THEN 1 ELSE 0 END) as won,
                   SUM(CASE WHEN cs.status != 'DONE' THEN 1 ELSE 0 END) as leading
            FROM rounds r
            INNER JOIN (
                SELECT state_code, ac_no, MAX(scraped_at) as latest
                FROM rounds GROUP BY state_code, ac_no
            ) latest ON r.state_code = latest.state_code
                AND r.ac_no = latest.ac_no
                AND r.scraped_at = latest.latest
            INNER JOIN constituency_status cs
                ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
            WHERE r.votes = (
                SELECT MAX(r2.votes) FROM rounds r2
                WHERE r2.state_code = r.state_code
                  AND r2.ac_no = r.ac_no
                  AND r2.scraped_at = r.scraped_at
            )
            {where}
            GROUP BY r.state_code, r.ac_no, r.party
        """
        # We need to group by party after getting per-AC data
        # Simpler: use the leading_seats df and join with status
        pass
    finally:
        conn.close()

    # Build from leading_seats + constituency_status
    conn = _connect(db_path)
    try:
        ac_status_rows = conn.execute(
            "SELECT state_code, ac_no, status FROM constituency_status"
        ).fetchall()
        status_map = {(r["state_code"], r["ac_no"]): r["status"] for r in ac_status_rows}
    finally:
        conn.close()

    df["status"] = df.apply(
        lambda r: status_map.get((r["state_code"], r["ac_no"]), "LIVE"), axis=1
    )
    df["is_won"] = (df["status"] == "DONE").astype(int)
    df["is_leading"] = (df["status"] != "DONE").astype(int)

    result = df.groupby("leading_party").agg(
        won=("is_won", "sum"),
        leading=("is_leading", "sum"),
    ).reset_index()
    result["total"] = result["won"] + result["leading"]
    result = result.sort_values("total", ascending=False).reset_index(drop=True)
    result.rename(columns={"leading_party": "party"}, inplace=True)
    return result


def get_party_totals_over_time(
    db_path: str, state_code: Optional[str] = None
) -> pd.DataFrame:
    """
    Aggregate cumulative votes per party per scrape timestamp.
    Used by dashboard for trend line chart.
    """
    conn = _connect(db_path)
    try:
        where = "AND r.state_code = ?" if state_code else ""
        params = (state_code,) if state_code else ()
        query = f"""
            SELECT r.scraped_at, r.party, SUM(r.votes) as total_votes
            FROM rounds r
            INNER JOIN (
                SELECT state_code, ac_no, MAX(scraped_at) as latest
                FROM rounds
                GROUP BY state_code, ac_no
            ) latest ON r.state_code = latest.state_code
                AND r.ac_no = latest.ac_no
                AND r.scraped_at = latest.latest
            WHERE 1=1 {where}
            GROUP BY r.scraped_at, r.party
            ORDER BY r.scraped_at
        """
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_constituency_rounds(
    db_path: str, state_code: str, ac_no: int
) -> pd.DataFrame:
    """
    Get all scrape snapshots for a specific constituency.
    Used for constituency drill-down in dashboard.
    """
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            """
            SELECT scraped_at, round_no, candidate, party, votes
            FROM rounds
            WHERE state_code = ? AND ac_no = ?
            ORDER BY scraped_at, party
            """,
            conn,
            params=(state_code, ac_no),
        )
    finally:
        conn.close()


def get_all_constituency_statuses(db_path: str) -> pd.DataFrame:
    """Get all constituency statuses for the system monitor tab."""
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM constituency_status ORDER BY state_code, ac_no",
            conn,
        )
    finally:
        conn.close()


def get_scrape_cycles(db_path: str) -> pd.DataFrame:
    """Get scrape cycle audit log."""
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            "SELECT * FROM scrape_cycles ORDER BY id DESC LIMIT 100",
            conn,
        )
    finally:
        conn.close()


def get_last_scrape_time(db_path: str) -> Optional[str]:
    """Get the most recent scraped_at timestamp across all rounds."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(scraped_at) as ts FROM rounds").fetchone()
        return row["ts"] if row else None
    finally:
        conn.close()
