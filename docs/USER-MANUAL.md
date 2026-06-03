# User Manual

Operational workflows for scraping, managing, and loading Indian election results into D1.

## Prerequisites

```bash
cd india-votes-data
uv sync                           # install dependencies
cp .env.example .env              # set D1_INGEST_URL and D1_INGEST_TOKEN
```

All commands below assume `uv run` from the project root. Scripts under `scripts/` can also be run with `.venv/bin/python`.

---

## 1. Scrape a Single Constituency

For ad-hoc corrections or one-off data fixes when a constituency's data is missing or wrong in the existing files.

### URL format

```
https://results.eci.gov.in/ResultAcGenMay2026/RoundwiseS{STATE_CODE}{AC_NO}.htm?ac={AC_NO}
```

Example — West Bengal AC 144 (FALTA):
```
https://results.eci.gov.in/ResultAcGenMay2026/RoundwiseS25144.htm?ac=144
```

### What to capture

A roundwise page has one table per round (R1 through R{N}). Each table has columns:
Candidate | Party | Votes Brought From Previous Rounds | Current Round | **Total**

The **Total** column in the **last round** gives the final EVM vote count per candidate.

For postal votes, fetch the constituencywise page:
```
https://results.eci.gov.in/ResultAcGenMay2026/Constituencywise{STATE_CODE}{AC_NO}.htm
```
Columns: S.N. | Candidate | Party | EVM Votes | Postal Votes | Total Votes | % of Votes

### Reusable functions

All HTML parsing is done by existing functions — no new BeautifulSoup needed.

```python
# HTTP fetching (curl-based, avoids Akamai TLS blocking)
from scripts.eci_bye_scraper import fetch_page

# Parse ALL rounds from a roundwise page
from scripts.eci_bye_scraper import extract_all_rounds

# Parse final results (EVM + Postal) from constituencywise page
from scripts.eci_bye_scraper import extract_postal_votes

# Normalize full party name to abbreviation
from scripts.eci_bye_scraper import normalize_party
```

To import `eci-bye-scraper.py` (hyphenated filename):
```python
import importlib.util
spec = importlib.util.spec_from_file_location('eci_bye', 'scripts/eci-bye-scraper.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

### Workflow: scrape, update files, load to D1

```python
import importlib.util, json, csv, re, sys
from bs4 import BeautifulSoup

