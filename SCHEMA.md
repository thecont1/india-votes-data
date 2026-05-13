# Database Schema

India Votes Data uses a normalized SQLite or PostgreSQL database. The schema is managed by `db_utils.py` and is version-agnostic — the same DDL works on both backends.

## Tables

### states

Reference table for states and union territories. ECI codes are the primary key because every other table uses them.

| Column | Type | Description |
|--------|------|-------------|
| `state_code` | TEXT PK | ECI code: S03, S11, U07 |
| `state_code_std` | TEXT | Standard code: AS, KL, DL |
| `state_name` | TEXT | Full name |
| `state_capital` | TEXT | Capital city |
| `state_status` | TEXT | State or UT |
| `population_2011` | INTEGER | Census 2011 |
| `region` | TEXT | Geographic region |
| `districts` | INTEGER | Number of districts |
| `assembly_seats` | INTEGER | Assembly constituencies |
| `loksabha_seats` | INTEGER | Lok Sabha constituencies |
| `rajyasabha_seats` | INTEGER | Rajya Sabha seats |

**36 rows** (28 states + 8 UTs).

### parties

Party metadata with normalization support. The `aliases` column holds variant spellings as comma-separated values.

| Column | Type | Description |
|--------|------|-------------|
| `abv` | TEXT PK | Canonical party name |
| `name` | TEXT | Full party name |
| `aliases` | TEXT | Comma-separated variant names |
| `chief` | TEXT | Party leader |
| `colour` | TEXT | Hex colour for charts |
| `founded` | INTEGER | Year founded |
| `symbol_name` | TEXT | Election symbol name |
| `symbol_emoji` | TEXT | Emoji representation |
| `seats_loksabha` | INTEGER | Current Lok Sabha seats |
| `seats_rajyasabha` | INTEGER | Current Rajya Sabha seats |
| `seats_assembly` | INTEGER | Current assembly seats |
| `wikipedia_url` | TEXT | Wikipedia link |
| `alliance` | TEXT | Alliance/coalition name |

**196 rows** (30 from ECI CSV + 166 auto-generated from scraped data).

**Party normalization chain**: exact match → case-insensitive → aliases → raw name. Built into `_normalize_party()`.

### rounds_ac

Vote counts per candidate per counting round. This is the core data table. The composite primary key prevents duplicate inserts.

| Column | Type | Description |
|--------|------|-------------|
| `state_code` | TEXT | ECI state code |
| `ac_no` | INTEGER | Assembly constituency number |
| `ac_name` | TEXT | Constituency name |
| `round_no` | INTEGER | Counting round (time axis) |
| `candidate` | TEXT | Candidate name |
| `party_abv` | TEXT | Party (FK → parties.abv) |
| `votes` | INTEGER | Vote count |

**Primary key**: `(state_code, ac_no, round_no, candidate, party_abv)`

**209,171 rows** (May 2026 elections: Assam, Kerala, Puducherry, Tamil Nadu, West Bengal).

**Design decisions**:
- `round_no` IS the time axis. Once declared, a round's results don't change. New rounds (including bye-elections) are just new rows with higher round_no.
- No `id` column — the composite PK is the natural key.
- No `election_type` — the table name tells you it's Assembly data.
- No `scraped_at` — `round_no` is the temporal ordering.

### rounds_pc

Reserved for Parliamentary Constituency (Lok Sabha) results. Same schema shape as `rounds_ac` but with `pc_no` instead of `ac_no`.

| Column | Type | Description |
|--------|------|-------------|
| `state_code` | TEXT | ECI state code |
| `pc_no` | INTEGER | Parliamentary constituency number |
| `pc_name` | TEXT | Constituency name |
| `round_no` | INTEGER | Counting round |
| `candidate` | TEXT | Candidate name |
| `party_abv` | TEXT | Party (FK → parties.abv) |
| `votes` | INTEGER | Vote count |

**Primary key**: `(state_code, pc_no, round_no, candidate, party_abv)`

**0 rows** (schema ready, data collection pending).

### constituency_status

Lifecycle tracking per Assembly Constituency. Updated by the scraper as counting progresses.

| Column | Type | Description |
|--------|------|-------------|
| `state_code` | TEXT | ECI state code |
| `ac_no` | INTEGER | Constituency number |
| `ac_name` | TEXT | Constituency name |
| `status` | TEXT | PENDING / LIVE / DONE / ERROR |
| `current_round` | INTEGER | Latest round scraped |
| `total_rounds` | INTEGER | Total rounds expected |
| `error_count` | INTEGER | Consecutive errors |
| `won` | INTEGER | 1 if ECI confirms winner |

**Primary key**: `(state_code, ac_no)`

**4,123 rows** (all ACs across 36 states/UTs, initially PENDING).

## Relationships

```
states.state_code ←── rounds_ac.state_code
states.state_code ←── rounds_pc.state_code
states.state_code ←── constituency_status.state_code
parties.abv       ←── rounds_ac.party_abv
parties.abv       ←── rounds_pc.party_abv
```

## ECI State Codes

The ECI uses its own coding scheme for states:

| ECI Code | Standard | State |
|----------|----------|-------|
| S01 | AP | Andhra Pradesh |
| S03 | AS | Assam |
| S11 | KL | Kerala |
| S22 | TN | Tamil Nadu |
| S25 | WB | West Bengal |
| U07 | PY | Puducherry |

Full mapping in `data/states.csv`.

## Bye-Elections

Handled naturally by the `round_no` time axis:

1. General election: rounds 1-7, candidate X wins (`won=1`)
2. Bye-election: round 8, candidate Y wins (`won=1` for Y, `won=0` for X)
3. `constituency_status.current_round` auto-updates via `GREATEST()`
4. History preserved — X's data remains in round 7, Y's in round 8

No schema changes needed. The scraper writes new rows; the dashboard reads the latest.

## Backends

Switch via `DATABASE_URL`:

```bash
# SQLite
DATABASE_URL="data/election_results.db"

# PostgreSQL
DATABASE_URL="postgresql://localhost:5432/election_results"
```

`db_utils.py` auto-detects the backend via `_IS_PG` flag. SQL uses `?` (SQLite) or `%s` (PostgreSQL) via `_placeholder()`.
