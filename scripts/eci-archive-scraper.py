#!/usr/bin/env python3
"""
Scrape archived ECI election results (old HTML table format) from archive.org.

Usage:
    python3 scripts/eci-archive-scraper.py <archive_url> [--state-code ST] [--dry-run]

Arguments:
    archive_url   Archive.org link to constituency #1 of a state
    --state-code  Two-letter state code for filenames (e.g. KA, MH, TN)
    --dry-run     Fetch only first 3 constituencies

Output is written incrementally (one constituency at a time) so that
an interruption never loses more than the current constituency.

Writes: data/csv/{year}Assembly-{ST}.csv
        data/json/{year}Assembly-{ST}.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import time

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ECI internal state codes -> standard 2-letter abbreviations
ECI_STATE_MAP = {
    "S01": "AN", "S02": "AP", "S03": "AR", "S04": "AS", "S05": "BR",
    "S06": "CH", "S07": "CG", "S08": "DN", "S09": "DD", "S10": "KA",
    "S11": "KL", "S12": "MP", "S13": "MH", "S14": "MN", "S15": "ML",
    "S16": "MZ", "S17": "NL", "S18": "OD", "S19": "PY", "S20": "PB",
    "S21": "RJ", "S22": "SK", "S23": "TN", "S24": "TS", "S25": "TR",
    "S26": "UP", "S27": "UK", "S28": "WB", "S29": "GA", "S30": "GJ",
    "S31": "HR", "S32": "HP", "S33": "JK", "S34": "JH",
}

CSV_HEADER = [
    "election_year", "election_type", "election_state",
    "constituency", "constituency_no", "serial_no",
    "candidate", "party", "evm_votes", "postal_votes",
]

MIN_JITTER = 1.0
MAX_JITTER = 2.5
REQUEST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def extract_archive_parts(url: str) -> tuple:
    """Split archive URL into (archive_prefix, eci_path)."""
    match = re.match(r"(https://web\.archive\.org/web/\d+/)(.*)", url)
    if not match:
        raise ValueError(f"Not an archive.org URL: {url}")
    return match.group(1), match.group(2)


def parse_eci_path(eci_path: str) -> tuple:
    """Extract (raw_code, election_id) from ECI URL path.

    raw_code is the concatenated state+ac string (e.g. "S101").
    The actual state code is resolved later from the page's hidden input.
    """
    match = re.search(r"/([^/]+)/Constituencywise(\w+)\.htm", eci_path)
    if not match:
        raise ValueError(f"Cannot parse ECI path: {eci_path}")
    return match.group(2), match.group(1)


def build_raw_url(archive_prefix: str, eci_url: str) -> str:
    """Convert archive.org URL to raw HTML version (id_ suffix)."""
    return archive_prefix.rstrip("/") + "id_/" + eci_url


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
    """Fetch a page via curl (avoids TLS fingerprint blocking)."""
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
# Page parsing
# ---------------------------------------------------------------------------

def discover_constituencies(html: str, raw_code: str) -> tuple:
    """Parse the hidden input to get all constituencies and the actual state code.

    Returns (state_code, constituencies)
    """
    soup = BeautifulSoup(html, "html.parser")
    state_code = None
    hidden = None

    for inp in soup.find_all("input", {"type": "hidden"}):
        inp_id = str(inp.get("id", "") or "")
        val = str(inp.get("value", "") or "")
        if inp_id.startswith("S") and "," in val and ";" in val:
            if raw_code.startswith(inp_id):
                hidden = inp
                state_code = inp_id
                break

    if not hidden:
        hidden = soup.find("input", {"id": raw_code, "type": "hidden"})
        if hidden:
            state_code = raw_code

    if not hidden:
        for inp in soup.find_all("input", {"type": "hidden"}):
            inp_id = str(inp.get("id", "") or "")
            val = str(inp.get("value", "") or "")
            if inp_id.startswith("S") and "," in val and ";" in val:
                hidden = inp
                state_code = inp_id
                break

    if not hidden or not state_code:
        raise ValueError(f"Could not find constituency list input for {raw_code}")

    raw = str(hidden.get("value", "") or "").strip().rstrip(";")
    if not raw:
        raise ValueError(f"Empty constituency list for {state_code}")

    constituencies = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",", 1)
        constituencies.append({"ac_no": int(parts[0].strip()), "ac_name": parts[1].strip()})

    constituencies.sort(key=lambda x: x["ac_no"])
    return state_code, constituencies


def extract_election_metadata(html: str) -> tuple:
    """Extract (title, year) from the page."""
    soup = BeautifulSoup(html, "html.parser")
    title_text = ""
    # Search h1-h5 using get_text() so nested tags (b, font, etc.) don't break matching
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        text = tag.get_text(strip=True)
        if re.search(r"(GENERAL|ASSEMBLY)\s+ELECTION", text, re.I):
            title_text = text
            break
    year_match = re.search(r"(\d{4})", title_text)
    year = int(year_match.group(1)) if year_match else 0
    title = title_text.replace("&amp;", "&") if title_text else f"Assembly Election {year}"
    return title, year


def parse_constituency_page(html: str) -> list:
    """Parse candidate rows from an old-format ECI constituency page."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    header_th = soup.find("th", string=re.compile(r"S\.?N\.?", re.I))  # type: ignore[call-overload]
    if not header_th:
        for th in soup.find_all("th"):
            if "S.N" in th.get_text() or "SN" in th.get_text().replace(".", ""):
                header_th = th
                break
    if not header_th:
        return candidates

    table = header_th.find_parent("table")
    if not table:
        return candidates

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

        candidates.append({
            "serial_no": first_text,
            "candidate": candidate_name,
            "party": cells[2].get_text(strip=True),
            "evm_votes": evm,
            "postal_votes": postal,
        })

    return candidates


