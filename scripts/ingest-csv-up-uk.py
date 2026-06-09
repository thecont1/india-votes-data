#!/usr/bin/env python3
"""
Ingest UP and UK data from CSV files (complete 403+70 constituencies).
The JSON files only had subsets (133+6). CSV has the full data.
"""

import json
import csv
import subprocess
import sys
import os
import time

DRY_RUN = '--dry-run' in sys.argv

STATE_MAP = {
    'UP': ('S24', 403),
    'UK': ('S28', 70),
}

ELECTION_ID = 'AC-2022-03'

CSV_FILES = {
    'UP': 'data/csv/2022Assembly-UP.csv',
    'UK': 'data/csv/2022Assembly-UK.csv',
}


def load_party_mapping():
    mapping = {}
    d1_path = '/tmp/d1_parties.json'
    if os.path.exists(d1_path):
        with open(d1_path) as f:
            mapping.update(json.load(f))
    csv_path = 'data/parties-ac2022-review.csv'
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            for row in csv.DictReader(f):
                name = row['csv_party_name'].strip()
                abv = row['d1_abv'].strip()
                if name and abv:
                    mapping[name] = abv
    mapping.update({
        'Independent': 'IND', 'None of the Above': 'NOTA',
        'Bharatiya Janata Party': 'BJP', 'Indian National Congress': 'INC',
        'Bahujan Samaj Party': 'BSP', 'Samajwadi Party': 'SP',
        'Aam Aadmi Party': 'AAP', 'Rashtriya Lok Dal': 'RLD',
    })
    return mapping


def esc(s):
    return s.replace("'", "''")


def normalize_party(name, mapping):
    if name in mapping:
        return mapping[name]
    return name.strip()[:20]


def main():
    print("=" * 60)
    print("CSV Ingestion for UP + UK (complete data)")
    if DRY_RUN:
        print("*** DRY RUN ***")
    print("=" * 60)

    party_mapping = load_party_mapping()
    print(f"Party mappings: {len(party_mapping)}")

    all_stmts = []

    for state_std, (state_code, total_seats) in sorted(STATE_MAP.items()):
        csv_path = CSV_FILES[state_std]
        print(f"\n--- {state_std} ({state_code}) ---")

        with open(csv_path) as f:
            rows = list(csv.DictReader(f))

        # Group by constituency
        acs = {}
        for row in rows:
            ac_no = int(row['constituency_no'])
            if ac_no not in acs:
                acs[ac_no] = {
                    'name': row['constituency'],
                    'candidates': []
                }
            evm = int(row['evm_votes'])
            postal = int(row['postal_votes'])
            acs[ac_no]['candidates'].append({
                'name': row['candidate'],
                'party': row['party'],
                'evm': evm,
                'postal': postal,
                'total': evm + postal,
            })

        print(f"  Constituencies: {len(acs)}/{total_seats}")

        for ac_no in sorted(acs.keys()):
            ac = acs[ac_no]
            ac_name = esc(ac['name'])

            for c in ac['candidates']:
                cand_name = esc(c['name'])
                party_abv = esc(normalize_party(c['party'], party_mapping))

                # Round 999 = final (EVM + postal)
                all_stmts.append(
                    f"INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes) "
                    f"VALUES ('{state_code}', {ac_no}, '{ac_name}', '{ELECTION_ID}', 999, '{cand_name}', '{party_abv}', {c['total']}) "
                    f"ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv) "
                    f"DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name"
                )

                # Round 998 = EVM-only
                if c['evm'] > 0:
                    all_stmts.append(
                        f"INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes) "
                        f"VALUES ('{state_code}', {ac_no}, '{ac_name}', '{ELECTION_ID}', 998, '{cand_name}', '{party_abv}', {c['evm']}) "
                        f"ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv) "
                        f"DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name"
                    )

            # Mark DONE
            all_stmts.append(
                f"INSERT INTO constituency_status (state_code, ac_no, ac_name, status, current_round) "
                f"VALUES ('{state_code}', {ac_no}, '{ac_name}', 'DONE', 999) "
                f"ON CONFLICT (state_code, ac_no) DO UPDATE SET "
                f"ac_name = COALESCE(excluded.ac_name, constituency_status.ac_name), "
                f"status = excluded.status, current_round = excluded.current_round"
            )

        print(f"  SQL statements so far: {len(all_stmts)}")

    # latest_rounds_ac upsert
    for state_std, (state_code, _) in STATE_MAP.items():
        all_stmts.append(
            f"INSERT INTO latest_rounds_ac (state_code, ac_no, max_round) "
            f"SELECT state_code, ac_no, MAX(round_no) FROM rounds_ac "
            f"WHERE state_code = '{state_code}' AND election_id = '{ELECTION_ID}' "
            f"GROUP BY state_code, ac_no "
            f"ON CONFLICT(state_code, ac_no) DO UPDATE SET max_round = excluded.max_round"
        )

    print(f"\nTotal statements: {len(all_stmts)}")

    # Execute
    batch_size = 50
    start = time.time()
    for i in range(0, len(all_stmts), batch_size):
        batch = all_stmts[i:i + batch_size]
        sql = ';\n'.join(batch) + ';'

        if DRY_RUN:
            print(f"  [DRY RUN] Batch {i // batch_size + 1}: {len(batch)} stmts")
            continue

        tmp = f'/tmp/d1_csv_batch_{i}.sql'
        with open(tmp, 'w') as f:
            f.write(sql)

        r = subprocess.run(
            ['npx', 'wrangler', 'd1', 'execute', 'election-results',
             '--remote', f'--file={tmp}'],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker')
        )

        if r.returncode != 0:
            print(f"  ERROR batch {i // batch_size + 1}: {r.stderr[:200]}")
            sys.exit(1)

        print(f"  Batch {i // batch_size + 1}/{(len(all_stmts) + batch_size - 1) // batch_size}: {len(batch)} stmts (ok)")
        os.unlink(tmp)

    elapsed = time.time() - start
    print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Done in {elapsed:.1f}s")

    # FTS rebuild
    if not DRY_RUN:
        print("Rebuilding FTS...")
        tmp = '/tmp/d1_fts_rebuild.sql'
        with open(tmp, 'w') as f:
            f.write("INSERT INTO search_fts(search_fts) VALUES('rebuild');")
        subprocess.run(
            ['npx', 'wrangler', 'd1', 'execute', 'election-results',
             '--remote', f'--file={tmp}'],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker')
        )
        print("  FTS rebuild complete")
        os.unlink(tmp)


if __name__ == '__main__':
    main()
