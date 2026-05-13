#!/usr/bin/env python3
"""
One-time migration: create PostgreSQL schema + import from SQLite.

Reads from data/election_results.db (new schema) and writes to PostgreSQL.

Usage:
    python migrate_to_pg.py
    DATABASE_URL=postgresql://... python migrate_to_pg.py
"""
import csv
import os
import sqlite3 as sq

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/election_results"
)
SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "election_results.db")
STATES_CSV = os.path.join(os.path.dirname(__file__), "data", "states.csv")
PARTIES_CSV = os.path.join(os.path.dirname(__file__), "data", "parties.csv")

# Target schema — matches db_utils.py
PG_SCHEMA = """
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


def create_schema(conn):
    """Execute DDL to create schema."""
    cur = conn.cursor()
    for stmt in PG_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()
    print("Schema created.")


def migrate_states(conn):
    """Load states from CSV into PostgreSQL."""
    cur = conn.cursor()
    count = 0
    with open(STATES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for s in reader:
            eci_code = s.get("state_code_eci") or s["state_code"]
            std_code = s["state_code"]
            cur.execute(
                """INSERT INTO states (state_code, state_code_std, state_name, state_capital,
                       state_status, population_2011, region, districts,
                       assembly_seats, loksabha_seats, rajyasabha_seats)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (state_code) DO NOTHING""",
                (eci_code, std_code, s["state_name"],
                 s.get("state_capital"), s.get("state_status"),
                 int(s["population_2011"]) if s.get("population_2011") else None,
                 s.get("region"),
                 int(s["districts"]) if s.get("districts") else None,
                 int(s["assembly_seats"]) if s.get("assembly_seats") else None,
                 int(s["loksabha_seats"]) if s.get("loksabha_seats") else None,
                 int(s["rajyasabha_seats"]) if s.get("rajyasabha_seats") else None),
            )
            count += 1
    conn.commit()
    print(f"Loaded {count} states.")


def migrate_parties(conn):
    """Load parties from CSV into PostgreSQL."""
    if not os.path.exists(PARTIES_CSV):
        print("No parties.csv found, skipping.")
        return
    cur = conn.cursor()
    count = 0
    with open(PARTIES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for s in reader:
            cur.execute(
                """INSERT INTO parties (abv, name, chief, colour, founded, symbol_name, symbol_emoji,
                       seats_loksabha, seats_rajyasabha, seats_assembly, wikipedia_url, alliance)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (abv) DO NOTHING""",
                (s["abv"], s["name"], s.get("chief") or None,
                 s.get("colour") or None,
                 int(s["founded"]) if s.get("founded") else None,
                 s.get("symbol_name") or None, s.get("symbol_emoji") or None,
                 int(s["seats_loksabha"]) if s.get("seats_loksabha") else 0,
                 int(s["seats_rajyasabha"]) if s.get("seats_rajyasabha") else 0,
                 int(s["seats_assembly"]) if s.get("seats_assembly") else 0,
                 s.get("wikipedia_url") or None, s.get("alliance") or None),
            )
            count += 1
    conn.commit()
    print(f"Loaded {count} parties.")


def migrate_rounds(conn):
    """Migrate rounds_ac from SQLite → PostgreSQL."""
    sq_conn = sq.connect(SQLITE_PATH)
    sq_conn.row_factory = sq.Row

    total = sq_conn.execute("SELECT COUNT(*) FROM rounds_ac").fetchone()[0]
    print(f"Migrating {total} rows from rounds_ac...")

    cur = conn.cursor()
    batch = []
    count = 0

    for row in sq_conn.execute("SELECT * FROM rounds_ac"):
        batch.append((
            row["state_code"], row["ac_no"], row["ac_name"],
            row["round_no"], row["candidate"], row["party_abv"], row["votes"],
        ))

        if len(batch) >= 5000:
            _insert_batch(cur, batch)
            count += len(batch)
            print(f"  {count}/{total} rows...")
            batch = []

    if batch:
        _insert_batch(cur, batch)
        count += len(batch)

    conn.commit()
    sq_conn.close()
    print(f"  Migrated {count} rows into rounds_ac.")


def migrate_constituency_status(conn):
    """Migrate constituency_status from SQLite → PostgreSQL."""
    sq_conn = sq.connect(SQLITE_PATH)
    sq_conn.row_factory = sq.Row

    total = sq_conn.execute("SELECT COUNT(*) FROM constituency_status").fetchone()[0]
    print(f"Migrating {total} rows from constituency_status...")

    cur = conn.cursor()
    batch = []
    count = 0

    for row in sq_conn.execute("SELECT * FROM constituency_status"):
        batch.append((
            row["state_code"], row["ac_no"], row["ac_name"],
            row["status"], row["current_round"], row["total_rounds"],
            row["error_count"], row["won"],
        ))

        if len(batch) >= 5000:
            cur.executemany(
                """INSERT INTO constituency_status
                   (state_code, ac_no, ac_name, status, current_round,
                    total_rounds, error_count, won)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                batch,
            )
            count += len(batch)
            batch = []

    if batch:
        cur.executemany(
            """INSERT INTO constituency_status
               (state_code, ac_no, ac_name, status, current_round,
                total_rounds, error_count, won)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            batch,
        )
        count += len(batch)

    conn.commit()
    sq_conn.close()
    print(f"  Migrated {count} rows into constituency_status.")


def _insert_batch(cur, batch):
    """Bulk insert a batch of round rows."""
    cur.executemany(
        """INSERT INTO rounds_ac
           (state_code, ac_no, ac_name, round_no,
            candidate, party_abv, votes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        batch,
    )


def migrate():
    """Main migration entry point."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        create_schema(conn)
        migrate_states(conn)
        migrate_parties(conn)
        migrate_rounds(conn)
        migrate_constituency_status(conn)
        print("\nMigration complete!")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
