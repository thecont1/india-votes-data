"""Shared data models for ECI results scraping."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ConstituencyResult:
    """Result data for a single constituency."""
    constituency_no: str = ""
    constituency: str = ""
    voting_tally: list = None
    
    def __post_init__(self):
        if self.voting_tally is None:
            self.voting_tally = []


@dataclass  
class RoundTallyEntry:
    """Vote tally entry for a specific round."""
    serial_no: str = ""
    candidate: str = ""
    party: str = ""
    votes_brought_forward: str = ""
    current_round: str = ""
    total: str = ""


@dataclass
class RoundResult:
    """Result for a single round within an AC."""
    round_number: int = 0
    tally: list = None
    
    def __post_init__(self):
        if self.tally is None:
            self.tally = []


@dataclass
class AcRoundsResult:
    """Complete round results for a single AC."""
    ac_no: int = 0
    constituency: str = ""
    rounds: list = None
    postal_votes: list = None
    
    def __post_init__(self):
        if self.rounds is None:
            self.rounds = []
        if self.postal_votes is None:
            self.postal_votes = []


@dataclass
class ScrapeResult:
    """Overall scrape result container."""
    election_year: str = ""
    election_type: str = ""
    election_state: str = ""
    constituencywise_results: list = None
    round_number: Optional[int] = None
    
    def __post_init__(self):
        if self.constituencywise_results is None:
            self.constituencywise_results = []