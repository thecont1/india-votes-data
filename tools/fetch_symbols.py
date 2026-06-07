#!/usr/bin/env python3
"""One-time script: fetch party election symbol images from Wikipedia.

For each party with a wikipedia_url, scrape the infobox to find the
"Election symbol" image and store its Wikimedia Commons URL in the DB.

Usage:
    python fetch_symbols.py           # dry-run (prints what would change)
    python fetch_symbols.py --apply   # writes to DB + CSV
"""

import csv
import os
import re
import sqlite3
import sys
import urllib.request

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "india-votes-data.db")
CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "parties.csv")


def fetch_symbol_url(wiki_url: str) -> str | None:
    """Scrape the Wikipedia infobox for the election symbol image URL."""
    try:
        req = urllib.request.Request(
            wiki_url, headers={"User-Agent": "IndiaVotesBot/1.0"}
        )
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8")
    except Exception as e:
        print(f"    FETCH ERROR: {e}")
        return None

    # Find the infobox table
    infobox_start = html.find('class="infobox')
    if infobox_start < 0:
        return None
    infobox_end = html.find("</table>", infobox_start)
    if infobox_end < 0:
        return None
    infobox = html[infobox_start:infobox_end]

    # Find "Election symbol" row in the infobox
    sym_idx = infobox.lower().find("election symbol")
    if sym_idx < 0:
        return None

    # Extract the first <img> src after "Election symbol"
    chunk = infobox[sym_idx : sym_idx + 1000]
    img_match = re.search(r'<img[^>]+src="([^"]+)"', chunk)
    if not img_match:
        return None

    url = img_match.group(1)
    # Normalize: ensure https:// prefix
    if url.startswith("//"):
        url = "https:" + url
    return url


def main():
    apply = "--apply" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT abv, name, wikipedia_url FROM parties "
        "WHERE wikipedia_url IS NOT NULL AND wikipedia_url != ''"
    )
    parties = cur.fetchall()
    print(f"Found {len(parties)} parties with wikipedia_url\n")

    updated = 0
    for abv, name, wiki_url in parties:
        print(f"[{abv}] {name}")
        print(f"  wiki: {wiki_url}")
        symbol_url = fetch_symbol_url(wiki_url)
        if symbol_url:
            print(f"  => {symbol_url}")
            if apply:
                cur.execute(
                    "UPDATE parties SET symbol_url = ? WHERE abv = ?",
                    (symbol_url, abv),
                )
                updated += 1
        else:
            print("  => (no symbol found)")

    if apply:
        conn.commit()
        print(f"\nUpdated {updated} rows in DB")

        # Also update CSV
        _update_csv()
        print(f"Updated {CSV_PATH}")
    else:
        print(f"\nDry run — no changes written. Use --apply to write.")

    conn.close()


def _update_csv():
    """Rewrite parties.csv with symbol_url from DB."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT abv, symbol_url FROM parties WHERE symbol_url IS NOT NULL")
    symbol_map = dict(cur.fetchall())
    conn.close()

    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    for row in rows:
        if row["abv"] in symbol_map:
            row["symbol_url"] = symbol_map[row["abv"]]

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
