#!/usr/bin/env python3
"""
One-time script: update election names from month-based to state-based.

Changes "AC 2026 May" to "AC 2026 AS/KL/TN" etc.
Reads states from each election's states field, converts ECI codes to
standard codes via data/states.csv, and updates the name column.

Usage:
    python3 scripts/update-election-names.py
    python3 scripts/update-election-names.py --dry-run
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys

WRANGLER_DB = "election-results"
STATES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "states.csv")


def run_wrangler(sql: str, wrangler_db: str, dry_run: bool = False):
    if dry_run:
        print(f"  [DRY] {sql[:120]}...")
        return []
    result = subprocess.run(
        ["wrangler", "d1", "execute", wrangler_db, "--command", sql, "--remote", "--json"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def main():
    dry_run = "--dry-run" in sys.argv

    # Load ECI -> std code map
    eci_to_std = {}
    with open(STATES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            eci_to_std[row["state_code_eci"]] = row["state_code"]

    # Read all elections
    data = run_wrangler(
        "SELECT election_id, name, states FROM elections;", WRANGLER_DB, dry_run
    )

    if not data or not data[0].get("results"):
        print("No elections found or dry-run mode.")
        return

    elections = data[0]["results"]
    print(f"Found {len(elections)} elections.\n")

    updates = 0
    for row in elections:
        eid = row["election_id"]
        old_name = row["name"]
        states_raw = row.get("states", "[]")

        try:
            states = json.loads(states_raw)
        except json.JSONDecodeError:
            print(f"  SKIP {eid}: bad states JSON: {states_raw}")
            continue

        # Determine prefix from election_id
        if eid.startswith("BYE-"):
            prefix = "Bye-Election"
        else:
            prefix = "AC"

        # Extract year from election_id (AC-YYYY-MM or BYE-YYYY-MM)
        parts = eid.split("-")
        year = parts[1] if len(parts) >= 2 else "????"

        # Convert ECI codes to standard, sort alphabetically
        std_codes = sorted(eci_to_std.get(e, e) for e in states)
        new_name = f"{prefix} {year} {'/'.join(std_codes)}"

        if new_name == old_name:
            print(f"  OK   {eid}: {old_name}")
            continue

        new_name_escaped = new_name.replace("'", "''")
        update_sql = (
            f"UPDATE elections SET name = '{new_name_escaped}' "
            f"WHERE election_id = '{eid}';"
        )
        run_wrangler(update_sql, WRANGLER_DB, dry_run)
        print(f"  FIX  {eid}: {old_name} -> {new_name}")
        updates += 1

    print(f"\nDone. {updates} election(s) updated.")


if __name__ == "__main__":
    main()
