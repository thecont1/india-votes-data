# India Votes Data

A Python tool for scraping, storing, and visualizing Indian election results from the [Election Commission of India](https://www.eci.gov.in/) website.

## Overview

Scrapes constituency-wise election results — candidate names, party affiliations, vote counts — and stores them in a normalized SQLite or PostgreSQL database. Includes a FastAPI + Chart.js dashboard for live visualization on counting day.

## Features

- **Multi-threaded scraping** — 5 concurrent workers (CLI), 3 (live client)
- **Dual database backend** — SQLite (local) or PostgreSQL via `DATABASE_URL`
- **Normalized schema** — states, parties, rounds_ac, constituency_status ([schema docs](docs/SCHEMA.md))
- **Live dashboard** — FastAPI + Chart.js with seat tally, striped bars (leading) vs solid (declared), auto-refresh
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
DATABASE_URL=data/election_results.db uvicorn server:app --reload
# Open http://localhost:8000
```

### Database

Dual-backend via `DATABASE_URL`:

```bash
# SQLite (default)
DATABASE_URL="data/election_results.db" uvicorn server:app --reload

# PostgreSQL
DATABASE_URL="postgresql://localhost:5432/election_results" uvicorn server:app --reload
```

## Project Structure

```
india-votes-data/
├── cli.py                       # CLI scraper (final results → DB)
├── server.py                    # FastAPI API server
├── eci-ResultsDayLiveClient.py  # Live client (round-by-round)
├── eci-live-scraper.py          # Alternative scraper (requests+BS4)
├── config.py                    # Election config (tracked states, URL template)
├── election.conf                # Party-wise URLs for current cycle
├── static/index.html            # Live dashboard (Chart.js)
├── db_utils.py                  # Database layer (SQLite + PostgreSQL)
├── core/
│   ├── scraper.py               # Selenium-based ECI extraction
│   ├── browser.py               # Chrome WebDriver setup
│   ├── output.py                # CSV/JSON writing (shared)
│   └── models.py                # Pydantic data models
├── tools/                       # Utilities and verification pipeline
│   ├── ocr_engine.py            # Vision LLM + Tesseract OCR engine
│   ├── normalize_parties.py     # Normalize party names in DB
│   ├── fetch_symbols.py         # Fetch party symbols from ECI
│   └── fetch_party_data.py      # Fetch party metadata
├── eci-verify-form20.py         # Form 20 batch verification CLI
├── data/
│   ├── states.csv               # 36 states/UTs reference
│   ├── parties.csv              # 30 major parties metadata
│   ├── csv/                     # CLI CSV output
│   ├── json/                    # CLI JSON output
│   └── election_results.db      # SQLite database (gitignored)
├── docs/
│   └── SCHEMA.md                # Database schema docs
├── notebooks/
│   └── analyse-this.ipynb       # Analysis notebook
├── archive/                     # Historical files
├── pyproject.toml               # uv project config
└── README.md                    # This file
```

## Database

See [docs/SCHEMA.md](docs/SCHEMA.md) for full schema documentation.

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

## Form 20 Verification

### What is Form 20?

Form 20 is the official election result document published by the Chief Electoral Officer of each state under Section 64 of the Representation of the People Act, 1951. It contains the complete, booth-wise breakdown of votes polled for every candidate in a constituency — the most granular official record of an election. This is the source document that returning officers sign before declaring results.

Our scraper captures aggregate vote totals from ECI's party-wise summary pages. Form 20 lets us verify those numbers against the original signed document.

### How it works

The verification pipeline (`eci-verify-form20.py` + `tools/ocr_engine.py`) downloads each constituency's Form 20 PDF, converts pages to images, and sends them to a Vision LLM (currently MiMo 2.5). The model reads candidate names and vote counts directly from the scanned document, then cross-checks them against our database.

Tesseract OCR runs as a fallback when no Vision API is configured. When Vision is available, Tesseract is skipped entirely — it takes 23 seconds per AC and the Vision LLM is more accurate on these tabular documents.

Page selection: the summary page (typically the last page) plus 20% random booth pages are sent to the Vision model. This balances thoroughness against API cost.

### Verification statuses

| Status | Meaning | Action |
|--------|---------|--------|
| **Verified** | All candidates confirmed, vote counts match | None — data is trustworthy |
| **Mismatch** | Vision LLM read different vote counts than our DB | Investigate — could be our data or LLM error |
| **Error** | Pipeline couldn't read the PDF (corrupt download, API failure) | Re-run — usually recovers on retry |
| **Unavailable** | No Form 20 URL set for this AC | Source the PDF from the CEO website |

### Running verification

```bash
# Verify all ACs in a state (skips already-verified, retries errors)
uv run eci-verify-form20.py S03

# Re-run everything from scratch
uv run eci-verify-form20.py S03 --force

# Single AC
uv run eci-verify-form20.py S25 110

# Tesseract only (no Vision API)
uv run eci-verify-form20.py S03 --skip-vision
```

The terminal shows a Braille grid — one block per AC, 20 per line. Pre-existing verified ACs appear as filled blocks from the start. Each new result appends to the grid in real time.

### Human in the loop

Automation catches gross errors (wrong totals, missing candidates) but cannot judge everything. Mismatched ACs need a human to decide: did the LLM misread a smudged scan, or does our scraped data have the wrong number? The dashboard sorts ACs by verification priority (Mismatch → Error → Unavailable → Verified) so the most suspicious constituencies surface first.

The goal is not zero mismatches — it's knowing exactly which constituencies to trust and which to double-check. That's what makes this data publishable.

## Output Files

CLI generates CSV/JSON in `data/csv/` and `data/json/`:

```
YYYY<Type>-<state>.csv   (e.g., 2026Assembly-WB.csv)
YYYY<Type>-<state>.json  (e.g., 2026Assembly-WB.json)
```

Also available at [Kaggle](https://www.kaggle.com/datasets/maheshshantaram/indian-elections-fresh-data/).

## Analysis

`analyse-this.ipynb` — Jupyter notebook for analyzing election results from CSVs. Includes:
- Party-wise vote shares and seat counts
- Victory margins and deposit losses
- Assembly composition treemaps
- Multi-state comparison across all available CSVs

Reads CSVs from `data/csv/`.

## Archive

Historical files in `archive/`:
- `eci-karnataka.html` — Karnataka election results page
- `2024 Parliamentary Elections India.csv` — 2024 Lok Sabha results

## License

MIT — see [LICENSE](LICENSE).
