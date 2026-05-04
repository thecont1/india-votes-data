#!/usr/bin/env python3
"""
ECI Live Election Scraper.

Scrapes round-wise counting results from results.eci.gov.in for all
constituencies in the configured election. Uses parallel Selenium
(headless Chrome) workers to stay within the 15-minute scrape window.

Called by scheduler.sh every 15 minutes on counting day.
"""

import logging
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False

from db_utils import (
    get_work_queue,
    init_db,
    insert_round_snapshot,
    record_cycle,
    upsert_constituency_status,
)
from states_may2026 import get_url, normalise_party

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = "live_results.db"
MAX_WORKERS = 5
PAGE_LOAD_TIMEOUT = 15
MIN_JITTER = 0.5
MAX_JITTER = 2.0
MAX_ERRORS_BEFORE_SKIP = 3

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Thread-local storage for Selenium drivers
_thread_local = threading.local()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Selenium driver management
# ---------------------------------------------------------------------------

def _create_driver() -> webdriver.Chrome:
    """Create a new headless Chrome driver, preferring undetected-chromedriver."""
    if HAS_UC:
        try:
            options = uc.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--blink-settings=imagesEnabled=false")
            options.add_argument("--window-size=1280,800")
            driver = uc.Chrome(options=options, version_main=None)
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(3)
            logger.info("Created undetected-chromedriver instance")
            return driver
        except Exception as e:
            logger.warning("undetected-chromedriver failed, falling back to standard: %s", e)

    # Fallback: standard Selenium Chrome
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    ua = random.choice(USER_AGENTS)
    options.add_argument(f"--user-agent={ua}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(3)
    logger.info("Created standard Chrome driver")
    return driver


def _get_driver() -> webdriver.Chrome:
    """Get or create a thread-local Selenium driver."""
    if not hasattr(_thread_local, "driver") or _thread_local.driver is None:
        _thread_local.driver = _create_driver()
    return _thread_local.driver


def _quit_driver() -> None:
    """Quit and clear the thread-local driver."""
    if hasattr(_thread_local, "driver") and _thread_local.driver is not None:
        try:
            _thread_local.driver.quit()
        except Exception:
            pass
        _thread_local.driver = None


# ---------------------------------------------------------------------------
# Page scraping logic
# ---------------------------------------------------------------------------

def scrape_constituency(task: dict) -> dict:
    """
    Scrape one constituency Roundwise page.

    task keys: state_code, state_name, ac_no, url

    Returns a result dict with keys:
        state_code, state_name, ac_no, url, ac_name,
        current_round, total_rounds, candidates, status
    """
    result = {
        "state_code": task["state_code"],
        "state_name": task["state_name"],
        "ac_no": task["ac_no"],
        "url": task["url"],
        "status": "ERROR",
        "ac_name": None,
        "current_round": 0,
        "total_rounds": 0,
        "candidates": [],
    }

    try:
        driver = _get_driver()
        driver.get(task["url"])

        # Check for 404
        title = driver.title
        if "404" in title or "Not Found" in title or "not found" in title.lower():
            result["status"] = "NOT_YET_LIVE"
            return result

        # Check page body for 404 indicators
        body_text = ""
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text[:500]
        except Exception:
            pass
        if "404" in body_text and "not found" in body_text.lower():
            result["status"] = "NOT_YET_LIVE"
            return result

        # Wait for h2 (constituency heading)
        wait = WebDriverWait(driver, PAGE_LOAD_TIMEOUT)
        h2_el = wait.until(EC.presence_of_element_located((By.TAG_NAME, "h2")))

        # Extract constituency name
        # Typical: "Assembly Constituency 47 – ERODE (Tamil Nadu)"
        # or span: "47 - ERODE(Tamil Nadu)"
        try:
            span_el = h2_el.find_element(By.TAG_NAME, "span")
            full_text = span_el.text
        except NoSuchElementException:
            full_text = h2_el.text

        result["ac_name"] = _parse_ac_name(full_text, task["ac_no"])

        # Read "Status as on Round, X/Y"
        round_info = _extract_round_info(driver)
        if round_info is None:
            logger.warning(
                "Could not find round info for %s%s",
                task["state_code"], task["ac_no"],
            )
            result["status"] = "NOT_YET_LIVE"
            return result

        current_round, total_rounds = round_info
        result["current_round"] = current_round
        result["total_rounds"] = total_rounds

        # Click the latest round button to get cumulative data
        if current_round > 1:
            _click_round_button(driver, wait, current_round)

        # Extract candidates table
        candidates = _extract_candidates(driver, wait)
        if not candidates:
            logger.warning(
                "No candidates found for %s%s (round %d/%d)",
                task["state_code"], task["ac_no"],
                current_round, total_rounds,
            )
            # Still mark as LIVE/DONE even if table extraction failed
            # (might be a DOM change issue)
            result["status"] = "ERROR"
            return result

        result["candidates"] = candidates
        result["status"] = (
            "DONE" if current_round == total_rounds and total_rounds > 0
            else "LIVE"
        )

    except TimeoutException:
        logger.warning("Timeout: %s", task["url"])
        result["status"] = "ERROR"
    except WebDriverException as e:
        logger.error("WebDriver error on %s: %s", task["url"], e)
        result["status"] = "ERROR"
        _quit_driver()  # Force fresh driver next call
    except Exception as e:
        logger.error("Unexpected error on %s: %s", task["url"], e)
        result["status"] = "ERROR"

    # Polite jitter
    time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))
    return result