# Import existing functions
spec = importlib.util.spec_from_file_location('eci_bye', 'scripts/eci-bye-scraper.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
fetch_page = mod.fetch_page
extract_all_rounds = mod.extract_all_rounds

AC_NO = 144
STATE_CODE = "S25"
BASE_URL = "https://results.eci.gov.in/ResultAcGenMay2026"
CWI_URL = f"{BASE_URL}/Constituencywise{STATE_CODE}{AC_NO}.htm"
RW_URL = f"{BASE_URL}/Roundwise{STATE_CODE}{AC_NO}.htm"

# 1. Fetch constituencywise page — parse final results with full party names
cw_html = fetch_page(CWI_URL)
soup = BeautifulSoup(cw_html, "html.parser")
tbody = soup.find("tbody")
candidates = []
for tr in tbody.find_all("tr"):
    tds = tr.find_all("td")
    if len(tds) >= 6:
        sn = tds[0].get_text(strip=True)
        cand = tds[1].get_text(strip=True)
        party = tds[2].get_text(strip=True)
        evm = tds[3].get_text(strip=True).replace(",", "")
        postal = tds[4].get_text(strip=True).replace(",", "")
        if cand and sn.isdigit():
            candidates.append({
                "serial_no": sn, "candidate": cand, "party": party,
                "evm_votes": evm, "postal_votes": postal,
            })

# 2. Fetch roundwise page — capture ALL rounds
rw_html = fetch_page(RW_URL)
round_data = extract_all_rounds(rw_html, AC_NO)
# round_data["rounds"] is a list of {round_no, candidates: [{candidate, party_abv, votes}]}

# 3. Update existing JSON (replace AC entry, preserving others)
JSON_PATH = "data/json/2026Assembly-WB.json"
with open(JSON_PATH) as f:
    wb = json.load(f)
new_entry = {
    "source_url": CWI_URL,
    "voting_data": {
        "constituency_no": str(AC_NO),
        "constituency": "FALTA",
        "voting_tally": candidates,
    }
}
for i, ac in enumerate(wb["constituencywise_results"]):
    if ac["voting_data"]["constituency_no"] == str(AC_NO):
        wb["constituencywise_results"][i] = new_entry
        break
with open(JSON_PATH, "w") as f:
    json.dump(wb, f, indent=2)

# 4. Update existing CSV (remove old rows, insert new, re-sort)
CSV_PATH = "data/csv/2026Assembly-WB.csv"
with open(CSV_PATH, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = [r for r in reader]
rows = [r for r in rows if r.get("constituency_no") != str(AC_NO)]
for c in candidates:
    rows.append({
        "election_year": "2026", "election_type": "Assembly",
        "election_state": "WB", "constituency": "FALTA",
        "constituency_no": str(AC_NO), **c,
    })
rows.sort(key=lambda r: (int(r["constituency_no"]), int(r["serial_no"])))
with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
```

### Loading to D1

**Individual rounds** (1 through N) — use `insert_round_snapshot` from `db/d1.py`:

```python
from db.d1 import insert_round_snapshot

for rd in round_data["rounds"]:
    candidates_d1 = [
        {"candidate": c["candidate"], "party_abv": c["party_abv"], "votes": c["votes"]}
        for c in rd["candidates"]
    ]
    insert_round_snapshot(
        state_code="S25", ac_no=144, ac_name="FALTA",
        round_no=rd["round_no"], candidates=candidates_d1,
        election_id="AC-2026-05",
    )
```

**Summary rounds** (998 = EVM totals, 999 = EVM+Postal) — write a temp JSON and run the loader:

```bash
# Write temp JSON with just this AC in the standard format, then:
uv run scripts/load-json-to-d1.py data/json/2026Assembly-WB-ac144.json \
    --election-id AC-2026-05 --skip-election --skip-verify
```

Delete the temp file after loading.

### Verification

```bash
wrangler d1 execute election-results --remote --json \
    --command "SELECT round_no, COUNT(*) as cands, SUM(votes) as total
               FROM rounds_ac
               WHERE state_code='S25' AND ac_no=144 AND election_id='AC-2026-05'
               GROUP BY round_no ORDER BY round_no;"
```

Should show rounds 1–22, 998, 999.

---

## 2. Bulk Scrape (Counting Day)

The normal workflow for scraping all ACs in a state during live counting.

### Live scraper

```bash
# Scrape all tracked states (reads election.conf + DB)
uv run scripts/eci-live-scraper.py
```

Runs continuously. Writes each round snapshot directly to D1 via the ingestion Worker. The scheduler calls this every 15 minutes on counting day.

### Day client (API-backed)

```bash
# One-shot: download all rounds for a state
uv run scripts/eci-day-client.py \
    --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S03.htm"

# Live mode: continuous monitoring
uv run scripts/eci-day-client.py --url "..." --live 15   # 15-second interval

# Single AC only
uv run scripts/eci-day-client.py --url "..." --only-ac 1
```

### CLI scraper (final results)

For scraping final results from party-wise summary pages:

```bash
uv run cli.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"
uv run cli.py --url "..." --csv --json        # also write files
uv run cli.py --url "..." --respect           # single-threaded, 1s pause every 10
```

---

## 3. Bye-Elections

Bye-elections use a separate URL pattern (`ResultAcBye`) and produce their own JSON.

### Scrape

```bash
uv run scripts/eci-bye-scraper.py \
    --url "https://results.eci.gov.in/ResultAcByeMay2026/index.htm" \
    --election-id "BYE-2026-05"
```

Output: `data/json/bye-elections/2026-05.json`

### Load to D1

```bash
uv run scripts/load-bye-to-d1.py data/json/bye-elections/2026-05.json
```

Bye-elections are contextual — they appear alongside general election data in the dashboard when relevant. The election ID format is `BYE-YYYY-MM`.

---

## 4. Archive Scraping (Historical Data)

For scraping old ECI results from archive.org (pre-2023 elections in legacy HTML format):

```bash
uv run scripts/eci-archive-scraper.py \
    "https://web.archive.org/web/2023xxxxxx/https://results.eci.gov.in/ResultAcGenMay2023/ConstituencywiseS101.htm" \
    --state-code KA

# Dry run (first 3 ACs only)
uv run scripts/eci-archive-scraper.py "..." --state-code KA --dry-run
```

Output: `data/csv/{year}Assembly-{ST}.csv` and `data/json/{year}Assembly-{ST}.json`

Writes incrementally — one constituency at a time so interruptions lose minimal data.

---

## 5. Load JSON to D1

For loading any election JSON file (bulk or single AC) into D1:

```bash
# Full load
uv run scripts/load-json-to-d1.py data/json/2026Assembly-WB.json --election-id AC-2026-05

# Preprocess only (validate, show party table, no writes)
uv run scripts/load-json-to-d1.py data/json/2026Assembly-WB.json --election-id AC-2026-05 --preprocess

# Resume (skip already-loaded constituencies)
uv run scripts/load-json-to-d1.py data/json/2026Assembly-WB.json --election-id AC-2026-05 --resume
```

Requires `D1_INGEST_URL` and `D1_INGEST_TOKEN` in `.env`.

The loader creates two round snapshots per AC:
- **Round 998**: EVM-only totals
- **Round 999**: EVM + Postal combined (this is what the dashboard displays)

---

## 6. Data Verification (Form 20)

Cross-checks scraped data against official Form 20 PDFs using Vision LLM:

```bash
# Verify all ACs in a state
uv run scripts/eci-verify-form20.py S03

# Single AC
uv run scripts/eci-verify-form20.py S25 110

# Re-run everything
uv run scripts/eci-verify-form20.py S03 --force

# Download PDFs only (Phase 1), then process separately (Phase 2)
uv run scripts/eci-verify-form20.py S03 --download-only
uv run scripts/eci-verify-form20.py S03 -j 2
```

Two-phase recommended for large states (avoids server rate-limiting).

---

## 7. Maintenance

### Normalize party abbreviations

```bash
# Check for unknown parties, write data/parties-pending.csv
uv run scripts/normalize-party-abv.py

# Dry run (no writes)
uv run scripts/normalize-party-abv.py --dry-run
```

Workflow: script finds unknown `party_abv` in `rounds_ac`, tries D1 `parties` table lookup, writes unresolved ones to `data/parties-pending.csv`. User fills the `abv` column, re-runs the script to upsert into D1 and rebuild `candidates_search`.

### Check data hygiene

Compare candidate counts between local CSVs and D1:

```bash
uv run scripts/check-hygiene.py                        # all CSVs
uv run scripts/check-hygiene.py data/csv/2023Assembly-KA.csv  # one file
uv run scripts/check-hygiene.py --fix                  # drop + reload mismatches
```

### Clean up search duplicates

One-time fix for `candidates_search` table:

```bash
uv run scripts/cleanup-search-duplicates.py
uv run scripts/cleanup-search-duplicates.py --dry-run
```

### Update election names

One-time migration from month-based to state-based naming:

```bash
uv run scripts/update-election-names.py
uv run scripts/update-election-names.py --dry-run
```

### Export D1 to SQLite

```bash
uv run scripts/export-d1.py
```

Outputs SQL INSERT statements to `data/export/`.

---

## ECI URL Patterns

| Type | Pattern | Example |
|------|---------|---------|
| Roundwise | `ResultAcGenMay2026/RoundwiseS{ST}{AC}.htm` | `RoundwiseS25144.htm` |
| Constituencywise | `ResultAcGenMay2026/ConstituencywiseS{ST}{AC}.htm` | `ConstituencywiseS25144.htm` |
| Candidateswise | `ResultAcGenMay2026/candidateswise-S{ST}{AC}.htm` | `candidateswise-S25144.htm` |
| Party-wise | `ResultAcGenMay2026/partywiseresult-{ST}.htm` | `partywiseresult-S25.htm` |
| Bye-election | `ResultAcByeMay2026/index.htm` | — |

State codes: S01=AP, S03=AS, S07=HR, S10=KA, S11=KL, S13=MH, S22=TN, S25=WB, U05=DL, U07=PY. Full list in `data/states.csv`.

---

## Round Numbers

| Round | Meaning |
|-------|---------|
| 1–N | Live counting rounds (EVM cumulative totals) |
| 998 | Final EVM-only totals (derived from last round) |
| 999 | EVM + Postal combined (final declared results) |

The dashboard reads round 999. Form 20 verification checks against round 999.

---

## D1 Ingestion

All writes go through the ingestion Worker at `D1_INGEST_URL`. Two endpoints:

- `POST /ingest/round` — single round snapshot (used by live scraper, bye-election loader, and single-AC fixes)
- `POST /ingest/batch` — multiple rounds in one request (used by `load-json-to-d1.py`; individual `/ingest/round` calls are more reliable)

The Worker performs DELETE-before-INSERT per (state_code, ac_no, round_no), making all writes idempotent.
