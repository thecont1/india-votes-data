#!/usr/bin/env python3
"""
One-time script: remove duplicate rows from candidates_search in D1.

The candidates_search table accumulated duplicate rows because it lacked
a UNIQUE constraint on entity_id. Each round ingestion inserted a new row
for the same candidate. This script keeps only the most-recently-inserted
row per entity_id (highest rowid = latest round data) and deletes the rest.

Usage:
    python3 scripts/cleanup-search-duplicates.py
    python3 scripts/cleanup-search-duplicates.py --dry-run
"""
from __future__ import annotations

import json
import subprocess
import sys

WRANGLER_DB = "election-results"


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

    # Count duplicates first
    print("Counting duplicates...")
    data = run_wrangler(
        "SELECT COUNT(*) as total, "
        "SUM(cnt - 1) as to_delete FROM ("
        "  SELECT entity_id, COUNT(*) as cnt FROM candidates_search "
        "  GROUP BY entity_id HAVING cnt > 1"
        ");",
        dry_run,
    )
    if data and data[0].get("results"):
        r = data[0]["results"][0]
        print(f"  Total rows: {r['total']}, Duplicates to delete: {r['to_delete']}")
    print()

    # Single statement: delete all but the latest rowid per entity_id
    print("Deleting duplicates (single statement)...")
    run_wrangler(
        "DELETE FROM candidates_search "
        "WHERE rowid NOT IN ("
        "  SELECT MAX(rowid) FROM candidates_search GROUP BY entity_id"
        ");",
        dry_run,
    )

    # Verify
    data = run_wrangler(
        "SELECT COUNT(*) as cnt FROM candidates_search;", dry_run
    )
    if data and data[0].get("results"):
        print(f"  Rows remaining: {data[0]['results'][0]['cnt']}")

    # Rebuild FTS index
    print("\nRebuilding FTS index...")
    run_wrangler(
        "INSERT INTO search_fts(search_fts) VALUES('rebuild');",
        dry_run,
    )
    print("Done.")


if __name__ == "__main__":
    main()
