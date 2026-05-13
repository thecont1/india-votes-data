# India Votes Data

A Python tool for scraping, storing, and visualizing Indian election results from the [Election Commission of India](https://www.eci.gov.in/) website.

## Overview

Scrapes constituency-wise election results — candidate names, party affiliations, vote counts — and stores them in a normalized SQLite or PostgreSQL database. Includes a Streamlit dashboard for live visualization on counting day.

## Features

- **Multi-threaded scraping** — 5 concurrent workers (CLI), 3 (live client)
- **Dual database backend** — SQLite (local) or PostgreSQL via `DATABASE_URL`
- **Normalized schema** — states, parties, rounds_ac, constituency_status ([schema docs](SCHEMA.md))
- **Live dashboard** — Streamlit app with seat tally, party trends, constituency drill-down
- **By-election support** — round_no is the time axis; new elections are just new rounds
- **CSV/JSON export** — optional file output alongside database writes

## Setup

```bash
git clone https://github.com/thecont1/india-votes-data.git
cd india-votes-data
uv sync
```

Requires Python 3.14+, Chrome (for Selenium).

## Usage

### CLI Scraper

Scrapes final results from an ECI party-wise URL. Always writes to DB; optional CSV/JSON.

```bash
# DB only (round_no=999)
uv run cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"

# DB + CSV
uv run cli.py --url "..." --csv

# DB + CSV + JSON
uv run cli.py --url "..." --csv --json

# Respectful mode (single-threaded, 1s pause every 10 URLs)
uv run cli.py --url "..." --respect

# Limit constituencies
uv run cli.py --url "..." 50
```

### Live Client

Monitors counting day results round-by-round. Connects to the API server.

```bash
# Continuous monitoring (every 5 minutes)
uv run eci-ResultsDayLiveClient.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S11.htm" --live

# Single pass (all rounds)
uv run eci-ResultsDayLiveClient.py --url "..."

# Start from specific round
uv run eci-ResultsDayLiveClient.py --url "..." --start-round 3

# Sequential mode (resource-constrained systems)
uv run eci-ResultsDayLiveClient.py --url "..." --sequential

# Multi-state: start server separately, then run clients with --no-server
uv run server.py --api
uv run eci-ResultsDayLiveClient.py --url "...S11.htm" --no-server
uv run eci-ResultsDayLiveClient.py --url "...S22.htm" --no-server
```

### API Server

FastAPI server for programmatic scraping.

```bash
uv run server.py --api
# or
uv run uvicorn server:app --reload
```

Endpoints:
- `GET /health` — Health check
- `POST /scrape` — Scrape constituency results from party-wise URL
- `POST /scrape/ac-rounds` — Scrape all rounds for a single AC
- `POST /scrape/all-rounds` — Scrape all rounds for all ACs

### Dashboard

```bash
uv run dashboard.py
# Open http://localhost:8501
```

### Database

Dual-backend via `DATABASE_URL`:

```bash
# SQLite (default)
DATABASE_URL="data/election_results.db" uv run dashboard.py

# PostgreSQL
DATABASE_URL="postgresql://localhost:5432/election_results" uv run dashboard.py
```

## Project Structure

```
india-votes-data/
├── cli.py                       # CLI scraper (final results → DB)
├── server.py                    # FastAPI API server
├── eci-ResultsDayLiveClient.py  # Live client (round-by-round)
├── eci-live-scraper.py          # Alternative scraper (requests+BS4)
├── dashboard.py                 # Streamlit dashboard
├── db_utils.py                  # Database layer (SQLite + PostgreSQL)
├── core/
│   ├── scraper.py               # Selenium-based ECI extraction
│   ├── browser.py               # Chrome WebDriver setup
│   ├── output.py                # CSV/JSON writing (shared)
│   └── models.py                # Pydantic data models
├── data/
│   ├── states.csv               # 36 states/UTs reference
│   ├── parties.csv              # 30 major parties metadata
│   └── election_results.db      # SQLite database (gitignored)
├── export_parquet.py            # DuckDB → Parquet export
├── migrate_to_pg.py             # SQLite → PostgreSQL migration
└── pyproject.toml               # uv project config
```

## Database

See [SCHEMA.md](SCHEMA.md) for full schema documentation.

Quick reference — 4 tables:

| Table | Rows | Purpose |
|-------|------|---------|
| `states` | 36 | Reference data (ECI codes as PK) |
| `parties` | 196 | Party metadata + aliases |
| `rounds_ac` | 209K | Vote counts per candidate per round |
| `constituency_status` | 4K | AC lifecycle tracking |

## Scraping Strategy

1. **Primary**: `curl` subprocess + BeautifulSoup — bypasses ECI's Akamai TLS fingerprint blocking
2. **Fallback**: Selenium headless Chrome — for pages requiring JavaScript rendering
3. **Rate limiting**: 0.2-0.8s jitter between requests per thread

## Output Files

CLI generates CSV/JSON in `data/csv/` and `data/json/`:

```
YYYY<Type>-<state>.csv   (e.g., 2026Assembly-WB.csv)
YYYY<Type>-<state>.json  (e.g., 2026Assembly-WB.json)
```

Also available at [Kaggle](https://www.kaggle.com/datasets/maheshshantaram/indian-elections-fresh-data/).

## License

MIT — see [LICENSE](LICENSE).
