#!/usr/bin/env python3
"""
Load local JSON election results into Cloudflare D1.

Reads a JSON file (e.g. data/json/2023Assembly-KA.json), transforms
the data to match the D1 schema, and POSTs to the ingestion Worker.

Usage:
    python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json
    python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json --preprocess
    python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json --resume

Requires D1_INGEST_URL and D1_INGEST_TOKEN env vars (or .env).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime

from dotenv import load_dotenv

# Add project root to path for db imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "states.csv")
PARTIES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "parties.csv")

MONTH_MAP = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Party names that the DB parties table may not have
HARDCODED_PARTIES = {
    "Janata Dal  (Secular)": "JD(S)",
    "Janata Dal (Secular)": "JD(S)",
    "Janata Dal  (United)": "JD(U)",
    "Janata Dal (United)": "JD(U)",
    "None of the Above": "NOTA",
    "Bharat Rashtra Samithi": "BRS",
    "Shiv Sena (Uddhav Balasaheb Thackeray)": "SHS(UBT)",
    "Shiv Sena (Uddhav Balasaheb Thackeray)": "SHS(UBT)",
    "Rashtriya Janata Dal": "RJD",
    "Rashtriya Lok Dal": "RLD",
    "Jammu & Kashmir National Conference": "JKNC",
    "Jammu and Kashmir National Conference": "JKNC",
    "Jammu & Kashmir Peoples Democratic Party": "JKPDP",
    "Indian National Lok Dal": "INLD",
    "Haryana Lokhit Party": "HLP",
    "Jannayak Janta Party": "JJP",
    "All India Majlis-E-Ittehadul Muslimeen": "AIMIM",
    "All India Majlis-e-Ittehadul Muslimeen": "AIMIM",
    "Communist Party of India (Marxist-Leninist) (Liberation)": "CPI(ML)(L)",
    "Communist Party of India  (Marxist)": "CPI(M)",
    "Communist Party of India (Marxist)": "CPI(M)",
    "Vikassheel Insaan Party": "VIP",
    "Hindustani Awam Morcha (Secular)": "HAM(S)",
    "Rashtriya Lok Morcha": "RLM",
    "Lok Janshakti Party(Ram Vilas)": "LJP(RV)",
}

DEFAULT_BATCH_SIZE = 80
DEFAULT_WRANGLER_DB = "election-results"


# ---------------------------------------------------------------------------
# State code lookup
# ---------------------------------------------------------------------------

def load_state_map() -> dict:
    """Load {state_code_std: row} from data/states.csv.

    Returns dict like {"KA": {"state_code_eci": "S10", "state_name": "Karnataka", ...}}
    """
    states = {}
    with open(STATES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            states[row["state_code"]] = row
    return states


def lookup_state_code(state_std: str, state_map: dict) -> tuple:
    """Look up ECI state code from standard 2-letter code.

    Returns (eci_code, state_name)
    """
    entry = state_map.get(state_std)
    if not entry:
        raise ValueError(
            f"Unknown state code '{state_std}' in states.csv. "
            f"Known: {', '.join(sorted(state_map.keys()))}"
        )
    return entry["state_code_eci"], entry["state_name"]


# ---------------------------------------------------------------------------
# Party normalization
# ---------------------------------------------------------------------------

_party_cache = None


def _load_party_map() -> dict:
    """Load {full_name: abbreviation} from data/parties.csv."""
    global _party_cache
    if _party_cache is not None:
        return _party_cache

    name_to_abv = {}
    try:
        with open(PARTIES_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                abv = row.get("abv", "").strip()
                name = row.get("name", "").strip()
                if abv and name:
                    name_to_abv[name] = abv
                # Also parse aliases (comma-separated)
                aliases = row.get("aliases", "").strip()
                if aliases:
                    for alias in aliases.split(","):
                        alias = alias.strip()
                        if alias:
                            name_to_abv[alias] = abv
    except FileNotFoundError:
        pass

    _party_cache = name_to_abv
    return name_to_abv


def normalize_party(name: str) -> str:
    """Normalize a party name to its abbreviation.

    Layered approach:
    1. Check if already an abbreviation (in parties.csv abv column)
    2. Check hardcoded map
    3. Check parties.csv name -> abv map
    4. Case-insensitive match
    5. Return input unchanged (caller warns)
    """
    if not name:
        return name

    name = name.strip()

    # 1. Hardcoded map (fast, covers common edge cases)
    if name in HARDCODED_PARTIES:
        return HARDCODED_PARTIES[name]

    # 2. Parties CSV
    party_map = _load_party_map()

    # Direct name match
    if name in party_map:
        return party_map[name]

    # Case-insensitive match
    name_lower = name.lower()
    for full_name, abv in party_map.items():
        if full_name.lower() == name_lower:
            return abv

    # 3. Check if input is already an abbreviation
    known_abvs = set(party_map.values())
    if name in known_abvs:
        return name

    # 4. Not found — return input unchanged
    return name


# ---------------------------------------------------------------------------
# Month extraction from title
# ---------------------------------------------------------------------------

def derive_month_from_title(title: str) -> int:
    """Extract month number from election title.

    e.g. "GENERAL ELECTION TO VIDHAN SABHA TRENDS & RESULT MAY-2023" -> 5
    """
    title_upper = title.upper()
    for month_name, month_num in MONTH_MAP.items():
        if month_name in title_upper:
            return month_num
    return 0


# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------

def validate_json(data: dict) -> list:
    """Validate JSON structure. Returns list of error strings (empty = valid)."""
    errors = []

    required_top = ["election_year", "election_type", "election_state", "constituencywise_results"]
    for key in required_top:
        if key not in data:
            errors.append(f"Missing required key: '{key}'")

    if errors:
        return errors  # Can't continue without top-level keys

    # Validate year
    try:
        int(data["election_year"])
    except (ValueError, TypeError):
        errors.append(f"election_year not a valid integer: '{data['election_year']}'")

    # Validate results list
    results = data.get("constituencywise_results", [])
    if not isinstance(results, list):
        errors.append("constituencywise_results must be a list")
        return errors

    if len(results) == 0:
        errors.append("constituencywise_results is empty")
        return errors

    # Validate each constituency
    seen_ac_nos = set()
    for i, result in enumerate(results):
        prefix = f"constituencywise_results[{i}]"

        vd = result.get("voting_data")
        if not vd:
            errors.append(f"{prefix}: missing 'voting_data'")
            continue

        ac_no_str = vd.get("constituency_no")
        if not ac_no_str:
            errors.append(f"{prefix}: missing 'constituency_no'")
        else:
            try:
                ac_no = int(ac_no_str)
                if ac_no in seen_ac_nos:
                    errors.append(f"{prefix}: duplicate constituency_no {ac_no}")
                seen_ac_nos.add(ac_no)
            except ValueError:
                errors.append(f"{prefix}: constituency_no not integer: '{ac_no_str}'")

        if not vd.get("constituency"):
            errors.append(f"{prefix}: missing 'constituency' name")

        tally = vd.get("voting_tally", [])
        if not isinstance(tally, list) or len(tally) == 0:
            errors.append(f"{prefix}: voting_tally is empty or not a list")
            continue

        for j, entry in enumerate(tally):
            tprefix = f"{prefix}.voting_tally[{j}]"
            for field in ["candidate", "party"]:
                if not entry.get(field):
                    errors.append(f"{tprefix}: missing '{field}'")

            # Validate votes are parseable
            for field in ["evm_votes", "postal_votes"]:
                val = entry.get(field, "")
                try:
                    int(str(val).replace(",", "").strip())
                except ValueError:
                    errors.append(f"{tprefix}: {field} not integer: '{val}'")

    return errors


# ---------------------------------------------------------------------------
# JSON transformation
# ---------------------------------------------------------------------------

def extract_metadata(data: dict, state_map: dict, election_id_override: str | None = None) -> dict:
    """Extract and validate election metadata from JSON.

    election_id is resolved in order:
      1. --election-id CLI flag (explicit override)
      2. election_id field in the JSON file
      3. Error — neither provided
    """
    year = int(data["election_year"])
    state_std = data["election_state"]
    title = data.get("title", f"Assembly Election {year}")

    month = derive_month_from_title(title)

    state_eci, state_name = lookup_state_code(state_std, state_map)

    # Resolve election_id: CLI flag > JSON field > error
    election_id = election_id_override or data.get("election_id")
    if not election_id:
        raise ValueError(
            "No election_id provided. Either:\n"
            "  1. Add \"election_id\": \"AC-YYYY-MM\" to the JSON file, or\n"
            "  2. Pass --election-id AC-YYYY-MM on the command line\n"
            "  Example: --election-id AC-2023-06"
        )

    # Validate format
    import re
    if not re.match(r"^AC-\d{4}-\d{2}$", election_id):
        raise ValueError(
            f"Invalid election_id format: '{election_id}'. "
            f"Expected AC-YYYY-MM (e.g. AC-2023-06)"
        )

    return {
        "election_id": election_id,
        "state_code": state_eci,
        "state_std": state_std,
        "state_name": state_name,
        "year": year,
        "month": month,
        "title": title,
    }


def transform_to_rounds(data: dict, meta: dict) -> tuple:
    """Transform JSON constituencywise_results into D1 round format.

    Returns (rounds, party_warnings)
        rounds: list of round dicts for insert_batch()
        party_warnings: dict {party_name: count} for unrecognized parties
    """
    rounds = []
    party_warnings = Counter()

    for result in data["constituencywise_results"]:
        vd = result["voting_data"]
        ac_no = int(vd["constituency_no"])
        ac_name = vd["constituency"]
        tally = vd["voting_tally"]

        candidates = []
        for entry in tally:
            party_name = entry["party"].strip()
            party_abv = normalize_party(party_name)

            # Track unrecognized parties
            if party_abv == party_name and party_name not in ("NOTA", "Independent"):
                party_warnings[party_name] += 1

            evm = int(str(entry["evm_votes"]).replace(",", "").strip())
            postal = int(str(entry["postal_votes"]).replace(",", "").strip())
            votes = evm + postal

            candidates.append({
                "candidate": entry["candidate"].strip(),
                "party_abv": party_abv,
                "votes": votes,
                "_orig_party": party_name,  # for preprocess display
            })

        rounds.append({
            "state_code": meta["state_code"],
            "election_id": meta["election_id"],
            "ac_no": ac_no,
            "ac_name": ac_name,
            "round_no": 999,  # Final result
            "candidates": candidates,
        })

    return rounds, party_warnings


# ---------------------------------------------------------------------------
# D1 election record
# ---------------------------------------------------------------------------

def ensure_election(meta: dict, wrangler_db: str, preprocess: bool) -> bool:
    """Ensure the elections table has a record for this election.

    If the election already exists, merges the new state into the states array.
    Uses wrangler d1 execute.
    Returns True if successful.
    """
    election_id = meta["election_id"]
    state_code = meta["state_code"]
    sort_date = f"{meta['year']}-{meta['month']:02d}"
    name = meta["title"][:60].replace("'", "''")

    def run_sql(sql):
        if preprocess:
            print(f"  [PREPROCESS] {sql[:120]}...")
            return []
        result = subprocess.run(
            ["wrangler", "d1", "execute", wrangler_db, "--command", sql, "--remote", "--json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(result.stdout) if result.stdout.strip() else []

    try:
        # Check if election already exists
        check_sql = f"SELECT states FROM elections WHERE election_id = '{election_id}';"
        data = run_sql(check_sql)

        if data and data[0].get("results"):
            # Election exists — merge state into states array
            existing_states = json.loads(data[0]["results"][0].get("states", "[]"))
            if state_code not in existing_states:
                existing_states.append(state_code)
                states_json = json.dumps(existing_states).replace("'", "''")
                update_sql = (
                    f"UPDATE elections SET states = '{states_json}' "
                    f"WHERE election_id = '{election_id}';"
                )
                run_sql(update_sql)
                print(f"  Election record: {election_id} updated — states: {existing_states}")
            else:
                print(f"  Election record: {election_id} exists — state {state_code} already included")
        else:
            # New election — insert
            states_json = json.dumps([state_code])
            insert_sql = (
                f"INSERT INTO elections (election_id, name, states, sort_date) "
                f"VALUES ('{election_id}', '{name}', '{states_json}', '{sort_date}');"
            )
            run_sql(insert_sql)
            print(f"  Election record: {election_id} created — states: [{state_code}]")

        return True
    except subprocess.CalledProcessError as e:
        print(f"  ERROR inserting election record: {e.stderr.strip()}")
        return False
    except FileNotFoundError:
        print("  ERROR: 'wrangler' CLI not found. Install with: npm i -g wrangler")
        return False


# ---------------------------------------------------------------------------
# Pre-processing: batching
# ---------------------------------------------------------------------------

def estimate_stmts(round_dict: dict) -> int:
    """Estimate D1 statements for one round (candidates + status + FTS)."""
    n = len(round_dict["candidates"])
    return n + 1 + n  # candidate rows + constituency_status + FTS entries


def build_batches(rounds: list, batch_size: int) -> list:
    """Pre-compute batches from rounds. Pure function, no I/O.

    Returns list of batches, where each batch is a list of round dicts.
    """
    batches = []
    current_batch = []
    current_stmts = 0

    for r in rounds:
        n_stmts = estimate_stmts(r)
        if current_stmts + n_stmts > batch_size and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_stmts = 0
        current_batch.append(r)
        current_stmts += n_stmts

    if current_batch:
        batches.append(current_batch)

    return batches


# ---------------------------------------------------------------------------
# D1 writing (pure I/O, no processing)
# ---------------------------------------------------------------------------

def check_env() -> tuple:
    """Check required env vars. Returns (url, token) or raises."""
    url = os.environ.get("D1_INGEST_URL", "")
    token = os.environ.get("D1_INGEST_TOKEN", "")
    if not url:
        raise RuntimeError(
            "D1_INGEST_URL not set. Add to .env or export it.\n"
            "  e.g. D1_INGEST_URL=https://election-ingest.<your-subdomain>.workers.dev"
        )
    if not token:
        raise RuntimeError("D1_INGEST_TOKEN not set. Add to .env or export it.")
    return url, token


def load_to_d1(batches: list) -> dict:
    """Send pre-built batches to D1 via ingestion Worker.

    Args:
        batches: list of batches (each batch = list of round dicts)

    Returns {"ok": int, "failed": int, "batches": int}
    """
    from db.d1 import insert_batch

    total_constituencies = sum(len(b) for b in batches)
    print(f"  Batches: {len(batches)} ({total_constituencies} constituencies)")

    ok = 0
    failed = 0
    for i, batch in enumerate(batches):
        total_cands = sum(len(r["candidates"]) for r in batch)
        print(f"    Batch {i+1}/{len(batches)}: {len(batch)} ACs, {total_cands} candidates...", end=" ", flush=True)
        try:
            insert_batch(batch)
            print(f"OK")
            ok += len(batch)
        except Exception as e:
            print(f"FAILED: {e}")
            failed += len(batch)

    return {"ok": ok, "failed": failed, "batches": len(batches)}


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def progress_path(json_path: str) -> str:
    return json_path.replace(".json", ".d1-progress.json")


def load_progress(json_path: str) -> dict:
    """Load or initialize progress tracking file."""
    ppath = progress_path(json_path)
    try:
        with open(ppath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"loaded": [], "failed": [], "last_updated": None}


def save_progress(json_path: str, progress: dict) -> None:
    """Save progress tracking file."""
    ppath = progress_path(json_path)
    progress["last_updated"] = datetime.now().isoformat()
    with open(ppath, "w") as f:
        json.dump(progress, f, indent=2)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_load(meta: dict, expected: int, wrangler_db: str, preprocess: bool) -> bool:
    """Verify D1 has the expected constituency count."""
    if preprocess:
        print("  [PREPROCESS] Skipping verification")
        return True

    sql = (
        f"SELECT COUNT(DISTINCT ac_no) as cnt FROM rounds_ac "
        f"WHERE state_code = '{meta['state_code']}' "
        f"AND election_id = '{meta['election_id']}';"
    )

    try:
        result = subprocess.run(
            ["wrangler", "d1", "execute", wrangler_db, "--command", sql, "--remote", "--json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        data = json.loads(result.stdout)
        # wrangler --json returns [{"results": [...]}]
        actual = data[0]["results"][0]["cnt"] if data and data[0].get("results") else 0

        if actual >= expected:
            print(f"  Verified: {actual} constituencies in D1")
            return True
        else:
            print(f"  WARNING: expected {expected}, found {actual} in D1")
            return False
    except Exception as e:
        print(f"  Verification failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Load local JSON election results into Cloudflare D1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json
  python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json --preprocess
  python3 scripts/load-json-to-d1.py data/json/2023Assembly-KA.json --resume
        """,
    )
    parser.add_argument("json_path", help="Path to the JSON results file")
    parser.add_argument("--preprocess", action="store_true", help="Validate and transform without writing to D1")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"D1 batch statement limit (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--election-id",
                        help="Election ID in AC-YYYY-MM format (e.g. AC-2023-06). "
                             "Required if not in JSON file. Multiple states sharing "
                             "a result date use the same ID.")
    parser.add_argument("--resume", action="store_true", help="Skip constituencies already loaded (tracked in .d1-progress.json)")
    parser.add_argument("--skip-election", action="store_true", help="Skip inserting the election record")
    parser.add_argument("--skip-verify", action="store_true", help="Skip post-load verification")
    parser.add_argument("--wrangler-db", default=DEFAULT_WRANGLER_DB, help=f"Wrangler D1 database name (default: {DEFAULT_WRANGLER_DB})")
    args = parser.parse_args()

    # ── Load and validate JSON ──────────────────────────────────────────
    print(f"Loading: {args.json_path}")
    if not os.path.exists(args.json_path):
        print(f"ERROR: File not found: {args.json_path}")
        sys.exit(1)

    with open(args.json_path, encoding="utf-8") as f:
        data = json.load(f)

    errors = validate_json(data)
    if errors:
        print(f"ERROR: {len(errors)} validation error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("  JSON validation: OK")

    # ── Extract metadata ────────────────────────────────────────────────
    state_map = load_state_map()
    try:
        meta = extract_metadata(data, state_map, args.election_id)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"  Election  : {meta['election_id']}")
    print(f"  State     : {meta['state_std']} -> {meta['state_code']} ({meta['state_name']})")
    print(f"  Title     : {meta['title']}")

    # ── Transform to D1 rounds ──────────────────────────────────────────
    rounds, party_warnings = transform_to_rounds(data, meta)
    total_candidates = sum(len(r["candidates"]) for r in rounds)
    print(f"  Constituencies: {len(rounds)}")
    print(f"  Candidates    : {total_candidates}")

    # ── Resume: filter already-loaded ───────────────────────────────────
    if args.resume:
        progress = load_progress(args.json_path)
        loaded_set = set(progress.get("loaded", []))
        before = len(rounds)
        rounds = [r for r in rounds if r["ac_no"] not in loaded_set]
        skipped = before - len(rounds)
        if skipped:
            print(f"\n  Resume: skipping {skipped} already-loaded constituencies")
        if not rounds:
            print("  All constituencies already loaded. Nothing to do.")
            sys.exit(0)
        print(f"  Remaining: {len(rounds)} constituencies")
    else:
        progress = {"loaded": [], "failed": []}

    # ── Dry-run check ───────────────────────────────────────────────────
    if args.preprocess:
        # Collect all unique parties from the dataset
        party_counts = Counter()
        abv_to_name = {}
        for r in rounds:
            for c in r["candidates"]:
                party_counts[c["party_abv"]] += 1
                abv_to_name[c["party_abv"]] = c.get("_orig_party", c["party_abv"])

        print(f"\nParties in dataset ({len(party_counts)} unique):")
        print(f"  {'ABBREV':<15s} {'CANDIDATES':>10s}  NAME")
        print(f"  {'-'*15} {'-'*10}  {'-'*35}")
        for abv, count in sorted(party_counts.items(), key=lambda x: -x[1]):
            orig = abv_to_name.get(abv, abv)
            print(f"  {abv:<15s} {count:>10d}  {orig}")

        print(f"\n{'='*60}")
        print("PREPROCESS COMPLETE — no data written to D1")
        print(f"{'='*60}")

    # ── Pre-process: build batches (pure, no I/O) ───────────────────────
    batches = build_batches(rounds, args.batch_size)
    total_constituencies = sum(len(b) for b in batches)

    if args.preprocess:
        print(f"\nBatches: {len(batches)} ({total_constituencies} constituencies, limit {args.batch_size} stmts)")
        for i, batch in enumerate(batches):
            total_cands = sum(len(r["candidates"]) for r in batch)
            print(f"    Batch {i+1}: {len(batch)} ACs, {total_cands} candidates")
        print(f"\nDone: {total_constituencies} constituencies, {total_candidates} candidates ready to load")
        sys.exit(0)

    # ── D1 write (pure I/O from here on) ────────────────────────────────
    try:
        check_env()
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    if not args.skip_election:
        print()
        print("Ensuring election record in D1...")
        if not ensure_election(meta, args.wrangler_db, False):
            print("ERROR: Could not create election record. Aborting.")
            sys.exit(1)

    print()
    print(f"Loading to D1:")
    result = load_to_d1(batches)

    # Update progress
    if not args.preprocess:
        for r in rounds:
            if result["failed"] == 0:
                progress["loaded"].append(r["ac_no"])
            else:
                # On partial failure, mark all as loaded (upsert is safe)
                progress["loaded"].append(r["ac_no"])
        progress["loaded"] = sorted(set(progress["loaded"]))
        save_progress(args.json_path, progress)

    # ── Verify ──────────────────────────────────────────────────────────
    if not args.skip_verify and not args.preprocess:
        print()
        print("Verification:")
        verify_load(meta, len(rounds), args.wrangler_db, args.preprocess)

    # ── Summary ─────────────────────────────────────────────────────────
    print()
    status = "Would load" if args.preprocess else "Loaded"
    print(f"Done: {status} {result['ok']} constituencies, {total_candidates} candidates to D1")
    if result["failed"]:
        print(f"  FAILED: {result['failed']} constituencies")
    print(f"  Batches: {result['batches']}")


if __name__ == "__main__":
    main()
