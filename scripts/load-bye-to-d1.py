#!/usr/bin/env python3
"""
Load bye-election results from JSON into Cloudflare D1.

Reads the JSON produced by eci-bye-scraper.py and ingests all rounds
into D1 via the ingestion Worker.

Usage:
    python3 scripts/load-bye-to-d1.py data/json/bye-elections/2026-05.json
    python3 scripts/load-bye-to-d1.py data/json/bye-elections/2026-05.json --dry-run

Requires D1_INGEST_URL and D1_INGEST_TOKEN env vars (or .env).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WRANGLER_DB = "election-results"
DEFAULT_BATCH_SIZE = 200  # rounds per batch (not candidates)
STATES_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "states.csv")

_eci_to_std_cache = None

def _eci_to_std_map() -> dict:
    """Build {eci_code: std_code} from states.csv, cached."""
    global _eci_to_std_cache
    if _eci_to_std_cache is None:
        _eci_to_std_cache = {}
        with open(STATES_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                _eci_to_std_cache[row["state_code_eci"]] = row["state_code"]
    return _eci_to_std_cache


def build_election_name(prefix: str, year, states_eci: list) -> str:
    """Build election name like 'Bye-Election 2026 AS/KL/TN' from ECI state codes."""
    eci_map = _eci_to_std_map()
    std_codes = sorted(eci_map.get(e, e) for e in states_eci)
    return f"{prefix} {year} {'/'.join(std_codes)}"


def check_env() -> tuple:
    """Check required env vars."""
    url = os.environ.get("D1_INGEST_URL", "")
    token = os.environ.get("D1_INGEST_TOKEN", "")
    if not url:
        raise RuntimeError("D1_INGEST_URL not set. Add to .env or export it.")
    if not token:
        raise RuntimeError("D1_INGEST_TOKEN not set. Add to .env or export it.")
    return url, token


# ---------------------------------------------------------------------------
# Election record
# ---------------------------------------------------------------------------

def ensure_election(election_id: str, name: str, states: list, sort_date: str,
                    wrangler_db: str, dry_run: bool) -> bool:
    """Ensure the elections table has a record for this bye-election."""
    def run_sql(sql):
        if dry_run:
            print(f"  [DRY] {sql[:120]}...")
            return []
        result = subprocess.run(
            ["wrangler", "d1", "execute", wrangler_db, "--command", sql, "--remote", "--json"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return json.loads(result.stdout) if result.stdout.strip() else []

    states_json = json.dumps(states)
    try:
        # Check if election exists
        check_sql = f"SELECT states FROM elections WHERE election_id = '{election_id}';"
        data = run_sql(check_sql)

        if data and data[0].get("results"):
            existing = json.loads(data[0]["results"][0].get("states", "[]"))
            merged = list(set(existing + states))
            if merged != existing:
                states_json = json.dumps(merged)
                update_sql = f"UPDATE elections SET states = '{states_json}' WHERE election_id = '{election_id}';"
                run_sql(update_sql)
                print(f"  Election {election_id} updated — states: {merged}")
            else:
                print(f"  Election {election_id} exists — states already included")
        else:
            insert_sql = (
                f"INSERT INTO elections (election_id, name, states, sort_date) "
                f"VALUES ('{election_id}', '{name}', '{states_json}', '{sort_date}');"
            )
            run_sql(insert_sql)
            print(f"  Election {election_id} created — states: {states}")

        return True
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: {e.stderr.strip()}")
        return False
    except FileNotFoundError:
        print("  ERROR: 'wrangler' CLI not found. Install with: npm i -g wrangler")
        return False


# ---------------------------------------------------------------------------
# Round ingestion
# ---------------------------------------------------------------------------

def ingest_rounds(data: dict, ingest_url: str, ingest_token: str,
                  batch_size: int, dry_run: bool) -> dict:
    """Ingest all rounds from the bye-election JSON into D1."""
    election_id = data["election_id"]
    constituencies = data["constituencies"]

    total_rounds = 0
    ok = 0
    failed = 0

    for ac in constituencies:
        state_code = ac["state_code"]
        ac_no = ac["ac_no"]
        ac_name = ac["ac_name"]

        # Build all rounds for this constituency
        rounds = []
        for r in ac["rounds"]:
            rounds.append({
                "state_code": state_code,
                "ac_no": ac_no,
                "ac_name": ac_name,
                "round_no": r["round_no"],
                "election_id": election_id,
                "candidates": [
                    {"candidate": c["candidate"], "party_abv": c["party_abv"], "votes": c["votes"]}
                    for c in r["candidates"]
                ],
            })

        # Add round 998 (EVM total = last counting round) and round 999 (EVM + Postal)
        if ac["rounds"]:
            last_round = ac["rounds"][-1]
            # Round 998: EVM total (same as last counting round)
            rounds.append({
                "state_code": state_code,
                "ac_no": ac_no,
                "ac_name": ac_name,
                "round_no": 998,
                "election_id": election_id,
                "candidates": [
                    {"candidate": c["candidate"], "party_abv": c["party_abv"], "votes": c["votes"]}
                    for c in last_round["candidates"]
                ],
            })

        if ac.get("postal_votes"):
            # Round 999: EVM + Postal combined
            last_round = ac["rounds"][-1] if ac["rounds"] else None
            if last_round:
                combined = []
                postal_map = {p["candidate"]: p["postal_votes"] for p in ac["postal_votes"]}
                for c in last_round["candidates"]:
                    postal = postal_map.get(c["candidate"], 0)
                    combined.append({
                        "candidate": c["candidate"],
                        "party_abv": c["party_abv"],
                        "votes": c["votes"] + postal,
                    })
                rounds.append({
                    "state_code": state_code,
                    "ac_no": ac_no,
                    "ac_name": ac_name,
                    "round_no": 999,
                    "election_id": election_id,
                    "candidates": combined,
                })

        total_rounds += len(rounds)

        if dry_run:
            print(f"  [DRY] {ac_name} ({state_code}/{ac_no}): {len(rounds)} rounds")
            continue

        # Send rounds to /ingest/round
        for r in rounds:
            payload = {
                "state_code": r["state_code"],
                "ac_no": r["ac_no"],
                "ac_name": r["ac_name"],
                "round_no": r["round_no"],
                "election_id": r["election_id"],
                "candidates": r["candidates"],
            }
            for attempt in range(3):
                try:
                    import requests
                    resp = requests.post(
                        f"{ingest_url}/ingest/round",
                        json=payload,
                        headers={"Authorization": f"Bearer {ingest_token}"},
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        ok += 1
                        break
                    elif resp.status_code == 429:
                        time.sleep(2 ** attempt)
                    else:
                        print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
                        failed += 1
                        break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1)
                    else:
                        print(f"  ERROR: {e}")
                        failed += 1

            time.sleep(0.05)  # rate limit

        print(f"  {ac_name} ({state_code}/{ac_no}): {len(rounds)} rounds ingested")

    return {"total_rounds": total_rounds, "ok": ok, "failed": failed}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Load bye-election results into D1")
    parser.add_argument("json_file", help="Path to bye-election JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Batch size (default: {DEFAULT_BATCH_SIZE})")
    args = parser.parse_args()

    # Load JSON
    with open(args.json_file) as f:
        data = json.load(f)

    election_id = data["election_id"]
    title = data.get("title", "Bye Election")
    constituencies = data["constituencies"]

    # Derive metadata
    states = list(set(c["state_code"] for c in constituencies))
    states.sort()
    eid_parts = election_id.split("-")
    year = eid_parts[1] if len(eid_parts) >= 2 else data.get("election_year", "2026")
    month = data.get("election_month") or (eid_parts[2] if len(eid_parts) >= 3 else "01")
    sort_date = f"{year}-{month}"
    name = build_election_name("Bye-Election", year, states)

    print(f"JSON        : {args.json_file}")
    print(f"Election ID : {election_id}")
    print(f"States      : {', '.join(states)}")
    print(f"ACs         : {len(constituencies)}")
    total_rounds = sum(len(c["rounds"]) + 1 for c in constituencies)  # +1 for postal
    print(f"Total rounds: ~{total_rounds}")
    print()

    # Check env
    ingest_url, ingest_token = check_env()

    # 1. Create election record
    print("Creating election record...")
    if not ensure_election(election_id, name, states, sort_date, WRANGLER_DB, args.dry_run):
        print("Failed to create election record. Aborting.")
        sys.exit(1)
    print()

    # 2. Ingest rounds
    print("Ingesting rounds...")
    stats = ingest_rounds(data, ingest_url, ingest_token, args.batch_size, args.dry_run)
    print()

    # Summary
    print(f"Done: {stats['ok']} rounds ingested, {stats['failed']} failed")
    if args.dry_run:
        print("(dry run — nothing was actually written)")


if __name__ == "__main__":
    main()
