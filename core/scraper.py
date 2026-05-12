"""Core scraping functions for ECI results."""

import re
import time
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


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


def get_state_code(state_name: str) -> str:
    """
    Convert state name to state code.
    
    Args:
        state_name: Full state name (e.g., "West Bengal", "Tamil Nadu")
    
    Returns:
        State code (e.g., "WB", "TN") or empty string if not found
    """
    state_codes = {
        "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR", "Assam": "AS",
        "Bihar": "BR", "Chhattisgarh": "CG", "Goa": "GA", "Gujarat": "GJ",
        "Haryana": "HR", "Himachal Pradesh": "HP", "Jammu & Kashmir": "JK",
        "Jharkhand": "JH", "Karnataka": "KA", "Kerala": "KL", "Ladakh": "LA",
        "Lakshadweep": "LD", "Madhya Pradesh": "MP", "Maharashtra": "MH",
        "Manipur": "MN", "Meghalaya": "ML", "Mizoram": "MZ", "Nagaland": "NL",
        "Odisha": "OD", "Puducherry": "PY", "Punjab": "PB", "Rajasthan": "RJ",
        "Sikkim": "SK", "Tamil Nadu": "TN", "Telangana": "TS", "Tripura": "TR",
        "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB"
    }
    return state_codes.get(state_name, "")


def extract_results(driver) -> dict:
    """Extract constituency results from a constituency page."""
    results = {}
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h2')))
        full_text = driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text
        
        parts = full_text.split(' - ')
        constituency_no = parts[0].strip()
        
        state_match = re.search(r'\(([^)]+)\)\s*$', parts[1])
        if state_match:
            constituency_name = parts[1][:state_match.start()].strip()
        else:
            constituency_name = parts[1]

        results["constituency_no"] = constituency_no
        results["constituency"] = constituency_name
        results["voting_tally"] = []

        candidates = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'tbody')))
        for candidate in candidates.find_elements(By.TAG_NAME, 'tr'):
            fieldnames = ["serial_no", "candidate", "party", "evm_votes", "postal_votes"]
            results["voting_tally"].append(dict(zip(fieldnames, map(lambda d: d.text, candidate.find_elements(By.TAG_NAME, 'td')))))
    
    except (NoSuchElementException, TimeoutException) as e:
        print(f"Error extracting results: {e}")
    
    return results


def extract_roundwise_results(driver, round_num: int, constituency_info: dict = None) -> dict:
    """
    Extract round-wise results from a round-wise page.
    
    Validates that Previous Rounds + Current Round = Total.
    Issues warnings if validation fails.
    """
    results = {"constituency_no": "", "constituency": "Unknown", "round_tally": []}
    try:
        if constituency_info is None or round_num == 1:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'h2')))
            full_text = driver.find_element(By.TAG_NAME, 'h2').find_element(By.TAG_NAME, 'span').text
            
            parts = full_text.split(' - ')
            constituency_no = parts[0].strip()
            
            state_match = re.search(r'\(([^)]+)\)\s*$', parts[1])
            if state_match:
                constituency_name = parts[1][:state_match.start()].strip()
            else:
                constituency_name = parts[1]
            
            results["constituency_no"] = constituency_no
            results["constituency"] = constituency_name
            results["round_num"] = round_num
        else:
            results["constituency_no"] = constituency_info.get("constituency_no", "")
            results["constituency"] = constituency_info.get("constituency", "Unknown")
            results["round_num"] = round_num
        
        results["round_tally"] = []

        try:
            round_button = driver.find_element(By.XPATH, f"//button[contains(text(), 'R{round_num}') or contains(text(), 'Round {round_num}')]")
            driver.execute_script("arguments[0].click();", round_button)
            time.sleep(0.2)
        except NoSuchElementException:
            pass
        
        target_div = None
        try:
            target_div = driver.find_element(By.ID, f"tab{round_num}")
        except NoSuchElementException:
            th_elements = driver.find_elements(By.XPATH, f"//th[contains(text(), 'Round {round_num}') or contains(text(), 'Round{round_num}')]")
            for th in th_elements:
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
                if len(cells) < 4:
                    continue
                
                candidate_name = cells[0].text.strip()
                if not candidate_name or candidate_name.lower() == "total":
                    continue
                
                candidate_data = {
                    "serial_no": str(len(results["round_tally"]) + 1),
                    "candidate": candidate_name,
                    "party": cells[1].text.strip(),
                    "votes_brought_forward": cells[2].text.strip(),
                    "current_round": cells[3].text.strip(),
                    "total": cells[4].text.strip() if len(cells) > 4 else cells[3].text.strip()
                }
                
                try:
                    prev_votes = int(candidate_data["votes_brought_forward"])
                    curr_votes = int(candidate_data["current_round"])
                    total_votes = int(candidate_data["total"])
                    
                    if prev_votes + curr_votes != total_votes:
                        print(f"WARNING: Vote mismatch for {candidate_data['candidate']}: {prev_votes} + {curr_votes} ≠ {total_votes}")
                except ValueError:
                    pass
                
                results["round_tally"].append(candidate_data)
    
    except (NoSuchElementException, TimeoutException) as e:
        pass
    
    return results


def scrape_ac_rounds_core(driver, election_identifier: str, state_code: str,
                          ac_no: int, start_round: int = 1) -> dict:
    """
    Core function to scrape all rounds for a single AC plus postal votes.
    
    This is the shared logic used by both /scrape/ac-rounds and /scrape/all-rounds.
    """
    result = {"ac_no": ac_no, "rounds": [], "constituency": "", "postal_votes": []}
    
    try:
        roundwise_url = build_roundwise_url(election_identifier, state_code, ac_no)
        driver.get(roundwise_url)
        
        if "404" in driver.title:
            return {"status": "done"}
        
        # First round: get constituency info
        first_round_result = extract_roundwise_results(driver, max(start_round, 1))
        constituency_info = {
            "constituency_no": first_round_result.get("constituency_no", ""),
            "constituency": first_round_result.get("constituency", "")
        }
        
        # First round already extracted
        if first_round_result.get("round_tally"):
            result["rounds"].append({
                "round": max(start_round, 1),
                "tally": first_round_result.get("round_tally", [])
            })
            result["constituency"] = first_round_result.get("constituency", "")
        
        # Remaining rounds
        for round_num in range(max(start_round, 1) + 1, 50):
            round_result = extract_roundwise_results(driver, round_num, constituency_info)
            if not round_result.get("round_tally"):
                break
            
            result["rounds"].append({
                "round": round_num,
                "tally": round_result.get("round_tally", [])
            })
        
        # Get postal votes from constituency page
        constituency_url = build_constituency_url(election_identifier, state_code, ac_no)
        driver.get(constituency_url)
        final_result = extract_results(driver)
        result["constituency"] = final_result.get("constituency", result["constituency"])
        result["postal_votes"] = final_result.get("voting_tally", [])
        
        return {"status": "success", "data": result}
        
    except Exception as e:
        return {"status": "error", "error": str(e)}


def scrape_constituency_sync(election_identifier: str, state_code: str,
                              limit: int = None, respect_mode: bool = False) -> dict:
    """Synchronous single-threaded scrape of constituency results."""
    from core.browser import create_chrome_driver
    
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
            
            if respect_mode and seq_no % 10 == 0:
                time.sleep(1)
            
            if limit and seq_no >= limit:
                break
                
            seq_no += 1
            
    except Exception as e:
        print(f"Scraping error: {e}")
    finally:
        driver.quit()
    
    return results