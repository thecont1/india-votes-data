# Plan: FastAPI + Chart.js Live Election Dashboard

## Goal

Replace Streamlit dashboard with a single-page FastAPI + Chart.js dashboard designed as a "counting day TV" — left open all day, auto-refreshing every 5 seconds with smooth animations. Bars are striped when a party is leading, solid when results are declared.

## Current State

- `server.py` — FastAPI app with scraping endpoints only (no dashboard data endpoints)
- `dashboard.py` — Streamlit dashboard (to be deleted after migration)
- `db_utils.py` — Dual-backend (SQLite/PostgreSQL), `_dict_factory` for pandas compat
- DB schema: `rounds_ac` (209K rows), `constituency_status` (won=0/1, status=PENDING/LIVE/DONE/ERROR), `states`, `parties`
- Party colors inlined in dashboard.py (to move into a shared location)

## Key Design Decisions

1. **Won vs Leading**: `constituency_status.won=1` = declared winner. AC is LIVE/DONE but won=0 = currently leading. This distinction already exists in the scraper (`update_won_status`).
2. **Polling, not WebSocket**: 5-second polling is fine. Simpler, no connection management.
3. **Single HTML file**: No build step. Chart.js via CDN. Self-contained.
4. **Dark theme**: TV-friendly, high contrast.

## Step-by-Step Plan

### Step 1: Add dashboard API endpoints to `server.py`

Add these GET endpoints:

- `GET /api/seat-tally?state={state_code}` — Returns party-wise won + leading counts
  - Query: JOIN rounds_ac (latest round per AC) with constituency_status (won flag)
  - Group by party_abv, SUM won seats, SUM leading seats
  - Include party name, color from parties table

- `GET /api/status` — Returns overall status summary
  - constituency_status grouped by status (PENDING/LIVE/DONE/ERROR)
  - Per-state breakdown
  - Last updated timestamp

- `GET /api/constituency/{state_code}/{ac_no}` — Returns round-by-round data for one AC
  - All rounds from rounds_ac for this AC
  - Used for drill-down view (optional, can add later)

### Step 2: Create `static/index.html`

Single HTML file served by FastAPI's `StaticFiles`:

**Layout (top to bottom):**
1. **Header bar**: Dark blue, title "ECI Live Election Tracker — {state}", date, status indicator
2. **State selector**: Pill buttons (Overall + 5 states), no page reload — JS fetches new data
3. **Seat Tally chart**: Horizontal bar chart
   - Solid fill = won (declared)
   - Striped/hatched fill = leading (not yet declared)
   - Majority line (dashed red)
   - Party abbreviations on Y-axis, seat counts on bars
4. **Status bar**: "X of Y ACs counting | Z declared | ⏱️ Last updated HH:MM:SS"

**Tech stack:**
- Chart.js 4.x via CDN
- Vanilla JS (no framework)
- CSS animations for smooth bar transitions
- Auto-refresh: `setInterval(fetchData, 5000)`

**Chart.js striped bars:**
- Use `chartjs-plugin-patterns` or canvas pattern fill for hatching
- Or: draw two datasets per party — one solid (won), one hatched (leading) stacked

### Step 3: Serve static files from FastAPI

Add to `server.py`:
```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="static", html=True))
```

### Step 4: Move party colors to `db_utils.py`

The parties table has a `colour` column (ECI's raw colors). But Mahesh's dashboard uses custom colors. Options:
- Store custom dashboard colors in `parties.dashboard_colour` column
- Or keep a simple dict in db_utils.py

Best: add `dashboard_colour` to the parties table. But that's a schema change. Simpler: keep a `PARTY_COLORS` dict in a shared location. Since config.py is now just URLs, put it in db_utils.py as a constant.

### Step 5: Delete `dashboard.py` (Streamlit)

Once the new dashboard works, remove the Streamlit dependency.

## Files to Change

| File | Action |
|------|--------|
| `server.py` | Add 2 API endpoints + static file mount |
| `static/index.html` | New — single-page dashboard |
| `db_utils.py` | Add query helpers for dashboard data |
| `dashboard.py` | Delete (after verification) |
| `requirements.txt` | Remove streamlit |

## Verification

1. Start server: `uvicorn server:app --reload`
2. Open http://localhost:8000 — should show the dashboard
3. Verify auto-refresh works (bars animate on data change)
4. Verify striped vs solid bars render correctly
5. Verify state selector switches data without page reload
6. Test with SQLite (default) — should show existing 209K rows
7. Test with PostgreSQL — should show empty state gracefully

## Risks

- Chart.js hatched pattern rendering performance with many parties (mitigate by grouping small parties into "Others")
- First paint latency if rounds_ac query is slow (mitigate with indexes)
- Dark theme CSS may need iteration for TV viewing distance
