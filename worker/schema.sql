-- D1 Schema for LET Live! Election Dashboard
-- Mirrors the existing SQLite schema from db_utils.py
-- FTS5 tables added in Phase 3

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
    symbol_url       TEXT,
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

CREATE TABLE IF NOT EXISTS elections (
    election_id     TEXT PRIMARY KEY,                     -- eg: "AC-2024-10"
    name            TEXT NOT NULL,                        -- "AC 2024 AS/BR/KL"
    states          TEXT NOT NULL,                        -- JSON array of state codes
    sort_date       TEXT NOT NULL                         -- "2024-10" for sorting
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
    form20_checked_at TEXT  DEFAULT NULL,
    PRIMARY KEY (state_code, ac_no)
);

-- Query-performance indexes
-- These cover the main access patterns in server.py endpoints

-- seat-tally: latest round per AC, then winner lookup
CREATE INDEX IF NOT EXISTS idx_rounds_ac_lookup
    ON rounds_ac (state_code, ac_no, round_no);

-- ac-races: filter by state, get rounds
CREATE INDEX IF NOT EXISTS idx_rounds_ac_state
    ON rounds_ac (state_code, round_no);

-- status endpoint: filter by state + status
CREATE INDEX IF NOT EXISTS idx_cs_state_status
    ON constituency_status (state_code, status);

-- constituency detail: fast PK lookup already covered, but AC name search
CREATE INDEX IF NOT EXISTS idx_cs_ac_name
    ON constituency_status (ac_name);

-- candidate history search: lookup by candidate name
CREATE INDEX IF NOT EXISTS idx_rounds_candidate
    ON rounds_ac (candidate);

-- election filtering: lookup by election_id
CREATE INDEX IF NOT EXISTS idx_rounds_election
    ON rounds_ac (election_id);

-- ============================================================
-- Cost emergency fixes (2026-06-10)
-- ============================================================

-- Fix 1: Materialised latest-round-per-AC summary
-- Eliminates ~60B row reads from GROUP BY on every request
CREATE TABLE IF NOT EXISTS latest_rounds_ac (
  state_code TEXT NOT NULL,
  ac_no      INTEGER NOT NULL,
  max_round  INTEGER NOT NULL,
  PRIMARY KEY (state_code, ac_no)
);

-- Fix 2: Missing indexes
-- constituency_status(state_code) — speeds up status endpoint GROUP BY
CREATE INDEX IF NOT EXISTS idx_constituency_status_state
  ON constituency_status(state_code);

-- rounds_ac(state_code, ac_no, round_no DESC) — speeds up latest_round lookups
CREATE INDEX IF NOT EXISTS idx_rounds_ac_state_ac_round_desc
  ON rounds_ac(state_code, ac_no, round_no DESC);

-- rounds_pc equivalents
CREATE INDEX IF NOT EXISTS idx_rounds_pc_state_pc_round
  ON rounds_pc(state_code, pc_no, round_no DESC);