def _parse_ac_name(text: str, fallback_no: int) -> str:
    """Parse constituency name from h2 text."""
    import re

    # Try pattern: "47 - ERODE(Tamil Nadu)" or "47 - ERODE (Tamil Nadu)"
    match = re.search(r"\d+\s*[-–]\s*(.+?)\s*\(", text)
    if match:
        return match.group(1).strip()

    # Try pattern: "Assembly Constituency 47 – ERODE (Tamil Nadu)"
    match = re.search(r"[-–]\s*(.+?)\s*\(", text)
    if match:
        return match.group(1).strip()

    # Fallback
    return f"AC-{fallback_no}"


def _extract_round_info(driver) -> tuple[int, int] | None:
    """Extract current round and total rounds from 'Status as on Round, X/Y'."""
    try:
        # Try XPath for text containing "Status as on Round"
        el = driver.find_element(
            By.XPATH,
            "//*[contains(text(), 'Status as on Round')]",
        )
        text = el.text
        # Parse: "Status as on Round, 12/27" or "Status as on Round 12/27"
        parts = text.split(",")
        if len(parts) >= 2:
            round_part = parts[-1].strip()
        else:
            # No comma — try splitting by "Round"
            round_part = text.split("Round")[-1].strip().lstrip(",").strip()

        if "/" in round_part:
            current, total = round_part.split("/", 1)
            return int(current.strip()), int(total.strip())
    except (NoSuchElementException, ValueError, IndexError):
        pass

    # Fallback: look for round buttons and count them
    try:
        round_btns = driver.find_elements(
            By.XPATH,
            "//*[contains(@class, 'round') or contains(@class, 'Round')]"
            "[starts-with(normalize-space(), 'R')]",
        )
        if round_btns:
            round_numbers = []
            for btn in round_btns:
                txt = btn.text.strip()
                if txt.startswith("R") and txt[1:].isdigit():
                    round_numbers.append(int(txt[1:]))
            if round_numbers:
                return max(round_numbers), max(round_numbers)
    except Exception:
        pass

    return None


def _click_round_button(driver, wait, round_no: int) -> bool:
    """
    Click the button for the given round number.
    Returns True if successfully clicked, False otherwise.
    """
    # Try multiple selectors — ECI uses different tags across elections
    selectors = [
        f"//button[normalize-space()='R{round_no}']",
        f"//a[normalize-space()='R{round_no}']",
        f"//div[normalize-space()='R{round_no}']",
        f"//span[normalize-space()='R{round_no}']",
        f"//*[contains(@class, 'round') and normalize-space()='R{round_no}']",
        f"//*[contains(@class, 'Round') and normalize-space()='R{round_no}']",
        f"//*[contains(@class, 'btn') and normalize-space()='R{round_no}']",
    ]

    for xpath in selectors:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            # Scroll into view
            driver.execute_script("arguments[0].scrollIntoView(true);", btn)
            time.sleep(0.3)
            # Try click
            try:
                btn.click()
            except Exception:
                # Fallback: ActionChains click
                ActionChains(driver).move_to_element(btn).click().perform()
            # Wait for table to refresh
            time.sleep(1.5)
            return True
        except NoSuchElementException:
            continue
        except Exception as e:
            logger.debug("Click failed for R%d with selector %s: %s", round_no, xpath, e)
            continue

    logger.warning("Could not click round button R%d", round_no)
    return False


