# Execution Plan: SQLite → PostgreSQL + Parquet Export

## Goal

Migrate the election scraper's storage layer from SQLite to PostgreSQL
for crash safety during power cuts, then export Parquet files for the
DuckDB-WASM dashboard.

## Current State

- Branch: `live-tracker`
- DB: SQLite (`data/live_results.db`) via WAL mode
- Schema: 3 tables (`rounds`, `constituency_status`, `scrape_cycles`)
- Files using SQLite: `db_utils.py`, `eci-ResultsDayLiveClient.py`,
  `eci-live-scraper.py`, `dashboard.py`
- Dependencies: `psycopg2-binary` and `duckdb` already in pyproject.toml
- PostgreSQL: NOT installed yet

## SQLite → PostgreSQL Translation Table

| SQLite                          | PostgreSQL                                    |
|---------------------------------|-----------------------------------------------|
| `PRAGMA journal_mode=WAL`       | Not needed (PG has WAL by default)            |
| `PRAGMA busy_timeout=60000`     | Not needed (PG handles locking)              |
| `sqlite3.connect(path)`         | `psycopg2.connect(DATABASE_URL)`             |
| `conn.row_factory = sqlite3.Row`| `RealDictCursor` from psycopg2.extras         |
| `conn.executescript(sql)`       | Split on `;` and execute each statement       |
| `INSERT OR IGNORE`              | `INSERT ... ON CONFLICT DO NOTHING`           |
| `INSERT OR REPLACE`             | `INSERT ... ON CONFLICT DO UPDATE SET ...`    |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY`                     |
| `sqlite3.OperationalError`      | `psycopg2.errors.SerializationFailure` or    |
|                                 | `psycopg2.OperationalError`                  |
| `?` placeholders                | `%s` placeholders                            |
| `TEXT` columns                  | `TEXT` (same, or `VARCHAR`)                   |
| `INTEGER` columns               | `INTEGER` (same)                              |

---

## Phase 1: Install PostgreSQL on Home Server

### Step 1.1: Install PostgreSQL 16

```bash
brew install postgresql@16
brew services start postgresql@16
```

### Step 1.2: Create database and user

```bash
createdb election_results
# Verify
psql election_results -c "SELECT version();"
```

### Step 1.3: Set up DATABASE_URL

Add to `.env` or shell profile:

```
DATABASE_URL=postgresql://localhost:5432/election_results
```

For local dev, trust auth works (no password needed for local socket).
For network access (scraper clients), set password auth:

```sql
ALTER USER <your_mac_user> WITH PASSWORD 'secure_password';
```

### Step 1.4: Run schema migration

Create `migrate_to_pg.py` (one-time script):

```python
#!/usr/bin/env python3
"""One-time migration: create PostgreSQL schema + import existing SQLite data."""
import os
import sqlite3 as sq
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ["DATABASE_URL"]
SQLITE_PATH = "data/live_results.db"

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id              SERIAL PRIMARY KEY,
    state_code      TEXT    NOT NULL,
    state_name      TEXT    NOT NULL,
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    total_rounds    INTEGER,
    candidate       TEXT    NOT NULL,
    party           TEXT    NOT NULL,
    votes           INTEGER NOT NULL,
    scraped_at      TIMESTAMPTZ NOT NULL
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

def migrate():
    pg = psycopg2.connect(DATABASE_URL)
    cur = pg.cursor()

    # Create schema
    for stmt in PG_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    pg.commit()

    # Import from SQLite if it exists
    if os.path.exists(SQLITE_PATH):
        sq_conn = sq.connect(SQLITE_PATH)
        sq_conn.row_factory = sq.Row

        for table in ["rounds", "constituency_status", "scrape_cycles"]:
            rows = sq_conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            cols = rows[0].keys()
            placeholders = ",".join(["%s"] * len(cols))
            col_names = ",".join(cols)
            insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
            for row in rows:
                cur.execute(insert_sql, [row[c] for c in cols])
            pg.commit()
            print(f"  Imported {len(rows)} rows into {table}")

        sq_conn.close()

    pg.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()
```

---

## Phase 2: Migrate db_utils.py to PostgreSQL

**File:** `db_utils.py` (553 lines)

This is the core change. Every function in db_utils.py uses SQLite.

### Step 2.1: Replace imports and connection helper

**Before (lines 1, 12, 117-122):**
```python
"""...Uses Python's built-in sqlite3 — no external DB server needed...."""
import sqlite3
...
def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
```

**After:**
```python
"""...PostgreSQL database layer for ECI Live Election Tracker...."""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/election_results"
)

def _connect():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn

