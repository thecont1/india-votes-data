#!/usr/bin/env python3
"""
Normalize all un-normalized party_abv in D1 rounds_ac.

Workflow:
  1. Find party_abv values in rounds_ac that aren't known abbreviations
  2. Try to match against D1 parties table (name → abv)
  3. Unresolved parties → write data/parties-pending.csv, stop, ask user to fill
  4. On re-run: read filled CSV → upsert D1 parties table → delete CSV → continue
  5. Fix rounds_ac party_abv, rebuild candidates_search (round 999 only)

Usage:
    python3 scripts/normalize-party-abv.py            # first run (may pause for input)
    python3 scripts/normalize-party-abv.py --dry-run  # check only, no writes
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys

WRANGLER_DB = "election-results"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_CSV = os.path.join(SCRIPT_DIR, "..", "data", "parties-pending.csv")


def run_wrangler(sql: str, dry_run: bool = False):
    if dry_run:
        print(f"  [DRY] {sql[:300]}...")
        return []
    result = subprocess.run(
        ["wrangler", "d1", "execute", WRANGLER_DB, "--command", sql, "--remote", "--json"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def load_d1_party_map() -> dict:
    """Load {full_name: abv} from D1 parties table."""
    party_map = {}
    data = run_wrangler("SELECT abv, name FROM parties;")
    if data and data[0].get("results"):
        for row in data[0]["results"]:
            if row["name"] and row["abv"]:
                party_map[row["name"]] = row["abv"]
    return party_map


def upsert_pending_parties():
    """Read parties-pending.csv and upsert into D1 parties table."""
    if not os.path.exists(PENDING_CSV):
        return 0

    count = 0
    with open(PENDING_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("name", "").strip()
            abv = row.get("abv", "").strip()
            if not name or not abv:
                continue
            name_esc = name.replace("'", "''")
            abv_esc = abv.replace("'", "''")
            run_wrangler(
                f"INSERT INTO parties (abv, name) VALUES ('{abv_esc}', '{name_esc}') "
                f"ON CONFLICT (abv) DO UPDATE SET name = '{name_esc}';"
            )
            count += 1

    os.remove(PENDING_CSV)
    return count


def find_unresolved_parties(all_abvs: list, party_map: dict) -> list:
    """Find party_abv values that can't be resolved to a known abbreviation."""
    known_abvs = set(party_map.values())
    unresolved = []
    for abv in all_abvs:
        if abv in known_abvs:
            continue
        # Try exact match in party_map
        if abv in party_map:
            continue
        # Try case-insensitive match
        abv_lower = abv.lower()
        found = False
        for full_name in party_map:
            if full_name.lower() == abv_lower:
                found = True
                break
        if found:
            continue
        unresolved.append(abv)
    return unresolved


def write_pending_csv(unresolved: list):
    """Write unresolved parties to CSV for manual review."""
    with open(PENDING_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "abv"])
        for name in sorted(unresolved):
            writer.writerow([name, ""])
    print(f"\n  Wrote {len(unresolved)} unresolved parties to:")
    print(f"    {PENDING_CSV}")
    print(f"\n  Please fill the 'abv' column for each party, then re-run this script.")


def build_fixes(all_abvs: list, party_map: dict) -> dict:
    """Build {old_abv: new_abv} for all resolvable party names."""
    known_abvs = set(party_map.values())
    fixes = {}
    for abv in all_abvs:
        if abv in known_abvs:
            continue
        # Exact match
        if abv in party_map:
            fixes[abv] = party_map[abv]
            continue
        # Case-insensitive match
        abv_lower = abv.lower()
        for full_name, target_abv in party_map.items():
            if full_name.lower() == abv_lower:
                fixes[abv] = target_abv
                break
    return fixes


def apply_fixes(fixes: dict, dry_run: bool = False):
    """Delete conflicting rows, then update all party_abv in one statement."""
    if not fixes:
        return

    # Delete rows where both old and new party_abv exist for same AC/round/candidate
    print("Deleting conflicting rows...")
    for old_abv, new_abv in sorted(fixes.items()):
        old_esc = old_abv.replace("'", "''")
        new_esc = new_abv.replace("'", "''")
        run_wrangler(
            f"DELETE FROM rounds_ac WHERE party_abv = '{old_esc}' "
            f"AND EXISTS ("
            f"  SELECT 1 FROM rounds_ac r2"
            f"  WHERE r2.state_code = rounds_ac.state_code"
            f"  AND r2.ac_no = rounds_ac.ac_no"
            f"  AND r2.election_id = rounds_ac.election_id"
            f"  AND r2.round_no = rounds_ac.round_no"
            f"  AND r2.candidate = rounds_ac.candidate"
            f"  AND r2.party_abv = '{new_esc}'"
            f");",
            dry_run,
        )

    # Single CASE-based UPDATE
    print("Updating party_abv (single statement)...")
    case_parts = []
    for old_abv, new_abv in sorted(fixes.items()):
        old_esc = old_abv.replace("'", "''")
        new_esc = new_abv.replace("'", "''")
        case_parts.append(f"WHEN '{old_esc}' THEN '{new_esc}'")
    case_expr = " ".join(case_parts)
    old_values = ", ".join(f"'{abv.replace(chr(39), chr(39)+chr(39))}'" for abv in fixes)
    run_wrangler(
        f"UPDATE rounds_ac SET party_abv = CASE party_abv {case_expr} END "
        f"WHERE party_abv IN ({old_values});",
        dry_run,
    )
    print(f"  Fixed {len(fixes)} party names.")


