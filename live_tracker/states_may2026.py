"""
Election config for General Assembly Elections — May 2026.

This is the single source of truth for the election being tracked.
To reuse for a future election, create a new states_{month}{year}.py file
and update the import in eci-live-scraper.py.
"""

ELECTION_ID = "May2026"
BASE_URL_TEMPLATE = (
    "https://results.eci.gov.in/ResultAcGen{election_id}/Roundwise{state_code}{ac_no}.htm"
)

STATES = [
    {
        "name": "Assam",
        "code": "S03",
        "ac_count": 126,
        "short": "AS",
    },
    {
        "name": "Kerala",
        "code": "S11",
        "ac_count": 140,
        "short": "KL",
    },
    {
        "name": "Puducherry",
        "code": "U07",
        "ac_count": 30,
        "short": "PY",
    },
    {
        "name": "Tamil Nadu",
        "code": "S22",
        "ac_count": 234,
        "short": "TN",
    },
    {
        "name": "West Bengal",
        "code": "S25",
        "ac_count": 294,
        "short": "WB",
    },
]

TOTAL_ACS = sum(s["ac_count"] for s in STATES)  # 824

# Party name normalisation: map ECI variations to canonical names
PARTY_NORMALISE = {
    "BJP": "Bharatiya Janata Party",
    "Bharatiya Janata Party": "Bharatiya Janata Party",
    "INC": "Indian National Congress",
    "Indian National Congress": "Indian National Congress",
    "Indian National Congress-Indira": "Indian National Congress",
    "TMC": "All India Trinamool Congress",
    "All India Trinamool Congress": "All India Trinamool Congress",
    "AITC": "All India Trinamool Congress",
    "DMK": "Dravida Munnetra Kazhagam",
    "Dravida Munnetra Kazhagam": "Dravida Munnetra Kazhagam",
    "AIADMK": "All India Anna Dravida Munnetra Kazhagam",
    "All India Anna Dravida Munnetra Kazhagam": "All India Anna Dravida Munnetra Kazhagam",
    "CPM": "Communist Party of India (Marxist)",
    "CPI(M)": "Communist Party of India (Marxist)",
    "Communist Party of India (Marxist)": "Communist Party of India (Marxist)",
    "CPI": "Communist Party of India",
    "Communist Party of India": "Communist Party of India",
    "IUML": "Indian Union Muslim League",
    "Indian Union Muslim League": "Indian Union Muslim League",
    "KC(M)": "Kerala Congress (M)",
    "Kerala Congress (M)": "Kerala Congress (M)",
    "AAP": "Aam Aadmi Party",
    "TVK": "Tamilaga Vettri Kazhagam",
    "Tamilaga Vettri Kazhagam": "Tamilaga Vettri Kazhagam",
    "Aam Aadmi Party": "Aam Aadmi Party",
    "NDPP": "Nationalist Democratic Progressive Party",
    "NPF": "Naga People's Front",
    "AGP": "Asom Gana Parishad",
    "Asom Gana Parishad": "Asom Gana Parishad",
    "AIUDF": "All India United Democratic Front",
    "All India United Democratic Front": "All India United Democratic Front",
    "BPF": "Bodoland People's Front",
    "Bodoland People's Front": "Bodoland People's Front",
    "UDF": "United Democratic Front",
    "LDF": "Left Democratic Front",
    "NOTA": "NOTA",
    "None of the Above": "NOTA",
    "Independent": "Independent",
    "IND": "Independent",
}

# Party colours for dashboard visualisation
PARTY_COLORS = {
    "Bharatiya Janata Party": "#FF6600",
    "Indian National Congress": "#00ADEF",
    "All India Trinamool Congress": "#20C997",
    "Dravida Munnetra Kazhagam": "#E63946",
    "All India Anna Dravida Munnetra Kazhagam": "#F4A261",
    "Communist Party of India (Marxist)": "#DC2626",
    "Communist Party of India": "#B91C1C",
    "Indian Union Muslim League": "#2D6A4F",
    "Kerala Congress (M)": "#F4D03F",
    "Tamilaga Vettri Kazhagam": "#FFD700",
    "Aam Aadmi Party": "#0A2463",
    "Asom Gana Parishad": "#8B5CF6",
    "All India United Democratic Front": "#059669",
    "Bodoland People's Front": "#D97706",
    "Independent": "#6B7280",
    "NOTA": "#374151",
    "Others": "#ADB5BD",
}


# Party short names for chart labels
PARTY_SHORT = {
    "Bharatiya Janata Party": "BJP",
    "Indian National Congress": "INC",
    "All India Trinamool Congress": "AITC",
    "Dravida Munnetra Kazhagam": "DMK",
    "All India Anna Dravida Munnetra Kazhagam": "AIADMK",
    "Tamilaga Vettri Kazhagam": "TVK",
    "Communist Party of India (Marxist)": "CPM",
    "Communist Party of India": "CPI",
    "Indian Union Muslim League": "IUML",
    "Bodoland People's Front": "BPF",
    "Bodoland Peoples Front": "BPF",
    "Asom Gana Parishad": "AGP",
    "All India United Democratic Front": "AIUDF",
    "All India N.R. Congress": "AINRC",
    "Kerala Congress": "KC",
    "Kerala Congress (M)": "KC(M)",
    "Revolutionary Socialist Party": "RSP",
    "Viduthalai Chiruthaigal Katchi": "VCK",
    "Pattali Makkal Katchi": "PMK",
    "Aam Aadmi Party": "AAP",
    "Aam Janata Unnayan party": "AJUP",
    "All India Secular Front": "AISF",
    "NOTA": "NOTA",
    "Independent": "IND",
    "Others": "Others",
}

# Majority thresholds per state (n/2 + 1)
MAJORITIES = {
    "S03": 64,   # Assam: 126/2 + 1
    "S11": 71,   # Kerala: 140/2 + 1
    "U07": 16,   # Puducherry: 30/2 + 1
    "S22": 118,  # Tamil Nadu: 234/2 + 1
    "S25": 148,  # West Bengal: 294/2 + 1
}

# Status colours for dashboard badges
STATUS_COLORS = {
    "DONE": "#16A34A",    # green
    "LIVE": "#F59E0B",    # amber
    "PENDING": "#6B7280", # grey
    "ERROR": "#DC2626",   # red
}


def normalise_party(party_name: str) -> str:
    """Normalise party name from ECI to canonical form."""
    if not party_name:
        return "Others"
    cleaned = party_name.strip()
    return PARTY_NORMALISE.get(cleaned, cleaned)


def get_url(state_code: str, ac_no: int) -> str:
    """Build the ECI Roundwise URL for a constituency."""
    return BASE_URL_TEMPLATE.format(
        election_id=ELECTION_ID,
        state_code=state_code,
        ac_no=ac_no,
    )


def get_all_urls() -> list[dict]:
    """Returns list of {state_code, state_name, ac_no, url} for all constituencies."""
    urls = []
    for state in STATES:
        for ac_no in range(1, state["ac_count"] + 1):
            urls.append(
                {
                    "state_code": state["code"],
                    "state_name": state["name"],
                    "ac_no": ac_no,
                    "url": get_url(state["code"], ac_no),
                }
            )
    return urls


def short(party_name: str) -> str:
    """Get short abbreviation for a party name."""
    return PARTY_SHORT.get(party_name, party_name[:20] if len(party_name) > 20 else party_name)


def state_code_for(state_name: str) -> str:
    """Get state code from state name."""
    for s in STATES:
        if s["name"] == state_name:
            return s["code"]
    return ""
