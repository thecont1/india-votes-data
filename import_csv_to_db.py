#!/usr/bin/env python3
"""
Import election results from CSV files into the SQLite database.

Final results are imported as round 999 (per SCHEMA.md convention).
"""

import csv
import os
import sys
import db_utils

# State code mapping: Standard code -> ECI code
STATE_CODE_MAP = {
    'HR': 'S07',  # Haryana
    'JH': 'S27',  # Jharkhand
    'JK': 'U08',  # Jammu & Kashmir
    'MH': 'S13',  # Maharashtra
    'BR': 'S04',  # Bihar
    'DL': 'U05',  # Delhi
}

# Party name to abbreviation mappings for normalization
PARTY_ABBREV_MAP = {
    'Bharatiya Janata Party': 'BJP',
    'Indian National Congress': 'INC',
    'Aam Aadmi Party': 'AAP',
    'Bahujan Samaj Party': 'BSP',
    'None of the Above': 'NOTA',
    'Independent': 'IND',
    # Add more as needed based on parties.csv
}


def normalize_party(party_name: str) -> str:
    """Normalize party name, returning abbreviation if found."""
    if party_name in PARTY_ABBREV_MAP:
        return PARTY_ABBREV_MAP[party_name]
    # Use db_utils normalization
    return db_utils._normalize_party(party_name)


def import_csv_to_db(csv_path: str, round_no: int = 999) -> dict:
    """
    Import a CSV file into the database.
    
    Returns stats about the import.
    """
    stats = {'constituencies': 0, 'candidates': 0, 'errors': 0}
    
    # Extract state from filename
    filename = os.path.basename(csv_path)
    state_std = filename.split('Assembly-')[1].split('.')[0]
    state_code = STATE_CODE_MAP.get(state_std)
    
    if not state_code:
        print(f"Unknown state in filename: {filename}")
        return stats
    
    # Group candidates by constituency
    ac_data = {}
    
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ac_no = int(row['constituency_no'])
            ac_name = row['constituency']
            candidate = row['candidate']
            party = normalize_party(row['party'])
            evm_votes = int(row['evm_votes']) if row['evm_votes'] else 0
            postal_votes = int(row['postal_votes']) if row['postal_votes'] else 0
            total_votes = evm_votes + postal_votes
            
            key = (ac_no, ac_name)
            if key not in ac_data:
                ac_data[key] = []
            
            ac_data[key].append({
                'candidate': candidate,
                'party': party,
                'votes': total_votes,
            })
    
    # Insert each constituency
    for (ac_no, ac_name), candidates in ac_data.items():
        try:
            db_utils.insert_round_snapshot(
                state_code=state_code,
                state_name='',  # ignored by db_utils
                ac_no=ac_no,
                ac_name=ac_name,
                round_no=round_no,
                total_rounds=round_no,
                candidates=candidates,
                scraped_at='',  # ignored by db_utils
            )
            
            # Update constituency status
            db_utils.upsert_constituency_status(
                state_code=state_code,
                ac_no=ac_no,
                ac_name=ac_name,
                status='DONE',
                current_round=round_no,
            )
            
            stats['constituencies'] += 1
            stats['candidates'] += len(candidates)
            
        except Exception as e:
            print(f"Error inserting {ac_name} ({state_std}-{ac_no}): {e}")
            stats['errors'] += 1
    
    return stats


def main():
    csv_files = [
        'data/csv/2024Assembly-HR.csv',
        'data/csv/2024Assembly-JH.csv',
        'data/csv/2024Assembly-JK.csv',
        'data/csv/2024Assembly-MH.csv',
        'data/csv/2025Assembly-BR.csv',
        'data/csv/2025Assembly-DL.csv',
    ]
    
    # Ensure we're in the project root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    # Initialize database (creates tables if needed)
    db_utils.init_db()
    
    total_stats = {'constituencies': 0, 'candidates': 0, 'errors': 0}
    
    for csv_file in csv_files:
        if os.path.exists(csv_file):
            print(f"Importing {csv_file}...")
            stats = import_csv_to_db(csv_file)
            total_stats['constituencies'] += stats['constituencies']
            total_stats['candidates'] += stats['candidates']
            total_stats['errors'] += stats['errors']
            print(f"  -> {stats['constituencies']} ACs, {stats['candidates']} candidates")
        else:
            print(f"File not found: {csv_file}")
    
    print(f"\nTotal: {total_stats['constituencies']} ACs, {total_stats['candidates']} candidates, {total_stats['errors']} errors")


if __name__ == '__main__':
    main()