"""
ECI Results Scraper - Server Entry Point (FastAPI)

This module provides the FastAPI server for ECI results scraping.
It imports core functionality from the core/ package.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.browser import create_chrome_driver
from core.scraper import (
    build_constituency_url,
    get_state_code,
    parse_partywise_url,
    scrape_ac_rounds_core,
    scrape_constituency_sync,
)

app = FastAPI(
    title="ECI Results Scraper API",
    description="API for scraping Election Commission of India election results",
    version="1.0.0"
)


# Pydantic models
class ScrapeRequest(BaseModel):
    url: str
    limit: int = 3
    respect: bool = False


class ScrapeAcRoundsRequest(BaseModel):
    url: str
    ac_no: int
    start_round: int = 1


class ScrapeAllRoundsRequest(BaseModel):
    url: str
    start_ac: int = 1
    end_ac: int = 0
    respect: bool = False


def save_results_to_files(results: dict) -> tuple:
    """Save results to JSON and CSV files. Returns (json_path, csv_path)."""
    os.makedirs("./results", exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_file = f"./results/{results.get('election_year', '2026')}{results.get('election_type', 'Assembly')}-{results.get('election_state', 'XX')}_{timestamp}.json"
    csv_file = f"./results/{results.get('election_year', '2026')}{results.get('election_type', 'Assembly')}-{results.get('election_state', 'XX')}_{timestamp}.csv"
    
    with open(json_file, "w") as f:
        json.dump(results, f, indent=4)
    
    with open(csv_file, 'w') as f_write:
        fieldnames = ['election_year', 'election_type', 'election_state', 'constituency', 'constituency_no', 'serial_no', 'candidate', 'party', 'evm_votes', 'postal_votes']
        writer = csv.DictWriter(f_write, fieldnames=fieldnames)
        writer.writeheader()
        
        for constituency in results.get('constituencywise_results', []):
            voting_data = constituency.get('voting_data', {})
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
        except Exception:
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/scrape/ac-rounds")
async def scrape_ac_rounds_endpoint(request: ScrapeAcRoundsRequest):
    """Scrape rounds for a single AC, plus postal votes from final results."""
    try:
        election_identifier, state_code = parse_partywise_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    driver = create_chrome_driver()
    try:
        result = scrape_ac_rounds_core(driver, election_identifier, state_code,
                                       request.ac_no, request.start_round)
        
        if result.get("status") == "done":
            return {"status": "error", "error": "AC not found (404)"}
        
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        driver.quit()


@app.post("/scrape/all-rounds")
async def scrape_all_rounds_endpoint(request: ScrapeAllRoundsRequest):
    """Scrape all rounds (1-N) for each AC, plus postal votes from final results."""
    try:
        election_identifier, state_code = parse_partywise_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    driver = create_chrome_driver()
    results = []
    
    try:
        # First, count total ACs if not specified
        if request.end_ac == 0:
            print("Counting ACs...")
            max_ac = 0
            for i in range(1, 1000):
                test_url = build_constituency_url(election_identifier, state_code, i)
                driver.get(test_url)
                if "404" in driver.title:
                    break
                max_ac = i
        else:
            max_ac = request.end_ac
        
        print(f"Processing ACs {request.start_ac} to {max_ac}...")
        
        for ac_no in range(request.start_ac, max_ac + 1):
            result = scrape_ac_rounds_core(driver, election_identifier, state_code, ac_no, 1)
            
            if result.get("status") == "done":
                break
            
            if result.get("status") == "success":
                ac_data = result.get("data", {})
                results.append(ac_data)
                print(f"  AC {ac_no}: {len(ac_data.get('rounds', []))} rounds")
            else:
                print(f"  AC {ac_no}: FAILED - {result.get('error', 'Unknown error')}")
            
            if request.respect and ac_no % 10 == 0:
                print("[Respect mode] Pausing 1 second...")
                time.sleep(1)
                
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        driver.quit()
    
    return {"status": "success", "data": results, "total_acs": len(results)}


if __name__ == "__main__":
    if "--api" in sys.argv:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        print("Use cli.py for command-line scraping, or run with --api flag to start server.")