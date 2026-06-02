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
        election_id: Optional election ID (e.g. "AC-2023-05"). Passed via kwargs.

    Returns:
        Response JSON from Worker
    """
    if not INGEST_URL:
        raise RuntimeError("D1_INGEST_URL not set — use db_utils for local SQLite")

    payload = {
        "state_code": state_code,
        "ac_no": ac_no,
        "ac_name": ac_name,
        "round_no": round_no,
        "candidates": [
            {"candidate": c["candidate"], "party_abv": c["party_abv"], "votes": c["votes"]}
            for c in candidates
        ],
    }
    if kwargs.get("election_id"):
        payload["election_id"] = kwargs["election_id"]

    resp = requests.post(
        f"{INGEST_URL}/ingest/round",
        json=payload,
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def insert_batch(rounds):
    """Write multiple round snapshots — one request per round.

    The /ingest/batch endpoint hits Workers CPU limits with large payloads.
    Individual /ingest/round calls are slower but reliable.

    Args:
        rounds: List of dicts with 'state_code', 'ac_no', 'ac_name',
                'round_no', 'candidates' keys. Optional 'election_id'.

    Returns dict with ok/failed counts.
    """
    if not INGEST_URL:
        raise RuntimeError("D1_INGEST_URL not set — use db_utils for local SQLite")

    import time
    ok = 0
    failed = 0
    for r in rounds:
        payload = {
            "state_code": r["state_code"],
            "ac_no": r["ac_no"],
            "ac_name": r.get("ac_name"),
            "round_no": r["round_no"],
            "candidates": [
                {"candidate": c["candidate"], "party_abv": c["party_abv"], "votes": c["votes"]}
                for c in r["candidates"]
            ],
        }
        if r.get("election_id"):
            payload["election_id"] = r["election_id"]

        for attempt in range(3):
            resp = requests.post(
                f"{INGEST_URL}/ingest/round",
                json=payload,
                headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
                timeout=30,
            )
            if resp.status_code == 200:
                ok += 1
                break
            elif resp.status_code == 429:
                time.sleep(2 ** attempt)
            else:
                failed += 1
                break
        time.sleep(0.05)

    return {"ok": ok, "failed": failed, "rounds": len(rounds)}
