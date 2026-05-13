#!/usr/bin/env python3
"""
One-time migration: create PostgreSQL schema + import existing SQLite data.

Usage:
    python migrate_to_pg.py
    DATABASE_URL=postgresql://... python migrate_to_pg.py
"""
import os
import sqlite3 as sq

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/election_results"
)
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "live_results.db")

# Target schema — matches db_utils.py
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id              SERIAL PRIMARY KEY,
    state_code      TEXT    NOT NULL,
    state_name      TEXT    NOT NULL DEFAULT '',
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    total_rounds    INTEGER,
    candidate       TEXT    NOT NULL,
    party           TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rounds_ac_scraped
    ON rounds (state_code, ac_no, scraped_at);
CREATE INDEX IF NOT EXISTS idx_rounds_party_scraped
    ON rounds (party, scraped_at);
CREATE INDEX IF NOT EXISTS idx_rounds_state_scraped
    ON rounds (state_code, scraped_at);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL,
    state_name      TEXT    NOT NULL DEFAULT '',
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    total_rounds    INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    last_scraped    TIMESTAMPTZ,
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


def create_schema(conn):
    """Create all PostgreSQL tables."""
    cur = conn.cursor()
    for stmt in PG_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    print("Schema created.")


def build_state_map(sqlite_path):
    """Build state_code_eci → state_name mapping from SQLite 'states' table."""
    sq_conn = sq.connect(sqlite_path)
    rows = sq_conn.execute(
        "SELECT state_code_eci, state_name FROM states WHERE state_code_eci IS NOT NULL"
    ).fetchall()
    sq_conn.close()
    return {r[0]: r[1] for r in rows}


def migrate_rounds(pg_conn, sqlite_path, state_map):
    """Migrate rounds table from SQLite → PostgreSQL."""
    sq_conn = sq.connect(sqlite_path)
    sq_conn.row_factory = sq.Row

    total = sq_conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
    print(f"Migrating {total} rows from rounds...")

    cur = pg_conn.cursor()
    batch = []
    count = 0

    for row in sq_conn.execute("SELECT * FROM rounds"):
        state_code = row["state_code"]
        state_name = state_map.get(state_code, "")
        batch.append((
            state_code,
            state_name,
            row["ac_no"],
            row["ac_name"],
            row["round_no"],
            None,           # total_rounds — unknown from old data
            row["candidate"],
            row["party"],
            row["votes"],
        ))

        if len(batch) >= 5000:
            _insert_batch(cur, batch)
            count += len(batch)
            print(f"  {count}/{total} rows...")
            batch = []

    if batch:
        _insert_batch(cur, batch)
        count += len(batch)

    pg_conn.commit()
    sq_conn.close()
    print(f"  Migrated {count} rows into rounds.")


def _insert_batch(cur, batch):
    """Bulk insert a batch of round rows."""
    cur.executemany(
        """INSERT INTO rounds
           (state_code, state_name, ac_no, ac_name, round_no,
            total_rounds, candidate, party, votes)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        batch,
    )


def migrate():
    """Main migration entry point."""
    if not os.path.exists(SQLITE_PATH):
        print(f"SQLite file not found: {SQLITE_PATH}")
        print("Creating empty PostgreSQL schema only.")
        conn = psycopg2.connect(DATABASE_URL)
        create_schema(conn)
        conn.close()
        return

    state_map = build_state_map(SQLITE_PATH)
    print(f"Loaded {len(state_map)} state mappings from SQLite.")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        create_schema(conn)
        migrate_rounds(conn, SQLITE_PATH, state_map)
        print("\nMigration complete!")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
