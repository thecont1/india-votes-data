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
    """Party-wise won + leading seat counts plus deposit-loss breakdown.

    Returns list of {party_abv, party_name, color, won, leading, total,
                      lost_no_deposit, lost_deposit} sorted by won descending.
    """
    conn = _connect()
    cur = _cursor(conn)
    try:
        sf = ""                               # state filter fragment
        params: list = []
        if state:
            sf = "AND r.state_code = %s" if IS_PG else "AND r.state_code = ?"
            params.append(state)
            params.append(state)  # appears in 2 CTEs (ac_winners + party_best)

        query = f"""
        WITH latest_rounds AS (
            SELECT state_code, ac_no, MAX(round_no) as max_round
            FROM rounds_ac
            GROUP BY state_code, ac_no
        ),
        ac_totals AS (
            SELECT r.state_code, r.ac_no, SUM(r.votes) as total_votes
            FROM rounds_ac r
            JOIN latest_rounds lr
                ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
                AND r.round_no = lr.max_round
            GROUP BY r.state_code, r.ac_no
        ),
        ac_winners AS (
            SELECT state_code, ac_no, winner_abv
            FROM (
                SELECT lr.state_code, lr.ac_no, p.abv as winner_abv,
                       ROW_NUMBER() OVER (
                           PARTITION BY lr.state_code, lr.ac_no
                           ORDER BY r.votes DESC
                       ) as rn
                FROM rounds_ac r
                JOIN latest_rounds lr
                    ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
                    AND r.round_no = lr.max_round
                JOIN parties p ON r.party_abv = p.name
                WHERE 1=1 {sf}
            ) WHERE rn = 1
        ),
        party_best AS (
            SELECT state_code, ac_no, party_abv, party_name,
                   votes, ac_declared, winner_abv, total_votes
            FROM (
                SELECT r.state_code, r.ac_no,
                       p.abv as party_abv, p.name as party_name,
                       r.votes, cs.won as ac_declared,
                       aw.winner_abv, at.total_votes,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.state_code, r.ac_no, p.abv
                           ORDER BY r.votes DESC
                       ) as rn
                FROM rounds_ac r
                JOIN latest_rounds lr
                    ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
                    AND r.round_no = lr.max_round
                JOIN constituency_status cs
                    ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
                JOIN parties p ON r.party_abv = p.name
                JOIN ac_totals at
                    ON r.state_code = at.state_code AND r.ac_no = at.ac_no
                JOIN ac_winners aw
                    ON r.state_code = aw.state_code AND r.ac_no = aw.ac_no
                WHERE 1=1 {sf}
            ) WHERE rn = 1
        )
        SELECT
            party_abv,
            MAX(party_name) as party_name,
            SUM(CASE WHEN party_abv = winner_abv AND ac_declared = 1
                     THEN 1 ELSE 0 END) as won_seats,
            SUM(CASE WHEN party_abv = winner_abv AND ac_declared = 0
                     THEN 1 ELSE 0 END) as leading_seats,
            SUM(CASE WHEN party_abv != winner_abv AND ac_declared = 1
                     AND votes * 6 >= total_votes
                     THEN 1 ELSE 0 END) as lost_no_deposit,
            SUM(CASE WHEN party_abv != winner_abv AND ac_declared = 1
                     AND votes * 6 < total_votes
                     THEN 1 ELSE 0 END) as lost_deposit
            , SUM(votes) as total_votes
        FROM party_best
        GROUP BY party_abv
        ORDER BY won_seats DESC
        """
        cur.execute(query, params)
        rows = cur.fetchall()

        # Check if won status is populated at all (historical data may have won=0 everywhere)
        check_q = "SELECT SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) as won_count FROM constituency_status"
        if state:
            p = "%s" if IS_PG else "?"
            check_q += f" WHERE state_code={p}"
        cur.execute(check_q, [state] if state else [])
        has_won_data = (cur.fetchone() or {}).get("won_count", 0) > 0

        result = []
        for row in rows:
            abv = row["party_abv"]
            won = row["won_seats"]
            leading = row["leading_seats"]
            lost_no_dep = row["lost_no_deposit"]
            lost_dep = row["lost_deposit"]
            # If no won status populated anywhere, treat all as won (historical data)
            if not has_won_data:
                won += leading
                leading = 0
                lost_no_dep = 0
                lost_dep = 0
            result.append({
                "party_abv": abv,
                "party_name": row.get("party_name", abv),
                "won": won,
                "leading": leading,
                "total": won + leading,
                "lost_no_deposit": lost_no_dep,
                "lost_deposit": lost_dep,
                "total_votes": row.get("total_votes", 0),
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


@app.get("/api/ac-races")
def ac_races(state: str = Query(..., description="State code (required)")):
    """Per-AC candidate data: all candidates in each AC's latest round.

    Returns every AC in the state with all candidates ranked by votes.
    """
    conn = _connect()
    cur = _cursor(conn)
    try:
        p = "%s" if IS_PG else "?"
        cur.execute(f"""
            WITH latest_rounds AS (
                SELECT state_code, ac_no, MAX(round_no) as max_round
                FROM rounds_ac
                WHERE state_code = {p}
                GROUP BY state_code, ac_no
            ),
            ranked AS (
                SELECT r.state_code, r.ac_no, r.ac_name,
                       r.candidate,
                       p.abv as party_abv, p.name as party_name,
                       r.votes,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.state_code, r.ac_no
                           ORDER BY r.votes DESC
                       ) as rank
                FROM rounds_ac r
                JOIN latest_rounds lr
                    ON r.state_code = lr.state_code
                    AND r.ac_no = lr.ac_no
                    AND r.round_no = lr.max_round
                JOIN parties p ON r.party_abv = p.name
            )
            SELECT ac_no, ac_name, candidate, party_abv, party_name,
                   votes, rank,
                   SUM(votes) OVER (PARTITION BY ac_no) as total_votes
            FROM ranked
            ORDER BY ac_no, rank
        """, (state,))
        rows = cur.fetchall()

        # Group by AC
        from collections import OrderedDict
        ac_map = OrderedDict()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {
                'ac_no': row[0], 'ac_name': row[1], 'candidate': row[2],
                'party_abv': row[3], 'party_name': row[4],
                'votes': row[5], 'rank': row[6], 'total_votes': row[7],
            }
            ac_no = d['ac_no']
            if ac_no not in ac_map:
                ac_map[ac_no] = {
                    'ac_no': ac_no,
                    'ac_name': d['ac_name'],
                    'total_votes': d['total_votes'],
                    'margin': 0,
                    'candidates': [],
                }
            d['color'] = PARTY_COLORS.get(d['party_abv'], DEFAULT_COLOR)
            ac_map[ac_no]['candidates'].append(d)

        result = list(ac_map.values())
        # Set margin = winner votes - runner-up votes
        for ac in result:
            cands = ac['candidates']
            if len(cands) >= 2:
                ac['margin'] = cands[0]['votes'] - cands[1]['votes']
            elif len(cands) == 1:
                ac['margin'] = cands[0]['votes']
        # Sort by margin descending (largest margin first = default selection)
        result.sort(key=lambda a: a['margin'], reverse=True)

        return {"races": result, "state": state}
    finally:
        conn.close()


@app.get("/api/roundwise")
def roundwise(state: str = Query(..., description="State code (required)")):
    """Roundwise progression: cumulative seats as ACs complete counting.

    For each AC, find its final winner and the round when counting completed.
    Then, for each round threshold N, count how many ACs finished by round N
    and how many seats each party won among those. This produces rising lines
    that show the mandate crystallising over time.
    """
    conn = _connect()
    cur = _cursor(conn)
    try:
        p = "%s" if IS_PG else "?"
        cur.execute(f"""
            WITH ranked AS (
                SELECT r.state_code, r.ac_no, r.round_no,
                       p.abv as party_abv,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.state_code, r.ac_no, r.round_no
                           ORDER BY r.votes DESC
                       ) as rank
                FROM rounds_ac r
                JOIN parties p ON r.party_abv = p.name
                WHERE r.state_code = {p}
                  AND r.round_no != 999
            ),
            -- For each AC, find its final winner (rank 1 in the latest round)
            ac_latest AS (
                SELECT state_code, ac_no, MAX(round_no) as max_round
                FROM rounds_ac
                WHERE state_code = {p} AND round_no != 999
                GROUP BY state_code, ac_no
            ),
            ac_winners AS (
                SELECT r.ac_no, r.party_abv, a.max_round
                FROM ranked r
                JOIN ac_latest a
                    ON r.state_code = a.state_code
                    AND r.ac_no = a.ac_no
                    AND r.round_no = a.max_round
                WHERE r.rank = 1
            )
            SELECT max_round as round_no, party_abv, COUNT(*) as seats
            FROM ac_winners
            GROUP BY max_round, party_abv
            ORDER BY max_round
        """, (state, state))
        rows = cur.fetchall()

        # Build: { round_no: { party_abv: seats } }
        from collections import defaultdict
        rounds_data = defaultdict(lambda: defaultdict(int))
        all_parties = set()
        for row in rows:
            rd = dict(row) if hasattr(row, 'keys') else {
                'round_no': row[0], 'party_abv': row[1], 'seats': row[2],
            }
            rounds_data[rd['round_no']][rd['party_abv']] = rd['seats']
            all_parties.add(rd['party_abv'])

        # Build cumulative series: for each round, accumulate seats
        round_nos = sorted(rounds_data.keys())
        cumulative = defaultdict(int)
        cumulative_series = {}
        for rn in round_nos:
            for party, seats in rounds_data[rn].items():
                cumulative[party] += seats
            cumulative_series[rn] = dict(cumulative)

        # Sort parties by final seat count (descending) for legend order
        final_seats = {p: cumulative.get(p, 0) for p in all_parties}
        sorted_parties = sorted(all_parties, key=lambda p: final_seats[p], reverse=True)

        series = []
        for party in sorted_parties:
            if final_seats[party] == 0:
                continue  # skip parties that never won
            series.append({
                'party_abv': party,
                'party_name': party,
                'color': PARTY_COLORS.get(party, DEFAULT_COLOR),
                'data': [cumulative_series[rn].get(party, 0) for rn in round_nos],
            })

        return {
            "state": state,
            "rounds": round_nos,
            "series": series,
        }
    finally:
        conn.close()


@app.get("/api/status")
def status_summary(state: str = Query(default=None)):
    """Counting progress summary — computed from actual rounds data.

    DONE = AC has valid scraped data (votes > 0, not just round-999
           summary, and latest round has > 1 candidate).
    LIVE = scraper is actively working on this AC.
    PENDING = everything else.
    Only counts ACs belonging to states that appear in rounds_ac
    (i.e. states tracked by this election cycle via election.conf).
    """
    conn = _connect()
    cur = _cursor(conn)
    try:
        p = "%s" if IS_PG else "?"
        state_filter = ""
        state_params: list = []
        if state:
            state_filter = f"AND cs.state_code = {p}"
            state_params.append(state)

        cur.execute(f"""
            SELECT
                CASE
                    WHEN cs.status = 'LIVE' THEN 'LIVE'
                    WHEN r.ac_no IS NOT NULL THEN 'DONE'
                    ELSE 'PENDING'
                END as effective_status,
                COUNT(*) as cnt
            FROM constituency_status cs
            LEFT JOIN (
                -- Only ACs with genuinely valid scraped data qualify as DONE:
                SELECT r1.state_code, r1.ac_no
                FROM rounds_ac r1
                JOIN (
                    SELECT state_code, ac_no,
                           MAX(round_no) AS max_round,
                           COUNT(DISTINCT round_no) AS n_rounds
                    FROM rounds_ac
                    GROUP BY state_code, ac_no
                ) lr ON r1.state_code = lr.state_code
                    AND r1.ac_no = lr.ac_no
                    AND r1.round_no = lr.max_round
                GROUP BY r1.state_code, r1.ac_no, lr.max_round, lr.n_rounds
                HAVING SUM(r1.votes) > 0                          -- criteria 2: real votes
                   AND NOT (lr.max_round = 999 AND lr.n_rounds = 1) -- criteria 3: not just summary page
                   AND COUNT(*) > 1                                -- criteria 4: >1 candidate in latest round
            ) r ON cs.state_code = r.state_code AND cs.ac_no = r.ac_no
            -- Only count ACs belonging to tracked states (states with data in rounds_ac)
            WHERE cs.state_code IN (SELECT DISTINCT state_code FROM rounds_ac)
              {state_filter}
            GROUP BY effective_status
        """, state_params)
        statuses = {row["effective_status"]: row["cnt"] for row in cur.fetchall()}
        return {
            "statuses": statuses,
            "active_states": 1 if state else len(statuses),
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
