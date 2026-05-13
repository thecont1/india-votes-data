"""
Election configuration — which states to track on ECI.

This is the single source of truth for the election being tracked.
To reuse for a future election, update TRACKED_STATES and ELECTION_ID.
"""

ELECTION_ID = "May2026"

BASE_URL_TEMPLATE = (
    "https://results.eci.gov.in/ResultAcGen{election_id}/Roundwise{state_code}{ac_no}.htm"
)

# States being tracked (subset of all states in the DB)
TRACKED_STATES = [
    {"code": "S03", "name": "Assam"},
    {"code": "S11", "name": "Kerala"},
    {"code": "U07", "name": "Puducherry"},
    {"code": "S22", "name": "Tamil Nadu"},
    {"code": "S25", "name": "West Bengal"},
]


def get_url(state_code: str, ac_no: int) -> str:
    """Build the ECI Roundwise URL for a constituency."""
    return BASE_URL_TEMPLATE.format(
        election_id=ELECTION_ID,
        state_code=state_code,
        ac_no=ac_no,
    )
