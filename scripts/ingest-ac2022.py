#!/usr/bin/env python3
"""
Ingest AC-2022-03 election data into D1 via wrangler.
Reads JSON files, maps party names to abbreviations, generates SQL INSERTs.

Usage: PYTHONUNBUFFERED=1 python3 scripts/ingest-ac2022.py [--dry-run]
"""

import json
import csv
import subprocess
import sys
import os
import time

DRY_RUN = '--dry-run' in sys.argv

# State code mapping (ECI code -> std code)
STATE_MAP = {
    'GA': 'S05',  # Goa
    'MN': 'S14',  # Manipur
    'PB': 'S19',  # Punjab
    'UP': 'S24',  # Uttar Pradesh
    'UK': 'S28',  # Uttarakhand
}

ELECTION_ID = 'AC-2022-03'

JSON_FILES = {
    'GA': 'data/json/2022Assembly-GA.json',
    'MN': 'data/json/2022Assembly-MN.json',
    'PB': 'data/json/2022Assembly-PB.json',
    'UP': 'data/json/2022Assembly-UP.json',
    'UK': 'data/json/2022Assembly-UK.json',
}


def load_party_mapping():
    """Build party name -> abbreviation mapping from D1 + CSV."""
    mapping = {}

    # 1. Load from D1 (via saved file)
    d1_path = '/tmp/d1_parties.json'
    if os.path.exists(d1_path):
        with open(d1_path) as f:
            d1_map = json.load(f)
            mapping.update(d1_map)

    # 2. Load from CSV
    csv_path = 'data/parties-ac2022-review.csv'
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['csv_party_name'].strip()
                abv = row['d1_abv'].strip()
                if name and abv:
                    mapping[name] = abv

    # 3. Hardcoded fallbacks
    mapping.update({
        'Independent': 'IND',
        'None of the Above': 'NOTA',
        'Bharatiya Janata Party': 'BJP',
        'Indian National Congress': 'INC',
        'Bahujan Samaj Party': 'BSP',
        'Samajwadi Party': 'SP',
        'Aam Aadmi Party': 'AAP',
        'Rashtriya Lok Dal': 'RLD',
        'Communist Party of India': 'CPI',
        'Communist Party of India  (Marxist)': 'CPI(M)',
        'Communist Party of India  (Marxist-Leninist)  (Liberation)': 'CPI(ML)(L)',
        'All India Trinamool Congress': 'AITC',
        'Nationalist Congress Party': 'NCP',
        'Shiv Sena': 'SHS',
        'Janata Dal  (Secular)': 'JD(S)',
        'Janata Dal  (United)': 'JD(U)',
        'All India Majlis-E-Ittehadul Muslimeen': 'AIMIM',
        'Rashtriya Janata Dal': 'RJD',
        'Indian Union Muslim League': 'IUML',
    })

    return mapping


def esc(s):
    """Escape single quotes for SQL."""
    return s.replace("'", "''")


def normalize_party(name, mapping):
    """Map party name to abbreviation. Falls back to truncated name."""
    if name in mapping:
        return mapping[name]
    return name.strip()[:20]


def generate_sql(state_std, state_code, data, party_mapping):
    """Generate SQL INSERT statements for one state's data."""
    stmts = []

    for ac in data['constituencywise_results']:
        vd = ac['voting_data']
        ac_no = int(vd['constituency_no'])
        ac_name = esc(vd['constituency'])

        for cand in vd['voting_tally']:
            candidate_name = esc(cand['candidate'])
            party_abv = esc(normalize_party(cand['party'], party_mapping))
            evm_votes = int(cand['evm_votes'])
            postal_votes = int(cand['postal_votes'])
            total_votes = evm_votes + postal_votes

            # Final results as round 999
            stmts.append(
                f"INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes) "
                f"VALUES ('{state_code}', {ac_no}, '{ac_name}', '{ELECTION_ID}', 999, '{candidate_name}', '{party_abv}', {total_votes}) "
                f"ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv) "
                f"DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name"
            )

            # EVM-only as round 998 (for slope analysis)
            if evm_votes > 0:
                stmts.append(
                    f"INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes) "
                    f"VALUES ('{state_code}', {ac_no}, '{ac_name}', '{ELECTION_ID}', 998, '{candidate_name}', '{party_abv}', {evm_votes}) "
                    f"ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv) "
                    f"DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name"
                )

        # Mark constituency as DONE
        stmts.append(
            f"INSERT INTO constituency_status (state_code, ac_no, ac_name, status, current_round) "
            f"VALUES ('{state_code}', {ac_no}, '{ac_name}', 'DONE', 999) "
            f"ON CONFLICT (state_code, ac_no) DO UPDATE SET "
            f"ac_name = COALESCE(excluded.ac_name, constituency_status.ac_name), "
            f"status = excluded.status, current_round = excluded.current_round"
        )

    return stmts


