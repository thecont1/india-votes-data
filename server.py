"""
ECI Results Scraper - Server Entry Point (FastAPI)

This module provides the FastAPI server for ECI results scraping
and the live election dashboard API.
"""

import os
import sys
import time
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db_utils import _connect, _cursor, IS_PG

app = FastAPI(
    title="ECI Results Scraper API",
    description="API for scraping Election Commission of India election results",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Party colors for dashboard (custom palette, not ECI raw hex)
# ---------------------------------------------------------------------------
PARTY_COLORS = {
    "BJP": "#FF6B00",
    "INC": "#00BFFF",
    "DMK": "#FF0000",
    "AIADMK": "#008000",
    "AITC": "#00FF7F",
    "CPM": "#CC0000",
    "TVK": "#FFD700",
    "INC": "#1E90FF",
    "IUML": "#006400",
    "AINRC": "#808080",
    "CPI": "#8B0000",
    "CPI(M)": "#CC0000",
    "BPF": "#00CED1",
    "AGP": "#32CD32",
    "VCK": "#8A2BE2",
    "PMK": "#A9A9A9",
    "IND": "#D3D3D3",
    "NCP": "#00008B",
    "JD(U)": "#008080",
    "SHS": "#FF4500",
    "TDP": "#FFD700",
    "YSRCP": "#1E90FF",
    "AAP": "#0066CC",
    "BRS": "#FF69B4",
}
DEFAULT_COLOR = "#888888"


# ---------------------------------------------------------------------------
# Pydantic models (scraping)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------
@app.get("/api/seat-tally")
def seat_tally(state: str = Query("", description="State code filter, empty=all")):
    """Party-wise won + leading seat counts.

    Returns list of {party_abv, party_name, color, won, leading, total}
    sorted by total descending.
    """
    conn = _connect()
    cur = _cursor(conn)
    try:
        # Find the latest round per AC, then pick the top candidate
        where = ""
        params: list = []
        if state:
            where = "AND r.state_code = %s" if IS_PG else "AND r.state_code = ?"
            params.append(state)

        p = "%s" if IS_PG else "?"

        query = f"""
        WITH latest_rounds AS (
            SELECT state_code, ac_no, MAX(round_no) as max_round
            FROM rounds_ac
            GROUP BY state_code, ac_no
        ),
        top_candidates AS (
            SELECT r.state_code, r.ac_no, p.abv as party_abv, r.votes, cs.won
            FROM rounds_ac r
            JOIN latest_rounds lr
                ON r.state_code = lr.state_code
                AND r.ac_no = lr.ac_no
                AND r.round_no = lr.max_round
            JOIN constituency_status cs
                ON r.state_code = cs.state_code
                AND r.ac_no = cs.ac_no
            JOIN parties p ON r.party_abv = p.name
            WHERE r.votes = (
                SELECT MAX(r2.votes) FROM rounds_ac r2
                WHERE r2.state_code = r.state_code
                  AND r2.ac_no = r.ac_no
                  AND r2.round_no = r.round_no
            )
            {where}
        )
        SELECT
            party_abv,
            SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) as won_seats,
            SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) as leading_seats,
            COUNT(*) as total
        FROM top_candidates
        GROUP BY party_abv
        ORDER BY total DESC
        """
        cur.execute(query, params)
        rows = cur.fetchall()

        # Check if won status is populated at all (historical data may have won=0 everywhere)
        check_q = f"SELECT SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) as won_count FROM constituency_status"
        if state:
            check_q += f" WHERE state_code={('%s' if IS_PG else '?')}" if IS_PG else " WHERE state_code=?"
        cur.execute(check_q, [state] if state else [])
        has_won_data = (cur.fetchone() or {}).get("won_count", 0) > 0

        result = []
        for row in rows:
            abv = row["party_abv"]
            won = row["won_seats"]
            leading = row["leading_seats"]
            # If no won status populated anywhere, treat all as won (historical data)
            if not has_won_data:
                won += leading
                leading = 0
            result.append({
                "party_abv": abv,
                "won": won,
                "leading": leading,
                "total": row["total"],
                "color": PARTY_COLORS.get(abv, DEFAULT_COLOR),
            })

        # Compute majority line from states table
        majority = None
        if state:
            cur.execute(
                f"SELECT assembly_seats FROM states WHERE state_code={('%s' if IS_PG else '?')}",
                (state,),
            )
            row = cur.fetchone()
            if row:
                majority = row["assembly_seats"] // 2 + 1
        else:
            # Overall: sum of all tracked states' assembly_seats
            cur.execute("SELECT SUM(assembly_seats) as total_seats FROM states WHERE state_code IN (SELECT DISTINCT state_code FROM constituency_status)")
            row = cur.fetchone()
            if row and row["total_seats"]:
                majority = row["total_seats"] // 2 + 1

        return {
            "parties": result,
            "majority": majority,
            "updated_at": datetime.now().isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/status")
def status_summary():
    """Counting progress summary."""
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute("""
            SELECT status, COUNT(*) as cnt
            FROM constituency_status
            GROUP BY status
        """)
        statuses = {row["status"]: row["cnt"] for row in cur.fetchall()}

        cur.execute("SELECT COUNT(*) as cnt FROM rounds_ac")
        total_rounds = cur.fetchone()["cnt"]

        cur.execute("SELECT COUNT(DISTINCT state_code) as cnt FROM constituency_status WHERE status != 'PENDING'")
        active_states = cur.fetchone()["cnt"]

        return {
            "statuses": statuses,
            "total_rounds_stored": total_rounds,
            "active_states": active_states,
            "updated_at": datetime.now().isoformat(),
        }
    finally:
        conn.close()


@app.get("/api/constituency/{state_code}/{ac_no}")
def constituency_rounds(state_code: str, ac_no: int):
    """Round-by-round data for one AC."""
    conn = _connect()
    cur = _cursor(conn)
    try:
        cur.execute(
            f"SELECT round_no, candidate, party_abv, votes "
            f"FROM rounds_ac WHERE state_code={('%s' if IS_PG else '?')} "
            f"AND ac_no={('%s' if IS_PG else '?')} ORDER BY round_no, votes DESC",
            (state_code, ac_no),
        )
        rows = cur.fetchall()
        return {"state_code": state_code, "ac_no": ac_no, "rounds": rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scraping endpoints
# ---------------------------------------------------------------------------
@app.post("/scrape")
async def scrape_endpoint(request: ScrapeRequest):
    """Scrape constituency results from ECI party-wise URL."""
    from core.browser import create_chrome_driver
    from core.scraper import (
        build_constituency_url, get_state_code,
        parse_partywise_url, scrape_constituency_sync,
    )
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        election_identifier, state_code = parse_partywise_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = scrape_constituency_sync(
        election_identifier, state_code,
        limit=request.limit,
        respect_mode=request.respect
    )

    if results["constituencywise_results"]:
        driver = create_chrome_driver()
        try:
            url = build_constituency_url(election_identifier, state_code, 1)
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, 'h1'))
            )
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

    return {"status": "success", "data": results}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/scrape/ac-rounds")
