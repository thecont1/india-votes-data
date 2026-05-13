"""
Election configuration — party-wise results URLs.

On counting day, provide the party-wise results URL for each state.
Everything else (election ID, state codes, roundwise URLs) is derived.
"""

from db_utils import get_state_name

# Party-wise results URLs for this election cycle
PARTYWISE_URLS = [
    "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S03.htm",
    "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S11.htm",
    "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-U07.htm",
    "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm",
    "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S25.htm",
]


def _parse_url(url: str) -> tuple[str, str]:
    """Extract (election_id, state_code) from a party-wise results URL."""
    # https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S03.htm
    parts = url.rstrip("/").split("/")
    election_id = parts[-2]  # ResultAcGenMay2026
    state_code = parts[-1].replace("partywiseresult-", "").replace(".htm", "")
    return election_id, state_code


def get_election_id() -> str:
    """Get the election identifier from the first URL."""
    return _parse_url(PARTYWISE_URLS[0])[0]


def get_tracked_states() -> list[dict]:
    """Derive tracked states from PARTYWISE_URLS + DB.

    Returns list of dicts: [{"code": "S03", "name": "Assam"}, ...]
    """
    seen = {}
    for url in PARTYWISE_URLS:
        _, state_code = _parse_url(url)
        if state_code not in seen:
            seen[state_code] = {
                "code": state_code,
                "name": get_state_name(state_code),
            }
    return list(seen.values())