def execute_batch(stmts, batch_size=50):
    """Execute SQL statements via wrangler in batches."""
    total = len(stmts)
    for i in range(0, total, batch_size):
        batch = stmts[i:i + batch_size]
        sql = ';\n'.join(batch) + ';'

        if DRY_RUN:
            print(f"  [DRY RUN] Batch {i // batch_size + 1}: {len(batch)} statements")
            continue

        tmp_path = f'/tmp/d1_batch_{i}.sql'
        with open(tmp_path, 'w') as f:
            f.write(sql)

        result = subprocess.run(
            ['npx', 'wrangler', 'd1', 'execute', 'election-results',
             '--remote', f'--file={tmp_path}'],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker')
        )

        if result.returncode != 0:
            print(f"  ERROR batch {i // batch_size + 1}: {result.stderr[:200]}")
            return False

        try:
            out = json.loads(result.stdout[result.stdout.index('{'):])
            meta = out[0]['meta']
            print(f"  Batch {i // batch_size + 1}/{(total + batch_size - 1) // batch_size}: "
                  f"{len(batch)} stmts, {meta['rows_read']}r/{meta['rows_written']}w "
                  f"({meta['sql_duration_ms']:.0f}ms)")
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            print(f"  Batch {i // batch_size + 1}: {len(batch)} stmts (ok)")

        os.unlink(tmp_path)

    return True


def main():
    print("=" * 60)
    print(f"AC-2022-03 Election Data Ingestion")
    print(f"Election: {ELECTION_ID}")
    print(f"States: {', '.join(STATE_MAP.keys())}")
    if DRY_RUN:
        print("*** DRY RUN MODE ***")
    print("=" * 60)

    party_mapping = load_party_mapping()
    print(f"Party mappings loaded: {len(party_mapping)}")

    all_stmts = []

    for state_std, state_code in sorted(STATE_MAP.items()):
        json_path = JSON_FILES[state_std]
        print(f"\n--- {state_std} ({state_code}) ---")

        with open(json_path) as f:
            data = json.load(f)

        n_acs = len(data['constituencywise_results'])
        print(f"  Constituencies: {n_acs}")

        stmts = generate_sql(state_std, state_code, data, party_mapping)
        print(f"  SQL statements: {len(stmts)}")
        all_stmts.extend(stmts)

    # latest_rounds_ac upsert for all states
    for state_std, state_code in sorted(STATE_MAP.items()):
        all_stmts.append(
            f"INSERT INTO latest_rounds_ac (state_code, ac_no, max_round) "
            f"SELECT state_code, ac_no, MAX(round_no) FROM rounds_ac "
            f"WHERE state_code = '{state_code}' AND election_id = '{ELECTION_ID}' "
            f"GROUP BY state_code, ac_no "
            f"ON CONFLICT(state_code, ac_no) DO UPDATE SET max_round = excluded.max_round"
        )

    print(f"\nTotal SQL statements: {len(all_stmts)}")
    print(f"Executing in batches of 50...")

    start = time.time()
    success = execute_batch(all_stmts)
    elapsed = time.time() - start

    if success:
        print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Ingestion complete in {elapsed:.1f}s")

        if not DRY_RUN:
            print("\nRebuilding FTS index...")
            tmp_path = '/tmp/d1_fts_rebuild.sql'
            with open(tmp_path, 'w') as f:
                f.write("INSERT INTO search_fts(search_fts) VALUES('rebuild');")
            result = subprocess.run(
                ['npx', 'wrangler', 'd1', 'execute', 'election-results',
                 '--remote', f'--file={tmp_path}'],
                capture_output=True, text=True, timeout=120,
                cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker')
            )
            if result.returncode == 0:
                print("  FTS rebuild complete")
            else:
                print(f"  FTS rebuild failed: {result.stderr[:200]}")
            os.unlink(tmp_path)
    else:
        print(f"\nIngestion FAILED after {elapsed:.1f}s")
        sys.exit(1)


if __name__ == '__main__':
    main()
