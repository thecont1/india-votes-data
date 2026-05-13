#!/usr/bin/env python3
"""
Periodically exports PostgreSQL rounds table to compressed Parquet.
Runs as a background process or cron job.

Usage:
    python export_parquet.py --once       # Export once and exit
    python export_parquet.py              # Export every 60s
    python export_parquet.py --interval 30  # Export every 30s
"""

import os
import time

import duckdb

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/election_results"
)
EXPORT_DIR = os.path.join(os.path.dirname(__file__), "data", "parquet")


def export_snapshot(output_dir: str = EXPORT_DIR) -> str:
    """Export current state to compressed Parquet. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "rounds_latest.parquet")

    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT * FROM postgres_scan('{DATABASE_URL}', 'public', 'rounds')
        ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION 'ZSTD')
    """)
    con.close()

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"Exported {output_path} ({size_mb:.1f} MB)")
    return output_path


def export_loop(interval: int = 60):
    """Export every N seconds."""
    while True:
        try:
            export_snapshot()
        except Exception as e:
            print(f"Export error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Export once and exit")
    parser.add_argument("--interval", type=int, default=60, help="Export interval in seconds")
    args = parser.parse_args()

    if args.once:
        export_snapshot()
    else:
        export_loop(args.interval)