def _extract_candidates(driver, wait) -> list[dict]:
    """
    Extract candidate data from the results table.
    Returns list of {"candidate": str, "party": str, "votes": int}.
    """
    candidates = []
    try:
        tbody = wait.until(EC.presence_of_element_located((By.TAG_NAME, "tbody")))
        rows = tbody.find_elements(By.TAG_NAME, "tr")
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) >= 5:
                candidate_name = cols[0].text.strip()
                party_name = cols[1].text.strip()
                # Column 4 (index 4) = Total votes
                total_votes_text = cols[4].text.strip().replace(",", "").replace(" ", "")
                if not total_votes_text.isdigit():
                    # Try column 2 as fallback (some pages have different layouts)
                    total_votes_text = cols[2].text.strip().replace(",", "").replace(" ", "")
                if candidate_name and party_name and total_votes_text.isdigit():
                    candidates.append(
                        {
                            "candidate": candidate_name,
                            "party": normalise_party(party_name),
                            "votes": int(total_votes_text),
                        }
                    )
    except (TimeoutException, NoSuchElementException) as e:
        logger.error("Table extraction failed: %s", e)

    return candidates


# ---------------------------------------------------------------------------
# Worker and cycle orchestration
# ---------------------------------------------------------------------------

def _worker_run(tasks: list[dict], scraped_at: str) -> list[dict]:
    """Each thread runs this with its assigned slice of tasks."""
    results = []
    for task in tasks:
        result = scrape_constituency(task)
        result["scraped_at"] = scraped_at
        results.append(result)
    return results


def run_cycle() -> None:
    """
    One complete scrape cycle across all non-DONE constituencies.
    Called every 15 minutes by scheduler.sh.
    """
    cycle_start = datetime.now(timezone.utc)
    cycle_start_iso = cycle_start.isoformat()
    logger.info("=== Cycle started at %s ===", cycle_start_iso)

    # Get work queue
    queue = get_work_queue(DB_PATH)
    logger.info("Work queue: %d constituencies to scrape", len(queue))

    if not queue:
        logger.info("All constituencies DONE or no live pages yet.")
        record_cycle(
            DB_PATH, cycle_start_iso, cycle_start_iso,
            0, 0, 0, 0, 0.0,
        )
        return

    # Build task list with URLs
    tasks = []
    for item in queue:
        tasks.append(
            {
                "state_code": item["state_code"],
                "state_name": item["state_name"],
                "ac_no": item["ac_no"],
                "url": get_url(item["state_code"], item["ac_no"]),
            }
        )

    # Shuffle to distribute load across states (avoid hammering one state)
    random.shuffle(tasks)

    # Split evenly across workers
    chunks = [[] for _ in range(MAX_WORKERS)]
    for i, task in enumerate(tasks):
        chunks[i % MAX_WORKERS].append(task)

    scraped_at = datetime.now(timezone.utc).isoformat()
    all_results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="scraper") as executor:
        futures = {
            executor.submit(_worker_run, chunk, scraped_at): i
            for i, chunk in enumerate(chunks)
            if chunk
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                worker_results = future.result()
                all_results.extend(worker_results)
                logger.info(
                    "Worker %d finished: %d results",
                    worker_id, len(worker_results),
                )
            except Exception as e:
                logger.error("Worker %d failed: %s", worker_id, e)

    # Write results to DB
    pages_success = 0
    pages_skipped = 0
    pages_error = 0

    for r in all_results:
        if r["status"] in ("LIVE", "DONE") and r["candidates"]:
            insert_round_snapshot(
                DB_PATH,
                state_code=r["state_code"],
                state_name=r["state_name"],
                ac_no=r["ac_no"],
                ac_name=r["ac_name"],
                round_no=r["current_round"],
                total_rounds=r["total_rounds"],
                candidates=r["candidates"],
                scraped_at=r["scraped_at"],
            )
            upsert_constituency_status(
                DB_PATH,
                state_code=r["state_code"],
                ac_no=r["ac_no"],
                ac_name=r["ac_name"],
                status=r["status"],
                current_round=r["current_round"],
                total_rounds=r["total_rounds"],
                state_name=r["state_name"],
            )
            pages_success += 1

        elif r["status"] == "NOT_YET_LIVE":
            pages_skipped += 1

        elif r["status"] == "ERROR":
            upsert_constituency_status(
                DB_PATH,
                state_code=r["state_code"],
                ac_no=r["ac_no"],
                ac_name=r["ac_name"],
                status="ERROR",
                current_round=0,
                total_rounds=0,
                state_name=r["state_name"],
            )
            pages_error += 1

    cycle_end = datetime.now(timezone.utc)
    cycle_end_iso = cycle_end.isoformat()
    duration = (cycle_end - cycle_start).total_seconds()

    record_cycle(
        DB_PATH, cycle_start_iso, cycle_end_iso,
        len(tasks), pages_success, pages_skipped, pages_error, duration,
    )

    logger.info(
        "=== Cycle done in %.1fs | success=%d skipped=%d error=%d ===",
        duration, pages_success, pages_skipped, pages_error,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db(DB_PATH)
    run_cycle()