def scrape_ac_rounds_endpoint(request: ScrapeAcRoundsRequest):
    from core.browser import create_chrome_driver
    from core.scraper import parse_partywise_url, scrape_ac_rounds_core

    try:
        election_identifier, state_code = parse_partywise_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    driver = create_chrome_driver()
    try:
        result = scrape_ac_rounds_core(
            driver, election_identifier, state_code,
            request.ac_no, request.start_round
        )
        if result.get("status") == "done":
            return {"status": "error", "error": "AC not found (404)"}
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        driver.quit()


@app.post("/scrape/all-rounds")
def scrape_all_rounds_endpoint(request: ScrapeAllRoundsRequest):
    from core.browser import create_chrome_driver
    from core.scraper import build_constituency_url, parse_partywise_url, scrape_ac_rounds_core

    try:
        election_identifier, state_code = parse_partywise_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    driver = create_chrome_driver()
    results = []

    try:
        if request.end_ac == 0:
            max_ac = 0
            for i in range(1, 1000):
                test_url = build_constituency_url(election_identifier, state_code, i)
                driver.get(test_url)
                if "404" in driver.title:
                    break
                max_ac = i
        else:
            max_ac = request.end_ac

        for ac_no in range(request.start_ac, max_ac + 1):
            result = scrape_ac_rounds_core(
                driver, election_identifier, state_code, ac_no, 1
            )
            if result.get("status") == "done":
                break
            if result.get("status") == "success":
                results.append(result.get("data", {}))
            if request.respect and ac_no % 10 == 0:
                time.sleep(1)

    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        driver.quit()

    return {"status": "success", "data": results, "total_acs": len(results)}


# ---------------------------------------------------------------------------
# Static file serving (dashboard must be mounted LAST)
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_dashboard():
    """Serve the live dashboard."""
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "Dashboard not found. Place index.html in static/"}


if __name__ == "__main__":
    if "--api" in sys.argv:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        print("Use cli.py for command-line scraping, or run with --api flag.")
