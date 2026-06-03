#!/usr/bin/env python3
"""
Scrape bye-election results (roundwise) from results.eci.gov.in.

Usage:
    python3 scripts/eci-bye-scraper.py
    python3 scripts/eci-bye-scraper.py --dry-run
    python3 scripts/eci-bye-scraper.py --url https://results.eci.gov.in/ResultAcByeMay2026/index.htm

Output: data/json/bye-elections/YYYY-MM.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import random

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_JITTER = 1.0
MAX_JITTER = 2.0
REQUEST_TIMEOUT = 20

# ECI state codes -> standard 2-letter codes (for output)
ECI_TO_STD = {
    "S01": "AN", "S02": "AP", "S03": "AR", "S04": "AS", "S05": "BR",
    "S06": "GJ", "S07": "HR", "S08": "HP", "S10": "KA", "S11": "KL",
    "S12": "MP", "S13": "MH", "S14": "MN", "S15": "ML", "S16": "MZ",
    "S17": "NL", "S18": "OD", "S19": "PY", "S20": "PB", "S21": "RJ",
    "S22": "SK", "S23": "TN", "S24": "TS", "S25": "TR", "S26": "UP",
    "S27": "UK", "S28": "WB", "S29": "GA", "S30": "GJ", "S31": "HR",
    "S32": "HP", "S33": "JK", "S34": "JH",
    "U01": "AN", "U02": "CH", "U03": "DD", "U05": "DL", "U06": "LD",
    "U07": "PY", "U08": "JK", "U09": "LA",
}

ECI_STATE_NAMES = {
    "S01": "Andhra Pradesh", "S02": "Arunachal Pradesh", "S03": "Assam",
    "S04": "Bihar", "S05": "Goa", "S06": "Gujarat", "S07": "Haryana",
    "S08": "Himachal Pradesh", "S10": "Karnataka", "S11": "Kerala",
    "S12": "Madhya Pradesh", "S13": "Maharashtra", "S14": "Manipur",
    "S15": "Meghalaya", "S16": "Mizoram", "S17": "Nagaland", "S18": "Odisha",
    "S19": "Punjab", "S20": "Rajasthan", "S21": "Sikkim", "S22": "Tamil Nadu",
    "S23": "Tripura", "S24": "Uttar Pradesh", "S25": "West Bengal",
    "S26": "Chhattisgarh", "S27": "Jharkhand", "S28": "Uttarakhand",
    "S29": "Telangana",
    "U01": "Andaman & Nicobar", "U02": "Chandigarh",
    "U03": "Dadra & Nagar Haveli", "U05": "Delhi", "U06": "Lakshadweep",
    "U07": "Puducherry", "U08": "Jammu & Kashmir", "U09": "Ladakh",
}

# Party name normalization (same as load-json-to-d1.py)
HARDCODED_PARTIES = {
    "Janata Dal  (Secular)": "JD(S)", "Janata Dal (Secular)": "JD(S)",
    "Janata Dal  (United)": "JD(U)", "Janata Dal (United)": "JD(U)",
    "None of the Above": "NOTA", "Bharat Rashtra Samithi": "BRS",
    "Shiv Sena (Uddhav Balasaheb Thackeray)": "SHS(UBT)",
    "Rashtriya Janata Dal": "RJD", "Rashtriya Lok Dal": "RLD",
    "Jammu & Kashmir National Conference": "JKNC",
    "Jammu and Kashmir National Conference": "JKNC",
    "Jammu & Kashmir Peoples Democratic Party": "JKPDP",
    "Indian National Lok Dal": "INLD", "Haryana Lokhit Party": "HLP",
    "Jannayak Janta Party": "JJP",
    "All India Majlis-E-Ittehadul Muslimeen": "AIMIM",
    "All India Majlis-e-Ittehadul Muslimeen": "AIMIM",
    "Communist Party of India (Marxist-Leninist) (Liberation)": "CPI(ML)(L)",
    "Communist Party of India  (Marxist)": "CPI(M)",
    "Communist Party of India (Marxist)": "CPI(M)",
    "Vikassheel Insaan Party": "VIP",
    "Hindustani Awam Morcha (Secular)": "HAM(S)",
    "Rashtriya Lok Morcha": "RLM",
    "Lok Janshakti Party(Ram Vilas)": "LJP(RV)",
}

_party_cache = None


def _load_party_map() -> dict:
    """Load {full_name: abbreviation} from data/parties.csv."""
    global _party_cache
    if _party_cache is not None:
        return _party_cache
    import csv
    name_to_abv = {}
    parties_csv = os.path.join(os.path.dirname(__file__), "..", "data", "parties.csv")
    try:
        with open(parties_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                abv = row.get("abv", "").strip()
                name = row.get("name", "").strip()
                if abv and name:
                    name_to_abv[name] = abv
                for alias in row.get("aliases", "").split(","):
                    alias = alias.strip()
                    if alias:
                        name_to_abv[alias] = abv
    except FileNotFoundError:
        pass
    _party_cache = name_to_abv
    return name_to_abv


def normalize_party(name: str) -> str:
    """Normalize party name to abbreviation."""
    if not name:
        return name
    name = name.strip()
    if name in HARDCODED_PARTIES:
        return HARDCODED_PARTIES[name]
    party_map = _load_party_map()
    if name in party_map:
        return party_map[name]
    for full_name, abv in party_map.items():
        if full_name.lower() == name.lower():
            return abv
    known_abvs = set(party_map.values())
    if name in known_abvs:
        return name
    return name


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
    """Fetch a page via curl (avoids Akamai TLS fingerprint blocking)."""
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "--compressed", "--max-time", str(timeout), url],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if r.returncode != 0:
                raise RuntimeError(f"curl exit {r.returncode}")
            html = r.stdout
            if not html or len(html) < 100:
                raise RuntimeError(f"Empty response ({len(html)} bytes)")
            if "Access Denied" in html[:500]:
                raise RuntimeError("Access Denied")
            return html
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 2
                print(f"  retry in {wait}s ({e})...", flush=True)
                time.sleep(wait)
            else:
                raise
    return ""


# ---------------------------------------------------------------------------
# Index page parsing
# ---------------------------------------------------------------------------

def discover_constituencies(index_url: str) -> list[dict]:
    """Parse the bye-election index page to find all constituencies.

    Returns list of {state_code, ac_no, ac_name, state_name, candidateswise_url}.
    """
    html = fetch_page(index_url)
    soup = BeautifulSoup(html, "html.parser")

    # Derive the base URL (everything before the filename)
    base_url = index_url.rsplit("/", 1)[0]

    constituencies = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        m = re.match(r"candidateswise-(S\d{2})(\d+)\.htm", href)
        if not m:
            continue
        state_code = m.group(1)
        ac_no = int(m.group(2))

        # Get constituency name and state from the card
        parent = a_tag.find_parent("div", class_=re.compile(r"const-box"))
        ac_name = ""
        state_name = ECI_STATE_NAMES.get(state_code, "")
        if parent:
            h3 = parent.find("h3")
            h4 = parent.find("h4")
            if h3 is not None:
                # Text like "UMRETH (111)" -> extract name
                raw = h3.get_text(strip=True)
                nm = re.match(r"(.+?)\s*\(\d+\)", raw)
                ac_name = nm.group(1).strip() if nm else raw
            if h4 is not None:
                state_name = h4.get_text(strip=True)

        constituencies.append({
            "state_code": state_code,
            "ac_no": ac_no,
            "ac_name": ac_name,
            "state_name": state_name,
        })

    constituencies.sort(key=lambda c: (c["state_code"], c["ac_no"]))
    return constituencies


# ---------------------------------------------------------------------------
# Roundwise page parsing (all rounds)
# ---------------------------------------------------------------------------

def extract_all_rounds(html: str, ac_no: int) -> dict:
    """Extract ALL rounds from a roundwise page.

    Returns {ac_name, rounds: [{round_no, candidates: [{candidate, party, votes}]}]}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Constituency name from h2 > span
    ac_name = f"AC-{ac_no}"
    h2 = soup.find("h2")
    if h2:
        span = h2.find("span")  # type: ignore[union-attr]
        full_text = span.get_text() if span else h2.get_text()  # type: ignore[union-attr]
        m = re.search(r"\d+\s*[-–]\s*(.+?)\s*\(", full_text)
        if m:
            ac_name = m.group(1).strip()

    # Find all tab divs
    tab_divs = soup.find_all("div", id=re.compile(r"^tab\d+$"))
    rounds = []
    for tab_div in tab_divs:
        tab_id = str(tab_div.get("id", ""))
        m = re.match(r"tab(\d+)$", tab_id)
        if not m:
            continue
        round_no = int(m.group(1))

        tbody = tab_div.find("tbody")
        if not tbody:
            continue

        candidates = []
        for row in tbody.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 5:
                continue
            candidate_name = cols[0].get_text(strip=True)
            party_name = cols[1].get_text(strip=True)
            total_votes_text = cols[4].get_text(strip=True).replace(",", "").replace(" ", "")
            if not candidate_name or candidate_name.lower() == "total":
                continue
            if not total_votes_text.isdigit():
                continue
            candidates.append({
                "candidate": candidate_name,
                "party_abv": normalize_party(party_name),
                "votes": int(total_votes_text),
            })

        if candidates:
            rounds.append({"round_no": round_no, "candidates": candidates})

    rounds.sort(key=lambda r: r["round_no"])
    return {"ac_name": ac_name, "rounds": rounds}


