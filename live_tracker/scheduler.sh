#!/bin/bash
# =============================================================================
# ECI Live Tracker — Scheduler
# Run this script before 8 AM on election day.
# It will loop until manually stopped (Ctrl+C) or until MAX_CYCLES is reached.
# =============================================================================

set -e
cd "$(dirname "$0")"

INTERVAL=300          # 5 minutes in seconds
MAX_CYCLES=210         # ~17.5 hours of coverage (70 × 15 min)
LOG_FILE="scraper.log"
CYCLE=0

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Scheduler started. Interval=${INTERVAL}s, Max cycles=${MAX_CYCLES}" | tee -a "$LOG_FILE"

while [ $CYCLE -lt $MAX_CYCLES ]; do
    CYCLE=$((CYCLE + 1))
    echo "" | tee -a "$LOG_FILE"
    echo "========================================" | tee -a "$LOG_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] CYCLE $CYCLE / $MAX_CYCLES starting..." | tee -a "$LOG_FILE"

    # Run the scraper; capture exit code but don't exit on error
    set +e
    uv run eci-live-scraper.py
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -ne 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: Scraper exited with code $EXIT_CODE" | tee -a "$LOG_FILE"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Scraper completed successfully." | tee -a "$LOG_FILE"
    fi

    # Check if all constituencies are DONE
    DONE_COUNT=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('live_results.db')
    c = conn.cursor()
    c.execute(\"SELECT COUNT(*) FROM constituency_status WHERE status='DONE'\")
    print(c.fetchone()[0])
    conn.close()
except Exception:
    print(0)
" 2>/dev/null || echo "0")

    TOTAL_COUNT=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('live_results.db')
    c = conn.cursor()
    c.execute(\"SELECT COUNT(*) FROM constituency_status\")
    print(c.fetchone()[0])
    conn.close()
except Exception:
    echo 824
" 2>/dev/null || echo "824")

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Progress: ${DONE_COUNT}/${TOTAL_COUNT} constituencies complete." | tee -a "$LOG_FILE"

    if [ "$DONE_COUNT" -ge "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt "0" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] All constituencies DONE. Stopping scheduler." | tee -a "$LOG_FILE"
        break
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sleeping ${INTERVAL}s until next cycle..." | tee -a "$LOG_FILE"
    sleep $INTERVAL
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Scheduler finished after $CYCLE cycles." | tee -a "$LOG_FILE"
