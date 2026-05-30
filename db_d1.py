"""Cloudflare D1 write client for election scraper.

Set D1_INGEST_URL env var to the ingestion Worker URL.
When unset, scrapers fall back to local SQLite via db_utils.
"""

import os
import requests

INGEST_URL = os.environ.get("D1_INGEST_URL", "")
INGEST_TOKEN = os.environ.get("D1_INGEST_TOKEN", "")


def insert_round_snapshot(state_code, ac_no, ac_name, round_no, candidates, **kwargs):
    """Write one round snapshot to D1 via ingestion Worker.

    Args:
        state_code: ECI state code (e.g. "S22")
        ac_no: Assembly constituency number
        ac_name: Assembly constituency name
        round_no: Round number (999 = postal/final)
        candidates: List of dicts with 'candidate', 'party', 'votes' keys

    Returns:
        Response JSON from Worker
    """
    if not INGEST_URL:
        raise RuntimeError("D1_INGEST_URL not set — use db_utils for local SQLite")

    resp = requests.post(
        f"{INGEST_URL}/ingest/round",
        json={
            "state_code": state_code,
            "ac_no": ac_no,
            "ac_name": ac_name,
            "round_no": round_no,
            "candidates": [
                {"candidate": c["candidate"], "party_abv": c["party"], "votes": c["votes"]}
                for c in candidates
            ],
        },
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def insert_batch(rounds):
    """Write multiple round snapshots in one request.

    Args:
        rounds: List of dicts with 'state_code', 'ac_no', 'ac_name',
                'round_no', 'candidates' keys

    Returns:
        Response JSON from Worker
    """
    if not INGEST_URL:
        raise RuntimeError("D1_INGEST_URL not set — use db_utils for local SQLite")

    resp = requests.post(
        f"{INGEST_URL}/ingest/batch",
        json={
            "rounds": [
                {
                    "state_code": r["state_code"],
                    "ac_no": r["ac_no"],
                    "ac_name": r.get("ac_name"),
                    "round_no": r["round_no"],
                    "candidates": [
                        {"candidate": c["candidate"], "party_abv": c["party"], "votes": c["votes"]}
                        for c in r["candidates"]
                    ],
                }
                for r in rounds
            ]
        },
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()
