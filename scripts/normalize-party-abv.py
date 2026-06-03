#!/usr/bin/env python3
"""
One-time script: normalize party_abv in D1 rounds_ac table.

Fixes party names that were stored as full names instead of abbreviations:
  - "Nationalist Congress Party" → "NCP"
  - "Nationalist Congress Party - Sharadchandra Pawar" → "NCP-SP"
  - "Nationalist Congress Party – Sharadchandra Pawar" → "NCP-SP"
  - "Shiv Sena" / "ShivSena" / "SHIVSS" → "SHS"
  - "Shiv Sena (Uddhav Balasaheb Thackeray)" → "SS(UBT)"

Then rebuilds candidates_search from scratch.

Usage:
    python3 scripts/normalize-party-abv.py
    python3 scripts/normalize-party-abv.py --dry-run
"""
from __future__ import annotations

import json
import subprocess
import sys

WRANGLER_DB = "election-results"

# party_abv fixes: old → new
PARTY_FIXES = {
    "Nationalist Congress Party": "NCP",
    "Nationalist Congress Party - Sharadchandra Pawar": "NCP-SP",
    "Nationalist Congress Party \u2013 Sharadchandra Pawar": "NCP-SP",
    "Shiv Sena": "SHS",
    "ShivSena": "SHS",
    "SHIVSS": "SHS",
    "Shiv Sena (Uddhav Balasaheb Thackeray)": "SS(UBT)",
}


def run_wrangler(sql: str, dry_run: bool = False):
    if dry_run:
        print(f"  [DRY] {sql[:200]}...")
        return []
    result = subprocess.run(
        ["wrangler", "d1", "execute", WRANGLER_DB, "--command", sql, "--remote", "--json"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def main():
    dry_run = "--dry-run" in sys.argv

    # 1. Find affected rows
    print("Finding rows with un-normalized party_abv...")
    placeholders = ", ".join(f"'{p}'" for p in PARTY_FIXES)
    data = run_wrangler(
        f"SELECT party_abv, COUNT(*) as cnt FROM rounds_ac "
        f"WHERE party_abv IN ({placeholders}) GROUP BY party_abv ORDER BY cnt DESC;",
        dry_run,
    )

    if data and data[0].get("results"):
        for row in data[0]["results"]:
            new_abv = PARTY_FIXES.get(row["party_abv"], row["party_abv"])
            print(f"  {row['party_abv']}: {row['cnt']} rows → {new_abv}")
    print()

    # 2. Delete rows where normalized version already exists (avoid PK conflict)
    print("Deleting rows that would conflict...")
    for old_abv, new_abv in PARTY_FIXES.items():
        old_esc = old_abv.replace("'", "''")
        new_esc = new_abv.replace("'", "''")
        # Delete old_abv rows where a new_abv row already exists for the same AC/round/candidate
        run_wrangler(
            f"DELETE FROM rounds_ac WHERE party_abv = '{old_esc}' "
            f"AND EXISTS ("
            f"  SELECT 1 FROM rounds_ac r2 "
            f"  WHERE r2.state_code = rounds_ac.state_code "
            f"  AND r2.ac_no = rounds_ac.ac_no "
            f"  AND r2.election_id = rounds_ac.election_id "
            f"  AND r2.round_no = rounds_ac.round_no "
            f"  AND r2.candidate = rounds_ac.candidate "
            f"  AND r2.party_abv = '{new_esc}'"
            f");",
            dry_run,
        )

    # 3. Update remaining rows
    print("Updating party_abv...")
    for old_abv, new_abv in PARTY_FIXES.items():
        old_esc = old_abv.replace("'", "''")
        new_esc = new_abv.replace("'", "''")
        run_wrangler(
            f"UPDATE rounds_ac SET party_abv = '{new_esc}' "
            f"WHERE party_abv = '{old_esc}';",
            dry_run,
        )

    # 4. Verify no problematic party_abv remain
    data = run_wrangler(
        f"SELECT party_abv, COUNT(*) as cnt FROM rounds_ac "
        f"WHERE party_abv IN ({placeholders}) GROUP BY party_abv;",
        dry_run,
    )
    remaining = data[0].get("results", []) if data else []
    if remaining:
        print("\nWARNING: Some rows still have un-normalized party_abv:")
        for row in remaining:
            print(f"  {row['party_abv']}: {row['cnt']} rows")
    else:
        print("  All party_abv values normalized.")

    # 5. Rebuild candidates_search from scratch
    print("\nClearing candidates_search...")
    run_wrangler("DELETE FROM candidates_search;", dry_run)

    print("Rebuilding candidates_search from rounds_ac...")
    # Inline the population query from search-schema.sql
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

    # 6. Rebuild FTS index
    print("Rebuilding FTS index...")
    run_wrangler("INSERT INTO search_fts(search_fts) VALUES('rebuild');", dry_run)

    # 7. Final count
    data = run_wrangler("SELECT COUNT(*) as cnt FROM candidates_search;", dry_run)
    if data and data[0].get("results"):
        print(f"\n  candidates_search rows: {data[0]['results'][0]['cnt']}")
    print("Done.")


if __name__ == "__main__":
    main()
