# Plan: Election Results Day Architecture — Final

## Goal

Design a crash-safe, multi-state, publicly accessible live results architecture
that handles power cuts, concurrent scrapers across states, and TV-style
public viewing at `apps.thecontrarian.in/elections`.

## Constraints (Confirmed)

| Constraint | Detail |
|------------|--------|
| Power cuts | Frequent during results day; DB must survive mid-write crashes |
| Multiple states | 5 recently (assembly), up to 30 during Lok Sabha |
| Public dashboard | Served via Cloudflare at `apps.thecontrarian.in/elections` |
| Postgres hosting | Home server for now; cloud-hosted if free/low-cost exists |
| DuckDB | Run in-browser via WebAssembly for client-side analytics |

## Final Architecture

```
  ┌──────────────────────────────────────────────────┐
  │              ECI Results Website                   │
  └────────┬──────────┬──────────┬───────────────────┘
           │          │          │
     ┌─────▼──┐ ┌─────▼──┐ ┌─────▼──┐
     │ Scraper │ │ Scraper │ │ Scraper │  one per state
     │  WB     │ │  TN     │ │  UP     │  (3 Chrome workers each)
     └─────┬──┘ └─────┬──┘ └─────┬──┘
           │          │          │
           └──────────┼──────────┘
                      │
                psycopg2 (network)
                      │
     ┌────────────────▼────────────────────┐
     │          PostgreSQL                  │  home server
     │    All states, all rounds            │  (Cloudflare Tunnel for access)
     │    Crash-safe WAL + fsync            │
     └────────────────┬────────────────────┘
                      │
              ┌───────┴────────┐
              │  Export job     │  every 60s: COPY TO Parquet
              │  (pg_dump or    │  compressed, ~2-5 MB per state
              │   python script)│
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │  Cloudflare R2  │  static object storage
              │  (or Workers)   │  serves Parquet files
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │  Static HTML    │  Cloudflare Pages
              │  + DuckDB-WASM  │  loads Parquet in browser
              │  all queries    │  zero server-side analytics
              │  run client-side│
              └────────────────┘
                      │
               Every viewer's
                  browser
```

### Why this architecture

1. **PostgreSQL on home server**: Zero hosting cost, full control, handles
   30 states × ~2M rows easily. Cloudflare Tunnel exposes it to the
   scraper clients without port forwarding.

2. **DuckDB-WASM in the browser**: The dashboard is a static site. No
   backend needed for serving chart data. Every analytical query (seat
   counts, time series, party trends) runs client-side via DuckDB's
   WebAssembly engine. Scales to thousands of viewers at zero marginal
   cost.

3. **Parquet as the bridge**: PostgreSQL exports compressed Parquet files
   every 60 seconds. DuckDB-WASM loads Parquet natively — no JSON
   parsing, no API endpoints, no server-side query engine. A 5-state
   snapshot is ~2-5 MB (highly compressible election data).

4. **Cloudflare for everything**: Tunnel (scraper → PG), R2 (Parquet
   storage), Pages (dashboard hosting). One ecosystem, free tier covers
   this use case.

## Data Volume Estimates

| Scenario | States | Rows | Parquet size |
|----------|--------|------|-------------|
| Assembly elections (typical) | 5 | ~325K | ~2-3 MB |
| Lok Sabha (maximum) | 30 | ~2M | ~10-15 MB |
| Single state (S25 WB) | 1 | ~65K | ~0.5 MB |

DuckDB-WASM handles all of these comfortably in-browser.

## PostgreSQL Hosting Options

| Option | Free tier | Limitations | Verdict |
|--------|-----------|-------------|---------|
| **Home server + CF Tunnel** | Unlimited | Needs your Mac running | **Best for now** |
| Neon | 0.5 GB storage, compute pauses | Auto-suspend after inactivity | Good fallback |
| Supabase | 500 MB, 500K rows | Tight for 5+ states | Too small |
| Railway | $5/mo after trial | Always-on cost | Overkill |
| Aiven | 1 GB | 1 GB only | Borderline |

**Recommendation**: Start with home server + Cloudflare Tunnel. If reliability
becomes an issue during results day, migrate to Neon (their Postgres handles
auto-scaling and you only pay for compute when the scraper is running).

## Step-by-Step Plan

### Phase 1: PostgreSQL on home server

1. **Install PostgreSQL** via Homebrew: `brew install postgresql@16`
2. **Create database**: `createdb election_results`
3. **Run schema** (from previous plan):
   ```sql
   CREATE TABLE rounds (
       state_code   TEXT NOT NULL,
       ac_no        INTEGER NOT NULL,
       round_no     INTEGER NOT NULL,
       candidate_number INTEGER NOT NULL,
       ac_name      TEXT,
       candidate    TEXT,
       party        TEXT,
       votes        INTEGER,
       scraped_at   TIMESTAMPTZ DEFAULT now(),
       PRIMARY KEY (state_code, ac_no, round_no, candidate_number)
   );
   ```
4. **Set up Cloudflare Tunnel**: expose port 5432 to the internet
   (or use an SSH tunnel as a simpler alternative)

### Phase 2: Migrate scraper writer to PostgreSQL

5. **Add `psycopg2-binary`** to `pyproject.toml`
6. **Refactor `eci-ResultsDayLiveClient.py`**:
   - `get_db_connection()` → `psycopg2.connect(DATABASE_URL)`
   - `init_database()` → `CREATE TABLE IF NOT EXISTS` (Postgres syntax)
   - `execute_with_retry()` → retry on Postgres error `40001` (serialization)
   - `commit_with_retry()` → retry on `40001`
   - Remove SQLite pragmas
   - `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (state_code, ac_no, round_no, candidate_number) DO UPDATE SET votes = EXCLUDED.votes`
