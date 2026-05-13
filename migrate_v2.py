#!/usr/bin/env python3
"""
V2 schema migration: normalize rounds, add states table, add election_type.

Changes from V1:
  - New `states` table (from data/states.csv)
  - `rounds`: drop state_name, scraped_at; add election_type
  - New `round_timestamps` table: one row per (state_code, ac_no, round_no)
  - `constituency_status`: drop state_name, last_scraped

Usage:
    python migrate_v2.py                          # SQLite
    DATABASE_URL="postgresql://..." python migrate_v2.py  # PostgreSQL
"""

import csv
import os
import sqlite3 as sq

DATABASE_URL = os.environ.get("DATABASE_URL", "data/election_results.db")
IS_PG = DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")
STATES_CSV = os.path.join(os.path.dirname(__file__), "data", "states.csv")

if IS_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# New DDL
# ---------------------------------------------------------------------------

_DDL_PG = """
CREATE TABLE IF NOT EXISTS states (
    state_code       TEXT PRIMARY KEY,
    state_code_eci   TEXT,
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

CREATE TABLE IF NOT EXISTS rounds (
    id              SERIAL PRIMARY KEY,
    state_code      TEXT    NOT NULL REFERENCES states(state_code),
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party           TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    election_type   TEXT    NOT NULL DEFAULT 'AC'
);

CREATE INDEX IF NOT EXISTS idx_rounds_ac ON rounds (state_code, ac_no);
CREATE INDEX IF NOT EXISTS idx_rounds_party ON rounds (party);

CREATE TABLE IF NOT EXISTS round_timestamps (
    state_code  TEXT    NOT NULL,
    ac_no       INTEGER NOT NULL,
    round_no    INTEGER NOT NULL,
    scraped_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (state_code, ac_no, round_no)
);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL REFERENCES states(state_code),
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    total_rounds    INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    won             INTEGER DEFAULT 0,
    PRIMARY KEY (state_code, ac_no)
);

CREATE TABLE IF NOT EXISTS scrape_cycles (
    id              SERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    pages_attempted INTEGER DEFAULT 0,
    pages_success   INTEGER DEFAULT 0,
    pages_skipped   INTEGER DEFAULT 0,
    pages_error     INTEGER DEFAULT 0,
    cycle_duration_sec REAL
);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS states (
    state_code       TEXT PRIMARY KEY,
    state_code_eci   TEXT,
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

CREATE TABLE IF NOT EXISTS rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party           TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    election_type   TEXT    NOT NULL DEFAULT 'AC'
);

CREATE INDEX IF NOT EXISTS idx_rounds_ac ON rounds (state_code, ac_no);
CREATE INDEX IF NOT EXISTS idx_rounds_party ON rounds (party);

CREATE TABLE IF NOT EXISTS round_timestamps (
    state_code  TEXT    NOT NULL,
    ac_no       INTEGER NOT NULL,
    round_no    INTEGER NOT NULL,
    scraped_at  TEXT    NOT NULL,
    PRIMARY KEY (state_code, ac_no, round_no)
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


def load_states_csv():
    """Load states from CSV file."""
    states = []
    with open(STATES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            states.append(row)
    return states


def migrate():
    """Run the V2 migration."""
    print(f"Backend: {'PostgreSQL' if IS_PG else 'SQLite'}")
    print(f"Database: {DATABASE_URL}")

    if IS_PG:
        migrate_pg()
    else:
        migrate_sqlite()


def migrate_pg():
    """Migrate PostgreSQL database."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Drop old tables
    for table in ["rounds", "constituency_status", "scrape_cycles"]:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    print("Dropped old tables.")

    # Create new schema
    for stmt in _DDL_PG.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    print("Created new schema.")

    # Load states
    states = load_states_csv()
    for s in states:
        cur.execute(
            """INSERT INTO states (state_code, state_code_eci, state_name, state_capital,
                   state_status, population_2011, region, districts,
                   assembly_seats, loksabha_seats, rajyasabha_seats)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (state_code) DO NOTHING""",
            (s["state_code"], s["state_code_eci"], s["state_name"],
             s["state_capital"], s["state_status"],
             int(s["population_2011"]) if s["population_2011"] else None,
             s["region"],
             int(s["districts"]) if s["districts"] else None,
             int(s["assembly_seats"]) if s["assembly_seats"] else None,
             int(s["loksabha_seats"]) if s["loksabha_seats"] else None,
             int(s["rajyasabha_seats"]) if s["rajyasabha_seats"] else None),
        )
    conn.commit()
    print(f"Loaded {len(states)} states from CSV.")

    # Seed constituency_status
    for s in states:
        ac_count = int(s["assembly_seats"]) if s["assembly_seats"] else 0
        for ac_no in range(1, ac_count + 1):
            cur.execute(
                """INSERT INTO constituency_status (state_code, ac_no, status)
                   VALUES (%s, %s, 'PENDING')
                   ON CONFLICT (state_code, ac_no) DO NOTHING""",
                (s["state_code"], ac_no),
            )
    conn.commit()
    print("Seeded constituency_status.")

    conn.close()


def migrate_sqlite():
    """Migrate SQLite database."""
    # Remove old DB and start fresh
    if os.path.exists(DATABASE_URL):
        os.remove(DATABASE_URL)
        print(f"Removed old {DATABASE_URL}")

    conn = sq.connect(DATABASE_URL)
    conn.executescript(_DDL_SQLITE)
    print("Created new schema.")

    # Load states
    states = load_states_csv()
    for s in states:
        conn.execute(
            """INSERT OR IGNORE INTO states (state_code, state_code_eci, state_name, state_capital,
                   state_status, population_2011, region, districts,
                   assembly_seats, loksabha_seats, rajyasabha_seats)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["state_code"], s["state_code_eci"], s["state_name"],
             s["state_capital"], s["state_status"],
             int(s["population_2011"]) if s["population_2011"] else None,
             s["region"],
             int(s["districts"]) if s["districts"] else None,
             int(s["assembly_seats"]) if s["assembly_seats"] else None,
             int(s["loksabha_seats"]) if s["loksabha_seats"] else None,
             int(s["rajyasabha_seats"]) if s["rajyasabha_seats"] else None),
        )
    conn.commit()
    print(f"Loaded {len(states)} states from CSV.")

    # Seed constituency_status
    for s in states:
        ac_count = int(s["assembly_seats"]) if s["assembly_seats"] else 0
        for ac_no in range(1, ac_count + 1):
            conn.execute(
                """INSERT OR IGNORE INTO constituency_status (state_code, ac_no, status)
                   VALUES (?, ?, 'PENDING')""",
                (s["state_code"], ac_no),
            )
    conn.commit()
    print("Seeded constituency_status.")

    conn.close()


if __name__ == "__main__":
    migrate()
