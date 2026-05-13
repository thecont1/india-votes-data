#!/usr/bin/env python3
"""
ECI Results Scraper - CLI Entry Point

This module provides the command-line interface for the ECI results scraper.
It imports core functionality from the core/ package.
"""

import argparse
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from time import perf_counter

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.browser import create_chrome_driver
from core.scraper import (
    build_constituency_url,
    extract_results,
    get_state_code,
    parse_partywise_url,
)


def show_usage():
    """Display friendly usage guide."""
    print("""
Usage: python cli.py --url <partywise_results_url> [limit] [--respect]

Description:
    Scrapes ECI election results from constituency-wise pages.

Required Arguments:
    --url       Party-wise results page URL (mandatory)
                Format: https://results.eci.gov.in/<election_identifier>/partywiseresult-<state_code>.htm
                Example: https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm
                Note: State codes use 'S##' for states and 'U##' for Union Territories

Optional Arguments:
    limit       Number of constituencies to scrape (default: 3)
                Set to a high number to scrape all constituencies until end of results
    --respect   Enable respectful scraping mode with 1-second pause every 10 URLs
                Without this flag, uses multi-threaded scraping (up to 5 workers)

Output Files:
    YYYYAssembly-XX_YYYYMMDD_HHMMSS.csv and .json

Examples:
    python cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
    python cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" 50
    python cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" --respect
""")


def scrape_constituency_worker(election_identifier: str, state_code: str,
                               result_list: list, url_counter: dict, lock: Lock,
                               respect_mode: bool = False):
    """
    Worker that continuously picks up new URL ranges until end of results.
    """
    driver = create_chrome_driver()
    
    try:
        while True:
            with lock:
                if url_counter.get('end_of_results'):
                    break
                seq_no = url_counter['current']
                url_counter['current'] += 1
            
            url = build_constituency_url(election_identifier, state_code, seq_no)
            driver.get(url)
            
            if "404" in driver.title:
                with lock:
                    if not url_counter.get('end_of_results'):
                        url_counter['end_of_results'] = True
                        print(f" {seq_no:03d}-STOP.")
                break

            result = extract_results(driver)
            if result:
                with lock:
                    result_list.append({"source_url": url, "voting_data": result})
                    constituency_label = result.get("constituency")
                    suffix = f" {seq_no:03d}-{constituency_label}." if constituency_label else ""
                    print(f"{suffix} Done.")

            # Respect mode: pause every 10 URLs
            if respect_mode:
                with lock:
                    url_counter['count'] = url_counter.get('count', 0) + 1
                    if url_counter['count'] % 10 == 0:
                        print("[Respect mode] Taking 1-second pause...")
                        time.sleep(1)
                    
    except (NoSuchElementException, TimeoutException, AssertionError) as e:
        with lock:
            print(f"Scraping stopped due to error: {e}")
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Scrape selected constituencies from ECI results")
    parser.add_argument(
        "--url",
        required=True,
        help="Party-wise results page URL (e.g., https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm)",
    )
    parser.add_argument(
        "limit",
        nargs="?",
        type=int,
        default=3,
        help="Number of constituencies to scrape (default: 3)",
    )
    parser.add_argument(
        "--respect",
        action="store_true",
        help="Enable respectful scraping mode with 1-second pause every 10 URLs",
    )
    
    args = parser.parse_args()
    
    # Parse the input URL to extract election identifier and state code
    try:
        election_identifier, state_code = parse_partywise_url(args.url)
    except ValueError as e:
        print(f"Error: {e}")
        show_usage()
        return
    
    seq_limit = max(1, args.limit)
    respect_mode = args.respect
    
    # Chrome browser setup for initial page load
    driver = create_chrome_driver()
    
    results = {}
    json_file = ""
    csv_file = ""
    thread_lock = Lock()

    try:
        url = build_constituency_url(election_identifier, state_code, 1)
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))

        # Initialize results dictionary with page title and other details
        h1 = driver.find_element(By.TAG_NAME, 'h1').text
        h2 = driver.find_element(By.TAG_NAME, 'h2').text.replace('<span>', '').replace('</span>', '').replace('<strong>', '').replace('</strong>', '').replace('  ',' ')
        state_name = h2.split('(')[-1].replace(')', '')
        results = {
            "title": h1,
            "election_year": h1.split('-')[-1].strip(),
            "election_type": ''.join(h2.split()[:1]),
            "election_state": get_state_code(state_name),
            "constituencywise_results": []
        }

        print(f"{results['election_year']} {results['election_type']} Elections, {state_name}")
        mode_msg = f"{'Respectful (1s pause after every 10 URLs)' if respect_mode else 'High-Speed (multi-threaded workers)'}"
        print(f"Download Engine: {mode_msg}\n")

        # Create dynamic filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = f"./data/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.json"
        csv_file = f"./data/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.csv"

        start_time = perf_counter()

        if respect_mode:
            # Single-threaded respectful scraping
            worker_state = {
                'current': 1,
                'end_of_results': False
            }
            scrape_constituency_worker(election_identifier, state_code,
                                       results["constituencywise_results"], worker_state, thread_lock, True)

            total_time = perf_counter() - start_time
            print(
                f"\nJob successful. Downloaded data for {len(results['constituencywise_results'])} constituencies in {total_time:.3f} seconds."
            )
        else:
            # Multi-threaded scraping with up to 5 workers
            num_workers = 5
            
            worker_state = {
                'current': 1,
                'end_of_results': False
            }
            
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = []
                for _ in range(num_workers):
                    futures.append(executor.submit(
                        scrape_constituency_worker,
                        election_identifier, state_code,
                        results["constituencywise_results"], worker_state, thread_lock, False
                    ))
                
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Worker error: {e}")

            total_time = perf_counter() - start_time
            print(
                f"\nJob successful. Downloaded data for {len(results['constituencywise_results'])} constituencies in {total_time:.3f} seconds."
            )
            
    except (NoSuchElementException, TimeoutException, AssertionError) as e:
        print(f"Scraping stopped due to error: {e}")

    finally:
        driver.quit()

        if results:
            # Sort results by constituency_no (ascending)
            results["constituencywise_results"].sort(
                key=lambda x: int(x["voting_data"]["constituency_no"])
            )
            
            # Sort each constituency's voting_tally by serial_no (ascending)
            for constituency in results["constituencywise_results"]:
                constituency["voting_data"]["voting_tally"].sort(
                    key=lambda x: int(x["serial_no"]) if x["serial_no"].isdigit() else 0
                )
            
            # Write results to JSON file
            os.makedirs("./data", exist_ok=True)
            with open(json_file, "w") as file:
                json.dump(results, file, indent=4)
                print(f"\nData stored in: \n{json_file}")

            # Write results to CSV file
            with open(csv_file, 'w') as f_write:
                fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'constituency_no', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
                writer = csv.DictWriter(f_write, fieldnames=fieldnames)
                writer.writeheader()
                for constituency in results['constituencywise_results']:
                    for candidate in constituency['voting_data']['voting_tally']:
                        candidate['election_year'] = results['election_year']
                        candidate['election_type'] = results['election_type']
                        candidate['election_state'] = results['election_state']
                        candidate['constituency'] = constituency['voting_data']['constituency']
                        candidate['constituency_no'] = constituency['voting_data']['constituency_no']
                        candidate['serial_no'] = candidate['serial_no']
                        writer.writerow(candidate)
                print(f"{csv_file}")


if __name__ == "__main__":
    main()