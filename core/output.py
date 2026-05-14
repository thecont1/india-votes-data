"""
Shared output functions for writing scraped results to CSV and JSON files.
"""

import csv
import json
import os
from datetime import datetime


def write_csv(results: list, path: str, meta: dict):
    """Write constituency results to CSV.

    Args:
        results: list of dicts, each with 'constituency_no', 'constituency', 'voting_tally'
        path: output file path
        meta: dict with 'election_year', 'election_type', 'election_state'
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = ['election_year', 'election_type', 'election_state',
                  'constituency', 'constituency_no', 'serial_no',
                  'candidate', 'party', 'evm_votes', 'postal_votes']

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for constituency in results:
            for candidate in constituency.get('voting_tally', []):
                writer.writerow({
                    'election_year': meta.get('election_year', ''),
                    'election_type': meta.get('election_type', ''),
                    'election_state': meta.get('election_state', ''),
                    'constituency': constituency.get('constituency', ''),
                    'constituency_no': constituency.get('constituency_no', ''),
                    'serial_no': candidate.get('serial_no', ''),
                    'candidate': candidate.get('candidate', ''),
                    'party': candidate.get('party', ''),
                    'evm_votes': candidate.get('evm_votes', ''),
                    'postal_votes': candidate.get('postal_votes', ''),
                })
    print(f"CSV saved: {path}")


def write_json(results: list, path: str, meta: dict):
    """Write constituency results to JSON.

    Args:
        results: list of dicts from extract_results()
        path: output file path
        meta: dict with 'election_year', 'election_type', 'election_state'
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "election_year": meta.get('election_year', ''),
        "election_type": meta.get('election_type', ''),
        "election_state": meta.get('election_state', ''),
        "constituencywise_results": results,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    print(f"JSON saved: {path}")


def output_path(data_dir: str, meta: dict, ext: str) -> str:
    """Generate a timestamped output file path.

    Args:
        data_dir: e.g. "./data/csv" or "./data/json"
        meta: dict with 'election_year', 'election_type', 'election_state'
        ext: file extension without dot, e.g. "csv" or "json"
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    year = meta.get('election_year', '')
    etype = meta.get('election_type', '')
    state = meta.get('election_state', '')
    return f"{data_dir}/{year}{etype}-{state}_{timestamp}.{ext}"
