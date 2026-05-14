"""
Election configuration — reads from election.conf.

election.conf is a plain text file with one party-wise results URL per line.
Everything else (election ID, state codes, roundwise URLs) is derived.
"""

import os

from db_utils import get_state_name

CONF_FILE = os.path.join(os.path.dirname(__file__), "election.conf")


def _load_urls() -> list[str]:
    """Load party-wise URLs from election.conf."""
    with open(CONF_FILE) as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def _parse_url(url: str) -> tuple[str, str]:
    """Extract (election_id, state_code) from a party-wise results URL."""
    parts = url.rstrip("/").split("/")
    election_id = parts[-2]
    state_code = parts[-1].replace("partywiseresult-", "").replace(".htm", "")
    return election_id, state_code


def get_election_id() -> str:
    """Get the election identifier from the first URL."""
    urls = _load_urls()
    return _parse_url(urls[0])[0] if urls else ""


def get_tracked_states() -> list[dict]:
    """Derive tracked states from election.conf + DB."""
    seen = {}
    for url in _load_urls():
        _, state_code = _parse_url(url)
        if state_code not in seen:
            seen[state_code] = {
                "code": state_code,
                "name": get_state_name(state_code),
            }
    return list(seen.values())
