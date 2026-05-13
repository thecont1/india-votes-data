#!/usr/bin/env python3
"""
ECI Results Scraper - CLI Entry Point

Scrapes ECI election results from constituency-wise pages.
Always writes to the database (rounds table, round_no=999).
Optionally saves CSV/JSON with --csv/--json flags.

Usage:
  python cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
  python cli.py --url "..." 50           # scrape 50 constituencies
  python cli.py --url "..." --csv        # also save CSV
  python cli.py --url "..." --json       # also save JSON
  python cli.py --url "..." --respect    # respectful mode
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter

from core.browser import create_chrome_driver
from core.output import write_csv, write_json, output_path
from core.scraper import (
    build_constituency_url,
    get_state_name,
    parse_partywise_url,
    scrape_worker,
)
from db_utils import insert_round_snapshot


def show_usage():
    print("""
Usage: python cli.py --url <partywise_results_url> [limit] [--csv] [--json] [--respect]

Description:
    Scrapes ECI election results from constituency-wise pages.
    Always writes to the database (rounds table, round_no=999 for final results).

Required Arguments:
    --url       Party-wise results page URL

Optional Arguments:
    limit       Number of constituencies to scrape (default: 3)
    --csv       Also save results to CSV
    --json      Also save results to JSON
    --respect   Respectful scraping mode (1s pause every 10 URLs)
""")


def write_to_db(results: list, state_code: str, state_name: str, election_type: str):
    """Write scraped results to the database (round_no=999 for final results)."""
    ac_count = 0
    for constituency in results:
        ac_no = int(constituency.get("constituency_no", 0))
        ac_name = constituency.get("constituency", f"AC-{ac_no}")
        tally = constituency.get("voting_tally", [])

        if not tally:
            continue

        candidates = [
            {
                "candidate": c.get("candidate", ""),
                "party": c.get("party", ""),
                "votes": int(c.get("evm_votes", 0) or 0) + int(c.get("postal_votes", 0) or 0),
            }
            for c in tally
        ]

        insert_round_snapshot(
            state_code=state_code,
            state_name=state_name,
            ac_no=ac_no,
            ac_name=ac_name,
            round_no=999,
            total_rounds=None,
            candidates=candidates,
            scraped_at=datetime.now(timezone.utc).isoformat(),
            election_type=election_type,
        )
        ac_count += 1

    return ac_count


def main():
    parser = argparse.ArgumentParser(description="Scrape ECI election results")
    parser.add_argument("--url", required=True,
                        help="Party-wise results page URL")
    parser.add_argument("limit", nargs="?", type=int, default=3,
                        help="Number of constituencies to scrape (default: 3)")
    parser.add_argument("--csv", action="store_true",
                        help="Also save results to CSV")
    parser.add_argument("--json", action="store_true",
                        help="Also save results to JSON")
    parser.add_argument("--respect", action="store_true",
                        help="Respectful scraping mode")
    args = parser.parse_args()

    try:
        election_identifier, state_code = parse_partywise_url(args.url)
    except ValueError as e:
        print(f"Error: {e}")
        show_usage()
        return

    driver = create_chrome_driver()
    results = []
    thread_lock = Lock()

    try:
        url = build_constituency_url(election_identifier, state_code, 1)
        driver.get(url)

        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))

        h1 = driver.find_element(By.TAG_NAME, 'h1').text
        h2 = driver.find_element(By.TAG_NAME, 'h2').text
        state_name = h2.split('(')[-1].replace(')', '')
        election_year = h1.split('-')[-1].strip()
        election_type = ''.join(h2.split()[:1])

        print(f"{election_year} {election_type} Elections, {state_name}")
        mode = "Respectful" if args.respect else "High-Speed (5 workers)"
        print(f"Download Engine: {mode}\n")

        start_time = perf_counter()

        if args.respect:
            state = {'current': 1, 'end_of_results': False}
            scrape_worker(election_identifier, state_code, results, state, thread_lock, True)
        else:
            num_workers = 5
            state = {'current': 1, 'end_of_results': False}
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [
                    executor.submit(scrape_worker, election_identifier, state_code,
                                    results, state, thread_lock, False)
                    for _ in range(num_workers)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Worker error: {e}")

        total_time = perf_counter() - start_time
        print(f"\nScraped {len(results)} constituencies in {total_time:.1f}s")

    except Exception as e:
        print(f"Scraping stopped due to error: {e}")
    finally:
        driver.quit()

    if not results:
        print("No results to save.")
        return

    results.sort(key=lambda x: int(x.get("constituency_no", 0)))

    # Always write to database
    state_name_full = get_state_name(state_code)
    ac_count = write_to_db(results, state_code, state_name_full, election_type)
    print(f"Written {ac_count} constituencies to database (round_no=999)")

    # Optionally save CSV/JSON
    meta = {
        'election_year': election_year,
        'election_type': election_type,
        'election_state': state_code,
    }
    if args.csv:
        write_csv(results, output_path("./data/csv", meta, "csv"), meta)
    if args.json:
        write_json(results, output_path("./data/json", meta, "json"), meta)


if __name__ == "__main__":
    main()
