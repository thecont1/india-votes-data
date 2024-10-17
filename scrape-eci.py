import csv
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def source_url(seq_no) -> str:
    return base_url + str(seq_no) + ".htm"     

def extract_results(driver) -> dict:
    results = {"assembly_constituency": " ".join(driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text.split()[2:-1])}
    results["voting_tally"] = []
    for candidate in driver.find_element(By.TAG_NAME, 'tbody').find_elements(By.TAG_NAME, 'tr'):
        results["voting_tally"].append(dict(zip(["serial_no", "candidate", "party", "evm_votes", "postal_votes"], list(map(lambda d: d.text, candidate.find_elements(By.TAG_NAME, 'td'))))))
    return results

global base_url 
base_url = "https://results.eci.gov.in/AcResultGenOct2024/ConstituencywiseS07"
seq_no = 1; seq_limit = 99

options = Options()
options.add_argument("--blink-settings=imagesEnabled=false")
# options.add_argument("--headless=new")
driver = webdriver.Chrome(options=options)
driver.get(source_url(seq_no))

results = {
    "title": driver.title,
    "headline": driver.find_element(By.TAG_NAME, 'h1').text, 
    "state": driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'strong').text.replace('(', '').replace(')', ''),
    "constituencywise_results": []
}
print(f"{results['headline']} \nState/UT: {results['state']}\n")
json_file = f"/Users/home/Downloads/{results['state']}.json"
csv_file = f"/Users/home/Downloads/{results['state']}.csv"

try:
    while seq_no <= seq_limit:
        print(f"Loading... {source_url(seq_no)}")
        results["constituencywise_results"].append({"source_url": source_url(seq_no)})
        results["constituencywise_results"][-1]["voting_data"] = extract_results(driver)
        seq_no += 1
        if seq_no <= seq_limit:
            driver.get(source_url(seq_no))
            assert (driver.title != "404 Not Found") 

except:
    print(f"No further results can be accessed.")
finally:
    driver.quit()

with open(json_file, "w") as file:
    json.dump(results, file)

with open(csv_file, 'w') as f_write:
    fieldnames = ['state', 'constituency', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
    writer = csv.DictWriter(f_write, fieldnames=fieldnames)
    writer.writeheader()
    for constituency in results['constituencywise_results']:
        for candidate in constituency['voting_data']['voting_tally']:
            candidate['state'] = results['state']
            candidate['constituency'] = constituency['voting_data']['assembly_constituency']
            writer.writerow(candidate)

