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
    base_url = "https://results.eci.gov.in/ResultAcGenFeb2025/ConstituencywiseU05"  # NCT of Delhi
    return base_url + str(seq_no) + ".htm"

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
        match = re.match(r'(.+?) \((.+?)\)', parts[1])
        if match:
            constituency_name = match.group(1)
            state_name = match.group(2)
        else:
            constituency_name = parts[1]
            state_name = ''
            
        results["constituency_number"] = constituency_number
        results["assembly_constituency"] = constituency_name
        results["state"] = state_name
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
    seq_limit = 3  # This could be dynamic based on the content of the website

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
        results = {
            "title": driver.title,
            "headline": driver.find_element(By.TAG_NAME, 'h1').text,
            "state": driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'strong').text.replace('(', '').replace(')', ''),
            "constituencywise_results": []
        }

        print(f"{results['headline']} \nState/UT: {results['state']}\n")

        # Create dynamic filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file = f"./results/{results['state']}_{timestamp}.json"
        csv_file = f"./results/{results['state']}_{timestamp}.csv"

        # Start scraping each constituency page
        while seq_no <= seq_limit:
            url = source_url(seq_no)
            print(f"Loading {url}...", end='')

            driver.get(url)
            if "404" in driver.title:
                print(f"\n\n404 Not Found at {url}. End scraping.")
                break

            result = extract_results(driver)
            if result:
                results["constituencywise_results"].append({"source_url": url, "voting_data": result})
                print("Done.")

            seq_no += 1

    except (NoSuchElementException, TimeoutException, AssertionError) as e:
        print(f"Scraping stopped due to error: {e}")
        # print(driver.page_source)

    finally:
        driver.quit()

        if results:
            # Write results to JSON file
            with open(json_file, "w") as file:
                json.dump(results, file, indent=4)
                print(f"Scraped data stored in {json_file}")

            # Write results to CSV file
            with open(csv_file, 'w') as f_write:
                fieldnames = ['state', 'constituency', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
                writer = csv.DictWriter(f_write, fieldnames=fieldnames)
                writer.writeheader()
                for constituency in results['constituencywise_results']:
                    for candidate in constituency['voting_data']['voting_tally']:
                        candidate['state'] = results['state']
                        candidate['constituency'] = constituency['voting_data']['assembly_constituency']
                        writer.writerow(candidate)
                print(f"Scraped data also stored in {csv_file}")

if __name__ == "__main__":
    main()