#!/usr/bin/env python3
"""
Normalize party names in rounds_ac and rounds_pc tables to use party abbreviations.
"""

import sqlite3
import re
from collections import defaultdict

DATABASE_URL = "data/india-votes-data.db"
conn = sqlite3.connect(DATABASE_URL)
cur = conn.cursor()


def generate_abbreviation(party_name: str) -> str:
    """Generate a deterministic abbreviation from a party name."""
    # Remove common prefixes/suffixes and normalize
    name = party_name.strip()
    
    # Handle special cases first
    special_cases = {
        "None of the Above": "NOTA",
        "Independent": "IND",
    }
    if name in special_cases:
        return special_cases[name]
    
    # Remove parenthetical content for abbreviation generation
    base = re.sub(r'\s*\([^)]+\)\s*', ' ', name)
    
    # Generate abbreviation from significant words
    words = base.split()
    
    # Common words to skip
    skip_words = {'the', 'and', 'of', 'for', 'in', 'on', 'at', 'to', 'by', 'with', 'a', 'an'}
    
    # Try to create abbreviation from first letters
    sig_words = [w for w in words if w.lower() not in skip_words]
    
    if not sig_words:
        sig_words = words
    
    # Method 1: First letters of significant words (up to 6 chars)
    abbr = ''.join(w[0].upper() for w in sig_words[:6])
    
    # If too short, use more letters from first word
    if len(abbr) < 3 and len(sig_words) > 0:
        abbr = (sig_words[0][:4] + abbr).upper()[:6]
    
    # Clean up - keep only alphanumeric
    abbr = re.sub(r'[^A-Z0-9]', '', abbr)
    
    return abbr if abbr else 'UNK'


def add_party_if_missing(party_name: str):
    """Add a party to the parties table if it doesn't exist."""
    abv = generate_abbreviation(party_name)
    
    # Check if abbreviation already exists
    cur.execute("SELECT abv FROM parties WHERE abv = ?", (abv,))
    if cur.fetchone():
        # Try adding a number suffix
        for i in range(1, 100):
            test_abv = f"{abv[:4]}{i}"
            cur.execute("SELECT abv FROM parties WHERE abv = ?", (test_abv,))
            if not cur.fetchone():
                abv = test_abv
                break
    
    try:
        cur.execute(
            "INSERT OR IGNORE INTO parties (abv, name) VALUES (?, ?)",
            (abv, party_name)
        )
        return abv
    except sqlite3.IntegrityError:
        # If still fails, find the existing abbreviation
        cur.execute("SELECT abv FROM parties WHERE name = ?", (party_name,))
        row = cur.fetchone()
        return row[0] if row else abv


def main():
    # Get all unique party names from rounds_ac for round 999 in imported states
    cur.execute('''
        SELECT DISTINCT party_abv, state_code, ac_no
        FROM rounds_ac 
        WHERE round_no = 999 AND state_code IN ('S07', 'S27', 'U08', 'S13', 'S04', 'U05')
    ''')
    
    party_names = set()
    for row in cur.fetchall():
        party_names.add(row[0])
    
    print(f"Found {len(party_names)} unique party names")
    
    # Get existing party names from parties table
    cur.execute("SELECT name FROM parties")
    existing_names = set(row[0] for row in cur.fetchall())
    
    # Find parties to add
    new_parties = party_names - existing_names
    print(f"New parties to add: {len(new_parties)}")
    
    # Add new parties
    party_abv_map = {}
    for party_name in sorted(new_parties):
        abv = add_party_if_missing(party_name)
        party_abv_map[party_name] = abv
        if party_name != abv:
            print(f"  {party_name} -> {abv}")
    
    conn.commit()
    
    # Get abbreviation mapping for all parties
    cur.execute("SELECT name, abv FROM parties")
    name_to_abv = {row[0]: row[1] for row in cur.fetchall()}
    
    # Update rounds_ac - normalize party names to abbreviations
    print("\nUpdating rounds_ac...")
    updated = 0
    for party_name, abv in name_to_abv.items():
        if party_name != abv:  # Only update if name differs from abbreviation
            cur.execute(
                "UPDATE rounds_ac SET party_abv = ? WHERE party_abv = ? AND round_no = 999",
                (abv, party_name)
            )
            updated += cur.rowcount
    
    print(f"Updated {updated} records in rounds_ac")
    
    # Also update existing parties table abbreviations
    for party_name, abv in name_to_abv.items():
        if party_name != abv:
            cur.execute(
                "UPDATE rounds_ac SET party_abv = ? WHERE party_abv = ?",
                (abv, party_name)
            )
    
    conn.commit()
    
    # Verify
    print("\n=== Verification ===")
    cur.execute("SELECT DISTINCT party_abv FROM rounds_ac WHERE round_no = 999 LIMIT 20")
    sample = [row[0] for row in cur.fetchall()]
    print(f"Sample abbreviations: {sample}")


if __name__ == '__main__':
    main()