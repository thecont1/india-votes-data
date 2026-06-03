#!/usr/bin/env python3
"""
Rapid hygiene checker: compare candidate counts between local CSVs and D1.

Reads each results CSV in data/csv/, counts candidates per constituency,
then queries the D1 API to compare. Reports discrepancies and optionally
drops + reloads affected constituencies.

Usage:
    python3 scripts/check-hygiene.py                   # check all CSVs
    python3 scripts/check-hygiene.py data/csv/2023Assembly-KA.csv  # check one
    python3 scripts/check-hygiene.py --fix              # drop+reload mismatches
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter

API_BASE = os.environ.get("API_BASE", "https://election-api.thecontrarian.workers.dev")
WRANGLER_DB = "election-results"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")
STATES_CSV = os.path.join(DATA_DIR, "states.csv")
CSV_DIR = os.path.join(DATA_DIR, "csv")

# ECI state codes — built from states.csv at runtime
_eci_map = None


def eci_map():
    global _eci_map
    if _eci_map is None:
        _eci_map = {}
        with open(STATES_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                _eci_map[row["state_code"]] = row["state_code_eci"]
    return _eci_map


def run_wrangler(sql: str, dry_run: bool = False):
    if dry_run:
        return []
    result = subprocess.run(
        ["wrangler", "d1", "execute", WRANGLER_DB, "--command", sql, "--remote", "--json"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def parse_csv(path: str) -> tuple:
    """Parse a results CSV. Returns (state_std, election_id, {ac_no: count})."""
    fname = os.path.basename(path)
    # Filename like 2023Assembly-KA.csv or 2026Assembly-WB.csv
    m = re.match(r"(\d{4})\w*-(\w+)\.csv", fname)
    if not m:
        return None, None, None
    year = m.group(1)
    state_std = m.group(2)

    # Derive election_id: AC-YYYY-MM — use 05 for Assembly elections
    # (the actual month is embedded in the JSON, but CSVs don't have it;
    #  we'll query D1 by state instead)
    counts = Counter()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            counts[row["constituency_no"]] += 1

    return state_std, year, dict(counts)


def query_d1_candidates(state_eci: str, election_id: str = None) -> dict:
    """Query D1 for candidate counts per AC. Returns {ac_no_str: count}."""
    # Use wrangler to query rounds_ac directly — much faster than API
    eid_filter = f"AND election_id = '{election_id}'" if election_id else ""
    sql = (
        f"SELECT ac_no, COUNT(DISTINCT candidate || '-' || party_abv) as cnt "
        f"FROM rounds_ac "
        f"WHERE state_code = '{state_eci}' {eid_filter} "
        f"GROUP BY ac_no;"
    )
    data = run_wrangler(sql)
    if data and data[0].get("results"):
        return {str(r["ac_no"]): r["cnt"] for r in data[0]["results"]}
    return {}


def find_election_id(state_eci: str) -> str:
    """Find the election_id for a state from the elections table."""
    sql = (
        f"SELECT election_id FROM elections "
        f"WHERE states LIKE '%{state_eci}%' "
        f"AND election_id NOT LIKE 'BYE-%' "
        f"ORDER BY sort_date DESC LIMIT 1;"
    )
    data = run_wrangler(sql)
    if data and data[0].get("results"):
        return data[0]["results"][0]["election_id"]
    return None


def check_csv(path: str, fix: bool = False) -> list:
    """Check one CSV against D1. Returns list of mismatches."""
    state_std, year, csv_counts = parse_csv(path)
    if not state_std:
        print(f"  SKIP: cannot parse {os.path.basename(path)}")
        return []

    emap = eci_map()
    state_eci = emap.get(state_std)
    if not state_eci:
        print(f"  SKIP: unknown state {state_std}")
        return []

    # Find election_id
    election_id = find_election_id(state_eci)
    if not election_id:
        print(f"  {state_std}: no election found in D1")
        return []

    d1_counts = query_d1_candidates(state_eci, election_id)

    # Compare
    mismatches = []
    all_acs = set(csv_counts.keys()) | set(d1_counts.keys())
    for ac in sorted(all_acs, key=lambda x: int(x) if x.isdigit() else 0):
        csv_c = csv_counts.get(ac, 0)
        d1_c = d1_counts.get(ac, 0)
        if csv_c != d1_c:
            mismatches.append({
                "state_std": state_std,
                "state_eci": state_eci,
                "ac_no": ac,
                "election_id": election_id,
                "csv_count": csv_c,
                "d1_count": d1_c,
                "diff": csv_c - d1_c,
            })

    return mismatches


def fix_mismatch(m: dict):
    """Drop and reload one constituency from CSV."""
    state_std = m["state_std"]
    ac_no = m["ac_no"]
    election_id = m["election_id"]

    print(f"    Dropping {state_std} AC {ac_no} ({election_id})...")

    # Delete from rounds_ac
    sql = (
        f"DELETE FROM rounds_ac "
        f"WHERE state_code = '{m['state_eci']}' AND ac_no = {ac_no} "
        f"AND election_id = '{election_id}';"
    )
    run_wrangler(sql)

    # Delete from candidates_search
    entity_prefix = f"{m['state_eci']}-{ac_no}-{election_id}-"
    run_wrangler(f"DELETE FROM candidates_search WHERE entity_id LIKE '{entity_prefix}%';")

    # Delete from constituency_status
    run_wrangler(
        f"DELETE FROM constituency_status "
        f"WHERE state_code = '{m['state_eci']}' AND ac_no = {ac_no};"
    )

    print(f"    Dropped. Re-run load-json-to-d1.py to reload.")


def main():
    parser = argparse.ArgumentParser(description="Check CSV vs D1 candidate counts")
    parser.add_argument("csv_files", nargs="*", help="Specific CSV files to check (default: all in data/csv/)")
    parser.add_argument("--fix", action="store_true", help="Drop discrepant constituencies from D1")
    args = parser.parse_args()

    if args.csv_files:
        csv_files = args.csv_files
    else:
        csv_files = sorted(glob.glob(os.path.join(CSV_DIR, "*.csv")))

    if not csv_files:
        print("No CSV files found.")
        sys.exit(1)

    print(f"Checking {len(csv_files)} CSV file(s)...\n")
    all_mismatches = []
    ok_count = 0

    for path in csv_files:
        fname = os.path.basename(path)
        mismatches = check_csv(path, fix=args.fix)
        if mismatches:
            state = mismatches[0]["state_std"]
            print(f"  {state}: {len(mismatches)} mismatch(es)")
            for m in mismatches:
                sign = "+" if m["diff"] > 0 else ""
                print(f"    AC {m['ac_no']}: CSV={m['csv_count']}, D1={m['d1_count']} ({sign}{m['diff']})")
            all_mismatches.extend(mismatches)
        else:
            ok_count += 1
            state = parse_csv(path)[0] or fname
            print(f"  {state}: OK")

    # Summary
    print(f"\n{'='*50}")
    print(f"Results: {ok_count} OK, {len(all_mismatches)} mismatch(es)")

    if all_mismatches and args.fix:
        print(f"\nDropping {len(all_mismatches)} discrepant constituency(es) from D1...")
        for m in all_mismatches:
            fix_mismatch(m)
        # Rebuild FTS
        print("\nRebuilding FTS index...")
        run_wrangler("INSERT INTO search_fts(search_fts) VALUES('rebuild');")
        print("Done. Re-run load-json-to-d1.py to reload the fixed constituencies.")
    elif all_mismatches:
        print("\nRun with --fix to drop discrepant constituencies from D1.")
        print("Then re-run load-json-to-d1.py to reload.")


if __name__ == "__main__":
    main()
