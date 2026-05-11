#!/usr/bin/env python3
"""
Thin client that calls eci-ResultsDayServer.py API to download all rounds for all ACs.

All heavy lifting (browser management, scraping) is handled by the API server.
This client only:
1. Starts the API server if needed
2. Calls /scrape/ac-rounds endpoint for each AC
3. Writes results to SQLite database

Modes:
- One-shot (default): Download all rounds, write to DB, terminate
- Live (--live): Continuously monitor and snapshot every N seconds for live dashboard

Usage:
  python eci-ResultsDayLiveClient.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S03.htm"
  python eci-ResultsDayLiveClient.py --url "..." --live          # 300s interval
  python eci-ResultsDayLiveClient.py --url "..." --live 15       # 15s interval
  python eci-ResultsDayLiveClient.py --url "..." --start-round 5
"""

import sqlite3
import sys
import time
import threading
import subprocess
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

API_URL = "http://localhost:8000"
DB_PATH = Path(__file__).parent / "live_results.db"

# Global lock for database access to prevent concurrent write issues
db_lock = threading.Lock()


def init_database():
    """Initialize database schema if not exists."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            state_code TEXT,
            ac_no INTEGER,
            round_no INTEGER,
            candidate_number INTEGER,
            ac_name TEXT,
            candidate TEXT,
            party TEXT,
            votes INTEGER,
            PRIMARY KEY (state_code, ac_no, round_no, candidate_number)
        )
    """)
    conn.commit()
    conn.close()


