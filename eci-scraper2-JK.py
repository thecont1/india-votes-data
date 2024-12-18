import csv
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from datetime import datetime

def source_url(seq_no) -> str:
    base_url = "https://results.eci.gov.in/AcResultGenOct2024/ConstituencywiseU08"
    return base_url + str(seq_no) + ".htm"     

def extract_results(driver) -> dict:
    results = {}
    try:
        # Wait for necessary elements to load before scraping
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h2')))
        constituency_name = " ".join(driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text.split()[2:-1])
        results["assembly_constituency"] = constituency_name
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
    # Chrome browser setup with performance and headless mode enabled
    options = Options()
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("user-agent=Mozilla/6.3 (Windows NT 10.0; Win64; x64) AppleWebKit/602.11 (KHTML, like Gecko) Chrome/116.1.2119.32 Safari/541.85")

    driver = webdriver.Chrome(options=options)
    
    seq_no = 1
    seq_limit = 500  # This could be dynamic based on the content of the website
    election_year = '2024'
    election_type = 'AC'
    election_state = 'JK'
    json_file = f"{election_year}{election_type}-{election_state}.json"
    csv_file = f"{election_year}{election_type}-{election_state}.csv"
    results = {}

    try:
        # Get initial state/UT information to create output filenames
        driver.get(source_url(seq_no))
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h1')))

        # Initialize results dictionary with page title and other details
        results = {
            'title': driver.title,
            'headline': driver.find_element(By.TAG_NAME, 'h1').text,
            'election_year': election_year,
            'election_type': election_type,
            'election_state': election_state,
#            "state": driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'strong').text.replace('(', '').replace(')', ''),
            'constituencywise_results': []
        }

        print(f"{results['headline']} \nState/UT: {results['election_state']}\n")

        # Start scraping each constituency page
        while seq_no <= seq_limit:
            url = source_url(seq_no)
            print(f"Loading {url}...", end='')

            driver.get(url)
            if "404" in driver.title:
                print(f"\nNo more data found. Scraping process terminates.")
                break

            result = extract_results(driver)
            if result:
                results["constituencywise_results"].append({"source_url": url, "voting_data": result})
                print("Done.")

            seq_no += 1

    except (NoSuchElementException, TimeoutException, AssertionError) as e:
        print(f"Scraping stopped due to error: {e}")

    finally:
        driver.quit()

        if results:
            # Write results to JSON file
            with open(json_file, "w") as file:
                json.dump(results, file, indent=4)
                print(f"Scraped data stored in {json_file}")

            # Write results to CSV file
            with open(csv_file, 'w') as f_write:
                fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
                writer = csv.DictWriter(f_write, fieldnames=fieldnames)
                writer.writeheader()
                for constituency in results['constituencywise_results']:
                    for candidate in constituency['voting_data']['voting_tally']:
                        candidate['election_year'] = election_year
                        candidate['election_type'] = election_type
                        candidate['election_state'] = election_state
                        candidate['constituency'] = constituency['voting_data']['assembly_constituency']
                        writer.writerow(candidate)
                print(f"Scraped data also stored in {csv_file}")

if __name__ == "__main__":
    main()