def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)
```

### Step 2.2: Update init_db()

**Before (lines 129-147):** Uses `conn.executescript()`, `sqlite3.OperationalError`,
`INSERT OR IGNORE`.

**After:**
```python
def init_db():
    """Create tables and seed constituency_status for all ACs (idempotent)."""
    conn = _connect()
    cur = _cursor(conn)
    try:
        for stmt in _PG_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        # Seed constituency_status
        for state in STATES:
            for ac_no in range(1, state["ac_count"] + 1):
                cur.execute(
                    """INSERT INTO constituency_status (state_code, state_name, ac_no, status)
                       VALUES (%s, %s, %s, 'PENDING')
                       ON CONFLICT (state_code, ac_no) DO NOTHING""",
                    (state["code"], state["name"], ac_no),
                )
        conn.commit()
    finally:
        conn.close()
```

### Step 2.3: Update all write functions

Key pattern changes across all functions:
- Remove `db_path` parameter (use `DATABASE_URL` global)
- `conn = _connect()` + `cur = _cursor(conn)` instead of `conn = _connect(db_path)`
- `cur.execute(sql, params)` with `%s` instead of `?`
- `conn.commit()` stays the same
- `conn.close()` stays the same

**Specific functions to update:**

| Function | Line | Changes |
|----------|------|---------|
| `get_work_queue()` | 154 | Remove `db_path` param, use `_cursor()` |
| `get_error_constituencies()` | 177 | Same |
| `upsert_constituency_status()` | 198 | Remove `db_path`, change `INSERT ... ON CONFLICT` syntax |
| `insert_round_snapshot()` | 228 | Remove `db_path`, use `executemany` with psycopg2 |
| `record_cycle()` | 264 | Remove `db_path` |
| `update_won_status()` | 287 | Remove `db_path`, fix placeholder syntax |
| `get_status_summary()` | 316 | Remove `db_path`, use `_cursor()` |
| `get_state_status_summary()` | 335 | Same |
| `get_leading_seats()` | 352 | Remove `db_path`, use `pd.read_sql_query` with psycopg2 conn |
| `get_party_seat_tally()` | 390 | Same |
| `get_party_seat_tally_won_leading()` | 404 | Same |
| `get_party_totals_over_time()` | 469 | Same |
| `get_constituency_rounds()` | 499 | Same |
| `get_all_constituency_statuses()` | 522 | Same |
| `get_scrape_cycles()` | 534 | Same |
| `get_last_scrape_time()` | 546 | Same |

### Step 2.4: Update SQL syntax

**`?` → `%s`:**
```python
# Before
cur.execute("SELECT * FROM rounds WHERE state_code = ?", (state_code,))
# After
cur.execute("SELECT * FROM rounds WHERE state_code = %s", (state_code,))
```

**`INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`:**
```python
# Before (line 75-78)
INSERT OR IGNORE INTO constituency_status (state_code, state_name, ac_no, status)
VALUES (?, ?, ?, 'PENDING')
# After
INSERT INTO constituency_status (state_code, state_name, ac_no, status)
VALUES (%s, %s, %s, 'PENDING')
ON CONFLICT (state_code, ac_no) DO NOTHING
```

**`INSERT OR REPLACE` → `ON CONFLICT DO UPDATE`:**
```python
# Before (eci-ResultsDayLiveClient.py line 174)
INSERT OR REPLACE INTO rounds (...) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
# After
INSERT INTO rounds (...) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (state_code, ac_no, round_no, candidate_number)
DO UPDATE SET votes = EXCLUDED.votes
```

### Step 2.5: Update pd.read_sql_query calls

`pd.read_sql_query(query, conn)` works with psycopg2 connections natively.
No change needed for the call itself — just ensure `conn` is a psycopg2
connection and not closed prematurely.

**Important:** Some functions open a connection, query, then open ANOTHER
connection before closing the first. Consolidate to single connection per
function call.

---

## Phase 3: Migrate eci-ResultsDayLiveClient.py

**File:** `eci-ResultsDayLiveClient.py` (418 lines)

This file has its OWN SQLite code (lines 23, 34-35, 41-43, 50-103, 158-199)
that duplicates what's in db_utils.py. It uses `INSERT OR REPLACE` directly.

### Step 3.1: Remove duplicate SQLite code

Delete:
- `import sqlite3` (line 23)
- `DB_PATH`, `TEST_DB_PATH` (lines 34-35)
- `USE_TEST_DB`, `get_db_path()` (lines 38-43)
- `db_lock` (line 47)
- `init_database()` (lines 50-68)
- `get_db_connection()` (lines 71-77)
- `execute_with_retry()` (lines 80-91)
- `commit_with_retry()` (lines 93-103)

### Step 3.2: Rewrite process_ac() to use db_utils

The `process_ac()` function (lines 106-204) has inline SQLite writes.
Replace with calls to `db_utils.insert_round_snapshot()`.

**Before (lines 158-199):**
```python
with db_lock:
    conn = get_db_connection()
    cursor = conn.cursor()
    candidate_map = {}
    for round_info in rounds:
        ...
        execute_with_retry(cursor, "INSERT OR REPLACE INTO rounds ...")
    ...
    execute_with_retry(cursor, "INSERT OR REPLACE INTO rounds ...")  # postal
    commit_with_retry(conn)
    conn.close()
