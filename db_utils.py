"""
Database layer for ECI Live Election Tracker.

Supports both SQLite and PostgreSQL backends.
Set DATABASE_URL env var:
  - File path (e.g. "data/election_results.db") → SQLite
  - postgres:// or postgresql:// URL → PostgreSQL

Tables:
  - states: reference (ECI code as PK, from data/states.csv)
  - parties: party metadata + aliases (from data/parties.csv)
  - rounds_ac: AC vote counts per candidate per round
  - rounds_pc: PC vote counts (reserved for future use)
  - constituency_status: per-AC lifecycle tracking
"""

import csv
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

DATABASE_URL = os.environ.get("DATABASE_URL", "data/election_results.db")
IS_PG = DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")
STATES_CSV = os.path.join(os.path.dirname(__file__), "data", "states.csv")
PARTIES_CSV = os.path.join(os.path.dirname(__file__), "data", "parties.csv")

if IS_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values


# ---------------------------------------------------------------------------
# Schema DDL (dialect-specific)
# ---------------------------------------------------------------------------

_DDL_PG = """
CREATE TABLE IF NOT EXISTS states (
    state_code       TEXT PRIMARY KEY,  -- ECI code: S03, S11, U07
    state_code_std   TEXT,              -- standard: AS, KL, DL
    state_name       TEXT NOT NULL,
    state_capital    TEXT,
    state_status     TEXT,
    population_2011  INTEGER,
    region           TEXT,
    districts        INTEGER,
    assembly_seats   INTEGER,
    loksabha_seats   INTEGER,
    rajyasabha_seats INTEGER
);

CREATE TABLE IF NOT EXISTS parties (
    abv              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    aliases          TEXT DEFAULT '',
    chief            TEXT,
    colour           TEXT,
    founded          INTEGER,
    symbol_name      TEXT,
    symbol_emoji     TEXT,
    seats_loksabha   INTEGER DEFAULT 0,
    seats_rajyasabha INTEGER DEFAULT 0,
    seats_assembly   INTEGER DEFAULT 0,
    wikipedia_url    TEXT,
    alliance         TEXT
);

CREATE TABLE IF NOT EXISTS rounds_ac (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, ac_no, round_no, candidate, party_abv)
);

CREATE TABLE IF NOT EXISTS rounds_pc (
    state_code      TEXT    NOT NULL,
    pc_no           INTEGER NOT NULL,
    pc_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, pc_no, round_no, candidate, party_abv)
);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    total_rounds    INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    won             INTEGER DEFAULT 0,
    PRIMARY KEY (state_code, ac_no)
);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS states (
    state_code       TEXT PRIMARY KEY,  -- ECI code: S03, S11, U07
    state_code_std   TEXT,              -- standard: AS, KL, DL
    state_name       TEXT NOT NULL,
    state_capital    TEXT,
    state_status     TEXT,
    population_2011  INTEGER,
    region           TEXT,
    districts        INTEGER,
    assembly_seats   INTEGER,
    loksabha_seats   INTEGER,
    rajyasabha_seats INTEGER
);

CREATE TABLE IF NOT EXISTS parties (
    abv              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    aliases          TEXT DEFAULT '',
    chief            TEXT,
    colour           TEXT,
    founded          INTEGER,
    symbol_name      TEXT,
    symbol_emoji     TEXT,
    seats_loksabha   INTEGER DEFAULT 0,
    seats_rajyasabha INTEGER DEFAULT 0,
    seats_assembly   INTEGER DEFAULT 0,
    wikipedia_url    TEXT,
    alliance         TEXT
);

CREATE TABLE IF NOT EXISTS rounds_ac (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, ac_no, round_no, candidate, party_abv)
);

CREATE TABLE IF NOT EXISTS rounds_pc (
    state_code      TEXT    NOT NULL,
    pc_no           INTEGER NOT NULL,
    pc_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, pc_no, round_no, candidate, party_abv)
);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    total_rounds    INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    won             INTEGER DEFAULT 0,
    PRIMARY KEY (state_code, ac_no)
);
"""


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect():
    if IS_PG:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect(DATABASE_URL, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


def _cursor(conn):
    if IS_PG:
        return conn.cursor(cursor_factory=RealDictCursor)
    else:
        conn.row_factory = sqlite3.Row
        return conn.cursor()


def _placeholder():
    return "?" if not IS_PG else "%s"


# ---------------------------------------------------------------------------
# Party name normalization
# ---------------------------------------------------------------------------

_party_alias_map = None
_party_name_set = None


def _load_party_cache():
    """Load party names and aliases into memory (once).

    Builds two lookups:
      _party_name_set: set of canonical names (parties.name)
      _party_alias_map: variant_name → canonical_name (from parties.aliases column)
    """
    global _party_alias_map, _party_name_set
    if _party_alias_map is not None:
        return
    conn = _connect()
    cur = _cursor(conn)
    try:
        _party_alias_map = {}
        _party_name_set = set()
        cur.execute("SELECT name, aliases FROM parties")
        for row in cur.fetchall():
            canonical = row["name"]
            _party_name_set.add(canonical)
            aliases_raw = row["aliases"]
            if aliases_raw:
                for alias in aliases_raw.split(","):
                    alias = alias.strip()
                    if alias and alias != canonical:
                        _party_alias_map[alias] = canonical
    except Exception:
        _party_alias_map = {}
        _party_name_set = set()
    finally:
        conn.close()


def _normalize_party(name: str) -> str:
    """Normalize a party name to its canonical form."""
    _load_party_cache()
    if name in _party_name_set:
        return name
    if name in _party_alias_map:
        return _party_alias_map[name]
    name_lower = name.lower().strip()
    for canonical in _party_name_set:
        if canonical.lower() == name_lower:
            return canonical
    return name


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db():
    """Create tables and seed from CSVs (idempotent)."""
    conn = _connect()
    cur = _cursor(conn)
    try:
        ddl = _DDL_PG if IS_PG else _DDL_SQLITE
        if IS_PG:
            for stmt in ddl.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
        else:
            cur.executescript(ddl)

        # Load states from CSV (ECI code as PK)
        with open(STATES_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for s in reader:
                p = _placeholder()
                # CSV has state_code (standard) and state_code_eci (ECI)
                # states table: state_code = ECI (PK), state_code_std = standard
                eci_code = s.get("state_code_eci") or s["state_code"]
                std_code = s["state_code"]
                vals = (
                    eci_code, std_code, s["state_name"],
                    s.get("state_capital"), s.get("state_status"),
                    int(s["population_2011"]) if s.get("population_2011") else None,
                    s.get("region"),
                    int(s["districts"]) if s.get("districts") else None,
                    int(s["assembly_seats"]) if s.get("assembly_seats") else None,
                    int(s["loksabha_seats"]) if s.get("loksabha_seats") else None,
                    int(s["rajyasabha_seats"]) if s.get("rajyasabha_seats") else None,
                )
                if IS_PG:
                    cur.execute(
                        f"""INSERT INTO states (state_code,state_code_std,state_name,state_capital,
                               state_status,population_2011,region,districts,
                               assembly_seats,loksabha_seats,rajyasabha_seats)
                           VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                           ON CONFLICT (state_code) DO NOTHING""", vals,
                    )
                else:
                    cur.execute(
                        f"""INSERT OR IGNORE INTO states (state_code,state_code_std,state_name,state_capital,
                               state_status,population_2011,region,districts,
                               assembly_seats,loksabha_seats,rajyasabha_seats)
                           VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})""", vals,
                    )

        # Load parties from CSV
        if os.path.exists(PARTIES_CSV):
            with open(PARTIES_CSV, newline="") as f:
                reader = csv.DictReader(f)
                for s in reader:
                    p = _placeholder()
                    vals = (
                        s["abv"], s["name"], s.get("chief") or None,
                        s.get("colour") or None,
                        int(s["founded"]) if s.get("founded") else None,
                        s.get("symbol_name") or None, s.get("symbol_emoji") or None,
                        int(s["seats_loksabha"]) if s.get("seats_loksabha") else 0,
                        int(s["seats_rajyasabha"]) if s.get("seats_rajyasabha") else 0,
                        int(s["seats_assembly"]) if s.get("seats_assembly") else 0,
                        s.get("wikipedia_url") or None, s.get("alliance") or None,
                    )
                    if IS_PG:
                        cur.execute(
                            f"""INSERT INTO parties (abv,name,chief,colour,founded,symbol_name,symbol_emoji,
                               seats_loksabha,seats_rajyasabha,seats_assembly,wikipedia_url,alliance)
                               VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
                               ON CONFLICT (abv) DO NOTHING""", vals,
                        )
                    else:
                        cur.execute(
                            f"""INSERT OR IGNORE INTO parties (abv,name,chief,colour,founded,symbol_name,symbol_emoji,
                               seats_loksabha,seats_rajyasabha,seats_assembly,wikipedia_url,alliance)
                               VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})""", vals,
                        )

        # Seed constituency_status (using ECI codes)
        with open(STATES_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for s in reader:
                eci_code = s.get("state_code_eci") or s["state_code"]
                ac_count = int(s["assembly_seats"]) if s.get("assembly_seats") else 0
                for ac_no in range(1, ac_count + 1):
                    p = _placeholder()
                    if IS_PG:
                        cur.execute(
                            f"""INSERT INTO constituency_status (state_code, ac_no, status)
                               VALUES ({p}, {p}, 'PENDING')
                               ON CONFLICT (state_code, ac_no) DO NOTHING""",
                            (eci_code, ac_no),
                        )
                    else:
                        cur.execute(
                            f"""INSERT OR IGNORE INTO constituency_status (state_code, ac_no, status)
                               VALUES ({p}, {p}, 'PENDING')""",
                            (eci_code, ac_no),
                        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Work queue
# ---------------------------------------------------------------------------

def get_work_queue() -> list[dict]:
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute("""
            SELECT cs.state_code, cs.ac_no, cs.ac_name,
                   cs.status, cs.current_round, cs.total_rounds
            FROM constituency_status cs
            WHERE cs.status NOT IN ('DONE', 'ERROR')
            ORDER BY CASE cs.status WHEN 'LIVE' THEN 0 ELSE 1 END,
                     cs.state_code, cs.ac_no
        """)
        return cur.fetchall()
    finally:
        conn.close()


def get_error_constituencies() -> list[dict]:
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute("""
            SELECT state_code, ac_no, ac_name, error_count
            FROM constituency_status
            WHERE status = 'ERROR'
            ORDER BY state_code, ac_no
        """)
        return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def upsert_constituency_status(
    state_code: str,
    ac_no: int,
    ac_name: Optional[str],
    status: str,
    current_round: int,
    total_rounds: int,
    state_name: str = "",  # ignored, kept for API compat
) -> None:
    p = _placeholder()
    conn = _connect()
    cur = _cursor(conn)
    try:
        if IS_PG:
            cur.execute(
                f"""INSERT INTO constituency_status
                    (state_code, ac_no, ac_name, status, current_round, total_rounds, error_count)
                   VALUES ({p}, {p}, {p}, {p}, {p}, {p}, 0)
                   ON CONFLICT (state_code, ac_no) DO UPDATE SET
                    ac_name      = EXCLUDED.ac_name,
                    status       = EXCLUDED.status,
                    current_round = EXCLUDED.current_round,
                    total_rounds  = EXCLUDED.total_rounds,
                    error_count   = CASE
                        WHEN EXCLUDED.status = 'ERROR'
                        THEN constituency_status.error_count + 1
                        ELSE 0
                    END""",
                (state_code, ac_no, ac_name, status, current_round, total_rounds),
            )
        else:
            cur.execute(
                f"""INSERT INTO constituency_status
                    (state_code, ac_no, ac_name, status, current_round, total_rounds, error_count)
                   VALUES ({p}, {p}, {p}, {p}, {p}, {p}, 0)
                   ON CONFLICT(state_code, ac_no) DO UPDATE SET
                    ac_name      = excluded.ac_name,
                    status       = excluded.status,
                    current_round = excluded.current_round,
                    total_rounds  = excluded.total_rounds,
                    error_count   = CASE
                        WHEN excluded.status = 'ERROR'
                        THEN error_count + 1
                        ELSE 0
                    END""",
                (state_code, ac_no, ac_name, status, current_round, total_rounds),
            )
        conn.commit()
    finally:
        conn.close()


def insert_round_snapshot(
    state_code: str,
    state_name: str,  # ignored, kept for API compat
    ac_no: int,
    ac_name: str,
    round_no: int,
    total_rounds: int,
    candidates: list[dict],
    scraped_at: str,  # ignored, kept for API compat
) -> None:
    """Bulk-insert one round snapshot for all candidates in a constituency."""
    if not candidates:
        return
    p = _placeholder()
    conn = _connect()
    cur = _cursor(conn)
    try:
        rows = [
            (state_code, ac_no, ac_name, round_no,
             c["candidate"], _normalize_party(c["party"]), c["votes"])
            for c in candidates
        ]
        if IS_PG:
            execute_values(
                cur,
                f"""INSERT INTO rounds_ac
                   (state_code, ac_no, ac_name, round_no, candidate, party_abv, votes)
                   VALUES %s""",
                rows,
            )
        else:
            cur.executemany(
                f"""INSERT OR IGNORE INTO rounds_ac
                   (state_code, ac_no, ac_name, round_no, candidate, party_abv, votes)
                   VALUES ({p},{p},{p},{p},{p},{p},{p})""",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def update_won_status(state_code: str, won_ac_nos: list[int]) -> None:
    p = _placeholder()
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute(
            f"UPDATE constituency_status SET won=0 WHERE state_code={p}",
            (state_code,),
        )
        if won_ac_nos:
            if IS_PG:
                cur.execute(
                    f"UPDATE constituency_status SET won=1 "
                    f"WHERE state_code={p} AND ac_no = ANY({p})",
                    (state_code, won_ac_nos),
                )
            else:
                placeholders = ",".join(["?"] * len(won_ac_nos))
                cur.execute(
                    f"UPDATE constituency_status SET won=1 "
                    f"WHERE state_code=? AND ac_no IN ({placeholders})",
                    [state_code] + list(won_ac_nos),
                )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads (for dashboard)
# ---------------------------------------------------------------------------

def get_status_summary(state_code: str = None) -> dict:
    p = _placeholder()
    conn = _connect()
    cur = _cursor(conn)
    try:
        if state_code:
            cur.execute(
                f"SELECT status, COUNT(*) as cnt FROM constituency_status "
                f"WHERE state_code = {p} GROUP BY status",
                (state_code,),
            )
        else:
            cur.execute(
                "SELECT status, COUNT(*) as cnt FROM constituency_status GROUP BY status"
            )
        return {r["status"]: r["cnt"] for r in cur.fetchall()}
    finally:
        conn.close()


def get_state_status_summary() -> list[dict]:
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute("""
            SELECT s.state_name, cs.status, COUNT(*) as cnt
            FROM constituency_status cs
            JOIN states s ON cs.state_code = s.state_code
            GROUP BY s.state_name, cs.status
            ORDER BY s.state_name, cs.status
        """)
        return cur.fetchall()
    finally:
        conn.close()


def get_leading_seats(state_code: Optional[str] = None) -> pd.DataFrame:
    """For each AC, find the candidate with highest votes in the latest round.
    Round_no IS the time axis — once declared, a round's results don't change."""
    p = _placeholder()
    conn = _connect()
    try:
        where = f"AND r.state_code = {p}" if state_code else ""
        params = (state_code,) if state_code else None
        query = f"""
            SELECT r.state_code, s.state_name, r.ac_no, r.ac_name,
                   r.candidate AS leading_candidate,
                   r.party_abv AS leading_party,
                   r.votes     AS leading_votes,
                   r.round_no
            FROM rounds_ac r
            JOIN states s ON r.state_code = s.state_code
            INNER JOIN (
                SELECT state_code, ac_no, MAX(round_no) as latest_round
                FROM rounds_ac
                GROUP BY state_code, ac_no
            ) lr ON r.state_code = lr.state_code
                AND r.ac_no = lr.ac_no
                AND r.round_no = lr.latest_round
            WHERE r.votes = (
                SELECT MAX(r2.votes)
                FROM rounds_ac r2
                WHERE r2.state_code = r.state_code
                  AND r2.ac_no = r.ac_no
                  AND r2.round_no = r.round_no
            )
            {where}
            GROUP BY r.state_code, s.state_name, r.ac_no, r.ac_name,
                     r.candidate, r.party_abv, r.votes, r.round_no
        """
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_party_seat_tally(state_code: Optional[str] = None) -> pd.DataFrame:
    df = get_leading_seats(state_code)
    if df.empty:
        return pd.DataFrame(columns=["party", "seats_leading"])
    tally = df.groupby("leading_party").size().reset_index(name="seats_leading")
    tally = tally.sort_values("seats_leading", ascending=False).reset_index(drop=True)
    tally.rename(columns={"leading_party": "party"}, inplace=True)
    return tally


def get_party_seat_tally_won_leading(state_code: Optional[str] = None) -> pd.DataFrame:
    df = get_leading_seats(state_code)
    if df.empty:
        return pd.DataFrame(columns=["party", "won", "leading", "total"])

    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute("SELECT state_code, ac_no, won FROM constituency_status")
        rows = cur.fetchall()
    finally:
        conn.close()

    won_map = {(r["state_code"], r["ac_no"]): r["won"] for r in rows}
    df["is_won"] = df.apply(lambda r: won_map.get((r["state_code"], r["ac_no"]), 0), axis=1)
    df["is_leading"] = 1 - df["is_won"]

    result = df.groupby("leading_party").agg(
        won=("is_won", "sum"),
        leading=("is_leading", "sum"),
    ).reset_index()
    result["total"] = result["won"] + result["leading"]
    result = result.sort_values("total", ascending=False).reset_index(drop=True)
    result.rename(columns={"leading_party": "party"}, inplace=True)
    return result


def get_party_totals_over_time(state_code: Optional[str] = None) -> pd.DataFrame:
    """Cumulative votes per party by round_no. Round is the time axis."""
    p = _placeholder()
    conn = _connect()
    try:
        where = f"AND r.state_code = {p}" if state_code else ""
        params = (state_code,) if state_code else None
        query = f"""
            SELECT r.round_no, r.party_abv, SUM(r.votes) as total_votes
            FROM rounds_ac r
            INNER JOIN (
                SELECT state_code, ac_no, MAX(round_no) as latest_round
                FROM rounds_ac
                GROUP BY state_code, ac_no
            ) lr ON r.state_code = lr.state_code
                AND r.ac_no = lr.ac_no
                AND r.round_no = lr.latest_round
            WHERE 1=1 {where}
            GROUP BY r.round_no, r.party_abv
            ORDER BY r.round_no
        """
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_constituency_rounds(state_code: str, ac_no: int) -> pd.DataFrame:
    p = _placeholder()
    conn = _connect()
    try:
        return pd.read_sql_query(
            f"""
            SELECT round_no, candidate, party_abv, votes
            FROM rounds_ac
            WHERE state_code = {p} AND ac_no = {p}
            ORDER BY round_no, party_abv
            """,
            conn,
            params=(state_code, ac_no),
        )
    finally:
        conn.close()


def get_all_constituency_statuses() -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(
            """SELECT cs.*, s.state_name
               FROM constituency_status cs
               JOIN states s ON cs.state_code = s.state_code
               ORDER BY cs.state_code, cs.ac_no""",
            conn,
        )
    finally:
        conn.close()


def get_state_name(state_code: str) -> str:
    """Look up state name from ECI code via states table."""
    p = _placeholder()
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute(
            f"SELECT state_name FROM states WHERE state_code = {p}",
            (state_code,),
        )
        row = cur.fetchone()
        return row["state_name"] if row else state_code
    finally:
        conn.close()
