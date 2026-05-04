#!/usr/bin/env python3
"""
ECI Live Election Scraper.

Scrapes round-wise counting results from results.eci.gov.in.
Primary: requests + BeautifulSoup (pages are server-rendered).
Fallback: Selenium headless Chrome (if requests fails / pages need JS).

Called by scheduler.sh every 15 minutes on counting day.
"""

import logging
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        NoSuchElementException,
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

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
MAX_WORKERS = 8  # requests is lightweight — can run more workers
PAGE_LOAD_TIMEOUT = 15
MIN_JITTER = 0.2
MAX_JITTER = 0.8

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Thread-local storage for requests sessions
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
# HTTP session management
# ---------------------------------------------------------------------------

def _get_session() -> requests.Session:
    """Get or create a thread-local requests session."""
    if not hasattr(_thread_local, "session") or _thread_local.session is None:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
        )
        _thread_local.session = s
    return _thread_local.session


# ---------------------------------------------------------------------------
# HTML parsing (requests + BeautifulSoup — primary method)
# ---------------------------------------------------------------------------

def _parse_page_bs4(html: str, task: dict) -> dict:
    """
    Parse an ECI Roundwise page using BeautifulSoup.
    All round tables are pre-rendered in the HTML — no JS needed.
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

    soup = BeautifulSoup(html, "html.parser")

    # Check for 404 / Access Denied
    title = soup.title.string if soup.title else ""
    if "404" in (title or "") or "Not Found" in (title or "") or "Access Denied" in (title or ""):
        result["status"] = "NOT_YET_LIVE"
        return result

    # Extract constituency name from h2 > span
    h2 = soup.find("h2")
    if not h2:
        result["status"] = "ERROR"
        return result

    span = h2.find("span")
    full_text = span.get_text() if span else h2.get_text()
    result["ac_name"] = _parse_ac_name(full_text, task["ac_no"])

    # Extract round info: "Status as on Round, X/Y"
    round_info = _extract_round_info_bs4(soup)
    if round_info is None:
        result["status"] = "NOT_YET_LIVE"
        return result

    current_round, total_rounds = round_info
    result["current_round"] = current_round
    result["total_rounds"] = total_rounds

    # Round 0 means counting hasn't started yet — no data to extract
    if current_round == 0:
        result["status"] = "NOT_YET_LIVE"
        return result

    # Extract candidates from the CURRENT round's table.
    # Each round is in <div id="tab{N}"> with a <table> inside.
    candidates = _extract_candidates_bs4(soup, current_round)
    if not candidates:
        # Fallback: try tab1 (sometimes only tab1 has data)
        candidates = _extract_candidates_bs4(soup, 1)

    if not candidates:
        return result

    result["candidates"] = candidates
    result["status"] = (
        "DONE" if current_round == total_rounds and total_rounds > 0
        else "LIVE"
    )
    return result


def _parse_ac_name(text: str, fallback_no: int) -> str:
    """Parse constituency name from h2 text."""
    # Pattern: "195 - THIRUPARANKUNDRAM(Tamil Nadu)"
    match = re.search(r"\d+\s*[-–]\s*(.+?)\s*\(", text)
    if match:
        return match.group(1).strip()
    # Pattern: "Assembly Constituency 195 – THIRUPARANKUNDRAM (Tamil Nadu)"
    match = re.search(r"[-–]\s*(.+?)\s*\(", text)
    if match:
        return match.group(1).strip()
    return f"AC-{fallback_no}"


def _extract_round_info_bs4(soup: BeautifulSoup) -> tuple[int, int] | None:
    """Extract current/total round from 'Status as on Round, X/Y'."""
    # The HTML has: <div class='round-status'> Status as on Round, <span>9</span>/26</div>
    round_div = soup.find("div", class_="round-status")
    if not round_div:
        # Fallback: search all text
        for el in soup.find_all(string=re.compile(r"Status as on Round")):
            text = el.strip()
            m = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m:
                return int(m.group(1)), int(m.group(2))
        return None

    text = round_div.get_text()
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _extract_candidates_bs4(soup: BeautifulSoup, round_no: int) -> list[dict]:
    """
    Extract candidate data for a specific round.
    Looks for <div id="tab{round_no}"> then its <tbody> rows.
    """
    candidates = []

    # Find the specific round's tab div
    tab_div = soup.find("div", id=f"tab{round_no}")
    if not tab_div:
        return candidates

    tbody = tab_div.find("tbody")
    if not tbody:
        return candidates

    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) >= 5:
            candidate_name = cols[0].get_text(strip=True)
            party_name = cols[1].get_text(strip=True)
            # Column 5 (index 4) = Total votes
            total_votes_text = cols[4].get_text(strip=True).replace(",", "").replace(" ", "")
            if candidate_name and party_name and total_votes_text.isdigit():
                candidates.append(
                    {
                        "candidate": candidate_name,
                        "party": normalise_party(party_name),
                        "votes": int(total_votes_text),
                    }
                )

    return candidates


# ---------------------------------------------------------------------------
# Selenium fallback (for pages that need JS)
# ---------------------------------------------------------------------------

_thread_local_driver = threading.local()


def _create_selenium_driver():
    """Create a headless Chrome driver."""
    if HAS_UC:
        try:
            options = uc.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1280,800")
            driver = uc.Chrome(options=options, version_main=None)
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            driver.implicitly_wait(3)
            return driver
        except Exception:
            pass

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--blink-settings=imagesEnabled=false")
    options.add_argument("--window-size=1280,800")
    options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.implicitly_wait(3)
    return driver


def _get_selenium_driver():
    if not hasattr(_thread_local_driver, "driver") or _thread_local_driver.driver is None:
        _thread_local_driver.driver = _create_selenium_driver()
    return _thread_local_driver.driver


def _quit_selenium_driver():
    if hasattr(_thread_local_driver, "driver") and _thread_local_driver.driver:
        try:
            _thread_local_driver.driver.quit()
        except Exception:
            pass
        _thread_local_driver.driver = None


def _parse_page_selenium(task: dict) -> dict:
    """Fallback scraper using Selenium."""
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
        driver = _get_selenium_driver()
        driver.get(task["url"])

        title = driver.title
        if "404" in title or "Not Found" in title or "Access Denied" in title:
            result["status"] = "NOT_YET_LIVE"
            return result

        wait = WebDriverWait(driver, PAGE_LOAD_TIMEOUT)
        h2_el = wait.until(EC.presence_of_element_located((By.TAG_NAME, "h2")))

        try:
            span_el = h2_el.find_element(By.TAG_NAME, "span")
            full_text = span_el.text
        except NoSuchElementException:
            full_text = h2_el.text

        result["ac_name"] = _parse_ac_name(full_text, task["ac_no"])

        # Read round info from the page source (more reliable)
        page_src = driver.page_source
        soup = BeautifulSoup(page_src, "html.parser")
        round_info = _extract_round_info_bs4(soup)
        if round_info is None:
            result["status"] = "NOT_YET_LIVE"
            return result

        current_round, total_rounds = round_info
        result["current_round"] = current_round
        result["total_rounds"] = total_rounds

        # Use BS4 to extract from the correct round's tab
        candidates = _extract_candidates_bs4(soup, current_round)
        if not candidates:
            candidates = _extract_candidates_bs4(soup, 1)

        if not candidates:
            result["status"] = "ERROR"
            return result

        result["candidates"] = candidates
        result["status"] = (
            "DONE" if current_round == total_rounds and total_rounds > 0
            else "LIVE"
        )

    except TimeoutException:
        result["status"] = "ERROR"
    except WebDriverException as e:
        logger.error("WebDriver error on %s: %s", task["url"], e)
        result["status"] = "ERROR"
        _quit_selenium_driver()
    except Exception as e:
        logger.error("Unexpected Selenium error on %s: %s", task["url"], e)
        result["status"] = "ERROR"

    return result


# ---------------------------------------------------------------------------
# Main scrape function (tries requests first, falls back to Selenium)
# ---------------------------------------------------------------------------

def scrape_constituency(task: dict) -> dict:
    """
    Scrape one constituency Roundwise page.
    Tries requests+BS4 first, falls back to Selenium if needed.
    """
    # --- Primary: requests + BeautifulSoup ---
    try:
        session = _get_session()
        resp = session.get(task["url"], timeout=PAGE_LOAD_TIMEOUT)

        # Check for Access Denied (Akamai CDN block)
        if resp.status_code == 403 or "Access Denied" in resp.text[:500]:
            logger.debug("requests blocked for %s, trying Selenium", task["url"])
            if HAS_SELENIUM:
                result = _parse_page_selenium(task)
                time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))
                return result
            else:
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
                return result

        if resp.status_code == 404:
            result = {
                "state_code": task["state_code"],
                "state_name": task["state_name"],
                "ac_no": task["ac_no"],
                "url": task["url"],
                "status": "NOT_YET_LIVE",
                "ac_name": None,
                "current_round": 0,
                "total_rounds": 0,
                "candidates": [],
            }
            return result

        result = _parse_page_bs4(resp.text, task)
        time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))
        return result

    except requests.RequestException as e:
        logger.debug("requests failed for %s: %s — trying Selenium", task["url"], e)
        if HAS_SELENIUM:
            result = _parse_page_selenium(task)
            time.sleep(random.uniform(MIN_JITTER, MAX_JITTER))
            return result
        raise


# ---------------------------------------------------------------------------
# Worker and cycle orchestration
# ---------------------------------------------------------------------------

def _worker_run(tasks: list[dict], scraped_at: str) -> list[dict]:
    """Each thread runs this with its assigned slice of tasks."""
    results = []
    for task in tasks:
        try:
            result = scrape_constituency(task)
            result["scraped_at"] = scraped_at
            results.append(result)
        except Exception as e:
            logger.error("Worker error on %s: %s", task["url"], e)
            results.append(
                {
                    "state_code": task["state_code"],
                    "state_name": task["state_name"],
                    "ac_no": task["ac_no"],
                    "url": task["url"],
                    "scraped_at": scraped_at,
                    "status": "ERROR",
                    "ac_name": None,
                    "current_round": 0,
                    "total_rounds": 0,
                    "candidates": [],
                }
            )
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
        record_cycle(DB_PATH, cycle_start_iso, cycle_start_iso, 0, 0, 0, 0, 0.0)
        return

    # Build task list with URLs
    tasks = [
        {
            "state_code": item["state_code"],
            "state_name": item["state_name"],
            "ac_no": item["ac_no"],
            "url": get_url(item["state_code"], item["ac_no"]),
        }
        for item in queue
    ]

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
                logger.info("Worker %d finished: %d results", worker_id, len(worker_results))
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