```

**After:**
```python
from db_utils import insert_round_snapshot
from datetime import datetime, timezone

# Build candidates list for insert_round_snapshot
all_candidates = []
for round_info in rounds:
    for c in round_info["tally"]:
        all_candidates.append({
            "candidate": c.get("candidate", ""),
            "party": c.get("party", ""),
            "votes": int(c.get("total", 0)),
            "round_no": round_info["round"],
        })

# Postal votes as round 999
for c in postal_votes:
    all_candidates.append({
        "candidate": c.get("candidate", ""),
        "party": c.get("party", ""),
        "votes": int(c.get("evm_votes", 0)) + int(c.get("postal_votes", 0)),
        "round_no": 999,
    })

# Insert each round separately (different round_no values)
for round_no in set(c["round_no"] for c in all_candidates):
    candidates_for_round = [c for c in all_candidates if c["round_no"] == round_no]
    insert_round_snapshot(
        state_code=state_code,
        state_name="",  # will be filled by db_utils
        ac_no=ac_no,
        ac_name=ac_name,
        round_no=round_no,
        total_rounds=None,
        candidates=candidates_for_round,
        scraped_at=datetime.now(timezone.utc).isoformat(),
    )
```

### Step 3.3: Remove --test-db flag

The test-db flag is SQLite-specific. PostgreSQL uses separate databases
or schemas for testing. Remove `--test-db` from argparse and related code.

### Step 3.4: Remove threading lock

`db_lock = threading.Lock()` was needed for SQLite's single-writer model.
PostgreSQL handles concurrent writes natively. Remove the lock.

---

## Phase 4: Migrate eci-live-scraper.py

**File:** `eci-live-scraper.py` (694 lines)

This file already uses `db_utils` functions (lines 49-56), so it's
mostly covered by Phase 2. Only direct SQLite references to fix:

### Step 4.1: Remove direct sqlite3 import and DB_PATH

**Before (lines 15, 63):**
```python
import sqlite3
...
DB_PATH = "data/live_results.db"
```

**After:** Delete both lines. All DB access goes through db_utils.

### Step 4.2: Update any direct sqlite3 usage

Grep shows `sqlite3.connect(DB_PATH, timeout=30)` at line 520.
Replace with db_utils function call.

---

## Phase 5: Migrate dashboard.py

**File:** `dashboard.py` (908 lines)

### Step 5.1: Remove sqlite3 import

**Before (line 8):**
```python
import sqlite3 as _sqlite3
```

**After:** Delete. Dashboard reads via db_utils functions which now use psycopg2.

### Step 5.2: Remove DB_PATH constant

**Before (line 37):**
```python
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "live_results.db")
```

**After:** Delete. Functions in db_utils use DATABASE_URL.

### Step 5.3: Update direct SQLite usage at line 660

```python
# Before
conn = _sqlite3.connect(db_path, timeout=30)
conn.row_factory = _sqlite3.Row
```

Replace with psycopg2 connection or db_utils query function.

### Step 5.4: Update all function calls

Every `db_utils.xyz(DB_PATH, ...)` becomes `db_utils.xyz(...)` (no path arg).

---

## Phase 6: Parquet Export Job

### Step 6.1: Create `export_parquet.py`

```python
#!/usr/bin/env python3
"""
Periodically exports PostgreSQL rounds table to compressed Parquet.
Runs as a background process or cron job.
"""
import os
import time
import duckdb

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/election_results"
)
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "parquet")

def export_snapshot(output_dir: str = EXPORT_DIR) -> str:
    """Export current state to compressed Parquet. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "rounds_latest.parquet")

    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT * FROM postgres_scan('{DATABASE_URL}', 'rounds')
        ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'ZSTD')
    """)
    con.close()

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Exported {output_path} ({size_mb:.1f} MB)")
    return output_path


def export_loop(interval: int = 60):
    """Export every N seconds."""
    while True:
        try:
            export_snapshot()
        except Exception as e:
            print(f"Export error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Export once and exit")
    parser.add_argument("--interval", type=int, default=60, help="Export interval in seconds")
    args = parser.parse_args()

    if args.once:
        export_snapshot()
    else:
        export_loop(args.interval)
```

### Step 6.2: Upload to Cloudflare R2

Create `upload_r2.py`:

```python
#!/usr/bin/env python3
"""Upload Parquet files to Cloudflare R2 via boto3 S3-compatible API."""
import boto3
import os