# ---------------------------------------------------------------------------
# Constituencywise page parsing (postal votes)
# ---------------------------------------------------------------------------

def extract_postal_votes(html: str) -> list[dict]:
    """Extract final results (EVM + Postal) from constituencywise page.

    Returns list of {candidate, party_abv, evm_votes, postal_votes, total_votes}.
    """
    soup = BeautifulSoup(html, "html.parser")

    header_th = soup.find("th", string=re.compile(r"S\.?N\.?", re.I))  # type: ignore[call-overload]
    if not header_th:
        for th in soup.find_all("th"):
            if "S.N" in th.get_text() or "SN" in th.get_text().replace(".", ""):
                header_th = th
                break
    if not header_th:
        return []

    # header_th might be None from the type checker's perspective
    table = header_th.find_parent("table") if header_th else None
    if not table:
        return []

    results = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        first_text = cells[0].get_text(strip=True)
        if not first_text.isdigit():
            continue
        candidate_name = cells[1].get_text(strip=True)
        if candidate_name.upper() == "TOTAL":
            continue

        evm = cells[3].get_text(strip=True).replace(",", "").replace(" ", "")
        postal = cells[4].get_text(strip=True).replace(",", "").replace(" ", "")
        total = cells[5].get_text(strip=True).replace(",", "").replace(" ", "")

        try:
            int(evm)
            int(postal)
            int(total)
        except ValueError:
            continue

        results.append({
            "candidate": candidate_name,
            "party_abv": normalize_party(cells[2].get_text(strip=True)),
            "evm_votes": int(evm),
            "postal_votes": int(postal),
            "total_votes": int(total),
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape bye-election results (roundwise) from ECI",
    )
    parser.add_argument(
        "--url",
        default="https://results.eci.gov.in/ResultAcByeMay2026/index.htm",
        help="Bye-election index page URL",
    )
    parser.add_argument(
        "--election-id",
        default="BYE-2026-05",
        help="Election ID (default: BYE-2026-05)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: data/json/bye-elections/YYYY-MM.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scrape first 2 only")
    args = parser.parse_args()

    # Derive output path
    if args.output is None:
        m = re.search(r"(\d{4})-(\d{2})", args.election_id)
        if m:
            args.output = f"data/json/bye-elections/{m.group(1)}-{m.group(2)}.json"
        else:
            args.output = "data/json/bye-elections/bye-election.json"

    print(f"Index URL   : {args.url}")
    print(f"Election ID : {args.election_id}")
    print(f"Output      : {args.output}")
    print()

    # 1. Discover constituencies from index page
    print("Fetching index page...", flush=True)
    constituencies = discover_constituencies(args.url)
    print(f"Found {len(constituencies)} constituencies")

    if args.dry_run:
        constituencies = constituencies[:2]
        print(f"DRY RUN: scraping first 2 only")
    print()

    # 2. Scrape each constituency
    base_url = args.url.rsplit("/", 1)[0]
    all_results = []
    errors = 0

    for i, ac in enumerate(constituencies):
        state_code = ac["state_code"]
        ac_no = ac["ac_no"]
        ac_name = ac["ac_name"]
        state_name = ac["state_name"]
        state_std = ECI_TO_STD.get(state_code, state_code)

        print(f"[{i+1}/{len(constituencies)}] {ac_name} ({state_code}/{ac_no}) — {state_name}", flush=True)

        try:
            # Fetch roundwise page
            roundwise_url = f"{base_url}/RoundwiseS{state_code[1:]}{ac_no}.htm"
            print(f"  Roundwise: {roundwise_url}", flush=True)
            rw_html = fetch_page(roundwise_url)

            round_data = extract_all_rounds(rw_html, ac_no)
            ac_name = round_data["ac_name"] or ac_name
            num_rounds = len(round_data["rounds"])
            print(f"  Rounds: {num_rounds}", end="", flush=True)

            # Fetch constituencywise page for postal votes
            constwise_url = f"{base_url}/ConstituencywiseS{state_code[1:]}{ac_no}.htm"
            print(f" | Postal...", end="", flush=True)
            cw_html = fetch_page(constwise_url)
            postal_data = extract_postal_votes(cw_html)
            print(f" {len(postal_data)} candidates")

            all_results.append({
                "state_code": state_code,
                "state_std": state_std,
                "state_name": state_name,
                "ac_no": ac_no,
                "ac_name": ac_name,
                "rounds": round_data["rounds"],
                "postal_votes": postal_data,
            })

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

        time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))

    # 3. Write output
    output = {
        "election_id": args.election_id,
        "title": "Bye Election to Assembly Constituencies: Results",
        "election_year": args.election_id.split("-")[1] if "-" in args.election_id else "2026",
        "election_month": args.election_id.split("-")[2] if len(args.election_id.split("-")) >= 3 else "",
        "election_type": "Bye-Election",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "constituencies": all_results,
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # 4. Summary
    total_rounds = sum(len(r["rounds"]) for r in all_results)
    total_candidates = sum(
        len(r["rounds"][-1]["candidates"]) if r["rounds"] else 0
        for r in all_results
    )
    print()
    print(f"Done: {len(all_results)} constituencies, {total_rounds} total rounds, {total_candidates} final candidates")
    if errors:
        print(f"  Errors: {errors}")
    print(f"  Output: {args.output}")


if __name__ == "__main__":
    main()