def rebuild_candidates_search(dry_run: bool = False):
    """Clear and rebuild candidates_search from rounds_ac (round 999 only)."""
    print("\nClearing candidates_search...")
    run_wrangler("DELETE FROM candidates_search;", dry_run)

    print("Rebuilding candidates_search from rounds_ac (round 999 only)...")
    rebuild_sql = (
        "INSERT INTO candidates_search "
        "(entity_type, entity_id, name, context, boost, votes, total_votes, election_sort, symbol_url) "
        "WITH final_rounds AS ("
        "  SELECT r.state_code, r.ac_no,"
        "    COALESCE("
        "      (SELECT MAX(r2.round_no) FROM rounds_ac r2"
        "       WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no = 999),"
        "      (SELECT MAX(r2.round_no) FROM rounds_ac r2"
        "       WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no != 999)"
        "    ) as final_round"
        "  FROM (SELECT DISTINCT state_code, ac_no FROM rounds_ac) r"
        ") "
        "SELECT 'candidate',"
        "  r.state_code || '-' || r.ac_no || '-' || CASE WHEN r.election_id != '' THEN r.election_id || '-' ELSE '' END || r.party_abv,"
        "  r.candidate,"
        "  r.party_abv || ' | ' || COALESCE(r.ac_name, '') || ' | ' || COALESCE(SUBSTR(e.sort_date, 1, 4), ''),"
        "  CASE WHEN cs.won = 1 THEN 1.5 WHEN cs.status = 'LIVE' THEN 1.2 ELSE 1.0 END,"
        "  r.votes, 0,"
        "  COALESCE(e.sort_date, ''),"
        "  COALESCE(p.symbol_url, '')"
        " FROM rounds_ac r"
        " JOIN final_rounds fr ON r.state_code = fr.state_code AND r.ac_no = fr.ac_no AND r.round_no = fr.final_round"
        " LEFT JOIN constituency_status cs ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no"
        " LEFT JOIN elections e ON r.election_id = e.election_id"
        " LEFT JOIN parties p ON r.party_abv = p.abv;"
    )
    run_wrangler(rebuild_sql, dry_run)

    print("Rebuilding FTS index...")
    run_wrangler("INSERT INTO search_fts(search_fts) VALUES('rebuild');", dry_run)

    data = run_wrangler("SELECT COUNT(*) as cnt FROM candidates_search;", dry_run)
    if data and data[0].get("results"):
        print(f"  candidates_search rows: {data[0]['results'][0]['cnt']}")


def verify(dry_run: bool = False):
    """Check for remaining long party_abv that look like full names."""
    print("\nVerifying...")
    data = run_wrangler(
        "SELECT party_abv, COUNT(*) as cnt FROM rounds_ac "
        "WHERE length(party_abv) > 8 GROUP BY party_abv HAVING cnt > 0 "
        "ORDER BY cnt DESC LIMIT 20;",
        dry_run,
    )
    if data and data[0].get("results"):
        party_map = load_d1_party_map()
        known_abvs = set(party_map.values())
        remaining = [r for r in data[0]["results"] if r["party_abv"] not in known_abvs]
        if remaining:
            print(f"  WARNING: {len(remaining)} party_abv values still look like full names:")
            for r in remaining[:10]:
                print(f"    {r['party_abv']}: {r['cnt']} rows")
        else:
            print("  All party_abv values look correct.")
    else:
        print("  All party_abv values look correct.")


def main():
    dry_run = "--dry-run" in sys.argv

    # Step 1: If pending CSV exists, process it first
    if os.path.exists(PENDING_CSV):
        print(f"Found {PENDING_CSV} — upserting into D1 parties table...")
        n = upsert_pending_parties()
        print(f"  Upserted {n} parties.\n")

    # Step 2: Build party map from D1
    party_map = load_d1_party_map()

    # Step 3: Find all distinct party_abv in rounds_ac
    print("Finding all party_abv values in rounds_ac...")
    data = run_wrangler(
        "SELECT DISTINCT party_abv FROM rounds_ac ORDER BY party_abv;", dry_run
    )
    all_abvs = [r["party_abv"] for r in (data[0].get("results", []) if data else [])]
    print(f"  Total distinct party_abv: {len(all_abvs)}")

    # Step 4: Check for unresolved parties
    unresolved = find_unresolved_parties(all_abvs, party_map)
    if unresolved:
        print(f"\n  {len(unresolved)} party name(s) not found in D1 parties table:")
        for name in unresolved:
            print(f"    - {name}")
        write_pending_csv(unresolved)
        return

    # Step 5: Build and apply fixes
    fixes = build_fixes(all_abvs, party_map)
    if fixes:
        print(f"\n  Found {len(fixes)} to fix:")
        for old, new in sorted(fixes.items()):
            print(f"    {old!r} -> {new}")
        apply_fixes(fixes, dry_run)
    else:
        print("  All party_abv values are already normalized.")

    # Step 6: Rebuild candidates_search
    rebuild_candidates_search(dry_run)

    # Step 7: Verify
    verify(dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
