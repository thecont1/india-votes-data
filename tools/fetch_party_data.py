#!/usr/bin/env python3
"""Fetch party data from Wikipedia with rate-limit handling."""

import sqlite3
import json
import re
import time
import urllib.request
import urllib.parse

DB_PATH = "data/india-votes-data.db"
DELAY = 1.5  # seconds between requests
MAX_RETRIES = 3

def wiki_request(params, retries=MAX_RETRIES):
    """Make a Wikipedia API request with retry on 429."""
    params["format"] = "json"
    url = f"https://en.wikipedia.org/w/api.php?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "IndiaVotesDataBot/1.0 (educational; contact: mahesh@example.com)"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = DELAY * (2 ** attempt)
                print(f"    Rate limited, waiting {wait:.1f}s...")
                time.sleep(wait)
            else:
                print(f"    HTTP {e.code} for {url[:80]}")
                return None
        except Exception as e:
            print(f"    Error: {e}")
            return None
    print(f"    Max retries exceeded")
    return None


def search_wikipedia(query):
    """Search Wikipedia for a party page."""
    data = wiki_request({
        "action": "query",
        "list": "search",
        "srsearch": f'"{query}" political party India',
        "srlimit": 3,
    })
    if data:
        results = data.get("query", {}).get("search", [])
        if results:
            return results[0]["title"]
    return None


def get_wiki_page(title):
    """Get the wikitext of a Wikipedia page."""
    data = wiki_request({
        "action": "parse",
        "page": title,
        "prop": "wikitext",
    })
    if data:
        return data.get("parse", {}).get("wikitext", {}).get("*", "")
    return ""