R2_ENDPOINT = os.environ["R2_ENDPOINT"]          # e.g. https://<account_id>.r2.cloudflarestorage.com
R2_ACCESS_KEY = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ.get("R2_BUCKET", "election-data")

def upload_parquet(local_path: str, remote_key: str = "rounds_latest.parquet"):
    s3 = boto3.client("s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
    )
    s3.upload_file(local_path, R2_BUCKET, remote_key,
                   ExtraArgs={"ContentType": "application/octet-stream"})
    print(f"Uploaded {local_path} → r2://{R2_BUCKET}/{remote_key}")
```

---

## Phase 7: Verify Everything Works

### Step 7.1: Test PostgreSQL connection

```bash
source .venv/bin/activate
DATABASE_URL=postgresql://localhost:5432/election_results python -c "
import psycopg2
conn = psycopg2.connect('postgresql://localhost:5432/election_results')
print('Connected:', conn.server_version)
conn.close()
"
```

### Step 7.2: Run schema migration

```bash
DATABASE_URL=postgresql://localhost:5432/election_results python migrate_to_pg.py
```

### Step 7.3: Test scraper → PostgreSQL

```bash
DATABASE_URL=postgresql://localhost:5432/election_results \
  python eci-ResultsDayLiveClient.py \
    --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S03.htm" \
    --only-ac 1
```

Verify:
```bash
psql election_results -c "SELECT * FROM rounds LIMIT 5;"
```

### Step 7.4: Test Parquet export

```bash
DATABASE_URL=postgresql://localhost:5432/election_results \
  python export_parquet.py --once
```

Verify:
```bash
python -c "
import duckdb
con = duckdb.connect()
print(con.execute(\"SELECT COUNT(*) FROM 'data/parquet/rounds_latest.parquet'\").fetchone())
con.close()
"
```

### Step 7.5: Power-cut recovery test

```bash
# Start scraper, kill mid-write, restart, verify data
DATABASE_URL=postgresql://localhost:5432/election_results \
  python eci-ResultsDayLiveClient.py --url ... --only-ac 1 &
SCRAPER_PID=$!
sleep 5
kill -9 $SCRAPER_PID  # simulate crash
# Verify no corruption
psql election_results -c "SELECT COUNT(*) FROM rounds;"
# Restart and verify it picks up cleanly
DATABASE_URL=postgresql://localhost:5432/election_results \
  python eci-ResultsDayLiveClient.py --url ... --only-ac 1
```

### Step 7.6: Concurrent writers test

```bash
# Run two state scrapers simultaneously against same PostgreSQL
DATABASE_URL=postgresql://localhost:5432/election_results \
  python eci-ResultsDayLiveClient.py --url ...PY... --no-server &
DATABASE_URL=postgresql://localhost:5432/election_results \
  python eci-ResultsDayLiveClient.py --url ...KL... --no-server &
wait
psql election_results -c "SELECT state_code, COUNT(*) FROM rounds GROUP BY state_code;"
```

---

## Files Likely to Change

| File | Change | Phase |
|------|--------|-------|
| `migrate_to_pg.py` | **New** — one-time migration script | 1 |
| `db_utils.py` | **Major** — sqlite3 → psycopg2 throughout | 2 |
| `eci-ResultsDayLiveClient.py` | **Major** — remove duplicate SQLite code, use db_utils | 3 |
| `eci-live-scraper.py` | **Minor** — remove sqlite3 import, DB_PATH | 4 |
| `dashboard.py` | **Minor** — remove sqlite3 import, DB_PATH | 5 |
| `export_parquet.py` | **New** — Parquet export job | 6 |
| `upload_r2.py` | **New** — R2 upload helper | 6 |
| `.env` | **New** — DATABASE_URL config | 1 |
| `pyproject.toml` | No change (psycopg2-binary already listed) | — |

## Risks and Tradeoffs

| Risk | Mitigation |
|------|------------|
| PostgreSQL not installed | Phase 1 handles this; fallback to Neon cloud PG |
| Dashboard queries break | db_utils is the single abstraction layer; test each function |
| Concurrent writer deadlocks | PG handles this natively; no app-level locking needed |
| Power cut during export | Export reads committed data only; partial export is harmless |
| R2 credentials not set | upload_r2.py is optional; Parquet can be served locally first |

## Open Questions

1. **db_utils API change**: Removing `db_path` from all functions changes the
   public API. Every caller needs updating. Acceptable since we control all
   callers (eci-live-scraper.py, dashboard.py).

2. **scraped_at type**: SQLite stores as TEXT (ISO-8601), PG uses TIMESTAMPTZ.
   All ISO-8601 strings parse correctly in PG. No data migration issue.

3. **Testing approach**: Run migration, then run scraper on 1 AC, verify
   in psql. No unit tests needed for this phase — it's infrastructure.