7. **Pass `DATABASE_URL`** via env var or `--database-url` CLI arg
8. **Test**: scrape 1 AC → verify in `psql`

### Phase 3: Parquet export job

9. **Create `live_tracker/export_parquet.py`** (new):
   ```python
   # Periodically exports rounds table to compressed Parquet
   # Runs as a background thread or cron job
   import duckdb
   import time

   def export_snapshot(db_url, output_path):
       con = duckdb.connect()
       con.execute(f"""
           COPY (SELECT * FROM postgres_scan('{db_url}', 'rounds'))
           TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'ZSTD')
       """)
       con.close()

   def export_loop(db_url, output_dir, interval=60):
       while True:
           export_snapshot(db_url, f"{output_dir}/rounds_latest.parquet")
           time.sleep(interval)
   ```
10. **Upload to Cloudflare R2** (or serve from the API endpoint):
    - Option A: `rclone` or `wrangler` CLI uploads to R2
    - Option B: FastAPI endpoint serves the Parquet file directly
    - Option C: Cloudflare Worker reads from PG and serves Parquet

### Phase 4: DuckDB-WASM dashboard

11. **Create `dashboard/index.html`** (new):
    - Load DuckDB-WASM from CDN
    - Fetch Parquet file from R2/API on load
    - Run SQL queries client-side for chart data
    - Auto-refresh every 60 seconds

12. **Key frontend queries** (run in DuckDB-WASM):
    ```sql
    -- Seat tally (bar chart)
    SELECT party, COUNT(*) as seats
    FROM (
        SELECT ac_no, party, votes,
            ROW_NUMBER() OVER (PARTITION BY ac_no ORDER BY votes DESC) as rn
        FROM rounds WHERE round_no = 999
    ) ranked WHERE rn = 1
    GROUP BY party ORDER BY seats DESC;

    -- Time series (line chart for one AC)
    SELECT round_no, party, SUM(votes) as total
    FROM rounds WHERE ac_no = 1 AND round_no < 999
    GROUP BY round_no, party ORDER BY round_no;

    -- State selector
    SELECT DISTINCT state_code, ac_name FROM rounds WHERE round_no = 999;
    ```

13. **Chart library**: Chart.js or Apache ECharts (lightweight, no build step)

### Phase 5: Deploy to Cloudflare

14. **Cloudflare Pages**: deploy `dashboard/` as a static site
    - Custom domain: `apps.thecontrarian.in/elections`
    - Auto-deploy from Git (if using GitHub)
15. **Cloudflare R2**: store Parquet files
    - Free tier: 10 GB storage, 10M reads/month — more than enough
16. **Cloudflare Tunnel**: expose home Postgres to scraper clients
    - `cloudflared tunnel create election-db`
    - Route: `db.thecontrarian.in → localhost:5432`

## Files Likely to Change

| File | Change |
|------|--------|
| `live_tracker/eci-ResultsDayLiveClient.py` | **Major** — sqlite3 → psycopg2 |
| `live_tracker/export_parquet.py` | **New** — Parquet export + R2 upload |
| `dashboard/index.html` | **New** — DuckDB-WASM dashboard |
| `dashboard/style.css` | **New** — dashboard styling |
| `pyproject.toml` | Add `psycopg2-binary`, `duckdb`, `boto3` (for R2) |
| `server.py` | Optional — add Parquet serve endpoint as R2 alternative |

## Tests / Validation

1. **Scraper → Postgres**: scrape 1 AC, verify with `psql -c "SELECT * FROM rounds LIMIT 5"`
2. **Power-cut recovery**: kill scraper mid-write → restart → verify data integrity
3. **Parquet export**: export snapshot → open with DuckDB locally → verify query results
4. **DuckDB-WASM**: open dashboard in browser → verify charts render with Parquet data
5. **Concurrent scrapers**: run 2 state scrapers simultaneously → no deadlocks
6. **Load test**: open dashboard in 5 browser tabs → all update independently

## Risks and Tradeoffs

| Risk | Mitigation |
|------|------------|
| Home server goes down during results day | Cloudflare Tunnel auto-reconnects; scraper retries on connection failure |
| DuckDB-WASM Parquet fetch is slow on mobile | Compressed Parquet is ~2-5 MB; acceptable for 60s refresh cycle |
| Parquet export misses partial writes | Export reads committed transactions only; `scraped_at` column for staleness detection |
| Cloudflare R2 free tier limits | 10 GB storage, 10M reads — election data is ~15 MB max, well within limits |
| PostgreSQL auth for scrapers over tunnel | Use cert-based auth or simple password via env var |

## Open Questions

1. **Parquet export timing**: Should the export run inside the scraper process
   (as a background thread) or as a separate cron job? Separate is cleaner
   but adds a process to manage.

2. **State selector UX**: Should the dashboard show all states at once (tabbed)
   or one state at a time? For 30 states during Lok Sabha, tabbed is better.

3. **Historical data**: Should past election results (2021, 2024) be loaded
   into Postgres for comparison charts? DuckDB-WASM can query multiple
   Parquet files simultaneously.

4. **Fallback if DuckDB-WASM fails**: Should the dashboard have a fallback
   that queries a server-side API (FastAPI) for older browsers?
