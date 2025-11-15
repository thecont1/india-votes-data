import argparse
import csv
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from datetime import datetime
from time import perf_counter

def source_url(seq_no) -> str:
    base_url = "https://results.eci.gov.in/ResultAcGenNov2025/ConstituencywiseS04"  # Bihar
    return base_url + str(seq_no) + ".htm"

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
        constituency_number = parts[0].strip()
        
        # Extract constituency name and state using regex
        import re
        state_name = ''
        match = re.match(r'(.+?) \((.+?)\)', parts[1])
        if match:
            constituency_name = match.group(1)
            state_name = match.group(2)
        else:
            constituency_name = parts[1]

        results["constituency_number"] = constituency_number
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

def main():
    seq_no = 1

    parser = argparse.ArgumentParser(description="Scrape selected constituencies from ECI results")
    # Optional parameter lets the caller cap how many constituency pages to scrape
    parser.add_argument(
        "limit",
        nargs="?",
        type=int,
        default=3,
        help="Number of constituencies to scrape (default: 3)",
    )
    seq_limit = max(1, parser.parse_args().limit)

    # Chrome browser setup with performance and headless mode enabled
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

    try:
        # Get initial state/UT information to create output filenames
        driver.get(source_url(seq_no))
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))

        # Initialize results dictionary with page title and other details
        h1 = driver.find_element(By.TAG_NAME, 'h1').text
        h2 = driver.find_element(By.TAG_NAME, 'h2').text.replace('<span>', '').replace('</span>', '').replace('<strong>', '').replace('</strong>', '').replace('  ', ' ')
        state_name = h2.split('(')[-1].replace(')', '')
        results = {
            "title": h1,
            "election_year": h1.split('-')[-1].strip(),
            "election_type": ''.join(h2.split()[:1]),
            "election_state": get_state_code(state_name),
            "constituencywise_results": []
        }

        print(f"{results['election_year']} {results['election_type']} Elections, {state_name}\n")

        # Create dynamic filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.json"
        csv_file = f"./results/{results['election_year']}{results['election_type']}-{results['election_state']}_{timestamp}.csv"

        # Start scraping each constituency page and stop early when no more data exists
        end_of_results = False
        start_time = perf_counter()
        while seq_no <= seq_limit:
            url = source_url(seq_no)
            print(f"Loading {url}...", end='')

            driver.get(url)
            if "404" in driver.title:
                print(" Stop.")
                print(f"\n404 Not Found at {url}.")
                end_of_results = True
                break

            result = extract_results(driver)
            if result:
                results["constituencywise_results"].append({"source_url": url, "voting_data": result})
                constituency_label = result.get("constituency")
                suffix = f" {seq_no:03d}-{constituency_label}." if constituency_label else ""
                print(f"{suffix} Done.")

            seq_no += 1

        total_time = perf_counter() - start_time
        if end_of_results:
            print(
                f"\nReached end of results. Downloaded data for {len(results['constituencywise_results'])} constituencies in {total_time:.3f} seconds."
            )
        else:
            print(
                f"\nJob successful. Downloaded data for {len(results['constituencywise_results'])} constituencies in {total_time:.3f} seconds."
            )
    except (NoSuchElementException, TimeoutException, AssertionError) as e:
        print(f"Scraping stopped due to error: {e}")

    finally:
        driver.quit()

        if results:
            # Write results to JSON file
            with open(json_file, "w") as file:
                json.dump(results, file, indent=4)
                print(f"\nData stored in: \n{json_file}")

            # Write results to CSV file
            with open(csv_file, 'w') as f_write:
                fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
                writer = csv.DictWriter(f_write, fieldnames=fieldnames)
                writer.writeheader()
                for constituency in results['constituencywise_results']:
                    for candidate in constituency['voting_data']['voting_tally']:
                        candidate['election_year'] = results['election_year']
                        candidate['election_type'] = results['election_type']
                        candidate['election_state'] = results['election_state']
                        candidate['constituency'] = constituency['voting_data']['constituency']
                        candidate['serial_no'] = candidate['serial_no']
                        writer.writerow(candidate)
                print(f"{csv_file}")

if __name__ == "__main__":
    main()