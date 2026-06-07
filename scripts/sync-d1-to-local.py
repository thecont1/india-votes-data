#!/usr/bin/env python3
"""
Sync Cloudflare D1 to local SQLite — full replacement.

Exports each table from D1 via wrangler, creates fresh SQLite with D1
schema, imports all data, and verifies row counts match.

Usage:
    uv run python3 scripts/sync-d1-to-local.py [--db data/india-votes-data.db]
"""

import os
import subprocess
import sys
import tempfile
import time

D1_DB = "election-results"
TABLES = [
    "states", "elections", "parties",
    "constituency_status", "rounds_ac", "rounds_pc",
    "candidates_search",
]
SKIP_FTS = True  # FTS5 virtual tables block wrangler d1 export

# D1 schema — must match production exactly.
# Regenerate with: wrangler d1 execute <db> --remote --command \
#   "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'search_fts%' AND name != '_cf_KV';"
SCHEMA = """
CREATE TABLE IF NOT EXISTS "states" (
    state_code       TEXT PRIMARY KEY,
    state_code_std   TEXT,
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

CREATE TABLE IF NOT EXISTS elections (
    election_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    states          TEXT NOT NULL,
    sort_date       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parties (
    abv              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    aliases          TEXT DEFAULT '',
    chief            TEXT,
    colour           TEXT,
    founded          INTEGER,
    symbol_url       TEXT,
    seats_loksabha   INTEGER DEFAULT 0,
    seats_rajyasabha INTEGER DEFAULT 0,
    seats_assembly   INTEGER DEFAULT 0,
    wikipedia_url    TEXT,
    alliance         TEXT
);

CREATE TABLE IF NOT EXISTS constituency_status (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    current_round   INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    won             INTEGER DEFAULT 0,
    form20_url      TEXT,
    form20_status   TEXT    NOT NULL DEFAULT 'UNAVAILABLE',
    form20_score    INTEGER DEFAULT NULL,
    form20_checked_at TEXT DEFAULT NULL,
    PRIMARY KEY (state_code, ac_no)
);

CREATE TABLE IF NOT EXISTS "rounds_ac" (
    state_code      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    election_id     TEXT    NOT NULL DEFAULT '',
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, ac_no, election_id, round_no, candidate, party_abv)
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

CREATE TABLE IF NOT EXISTS candidates_search (
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    context        TEXT DEFAULT '',
    boost          REAL DEFAULT 1.0,
    votes          INTEGER DEFAULT 0,
    total_votes    INTEGER DEFAULT 0,
    election_sort  TEXT DEFAULT '',
    symbol_url     TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cs_ac_name ON constituency_status (ac_name);
CREATE INDEX IF NOT EXISTS idx_cs_state_status ON constituency_status (state_code, status);
CREATE INDEX IF NOT EXISTS idx_rounds_ac_lookup ON rounds_ac (state_code, ac_no, round_no);
CREATE INDEX IF NOT EXISTS idx_rounds_ac_state ON rounds_ac (state_code, round_no);
CREATE INDEX IF NOT EXISTS idx_rounds_candidate ON rounds_ac (candidate);
CREATE INDEX IF NOT EXISTS idx_rounds_election ON rounds_ac (election_id);
"""


def run(cmd, timeout=120):
    """Run a shell command, return (stdout, returncode)."""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.returncode


def d1_count(table):
    """Get row count from D1."""
    out, rc = run(
        f'wrangler d1 execute {D1_DB} --remote --json '
        f'--command "SELECT COUNT(*) as c FROM {table};"'
    )
    if rc != 0:
        return None
    import json
    data = json.loads(out)
    return data[0]["results"][0]["c"]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync D1 to local SQLite")
    parser.add_argument("--db", default="data/india-votes-data.db",
                        help="Local SQLite path (default: data/india-votes-data.db)")
    parser.add_argument("--keep-export", action="store_true",
                        help="Keep temp SQL files (for debugging)")
    args = parser.parse_args()

    db_path = args.db
    tmpdir = tempfile.mkdtemp(prefix="d1_sync_")

    print(f"D1 database: {D1_DB}")
    print(f"Local target: {db_path}")
    print(f"Temp dir: {tmpdir}")
    print()

    # ── Phase 1: Export from D1 ──
    print("Phase 1: Exporting from D1...")
    t0 = time.time()
    for tbl in TABLES:
        sql_path = os.path.join(tmpdir, f"{tbl}.sql")
        print(f"  {tbl}...", end=" ", flush=True)
        _, rc = run(
            f"wrangler d1 export {D1_DB} --remote "
            f"--output {sql_path} --table {tbl} --no-schema",
            timeout=180,
        )
        if rc != 0:
            print(f"FAILED (exit {rc})")
            sys.exit(1)
        size_kb = os.path.getsize(sql_path) / 1024
        print(f"{size_kb:.0f} KB")
    export_time = time.time() - t0
    print(f"  Export done in {export_time:.1f}s\n")

    # ── Phase 2: Create local SQLite ──
    print("Phase 2: Creating local SQLite...")
    if os.path.exists(db_path):
        # Remove old DB and its WAL/SHM
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            if os.path.exists(p):
                os.remove(p)
                print(f"  Removed {p}")

    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()
    print(f"  Schema created: {db_path}\n")

    # ── Phase 3: Import SQL files ──
    print("Phase 3: Importing data...")
    t0 = time.time()
    for tbl in TABLES:
        sql_path = os.path.join(tmpdir, f"{tbl}.sql")
        print(f"  {tbl}...", end=" ", flush=True)
        _, rc = run(f"sqlite3 {db_path} < {sql_path}", timeout=300)
        if rc != 0:
            print(f"FAILED (exit {rc})")
            sys.exit(1)
        conn = sqlite3.connect(db_path)
        cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        conn.close()
        print(f"{cnt} rows")
    import_time = time.time() - t0
    print(f"  Import done in {import_time:.1f}s\n")

    # ── Phase 4: Verify ──
    print("Phase 4: Verifying row counts match D1...")
    all_ok = True
    for tbl in TABLES:
        conn = sqlite3.connect(db_path)
        local_cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        conn.close()
        d1_cnt = d1_count(tbl)
        match = "OK" if local_cnt == d1_cnt else "MISMATCH"
        if match == "MISMATCH":
            all_ok = False
        print(f"  {tbl:25s} local={local_cnt:>8}  d1={d1_cnt or '?':>8}  {match}")

    print()
    if all_ok:
        total_time = export_time + import_time
        print(f"Sync complete: {db_path} ({total_time:.1f}s total)")
    else:
        print("ERROR: Row count mismatches detected!")
        sys.exit(1)

    # Cleanup
    if not args.keep_export:
        import shutil
        shutil.rmtree(tmpdir)
        print(f"Cleaned up {tmpdir}")


if __name__ == "__main__":
    main()
