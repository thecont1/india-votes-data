"""
Database layer for ECI Election Tracker.

Supports SQLite (local) and PostgreSQL backends.
Also provides Cloudflare D1 write support via db.d1.

Usage:
    from db import init_db, insert_round_snapshot, get_state_name
    from db.sqlite import _connect  # internal access
    from db.d1 import insert_batch   # D1-specific
"""

# Re-export everything from sqlite so existing imports work:
#   from db_utils import X  →  from db import X
from db.sqlite import *  # noqa: F401,F403
from db.sqlite import (
    DATABASE_URL,
    IS_PG,
    _connect,
    _cursor,
    _dict_factory,
    _load_party_cache,
    _normalize_party,
    _placeholder,
    get_all_constituency_statuses,
    get_constituency_rounds,
    get_current_election,
    get_election_by_id,
    get_elections,
    get_error_constituencies,
    get_leading_seats,
    get_party_seat_tally,
    get_party_seat_tally_won_leading,
    get_party_totals_over_time,
    get_state_name,
    get_state_status_summary,
    get_status_summary,
    get_work_queue,
    init_db,
    insert_round_snapshot,
    update_won_status,
    upsert_constituency_status,
    upsert_election,
)
