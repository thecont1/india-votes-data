#!/bin/bash
# =============================================================================
# ECI Live Tracker — Post-Day Archive
# Run ONCE at end of day after all counting is complete.
# =============================================================================

set -e
cd "$(dirname "$0")/.."

DATE=$(date +%Y%m%d)
RESULTS_DIR="results"
LIVE_DIR="live_tracker"

echo "=== Post-Day Archive: $DATE ==="

# 1. Export SQLite rounds table to CSV
echo "Exporting rounds table..."
python3 -c "
import sqlite3, csv
conn = sqlite3.connect('${LIVE_DIR}/live_results.db')
cur = conn.cursor()
cur.execute('SELECT * FROM rounds ORDER BY state_code, ac_no, scraped_at')
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
with open('${RESULTS_DIR}/live_rounds_${DATE}.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(cols)
    w.writerows(rows)
conn.close()
print(f'Exported {len(rows)} rows to live_rounds_${DATE}.csv')
"

# 2. Export constituency_status summary
echo "Exporting constituency status..."
python3 -c "
import sqlite3, csv
conn = sqlite3.connect('${LIVE_DIR}/live_results.db')
cur = conn.cursor()
cur.execute('SELECT * FROM constituency_status ORDER BY state_code, ac_no')
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
with open('${RESULTS_DIR}/constituency_status_${DATE}.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(cols)
    w.writerows(rows)
conn.close()
print(f'Exported {len(rows)} rows to constituency_status_${DATE}.csv')
"

# 3. Git commit
echo "Committing to git..."
git add "${RESULTS_DIR}/live_rounds_${DATE}.csv"
git add "${RESULTS_DIR}/constituency_status_${DATE}.csv"
git commit -m "May 2026 Election: live tracking data archive [${DATE}]" || echo "Nothing to commit."

echo ""
echo "=== Archive complete ==="
echo "Files created:"
echo "  ${RESULTS_DIR}/live_rounds_${DATE}.csv"
echo "  ${RESULTS_DIR}/constituency_status_${DATE}.csv"
echo ""
echo "To push: git push origin live-tracker"
