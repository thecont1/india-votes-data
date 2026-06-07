#!/usr/bin/env python3
"""Export SQLite data to SQL INSERT statements for D1 import."""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "india-votes-data.db")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "export")

TABLES = ["states", "parties", "elections", "constituency_status", "rounds_ac"]


def sql_escape(val):
    """Escape a value for SQL. Returns 'NULL' or quoted string."""
    if val is None:
        return "NULL"
    if isinstance(val, int):
        return str(val)
    # Escape single quotes and wrap in quotes
    return "'" + str(val).replace("'", "''") + "'"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    for table in TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"{table}: 0 rows (skipped)")
            continue

        cols = rows[0].keys()
        col_list = ",".join(cols)
        out_path = os.path.join(OUT_DIR, f"{table}.sql")

        with open(out_path, "w") as f:
            for row in rows:
                vals = ",".join(sql_escape(row[c]) for c in cols)
                f.write(f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({vals});\n")

        size_kb = os.path.getsize(out_path) / 1024
        print(f"{table}: {len(rows)} rows ({size_kb:.0f} KB)")

    conn.close()
    print(f"\nExported to {OUT_DIR}/")


if __name__ == "__main__":
    main()
