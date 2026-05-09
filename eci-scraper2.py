import argparse
import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from datetime import datetime
from time import perf_counter


def parse_partywise_url(url: str) -> tuple[str, str]:
    """
    Parse a party-wise results URL to extract election identifier and state code.
    
    Expected format: https://results.eci.gov.in/<election_identifier>/partywiseresult-<state_code>.htm
    Example: https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm
    
    Returns:
        tuple of (election_identifier, state_code)
    
    Raises:
        ValueError: If the URL doesn't match the expected format
    """
    pattern = r'^https://results\.eci\.gov\.in/([^/]+)/partywiseresult-([A-Z]\d+)\.htm$'
    match = re.match(pattern, url)
    if not match:
        raise ValueError(
            f"Invalid URL format. Expected: https://results.eci.gov.in/<election_identifier>/partywiseresult-<state_code>.htm\n"
            f"Example: https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
        )
    return match.group(1), match.group(2)


def build_constituency_url(election_identifier: str, state_code: str, constituency_code: int) -> str:
    """
    Build the constituency results URL.
    
    Format: https://results.eci.gov.in/<election_identifier>/Constituencywise<state_code><constituency_code>.htm
    """
    return f"https://results.eci.gov.in/{election_identifier}/Constituencywise{state_code}{constituency_code}.htm"


def show_usage():
    """Display friendly usage guide."""
    print("""
Usage: python eci-scraper2.py --url <partywise_results_url> [limit] [--respect]

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

Examples:
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" 50
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" --respect
""")


def get_state_code(state_name):
    import pandas as pd
    # Read states.csv and create a mapping of state names to codes
    states = pd.read_csv('states.csv')
    states['state_name'].str.lower()
    state_code = states[states['state_name'] == state_name]['state_code'].iloc[0]
    return state_code


def extract_results(driver) -> dict:
    results = {}
    try:
        # Wait for necessary elements to load before scraping
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h2')))
        full_text = driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text
        
        # Split by ' - ' to get constituency number and remaining text
        parts = full_text.split(' - ')
        constituency_no = parts[0].strip()
        
        # Extract constituency name and state using regex
        # Format: "constituency_no - constituency_name (suffix) (state)" or "constituency_no - constituency_name (state)"
        state_name = ''
        # Extract state from the LAST set of parentheses (greedy match to end)
        state_match = re.search(r'\(([^)]+)\)\s*$', parts[1])
        if state_match:
            state_name = state_match.group(1)
            # Everything before the last "(state)" is the constituency name
            constituency_name = parts[1][:state_match.start()].strip()
        else:
            constituency_name = parts[1]

        results["constituency_no"] = constituency_no
        results["constituency"] = constituency_name
        results["voting_tally"] = []

        # Extracting candidate results
        candidates = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'tbody')))
        for candidate in candidates.find_elements(By.TAG_NAME, 'tr'):
            fieldnames = ["serial_no", "candidate", "party", "evm_votes", "postal_votes"]
            results["voting_tally"].append(dict(zip(fieldnames, map(lambda d: d.text, candidate.find_elements(By.TAG_NAME, 'td')))))
    
    except (NoSuchElementException, TimeoutException) as e:
        print(f"Error extracting results: {e}")
    
    return results


def scrape_constituency_worker(election_identifier: str, state_code: str, 
                               result_list: list, url_counter: dict, lock: Lock, 
                               respect_mode: bool = False):
    """
    Worker that continuously picks up new URL ranges until end of results.
    
    Args:
        election_identifier: Election identifier from URL
        state_code: State code from URL
        result_list: Shared list to append results (thread-safe)
        url_counter: Shared counter and next URL pointer
        lock: Thread lock for shared resources
        respect_mode: If True, add 1-second pause every 10 URLs
    """
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.5481.77 Safari/537.36")

    driver = webdriver.Chrome(options=options)
    
    try:
        while True:
            # Get next URL to scrape (thread-safe)
            with lock:
                if url_counter.get('end_of_results'):
                    break
                seq_no = url_counter['current']
                url_counter['current'] += 1
            
            url = build_constituency_url(election_identifier, state_code, seq_no)
            
            driver.get(url)
            if "404" in driver.title:
                with lock:
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
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.5481.77 Safari/537.36")

    driver = webdriver.Chrome(options=options)
    
    results = {}
    json_file = ""
    csv_file = ""
    thread_lock = Lock()

    try:
        # Get initial state/UT information to create output filenames
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
        print(f"Mode: {'Respectful (1s pause every 10 URLs)' if respect_mode else 'Multi-threaded (up to 5 workers)'}")

        # Create dynamic filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.json"
        csv_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.csv"

        start_time = perf_counter()

        if respect_mode:
            # Single-threaded respectful scraping (uses same worker logic with 1 worker)
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
            # Workers continuously pick up next URL from shared counter until end of results
            num_workers = 5
            
            # Shared state for workers
            worker_state = {
                'current': 1,  # Next URL number to scrape
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
                
                # Wait for all workers to complete (they exit when end_of_results is True)
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