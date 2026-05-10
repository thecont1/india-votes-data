import argparse
import csv
import json
import os
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

# FastAPI imports (optional - only needed for API mode)
try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False


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


def build_roundwise_url(election_identifier: str, state_code: str, constituency_code: int) -> str:
    """
    Build the round-wise results URL.
    
    Format: https://results.eci.gov.in/<election_identifier>/Roundwise<state_code><constituency_code>.htm
    Example: https://results.eci.gov.in/ResultAcGenMay2026/RoundwiseU071.htm
    """
    return f"https://results.eci.gov.in/{election_identifier}/Roundwise{state_code}{constituency_code}.htm"


def show_usage():
    """Display friendly usage guide."""
    print("""
Usage: python eci-scraper2.py --url <partywise_results_url> [limit] [--respect] [--by-round ROUND_NUM]

Description:
    Scrapes ECI election results from constituency-wise pages or round-wise pages.

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
    --by-round  Round number to scrape from round-wise pages (integer >= 1)
                When specified, scrapes only the specified round's Total results
                by candidate (evm_votes = total votes from that round)
                Example: --by-round 2

Output Files:
    Standard mode: YYYYAssembly-XX_YYYYMMDD_HHMMSS.csv
    Round mode:    YYYYAssembly-XX_YYYYMMDD_HHMMSS-RN.csv

Examples:
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" 50
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" --respect
    python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-U07.htm" --by-round 1
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


def extract_roundwise_results(driver, round_num: int) -> dict:
    """
    Extract round-wise results from a round-wise page.
    
    Validates that Previous Rounds + Current Round = Total.
    Issues warnings if validation fails.
    
    Args:
        driver: Selenium WebDriver instance
        round_num: The round number to extract
        
    Returns:
        dict with candidate data including round-specific vote counts
    """
    # Initialize results with defaults
    results = {"constituency_no": "", "constituency": "Unknown", "round_tally": []}
    try:
        # Wait for necessary elements to load
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h2')))
        full_text = driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text
        
        # Parse constituency info
        parts = full_text.split(' - ')
        constituency_no = parts[0].strip()
        
        state_match = re.search(r'\(([^)]+)\)\s*$', parts[1])
        if state_match:
            state_name = state_match.group(1)
            constituency_name = parts[1][:state_match.start()].strip()
        else:
            state_name = ''
            constituency_name = parts[1]

        results["constituency_no"] = constituency_no
        results["constituency"] = constituency_name
        results["round_num"] = round_num
        results["round_tally"] = []  # Initialize as empty - will be populated if round exists

        # Click the round button to make the table visible
        # Round buttons are typically labeled "R1", "R2", "R3", etc.
        try:
            round_button = driver.find_element(By.XPATH, f"//button[contains(text(), 'R{round_num}') or contains(text(), 'Round {round_num}')]")
            driver.execute_script("arguments[0].click();", round_button)
            time.sleep(0.5)  # Brief wait for table to render
        except NoSuchElementException:
            # Button not found, try direct div access
            pass
        
        # Find the round table by looking for div with id='tabN' where N corresponds to round_num
        target_div = None
        try:
            # Try to find div with id 'tab{round_num}'
            target_div = driver.find_element(By.ID, f"tab{round_num}")
        except NoSuchElementException:
            # Fallback: search for th containing "Round {round_num}"
            th_elements = driver.find_elements(By.XPATH, f"//th[contains(text(), 'Round {round_num}') or contains(text(), 'Round{round_num}')]")
            for th in th_elements:
                # Find the parent table of this th
                try:
                    target_div = th.find_element(By.XPATH, "./ancestor::div[contains(@class, 'tabcontent')]")
                    break
                except NoSuchElementException:
                    continue
        
        if target_div is not None:
            tbody = target_div.find_element(By.TAG_NAME, 'tbody')
            rows = tbody.find_elements(By.TAG_NAME, 'tr')
            
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, 'td')
                # Skip rows without enough data (footer rows use <th> not <td>)
                if len(cells) < 4:
                    continue
                
                candidate_name = cells[0].text.strip()
                # Skip total/empty rows (they use <th> not <td>)
                if not candidate_name or candidate_name.lower() == "total":
                    continue
                
                candidate_data = {
                    "serial_no": str(len(results["round_tally"]) + 1),  # Generate serial number
                    "candidate": candidate_name,
                    "party": cells[1].text.strip(),
                    "votes_brought_forward": cells[2].text.strip(),
                    "current_round": cells[3].text.strip(),
                    "total": cells[4].text.strip() if len(cells) > 4 else cells[3].text.strip()
                }
                
                # Validate: Previous + Current = Total
                try:
                    prev_votes = int(candidate_data["votes_brought_forward"])
                    curr_votes = int(candidate_data["current_round"])
                    total_votes = int(candidate_data["total"])
                    
                    if prev_votes + curr_votes != total_votes:
                        print(f"WARNING: Vote mismatch for {candidate_data['candidate']}: "
                              f"{prev_votes} + {curr_votes} ≠ {total_votes}")
                except ValueError:
                    pass  # Skip validation if non-numeric values
                
                results["round_tally"].append(candidate_data)
        else:
            # Round not found - silently handle
            pass
    
    except (NoSuchElementException, TimeoutException) as e:
        # Silently handle extraction errors - these indicate round doesn't exist
        pass
    
    return results


def scrape_roundwise_worker(election_identifier: str, state_code: str,
                               result_list: list, url_counter: dict, lock: Lock,
                               by_round: int, respect_mode: bool = False):
    """
    Worker for round-wise scraping that continuously picks up new constituency numbers.
    
    Args:
        election_identifier: Election identifier from URL
        state_code: State code from URL
        result_list: Shared list to append results (thread-safe)
        url_counter: Shared counter and next URL pointer
        lock: Thread lock for shared resources
        by_round: Round number to extract
        respect_mode: If True, add 1-second pause every 10 URLs
    """
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    options.add_argument("accept-language=en-US,en;q=0.9")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    
    try:
        while True:
            with lock:
                if url_counter.get('end_of_results'):
                    break
                seq_no = url_counter['current']
                url_counter['current'] += 1
            
            url = build_roundwise_url(election_identifier, state_code, seq_no)
            driver.get(url)
            
            if "404" in driver.title:
                with lock:
                    if not url_counter.get('end_of_results'):
                        url_counter['end_of_results'] = True
                break
            
            result = extract_roundwise_results(driver, by_round)
            if result and result.get("round_tally"):
                with lock:
                    result_list.append({"source_url": url, "voting_data": result})
                    print(f" {seq_no:03d}-{result.get('constituency', 'Unknown')}. Done.")

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
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    options.add_argument("accept-language=en-US,en;q=0.9")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(options=options)
    # Execute CDP command to make Selenium less detectable
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
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
    parser.add_argument(
        "--by-round",
        type=int,
        metavar="ROUND_NUM",
        help="Round number to scrape from round-wise pages (integer >= 1)",
    )
    
    args = parser.parse_args()
    
    # Validate round number if provided
    if args.by_round is not None and args.by_round < 1:
        print("Error: --by-round must be an integer >= 1")
        show_usage()
        return
    
    # Parse the input URL to extract election identifier and state code
    try:
        election_identifier, state_code = parse_partywise_url(args.url)
    except ValueError as e:
        print(f"Error: {e}")
        show_usage()
        return
    
    seq_limit = max(1, args.limit)
    respect_mode = args.respect
    by_round = args.by_round

    # Chrome browser setup for initial page load
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    options.add_argument("accept-language=en-US,en;q=0.9")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=options)
    # Execute CDP command to make Selenium less detectable
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
    results = {}
    json_file = ""
    csv_file = ""
    thread_lock = Lock()

    try:
        # Get initial state/UT information to create output filenames
        # Use roundwise URL for by-round mode, otherwise constituency URL
        if by_round is not None:
            url = build_roundwise_url(election_identifier, state_code, 1)
            mode_label = "Round-wise"
        else:
            url = build_constituency_url(election_identifier, state_code, 1)
            mode_label = "Constituency-wise"
        
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
            "round_number": by_round if by_round else None,
            "constituencywise_results": []
        }

        print(f"{results['election_year']} {results['election_type']} Elections, {state_name}")
        mode_msg = f"{'Respectful (1s pause after every 10 URLs)' if respect_mode else 'High-Speed (multi-threaded workers)'}"
        print(f"Download Engine: {mode_msg}\n")

        # Create dynamic filenames - append round number if by-round mode
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        round_suffix = f"-R{by_round}" if by_round else ""
        json_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}{round_suffix}.json"
        csv_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}{round_suffix}.csv"

        start_time = perf_counter()

        if by_round is not None:
            # Round-wise scraping mode
            print(f"Scraping Round {by_round} results...")
            
            # Multi-threaded round-wise scraping
            num_workers = 5
            
            # Shared state for workers
            worker_state = {
                'current': 1,  # Next constituency number to scrape
                'end_of_results': False,
                'count': 0
            }
            
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = []
                for _ in range(num_workers):
                    futures.append(executor.submit(
                        scrape_roundwise_worker,
                        election_identifier, state_code,
                        results["constituencywise_results"], worker_state, thread_lock,
                        by_round, respect_mode
                    ))
                
                # Wait for all workers to complete
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Worker error: {e}")

            total_time = perf_counter() - start_time
            count = len(results['constituencywise_results'])
            if count == 0:
                print(f"\nR{by_round} not found in any of the constituencies.")
            else:
                print(
                    f"\nJob successful. Downloaded data for {count} constituencies in {total_time:.3f} seconds."
                )
        elif respect_mode:
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
            
            # Handle sorting and CSV output based on mode
            if by_round is not None:
                # Round-wise mode: sort round_tally by serial_no
                for constituency in results["constituencywise_results"]:
                    if "round_tally" in constituency["voting_data"] and constituency["voting_data"]["round_tally"]:
                        constituency["voting_data"]["round_tally"].sort(
                            key=lambda x: int(x["serial_no"]) if x["serial_no"].isdigit() else 0
                        )
                
                # Write round-wise results to JSON file
                with open(json_file, "w") as file:
                    json.dump(results, file, indent=4)
                    print(f"\nData stored in: \n{json_file}")

                # Write round-wise results to CSV file (same format as standard mode)
                with open(csv_file, 'w') as f_write:
                    fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'constituency_no', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
                    writer = csv.DictWriter(f_write, fieldnames=fieldnames)
                    writer.writeheader()
                    for constituency in results['constituencywise_results']:
                        for candidate in constituency['voting_data'].get('round_tally', []):
                            writer.writerow({
                                'election_year': results['election_year'],
                                'election_type': results['election_type'],
                                'election_state': results['election_state'],
                                'constituency': constituency['voting_data']['constituency'],
                                'constituency_no': constituency['voting_data']['constituency_no'],
                                'serial_no': candidate['serial_no'],
                                'candidate': candidate['candidate'],
                                'party': candidate['party'],
                                'evm_votes': candidate['total'],  # Total votes from round = EVM votes
                                'postal_votes': ''  # Round-wise pages don't have postal votes
                            })
                    print(f"{csv_file}")
            else:
                # Standard constituency-wise mode
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

# ============== API Layer ==============

def create_chrome_driver():
    """Create and configure Chrome WebDriver instance."""
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
    options.add_argument("accept-language=en-US,en;q=0.9")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def scrape_constituency_sync(election_identifier: str, state_code: str, 
                                limit: int = None, respect_mode: bool = False) -> dict:
    """
    Synchronous single-threaded scrape of constituency results.
    
    Returns the results dictionary directly for API use.
    """
    driver = create_chrome_driver()
    results = {
        "constituencywise_results": [],
        "election_year": "",
        "election_type": "",
        "election_state": ""
    }
    
    try:
        seq_no = 1
        while True:
            url = build_constituency_url(election_identifier, state_code, seq_no)
            driver.get(url)
            
            if "404" in driver.title:
                break
            
            result = extract_results(driver)
            if result:
                results["constituencywise_results"].append({
                    "source_url": url, 
                    "voting_data": result
                })
            
            # Respect mode: pause every 10 URLs
            if respect_mode and seq_no % 10 == 0:
                time.sleep(1)
            
            # Check limit
            if limit and seq_no >= limit:
                break
                
            seq_no += 1
            
    except Exception as e:
        print(f"Scraping error: {e}")
    finally:
        driver.quit()
    
    return results


def scrape_roundwise_sync(election_identifier: str, state_code: str,
                           round_num: int, respect_mode: bool = False) -> dict:
    """
    Synchronous single-threaded scrape of round-wise results.
    
    Returns the results dictionary directly for API use.
    """
    driver = create_chrome_driver()
    results = {
        "constituencywise_results": [],
        "round_number": round_num
    }
    
    try:
        seq_no = 1
        while True:
            url = build_roundwise_url(election_identifier, state_code, seq_no)
            driver.get(url)
            
            if "404" in driver.title:
                break
            
            result = extract_roundwise_results(driver, round_num)
            if result and result.get("round_tally"):
                results["constituencywise_results"].append({
                    "source_url": url,
                    "voting_data": result
                })
            
            # Respect mode: pause every 10 URLs
            if respect_mode and seq_no % 10 == 0:
                time.sleep(1)
                
            seq_no += 1
            
    except Exception as e:
        print(f"Scraping error: {e}")
    finally:
        driver.quit()
    
    return results


def save_results_to_files(results: dict, by_round: int = None) -> tuple:
    """
    Save results to JSON and CSV files.
    
    Returns: (json_file_path, csv_file_path)
    """
    # Ensure results directory exists
    os.makedirs("./results", exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    round_suffix = f"-R{by_round}" if by_round else ""
    json_file = f"./results/{results.get('election_year', '2026')}{results.get('election_type', 'Assembly')}-{results.get('election_state', 'XX')}_{timestamp}{round_suffix}.json"
    csv_file = f"./results/{results.get('election_year', '2026')}{results.get('election_type', 'Assembly')}-{results.get('election_state', 'XX')}_{timestamp}{round_suffix}.csv"
    
    # Save JSON
    with open(json_file, "w") as f:
        json.dump(results, f, indent=4)
    
    # Save CSV
    with open(csv_file, 'w') as f_write:
        fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'constituency_no', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
        writer = csv.DictWriter(f_write, fieldnames=fieldnames)
        writer.writeheader()
        
        for constituency in results.get('constituencywise_results', []):
            voting_data = constituency.get('voting_data', {})
            
            if by_round is not None:
                # Round-wise mode
                for candidate in voting_data.get('round_tally', []):
                    writer.writerow({
                        'election_year': results.get('election_year', ''),
                        'election_type': results.get('election_type', ''),
                        'election_state': results.get('election_state', ''),
                        'constituency': voting_data.get('constituency', ''),
                        'constituency_no': voting_data.get('constituency_no', ''),
                        'serial_no': candidate.get('serial_no', ''),
                        'candidate': candidate.get('candidate', ''),
                        'party': candidate.get('party', ''),
                        'evm_votes': candidate.get('total', ''),
                        'postal_votes': ''
                    })
            else:
                # Standard mode
                for candidate in voting_data.get('voting_tally', []):
                    writer.writerow({
                        'election_year': results.get('election_year', ''),
                        'election_type': results.get('election_type', ''),
                        'election_state': results.get('election_state', ''),
                        'constituency': voting_data.get('constituency', ''),
                        'constituency_no': voting_data.get('constituency_no', ''),
                        'serial_no': candidate.get('serial_no', ''),
                        'candidate': candidate.get('candidate', ''),
                        'party': candidate.get('party', ''),
                        'evm_votes': candidate.get('evm_votes', ''),
                        'postal_votes': candidate.get('postal_votes', '')
                    })
    
    return json_file, csv_file


# FastAPI models (only defined if FastAPI is available)
if FASTAPI_AVAILABLE:
    class ScrapeRequest(BaseModel):
        url: str
        limit: int = 3
        respect: bool = False
    
    class ScrapeRoundRequest(BaseModel):
        url: str
        round: int
        respect: bool = False


# FastAPI app creation
if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="ECI Results Scraper API",
        description="API for scraping Election Commission of India election results",
        version="1.0.0"
    )
    
    @app.post("/scrape")
    async def scrape_endpoint(request: ScrapeRequest):
        """Scrape constituency results from ECI party-wise URL."""
        try:
            election_identifier, state_code = parse_partywise_url(request.url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        results = scrape_constituency_sync(
            election_identifier, state_code, 
            limit=request.limit, 
            respect_mode=request.respect
        )
        
        # Get metadata from first constituency
        if results["constituencywise_results"]:
            driver = create_chrome_driver()
            try:
                url = build_constituency_url(election_identifier, state_code, 1)
                driver.get(url)
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
                h1 = driver.find_element(By.TAG_NAME, 'h1').text
                h2 = driver.find_element(By.TAG_NAME, 'h2').text
                state_name = h2.split('(')[-1].replace(')', '')
                
                results["election_year"] = h1.split('-')[-1].strip()
                results["election_type"] = ''.join(h2.split()[:1])
                results["election_state"] = get_state_code(state_name)
            except:
                pass
            finally:
                driver.quit()
        
        json_file, csv_file = save_results_to_files(results)
        
        return {
            "status": "success",
            "data": results,
            "files": {
                "json": json_file,
                "csv": csv_file
            }
        }
    
    @app.post("/scrape/round")
    async def scrape_round_endpoint(request: ScrapeRoundRequest):
        """Scrape round-wise results from ECI party-wise URL."""
        if request.round < 1:
            raise HTTPException(status_code=400, detail="Round must be >= 1")
        
        try:
            election_identifier, state_code = parse_partywise_url(request.url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        results = scrape_roundwise_sync(
            election_identifier, state_code,
            round_num=request.round,
            respect_mode=request.respect
        )
        
        # Get metadata
        if results["constituencywise_results"]:
            driver = create_chrome_driver()
            try:
                url = build_roundwise_url(election_identifier, state_code, 1)
                driver.get(url)
                WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))
                h1 = driver.find_element(By.TAG_NAME, 'h1').text
                h2 = driver.find_element(By.TAG_NAME, 'h2').text
                state_name = h2.split('(')[-1].replace(')', '')
                
                results["election_year"] = h1.split('-')[-1].strip()
                results["election_type"] = ''.join(h2.split()[:1])
                results["election_state"] = get_state_code(state_name)
            except:
                pass
            finally:
                driver.quit()
        
        json_file, csv_file = save_results_to_files(results, by_round=request.round)
        
        return {
            "status": "success",
            "data": results,
            "files": {
                "json": json_file,
                "csv": csv_file
            }
        }
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy"}


if __name__ == "__main__":
    main()