def get_db_connection():
    """Get SQLite database connection with WAL mode for concurrent access."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def execute_with_retry(cursor, query, params=(), max_retries=10):
    """Execute SQL with retry on database lock."""
    for attempt in range(max_retries):
        try:
            cursor.execute(query, params)
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise


def commit_with_retry(conn, max_retries=10):
    """Commit with retry on database lock."""
    for attempt in range(max_retries):
        try:
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise


def process_ac(ac_no: int, url: str, state_code: str, start_round: int = 1):
    """
    Process a single AC by calling the API endpoint.
    Returns dict with status info.
    
    Args:
        ac_no: AC number to process
        url: Base URL for the results page
        state_code: State code (e.g., 'S03')
        start_round: Start downloading from this round number (default 1 = all rounds)
    """
    # Retry logic with increased timeout for slow API responses
    max_retries = 3
    response = None
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{API_URL}/scrape/ac-rounds",
                json={"url": url, "ac_no": ac_no, "start_round": start_round},
                timeout=120  # Increased from 60 to handle slower responses
            )
            break  # Success, exit retry loop
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                print(f"  AC {ac_no}: Timeout, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
                continue
            return {"status": "error", "ac_no": ac_no, "error": "Timeout after 3 attempts"}
        except requests.exceptions.ConnectionError as e:
            if attempt < max_retries - 1:
                print(f"  AC {ac_no}: Connection error, retrying ({attempt + 1}/{max_retries})...")
                time.sleep(2)
                continue
            return {"status": "error", "ac_no": ac_no, "error": str(e)}
    
    # Process the response
    try:
        data = response.json()
        
        if data.get("status") == "error" and "404" in data.get("error", ""):
            return {"status": "done", "ac_no": ac_no}  # AC doesn't exist
        
        if data.get("status") != "success":
            return {"status": "error", "ac_no": ac_no, "error": data.get("error")}
        
        ac_data = data.get("data", {})
        ac_name = ac_data.get("constituency", f"AC-{ac_no}")
        rounds = ac_data.get("rounds", [])
        postal_votes = ac_data.get("postal_votes", [])
        
        # Store to database (serialized with lock to avoid concurrent access issues)
        with db_lock:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            candidate_map = {}
            
            for round_info in rounds:
                round_num = round_info["round"]
                for c in round_info["tally"]:
                    candidate_name = c.get("candidate", "")
                    if candidate_name not in candidate_map:
                        candidate_map[candidate_name] = len(candidate_map) + 1
                    
                    execute_with_retry(cursor, """
                        INSERT OR REPLACE INTO rounds 
                        (state_code, ac_no, round_no, candidate_number, ac_name,
                         candidate, party, votes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (state_code, ac_no, round_num, candidate_map[candidate_name],
                          ac_name, candidate_name, c.get("party", ""),
                          int(c.get("total", 0))))
            
            # Store postal votes as round 999
            for c in postal_votes:
                candidate_name = c.get("candidate", "")
                if candidate_name not in candidate_map:
                    candidate_map[candidate_name] = len(candidate_map) + 1
                
                execute_with_retry(cursor, """
                    INSERT OR REPLACE INTO rounds 
                    (state_code, ac_no, round_no, candidate_number, ac_name,
                     candidate, party, votes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (state_code, ac_no, 999, candidate_map[candidate_name],
                      ac_name, candidate_name, c.get("party", ""),
                      int(c.get("evm_votes", 0)) + int(c.get("postal_votes", 0))))
            
            commit_with_retry(conn)
            conn.close()
        
        return {"status": "success", "ac_no": ac_no, "ac_name": ac_name, "rounds": len(rounds)}
        
    except Exception as e:
        return {"status": "error", "ac_no": ac_no, "error": str(e)}


def run_cycle(url: str, state_code: str, start_round: int, test_ac: int = 0, sequential: bool = False):
    """Run a single processing cycle for all ACs."""
    results = []
    num_workers = 2  # Reduced from 5 to prevent resource exhaustion with concurrent Chrome instances
    
    if test_ac > 0:
        result = process_ac(test_ac, url, state_code, start_round)
        results.append(result)
        print(f"  AC {test_ac}: {result}")
    elif sequential:
        # Sequential mode: process ACs one at a time (safest for resource-constrained systems)
        while True:
            ac_no = len(results) + 1
            result = process_ac(ac_no, url, state_code, start_round)
            results.append(result)
            print(f"  AC {ac_no}: {result.get('ac_name', 'Error')} ({result.get('rounds', 0)}r)" if result["status"] == "success" else f"  AC {ac_no}: FAILED - {result.get('error', 'Unknown error')}")
            if result["status"] in ("done", "error"):
                break
    else:
        worker_state = {"current": 1, "end_of_results": False}
        lock = threading.Lock()
        
        def worker():
            while True:
                with lock:
                    if worker_state["end_of_results"]:
                        break
                    ac_no = worker_state["current"]
                    worker_state["current"] += 1
                
                result = process_ac(ac_no, url, state_code, start_round)
                results.append(result)
                
                if result["status"] == "done":
                    with lock:
                        worker_state["end_of_results"] = True
                    break
                
                # Show actual error message if there's one
                if result["status"] == "error":
                    print(f"  AC {ac_no}: FAILED - {result.get('error', 'Unknown error')}")
                else:
                    print(f"  AC {ac_no}: {result.get('ac_name', 'Error')} ({result.get('rounds', 0)}r)")
        
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker) for _ in range(num_workers)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"Worker error: {e}")
    
    return results


def main(url: str, test_ac: int = 0, flush_db: bool = False, 
         live: int = 0, interval: int = 30, start_round: int = 1, sequential: bool = False):
    """Main entry point.
    
    Args:
        live: If > 0, enables live mode with this as the interval (seconds)
        interval: Interval for live mode when live=0 (used as fallback)
    """
    if not url:
        print("Error: --url parameter is required")
        sys.exit(1)
    
    # Initialize database schema
    init_database()
    
    print("Starting download of election results...")
    print("=" * 60)
    
    # Start API server
    print("Starting API server...")
    
    # Get the script directory for finding the server
    script_dir = Path(__file__).parent.parent
    server_path = script_dir / "eci-ResultsDayServer.py"
    
    api_process = subprocess.Popen(
        [sys.executable, str(server_path), "--api"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(script_dir)
    )
    
    # Wait for server to start (check health incrementally)
    max_wait = 15
    for i in range(max_wait):
        time.sleep(1)
        try:
            response = requests.get(f"{API_URL}/health", timeout=2)
            if response.status_code == 200:
                print(f"API server: {response.json()}")
                break
        except:
            if i == max_wait - 1:
                print(f"Error: API server failed to start after {max_wait}s")
                api_process.terminate()
                sys.exit(1)
            continue
    else:
        print(f"Error: Could not connect to API server")
    
    # Parse URL to get state code
    import re
    match = re.match(r'^https://results\.eci\.gov\.in/([^/]+)/partywiseresult-([A-Z]\d+)\.htm$', url)
    if not match:
        print("Error: Invalid URL format")
        api_process.terminate()
        sys.exit(1)
    
    election_id = match.group(1)
    state_code = match.group(2)
    
    print(f"\nProcessing: {url}")
    print(f"  State: {state_code}")
    if start_round > 1:
        print(f"  Start round: {start_round} (incremental mode)")
    
    # Live mode loop
    cycle_num = 0
    try:
        while True:
            cycle_num += 1
            print(f"\n{'='*60}")
            print(f"Cycle {cycle_num} - {time.strftime('%H:%M:%S')}")
            
            start_time = time.time()
            results = run_cycle(url, state_code, start_round, test_ac, sequential)
            
            elapsed = time.time() - start_time
            successful = sum(1 for r in results if r["status"] == "success")
            
            print(f"\nCycle {cycle_num} completed: {successful} ACs in {elapsed:.1f}s")
            
            if live <= 0:
                break
            
            print(f"Next update in {interval}s... (Ctrl+C to stop)")
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\n\nLive monitoring stopped by user")
    
    # Cleanup
    api_process.terminate()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--test-ac", type=int, default=0)
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--live", type=int, nargs="?", const=300, default=0, 
                        help="Continuous monitoring mode with optional seconds interval (default: 300s)")
    parser.add_argument("--start-round", type=int, default=1, help="Start downloading from this round (incremental mode)")
    parser.add_argument("--sequential", action="store_true", 
                        help="Process ACs sequentially instead of concurrently (safer for resource-constrained systems)")
    args = parser.parse_args()
    
    main(url=args.url, test_ac=args.test_ac, flush_db=args.flush,
         live=args.live, interval=args.live if args.live > 0 else 30, start_round=args.start_round, sequential=args.sequential)