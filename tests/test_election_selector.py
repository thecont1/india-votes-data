"""Tests for election selector functionality."""
import json
import pytest
import sqlite3
import os
import tempfile


@pytest.fixture
def test_db():
    """Create a test database with elections table using temp file."""
    # Create temp file database
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Create elections table
    cur.execute("""
        CREATE TABLE elections (
            election_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            states TEXT NOT NULL,
            sort_date TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    
    # Patch db_utils to use our test database
    import db_utils
    original_connect = db_utils._connect
    original_is_pg = db_utils.IS_PG
    
    # Create a connection factory that returns new connections to same file
    db_utils._connect = lambda: sqlite3.connect(db_path)
    db_utils.IS_PG = False
    
    yield db_path
    
    # Restore
    db_utils._connect = original_connect
    db_utils.IS_PG = original_is_pg
    
    # Cleanup
    os.unlink(db_path)


class TestElectionSelector:
    """Tests for election selector database functions."""

    def test_upsert_and_get_elections(self, test_db):
        """Test upsert_election and get_elections work together."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10")
        
        elections = db_utils.get_elections()
        assert len(elections) == 1
        
        election = elections[0]
        assert election["election_id"] == "AC-2024-10"
        assert election["name"] == "AC 2024 Oct"
        assert election["states"] == ["S07", "U08"]
        assert election["sort_date"] == "2024-10"

    def test_multiple_elections_sorted(self, test_db):
        """Test multiple elections are sorted by sort_date DESC."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10")
        db_utils.upsert_election("AC-2025-05", "AC 2025 May", ["S03", "S11"], "2025-05")
        db_utils.upsert_election("AC-2024-11", "AC 2024 Nov", ["S13"], "2024-11")
        
        elections = db_utils.get_elections()
        assert len(elections) == 3
        assert elections[0]["election_id"] == "AC-2025-05"
        assert elections[1]["election_id"] == "AC-2024-11"
        assert elections[2]["election_id"] == "AC-2024-10"

    def test_get_election_by_id(self, test_db):
        """Test get_election_by_id retrieves correct election."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10")
        
        election = db_utils.get_election_by_id("AC-2024-10")
        assert election is not None
        assert election["name"] == "AC 2024 Oct"
        assert election["states"] == ["S07", "U08"]
        
        # Non-existent election
        assert db_utils.get_election_by_id("NON-EXISTENT") is None

    def test_get_current_election(self, test_db):
        """Test get_current_election returns most recent election."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07"], "2024-10")
        db_utils.upsert_election("AC-2025-02", "AC 2025 Feb", ["U05"], "2025-02")
        
        current = db_utils.get_current_election()
        assert current is not None
        assert current["election_id"] == "AC-2025-02"

    def test_upsert_updates_existing(self, test_db):
        """Test upsert_election updates existing election on conflict."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10")
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct - Updated", ["S07", "U08", "S09"], "2024-10")
        
        elections = db_utils.get_elections()
        assert len(elections) == 1  # Should still be 1, not 2
        
        election = elections[0]
        assert election["name"] == "AC 2024 Oct - Updated"
        assert election["states"] == ["S07", "U08", "S09"]

    def test_states_deserialized_as_list(self, test_db):
        """Test that states are returned as Python list, not JSON string."""
        import db_utils
        
        db_utils.upsert_election("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10")
        
        election = db_utils.get_election_by_id("AC-2024-10")
        assert election is not None
        assert isinstance(election["states"], list)
        assert election["states"][0] == "S07"


class TestElectionSampleData:
    """Test sample election data from requirements."""

    def test_requirement_data(self, test_db):
        """Test that sample election data from requirements works."""
        import db_utils
        
        # From requirements:
        # AC 2024 Oct - S07, U08
        # AC 2024 Nov - S13, S27
        # AC 2025 Feb - U05
        # AC 2025 Nov - S04
        # AC 2026 May - S03, S11, U07, S22, S25
        elections_data = [
            ("AC-2024-10", "AC 2024 Oct", ["S07", "U08"], "2024-10"),
            ("AC-2024-11", "AC 2024 Nov", ["S13", "S27"], "2024-11"),
            ("AC-2025-02", "AC 2025 Feb", ["U05"], "2025-02"),
            ("AC-2025-11", "AC 2025 Nov", ["S04"], "2025-11"),
            ("AC-2026-05", "AC 2026 May", ["S03", "S11", "U07", "S22", "S25"], "2026-05"),
        ]
        
        for election_id, name, states, sort_date in elections_data:
            db_utils.upsert_election(election_id, name, states, sort_date)
        
        elections = db_utils.get_elections()
        assert len(elections) == 5
        
        # First should be most recent (2026-05)
        assert elections[0]["election_id"] == "AC-2026-05"
        assert elections[0]["states"] == ["S03", "S11", "U07", "S22", "S25"]
        
        # Last should be oldest (2024-10)
        assert elections[-1]["election_id"] == "AC-2024-10"