def clean_constituency_name(raw_name: str) -> str:
    """Clean constituency name from header row.

    "Karnataka-Nippani -1" -> "NIPPANI"
    """
    if "-" in raw_name:
        raw_name = raw_name.split("-", 1)[1]
    raw_name = re.sub(r"\s*-\s*\d+\s*$", "", raw_name)
    return " ".join(raw_name.split()).strip().upper()


# ---------------------------------------------------------------------------
# Incremental writers
# ---------------------------------------------------------------------------

def init_csv(filepath: str) -> None:
    """Write CSV header if file doesn't exist or is empty."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)


def append_csv(filepath: str, result: dict, election_year: int, state_code_out: str) -> None:
    """Append one constituency's candidates to the CSV file."""
    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for c in result["candidates"]:
            writer.writerow([
                election_year, "Assembly", state_code_out,
                result["constituency_name"], result["constituency_no"],
                c["serial_no"], c["candidate"], c["party"],
                c["evm_votes"], c["postal_votes"],
            ])


def write_json(filepath: str, title: str, election_id: str, election_year: int,
               state_code_out: str, all_results: list) -> None:
    """Rewrite the full JSON file (called after each constituency).

    all_results is a list of constituencywise_results entries, each with
    source_url and voting_data keys.
    """
    output = {
        "title": title,
        "election_id": election_id,
        "election_year": str(election_year),
        "election_type": "Assembly",
        "election_state": state_code_out,
        "constituencywise_results": all_results,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def load_scraped_nos(csv_path: str) -> set:
    """Load constituency numbers already present in existing CSV."""
    scraped = set()
    try:
        with open(csv_path, "r") as f:
            for row in csv.DictReader(f):
                scraped.add(int(row["constituency_no"]))
    except FileNotFoundError:
        pass
    return scraped


def load_json_results(json_path: str) -> list:
    """Load existing constituencywise_results from JSON for resume."""
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
        return data.get("constituencywise_results", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape archived ECI election results from archive.org",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/eci-archive-scraper.py \\
    "https://web.archive.org/web/20230602041714/https://results.eci.gov.in/ResultAcGenMay2023/ConstituencywiseS101.htm?ac=1" \\
    --state-code KA
        """,
    )
    parser.add_argument("archive_url", help="Archive.org URL for constituency #1")
    parser.add_argument("--state-code", help="Two-letter state code for output (e.g. KA)")
    parser.add_argument("--election-id", required=True,
                        help="Election ID in AC-YYYY-MM format (e.g. AC-2023-06). "
                             "Multiple states sharing a result date use the same ID.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only first 3 constituencies")
    args = parser.parse_args()

    # 1. Parse URL
    archive_prefix, eci_path = extract_archive_parts(args.archive_url)
    raw_code, election_id = parse_eci_path(eci_path)
    print(f"Election ID : {election_id}")
    print(f"Archive     : {archive_prefix}")

    # 2. Fetch first page, discover constituencies
    raw_url = build_raw_url(archive_prefix, eci_path)
    print(f"Fetching constituency list...", flush=True)
    html = fetch_page(raw_url)
    state_code, constituencies = discover_constituencies(html, raw_code)
    title, election_year = extract_election_metadata(html)
    state_code_out = args.state_code or ECI_STATE_MAP.get(state_code, state_code) or state_code

    # Fallback: parse year from --election-id (format AC-YYYY-MM) if page scrape failed
    if election_year == 0:
        id_year_match = re.search(r"(\d{4})", args.election_id)
        if id_year_match:
            election_year = int(id_year_match.group(1))

    print(f"ECI state   : {state_code} -> {state_code_out}")
    print(f"Election    : {title}")
    print(f"Year        : {election_year}")
    print(f"Found {len(constituencies)} constituencies")

    if args.dry_run:
        constituencies = constituencies[:3]
        print(f"DRY RUN: first 3 only")
    print()

    # 3. Prepare output files (resume-aware)
    csv_path = f"data/csv/{election_year}Assembly-{state_code_out}.csv"
    json_path = f"data/json/{election_year}Assembly-{state_code_out}.json"
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    scraped_nos = load_scraped_nos(csv_path)
    json_results = load_json_results(json_path)

    if scraped_nos:
        print(f"Resuming: {len(scraped_nos)} constituencies already in {csv_path}")
        print()

    init_csv(csv_path)

    # 4. Scrape loop — write incrementally
    eci_base = f"https://results.eci.gov.in/{election_id}/"
    total = len(constituencies)
    errors = 0

    for i, ac in enumerate(constituencies):
        ac_no = ac["ac_no"]
        ac_name = ac["ac_name"]

        if ac_no in scraped_nos:
            print(f"[{i+1}/{total}] {ac_name} ({ac_no}) — already scraped, skipping")
            continue

        eci_url = f"{eci_base}Constituencywise{state_code}{ac_no}.htm?ac={ac_no}"
        url = build_raw_url(archive_prefix, eci_url)
        print(f"[{i+1}/{total}] {ac_name} ({ac_no})...", end=" ", flush=True)

        try:
            page_html = fetch_page(url)
            candidates = parse_constituency_page(page_html)

            # Get clean constituency name from the page
            page_soup = BeautifulSoup(page_html, "html.parser")
            name_td = page_soup.find("td", string=re.compile(  # type: ignore[call-overload]
                r"Karnataka-|Maharashtra-|Tamil Nadu-|Uttar Pradesh-|Madhya Pradesh-"
                r"|West Bengal-|Gujarat-|Rajasthan-|Andhra Pradesh-|Telangana-"
                r"|Odisha-|Kerala-|Punjab-|Haryana-|Jharkhand-|Bihar-"
                r"|Chhattisgarh-|Assam-|Goa-|Himachal Pradesh-|Uttarakhand-"
                r"|Tripura-|Manipur-|Meghalaya-|Mizoram-|Nagaland-|Sikkim-"
                r"|Arunachal Pradesh-|Puducherry-|Jammu"
            ))
            display_name = clean_constituency_name(
                name_td.get_text(strip=True)
            ) if name_td else ac_name.upper()

            result = {
                "constituency_no": ac_no,
                "constituency_name": display_name,
                "source_url": eci_url,
                "candidates": candidates,
            }

            # Incremental write: append CSV, rewrite JSON
            append_csv(csv_path, result, election_year, state_code_out)
            json_results.append({
                "source_url": eci_url,
                "voting_data": {
                    "constituency_no": str(ac_no),
                    "constituency": display_name,
                    "voting_tally": [
                        {
                            "serial_no": c["serial_no"],
                            "candidate": c["candidate"],
                            "party": c["party"],
                            "evm_votes": c["evm_votes"],
                            "postal_votes": c["postal_votes"],
                        }
                        for c in candidates
                    ],
                },
            })
            write_json(json_path, title, args.election_id, election_year, state_code_out, json_results)

            scraped_nos.add(ac_no)
            print(f"{len(candidates)} candidates")

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

        time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))

    # 5. Summary
    total_candidates = sum(
        len(r.get("voting_data", {}).get("voting_tally", [])) for r in json_results
    )
    print()
    print(f"Done: {len(json_results)} constituencies, {total_candidates} candidates")
    if errors:
        print(f"  Errors: {errors} constituencies failed (re-run to retry)")
    print(f"  CSV : {csv_path}")
    print(f"  JSON: {json_path}")


if __name__ == "__main__":
    main()
