# Schema Optimization Plan (AC Only)

## Goal

Clean up the SQLite schema: drop unused columns, fix naming, enforce proper PKs, eliminate redundancy. PC tables deferred to a separate effort.

## Changes

### 1. states table — ECI code as PK

```sql
CREATE TABLE states (
    state_code       TEXT PRIMARY KEY,  -- ECI code: S03, S11, U07
    state_code_std   TEXT,              -- standard: AS, KL, DL
    state_name       TEXT NOT NULL,
    ...other columns unchanged...
);
```

- `state_code` (currently standard: AS, KL) → renamed to `state_code_std`
- `state_code_eci` (currently: S03, S11) → becomes the PK `state_code`
- Eliminates every JOIN needing `r.state_code = s.state_code_eci`

### 2. rounds table — composite PK, drop id

```sql
CREATE TABLE rounds (
    state_code      TEXT    NOT NULL,  -- ECI code
    ac_no           INTEGER NOT NULL,
    ac_name         TEXT,
    round_no        INTEGER NOT NULL,
    candidate       TEXT    NOT NULL,
    party_abv       TEXT    NOT NULL,  -- FK → parties.abv
    votes           INTEGER NOT NULL,
    PRIMARY KEY (state_code, ac_no, round_no, candidate, party_abv)
);
```

- Drop `id` (AUTOINCREMENT) — never referenced in any query
- Drop `election_type` — always 'AC', table name tells you
- Rename `party` → `party_abv` — explicit FK to parties.abv
- Composite PK prevents the 13 existing duplicate rows
- `sqlite_sequence` disappears automatically

### 3. constituency_status — no schema change

Already correct: `PRIMARY KEY (state_code, ac_no)` with ECI codes.

### 4. parties — no schema change

Already correct: `abv` PK with aliases column.

## Migration Strategy

1. Create new tables with correct schema
2. INSERT...SELECT from old tables (transforming column names)
3. Drop old tables
4. Rename new tables
5. Recreate indexes

The 13 duplicate rows (same 5-col key) will be silently dropped by the composite PK.

## Files to Change

| File | Changes |
|------|---------|
| db_utils.py | Schema DDL, all queries: `party` → `party_abv`, `state_code_eci` → direct use, drop `election_type` |
| core/scraper.py | `insert_round_snapshot()` column names |
| core/output.py | CSV/JSON field names |
| cli.py | `election_type` parameter removed from write_to_db() |
| server.py | Query column names |
| eci-live-scraper.py | Query column names |
| dashboard.py | Query column names |
| migrate_v2.py | Schema DDL (for reference) |

## Verification

1. `sqlite3 data/election_results.db ".schema"` — confirm new DDL
2. `SELECT COUNT(*) FROM rounds` — should be ~209,171 (13 dupes removed)
3. `PRAGMA table_info(rounds)` — no `id` column
4. `SELECT name FROM sqlite_master WHERE type='table'` — no sqlite_sequence
5. All Python files pass `ast.parse()` syntax check
6. All db_utils functions return identical results
