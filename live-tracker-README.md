# ECI Live Election Tracker - May 2026

A real-time election tracking system for monitoring the 2026 Indian General Assembly elections in Assam, Kerala, Puducherry, Tamil Nadu, and West Bengal.

## Overview

This project scrapes live counting results from the Election Commission of India's official results website and presents them through an interactive Streamlit dashboard. It tracks 824 constituencies across 5 states during the counting day.

## Features

- **Real-time Scraping**: Automatically scrapes round-wise results every 5 minutes during counting
- **Parallel Processing**: Multi-threaded scraper using ThreadPoolExecutor (8 workers)
- **Robust Anti-Bot Handling**: Uses curl subprocess to bypass Akamai TLS fingerprint blocking (ECI blocks Python requests)
- **Selenium Fallback**: Falls back to headless Chrome (undetected-chromedriver) for pages requiring JavaScript
- **Live Dashboard**: Single-page Streamlit app with:
  - Seat tally by party (won vs leading)
  - Party fortunes trend chart across counting rounds
  - Constituency-level drill-down
  - Dark/light mode toggle
- **Database Storage**: SQLite with WAL mode for concurrent read/write operations

## Project Structure

```
live_tracker/
├── eci-live-scraper.py    # Main scraper (requests + BeautifulSoup primary)
├── db_utils.py            # SQLite database layer and analytics queries
├── dashboard.py           # Streamlit dashboard (single-page app)
├── states_may2026.py      # Election configuration (states, parties, URLs)
├── scheduler.sh           # Bash scheduler (runs scraper in loop)
├── archive_final.sh       # Post-day archive script (exports CSV)
├── pyproject.toml         # uv project configuration
├── live_results.db        # SQLite database (created at runtime)
├── scraper.log            # Scraper activity log
└── scheduler_stdout.log   # Scheduler output log
```

## Requirements

- Python 3.11+
- uv (package manager) or pip
- Chrome/Chromium browser (for Selenium fallback)

## Installation

```bash
cd live_tracker
uv sync  # or: pip install -r requirements.txt
```

## Usage

### 1. Run the Scraper (Counting Day)

Start the scheduler before 8 AM on counting day:

```bash
./scheduler.sh
```

This will:
- Run every 5 minutes (300 seconds)
- Track progress across all 824 constituencies
- Stop automatically when all are marked DONE

### 2. View the Dashboard

```bash
uv run dashboard.py
```

Then open http://localhost:8501 in your browser.

### 3. Post-Day Archive

After counting is complete:

```bash
./archive_final.sh
```

This exports:
- `live_rounds_YYYYMMDD.csv` - All vote snapshots by round
- `constituency_status_YYYYMMDD.csv` - Final status per constituency

## Database Schema

### Tables

**rounds**: Time-series vote snapshots
| Column | Type | Description |
|--------|------|-------------|
| state_code | TEXT | ECI state code (S03, S11, etc.) |
| ac_no | INTEGER | Constituency number |
| round_no | INTEGER | Current counting round |
| total_rounds | INTEGER | Total rounds expected |
| candidate | TEXT | Candidate name |
| party | TEXT | Canonical party name |
| votes | INTEGER | Vote count |
| scraped_at | TEXT | ISO-8601 UTC timestamp |

**constituency_status**: Lifecycle tracking per AC
| Column | Type | Description |
|--------|------|-------------|
| state_code | TEXT | State code |
| ac_no | INTEGER | Constituency number |
| status | TEXT | PENDING/LIVE/DONE/ERROR |
| current_round | INTEGER | Current round |
| total_rounds | INTEGER | Total rounds |
| won | INTEGER | 1 if ECI confirms win |

**scrape_cycles**: Audit log of each scrape cycle
| Column | Type | Description |
|--------|------|-------------|
| started_at | TEXT | Cycle start timestamp |
| pages_attempted | INTEGER | Pages scraped |
| pages_success | INTEGER | Successful pages |
| pages_error | INTEGER | Failed pages |
| duration_sec | REAL | Cycle duration |

## Configuration

Edit `states_may2026.py` to modify:

- **STATES**: Add/remove states, update AC counts
- **PARTY_NORMALISE**: Add party name mappings
- **PARTY_COLORS**: Update party display colors
- **MAJORITIES**: Update majority thresholds per state

## Scraping Strategy

1. **Primary**: `curl` subprocess + BeautifulSoup
   - Bypasses Akamai TLS fingerprint blocking
   - ECI blocks Python requests library (JA3 fingerprinting)

2. **Fallback**: Selenium headless Chrome
   - Uses undetected-chromedriver for better bot detection avoidance
   - Only triggered on curl failure

3. **Rate Limiting**: 0.2-0.8s jitter between requests per thread

## Party Mapping

Party names from ECI are normalised to canonical forms:
- `BJP` → Bharatiya Janata Party
- `INC` / `Indian National Congress-Indira` → Indian National Congress
- `TMC` / `AITC` → All India Trinamool Congress
- etc.

## Dashboard Features

- **Seat Tally Tab**: Horizontal bar chart showing won vs leading seats by party
- **Party Fortunes Tab**: Line chart of cumulative votes across counting rounds
- **Constituency Drill-Down**: When viewing a state, shows vote progression per candidate
- **Settings Dialog**: System monitor, error list, cycle history
- **Auto-refresh**: 2-minute page meta-refresh

## States Tracked (May 2026)

| State | Code | AC Count |
|-------|------|----------|
| Assam | S03 | 126 |
| Kerala | S11 | 140 |
| Puducherry | U07 | 30 |
| Tamil Nadu | S22 | 234 |
| West Bengal | S25 | 294 |

**Total**: 824 constituencies

## Troubleshooting

- **404 / Access Denied pages**: Normal before counting starts; marked as `NOT_YET_LIVE`
- **Scraper errors**: Marked in database; retry attempted in subsequent cycles
- **Database locked**: WAL mode enabled; concurrent reads allowed during writes