def extract_infobox(wikitext):
    """Extract infobox fields from wikitext."""
    result = {}
    
    # Find infobox
    for pattern in [
        r'\{\{Infobox\s+political party(.*?)\n\}\}',
        r'\{\{Infobox\s+party(.*?)\n\}\}',
        r'\{\{Infobox\s+(?:Indian\s+)?political\s+party(.*?)\n\}\}',
    ]:
        m = re.search(pattern, wikitext, re.DOTALL | re.IGNORECASE)
        if m:
            infobox = m.group(0)
            break
    else:
        return result
    
    def clean_wiki(val):
        val = re.sub(r'\[\[([^|\]]*\|)?([^\]]+)\]\]', r'\2', val)
        val = re.sub(r'\{\{(?:coord\|[^}]*\}\}|[^}]+\}\})', '', val)
        val = re.sub(r'<ref[^>]*>.*?</ref>', '', val, flags=re.DOTALL)
        val = re.sub(r'<ref[^/]*/>', '', val)
        val = re.sub(r'<[^>]+>', '', val)
        val = val.strip()
        return val
    
    # Leader/chief
    for field in ["leader_name", "leader", "leader1", "president", "general_secretary", "party_leader"]:
        m = re.search(r'\|\s*' + field + r'\s*=\s*(.+?)(?:\n|\|)', infobox, re.IGNORECASE)
        if m:
            val = clean_wiki(m.group(1))
            if val and len(val) < 200 and not val.startswith("{{"):
                result["leader"] = val
                break
    
    # Founded
    for field in ["founded", "foundation", "date"]:
        m = re.search(r'\|\s*' + field + r'\s*=\s*(.+?)(?:\n|\|)', infobox, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            year_match = re.search(r'(\d{4})', val)
            if year_match:
                yr = int(year_match.group(1))
                if 1900 <= yr <= 2026:
                    result["founded"] = yr
            break
    
    # Colours
    for field in ["colour", "color", "colors", "colours"]:
        m = re.search(r'\|\s*' + field + r'\s*=\s*(.+?)(?:\n|\|)', infobox, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            hex_colors = re.findall(r'#(?:[0-9A-Fa-f]{3}){1,2}', val)
            if hex_colors:
                result["colour"] = ",".join(hex_colors[:3])
            break
    
    # Symbol
    for field in ["symbol", "logo"]:
        m = re.search(r'\|\s*' + field + r'\s*=\s*(.+?)(?:\n|\|)', infobox, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            img_match = re.search(r'(https?://upload\.wikimedia\.org/[^\s\]|}]+)', val)
            if img_match:
                url = img_match.group(1)
                # Clean up thumbnail URL to get original
                url = re.sub(r'/thumb/', '/', url)
                url = re.sub(r'/\d+px-[^/]+$', '', url)
                result["symbol_url"] = url
            break
    
    # Alliance
    for field in ["alliance", "coalition"]:
        m = re.search(r'\|\s*' + field + r'\s*=\s*(.+?)(?:\n|\|)', infobox, re.IGNORECASE)
        if m:
            val = clean_wiki(m.group(1))
            if val and len(val) < 200 and not val.startswith("{{"):
                result["alliance"] = val
            break
    
    return result


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get parties needing update (no wiki url) with significant votes
    cur.execute("""
        SELECT p.abv, p.name, p.wikipedia_url, p.chief, p.colour, p.founded, p.alliance, p.symbol_url,
               COALESCE(SUM(r.votes), 0) as total_votes
        FROM parties p
        LEFT JOIN rounds_ac r ON p.abv = r.party_abv
        WHERE p.abv != 'IND'
          AND (p.wikipedia_url IS NULL OR p.wikipedia_url = '')
        GROUP BY p.abv
        HAVING total_votes > 5000
        ORDER BY total_votes DESC
    """)
    parties = cur.fetchall()
    print(f"Parties needing wiki data (>5k votes): {len(parties)}")
    
    updated = 0
    no_page = 0
    
    for i, party in enumerate(parties):
        abv = party["abv"]
        name = party["name"]
        
        print(f"[{i+1}/{len(parties)}] {abv} - {name}")
        
        wiki_title = search_wikipedia(name)
        time.sleep(DELAY)
        
        if not wiki_title:
            print(f"  No Wikipedia page found")
            no_page += 1
            continue
        
        wiki_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(wiki_title.replace(' ', '_'))}"
        print(f"  Wiki: {wiki_title}")
        
        wikitext = get_wiki_page(wiki_title)
        time.sleep(DELAY)
        
        if not wikitext:
            # At least save the URL
            cur.execute("UPDATE parties SET wikipedia_url = ? WHERE abv = ? AND (wikipedia_url IS NULL OR wikipedia_url = '')", (wiki_url, abv))
            conn.commit()
            updated += 1
            continue
        
        info = extract_infobox(wikitext)
        
        sets = ["wikipedia_url = ?"]
        params = [wiki_url]
        
        if info.get("leader") and not party["chief"]:
            sets.append("chief = ?")
            params.append(info["leader"])
        if info.get("colour") and not party["colour"]:
            sets.append("colour = ?")
            params.append(info["colour"])
        if info.get("founded") and not party["founded"]:
            sets.append("founded = ?")
            params.append(info["founded"])
        if info.get("alliance") and not party["alliance"]:
            sets.append("alliance = ?")
            params.append(info["alliance"])
        if info.get("symbol_url") and not party["symbol_url"]:
            sets.append("symbol_url = ?")
            params.append(info["symbol_url"])
        
        params.append(abv)
        cur.execute(f"UPDATE parties SET {', '.join(sets)} WHERE abv = ?", params)
        conn.commit()
        updated += 1
        
        fields = [s.split(" =")[0] for s in sets]
        print(f"  Updated: {', '.join(fields)}")
    
    # Now fill in missing data for parties that already have wiki_url
    print("\n=== Filling gaps for parties with existing wiki_url ===")
    cur.execute("""
        SELECT abv, name, wikipedia_url, chief, colour, founded, alliance, symbol_url
        FROM parties
        WHERE wikipedia_url IS NOT NULL AND wikipedia_url != ''
          AND (chief IS NULL OR chief = '' OR colour IS NULL OR colour = '' OR founded IS NULL OR symbol_url IS NULL OR symbol_url = '')
    """)
    incomplete = cur.fetchall()
    print(f"Parties with partial data: {len(incomplete)}")
    
    for party in incomplete:
        abv = party["abv"]
        wiki_url = party["wikipedia_url"]
        title = urllib.parse.unquote(wiki_url.split("/wiki/")[-1]) if "/wiki/" in wiki_url else None
        if not title:
            continue
        
        print(f"  {abv} - {party['name']}")
        
        wikitext = get_wiki_page(title)
        time.sleep(DELAY)
        
        if not wikitext:
            continue
        
        info = extract_infobox(wikitext)
        
        sets = []
        params = []
        
        if info.get("leader") and not party["chief"]:
            sets.append("chief = ?")
            params.append(info["leader"])
        if info.get("colour") and not party["colour"]:
            sets.append("colour = ?")
            params.append(info["colour"])
        if info.get("founded") and not party["founded"]:
            sets.append("founded = ?")
            params.append(info["founded"])
        if info.get("alliance") and not party["alliance"]:
            sets.append("alliance = ?")
            params.append(info["alliance"])
        if info.get("symbol_url") and not party["symbol_url"]:
            sets.append("symbol_url = ?")
            params.append(info["symbol_url"])
        
        if sets:
            params.append(abv)
            cur.execute(f"UPDATE parties SET {', '.join(sets)} WHERE abv = ?", params)
            conn.commit()
            print(f"    +{', '.join(s.split(' =')[0] for s in sets)}")
    
    conn.close()
    print(f"\n=== Done: {updated} updated, {no_page} no page found ===")


if __name__ == "__main__":
    main()
