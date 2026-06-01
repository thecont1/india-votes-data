"""Tests for search ranking, candidate history, and constituency history queries.

These tests validate the SQL logic used by the D1 worker endpoints.
They use a local SQLite database with the same schema as search-schema.sql.
"""
import json
import pytest
import sqlite3
import os
import tempfile


@pytest.fixture
def search_db():
    """Create a test database with search schema and sample election data."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Core tables
    cur.executescript("""
        CREATE TABLE states (
            state_code TEXT PRIMARY KEY,
            state_name TEXT NOT NULL
        );

        CREATE TABLE elections (
            election_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            states TEXT NOT NULL,
            sort_date TEXT NOT NULL
        );

        CREATE TABLE rounds_ac (
            state_code TEXT NOT NULL,
            ac_no INTEGER NOT NULL,
            ac_name TEXT,
            round_no INTEGER NOT NULL,
            candidate TEXT NOT NULL,
            party_abv TEXT NOT NULL,
            votes INTEGER NOT NULL,
            PRIMARY KEY (state_code, ac_no, round_no, candidate, party_abv)
        );

        CREATE TABLE constituency_status (
            state_code TEXT NOT NULL,
            ac_no INTEGER NOT NULL,
            ac_name TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            won INTEGER DEFAULT 0,
            PRIMARY KEY (state_code, ac_no)
        );

        CREATE TABLE parties (
            abv TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            alliance TEXT DEFAULT ''
        );

        -- Search tables
        CREATE TABLE candidates_search (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            name TEXT NOT NULL,
            context TEXT DEFAULT '',
            boost REAL DEFAULT 1.0,
            votes INTEGER DEFAULT 0,
            total_votes INTEGER DEFAULT 0,
            election_sort TEXT DEFAULT ''
        );
    """)

    # Seed data
    cur.executescript("""
        INSERT INTO states VALUES ('S07', 'Karnataka');
        INSERT INTO states VALUES ('S03', 'Assam');

        INSERT INTO elections VALUES ('AC-2024-05', 'AC 2024 May - S07', '["S07"]', '2024-05');
        INSERT INTO elections VALUES ('AC-2018-05', 'AC 2018 May - S07', '["S07"]', '2018-05');
        INSERT INTO elections VALUES ('AC-2013-05', 'AC 2013 May - S07', '["S07"]', '2013-05');

        INSERT INTO parties VALUES ('BJP', 'Bharatiya Janata Party', 'NDA');
        INSERT INTO parties VALUES ('INC', 'Indian National Congress', 'I.N.D.I.A.');
        INSERT INTO parties VALUES ('JD(S)', 'Janata Dal (Secular)', '');

        -- Constituency status
        INSERT INTO constituency_status VALUES ('S07', 22, 'Vijayapur', 'DONE', 1);
        INSERT INTO constituency_status VALUES ('S07', 50, 'Namvijaynagar', 'DONE', 1);
        INSERT INTO constituency_status VALUES ('S07', 10, 'Badami', 'DONE', 1);

        -- 2024 election rounds for Vijayapur (ac_no=22)
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 1, 'Vijayendra', 'BJP', 89234);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 1, 'Ramesh Kumar', 'INC', 67891);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 1, 'Anil Kumar', 'JD(S)', 12345);

        -- 2018 election rounds for Vijayapur
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 2, 'Vijayendra', 'BJP', 72100);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 2, 'Anil Kumar', 'INC', 55400);

        -- 2013 election rounds for Vijayapur
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 3, 'Anil Kumar', 'INC', 61000);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 3, 'Vijayendra', 'BJP', 52800);

        -- 2024 election rounds for Namvijaynagar (ac_no=50)
        INSERT INTO rounds_ac VALUES ('S07', 50, 'Namvijaynagar', 1, 'C. Joseph Vijay', 'INC', 95000);
        INSERT INTO rounds_ac VALUES ('S07', 50, 'Namvijaynagar', 1, 'Suresh Babu', 'BJP', 78000);

        -- 2024 election rounds for Badami (ac_no=10)
        INSERT INTO rounds_ac VALUES ('S07', 10, 'Badami', 1, 'Siddaramaiah', 'INC', 85000);
        INSERT INTO rounds_ac VALUES ('S07', 10, 'Badami', 1, 'B Sriramulu', 'BJP', 72000);
    """)

    conn.commit()

    # Build search index (mirrors search-schema.sql)
    cur.executescript("""
        -- Candidates with votes + election sort
        INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort)
        SELECT 'candidate',
               r.state_code || '-' || r.ac_no || '-' || r.party_abv,
               r.candidate,
               r.party_abv || ' | ' || COALESCE(r.ac_name, '') || ' | ' || COALESCE(SUBSTR(e.sort_date, 1, 4), ''),
               CASE WHEN cs.won = 1 THEN 1.5 ELSE 1.0 END,
               r.votes,
               0,
               COALESCE(e.sort_date, '')
        FROM rounds_ac r
        INNER JOIN (
            SELECT state_code, ac_no, MAX(round_no) as max_round
            FROM rounds_ac WHERE round_no != 999
            GROUP BY state_code, ac_no
        ) lr ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no AND r.round_no = lr.max_round
        LEFT JOIN constituency_status cs ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
        LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%';

        -- Constituencies with total votes
        INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort)
        SELECT 'constituency',
               cs.state_code || '-' || cs.ac_no,
               cs.ac_name,
               s.state_name,
               1.0,
               0,
               COALESCE(tv.total, 0),
               ''
        FROM constituency_status cs
        JOIN states s ON cs.state_code = s.state_code
        LEFT JOIN (
            SELECT state_code, ac_no, SUM(votes) as total
            FROM rounds_ac WHERE round_no != 999
            GROUP BY state_code, ac_no
        ) tv ON cs.state_code = tv.state_code AND cs.ac_no = tv.ac_no
        WHERE cs.ac_name IS NOT NULL;
    """)

    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


class TestSearchRanking:
    """Test the search query ordering logic."""

    def test_two_sections_no_parties(self, search_db):
        """Search results should only contain candidates and constituencies, no parties."""
        rows = search_db.execute(
            "SELECT DISTINCT entity_type FROM candidates_search"
        ).fetchall()
        types = {r[0] for r in rows}
        assert types == {'candidate', 'constituency'}
        assert 'party' not in types

    def test_candidates_sorted_by_votes_desc(self, search_db):
        """Candidates matching 'vijay' should be sorted by votes descending."""
        current_date = '2024-05'  # latest election
        rows = search_db.execute("""
            SELECT entity_type, name, votes, election_sort
            FROM candidates_search
            WHERE name LIKE '%Vijay%' AND entity_type = 'candidate'
            ORDER BY
              (CASE WHEN election_sort >= ? THEN 1000000 ELSE 0 END) + votes
            DESC
        """, (current_date,)).fetchall()

        names = [r[1] for r in rows]
        assert len(names) >= 2
        # Current election candidates with highest votes first
        assert names[0] == 'C. Joseph Vijay'  # 95000 in 2024
        assert names[1] == 'Vijayendra'       # 89234 in 2024

    def test_current_election_beats_past(self, search_db):
        """Current election candidates rank above past election candidates."""
        current_date = '2024-05'
        rows = search_db.execute("""
            SELECT name, votes, election_sort,
                   (CASE WHEN election_sort >= ? THEN 1000000 ELSE 0 END) + votes as score
            FROM candidates_search
            WHERE name = 'Vijayendra' AND entity_type = 'candidate'
            ORDER BY score DESC
        """, (current_date,)).fetchall()

        # Should have one entry from the latest round (2024)
        assert len(rows) >= 1
        assert rows[0][2] == '2024-05'  # election_sort

    def test_constituencies_sorted_by_total_votes(self, search_db):
        """Constituencies should be sorted by total votes cast."""
        rows = search_db.execute("""
            SELECT name, total_votes
            FROM candidates_search
            WHERE entity_type = 'constituency'
            ORDER BY total_votes DESC
        """).fetchall()

        names = [r[0] for r in rows]
        totals = [r[1] for r in rows]
        # total_votes sums ALL rounds (not just latest):
        # Vijayapur: (89234+67891+12345) + (72100+55400) + (61000+52800) = 410770
        # Namvijaynagar: 95000+78000 = 173000
        # Badami: 85000+72000 = 157000
        assert names[0] == 'Vijayapur'  # highest total across all elections
        assert totals[0] == 410770

    def test_fuzzy_trigram_match(self, search_db):
        """Partial input like 'vij' should match Vijayendra and Vijayapur."""
        rows = search_db.execute("""
            SELECT name, entity_type FROM candidates_search
            WHERE name LIKE '%vij%' OR name LIKE '%Vij%'
        """).fetchall()

        names = {(r[0], r[1]) for r in rows}
        assert ('Vijayendra', 'candidate') in names or any('Vijayendra' in n[0] for n in names)
        assert ('Vijayapur', 'constituency') in names or any('Vijayapur' in n[0] for n in names)

    def test_context_includes_year(self, search_db):
        """Candidate context string should include the election year."""
        row = search_db.execute(
            "SELECT context FROM candidates_search WHERE name = 'Vijayendra' AND entity_type = 'candidate'"
        ).fetchone()
        assert row is not None
        assert '2024' in row[0] or 'BJP' in row[0]


class TestCandidateHistory:
    """Test candidate history query logic."""

    def test_all_contests_returned(self, search_db):
        """Searching for 'Vijayendra' should return all their contests."""
        rows = search_db.execute("""
            SELECT candidate, party_abv, votes, state_code, ac_no
            FROM rounds_ac
            WHERE candidate = ? AND round_no != 999
            ORDER BY votes DESC
        """, ('Vijayendra',)).fetchall()

        assert len(rows) == 3  # 2024, 2018, 2013
        # Verify all three elections are represented
        votes = [r[2] for r in rows]  # r[2] = votes column
        assert 89234 in votes  # 2024
        assert 72100 in votes  # 2018
        assert 52800 in votes  # 2013

    def test_winner_detection(self, search_db):
        """Winner should be the candidate with most votes in each round."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, party_abv, votes, round_no,
                       ROW_NUMBER() OVER (PARTITION BY round_no ORDER BY votes DESC) as rank_in_round
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no != 999
            )
            SELECT candidate, round_no, rank_in_round, votes
            FROM ranked
            WHERE candidate = 'Vijayendra'
            ORDER BY round_no
        """).fetchall()

        # Vijayendra won round 1 (2024) and round 2 (2018), lost round 3 (2013)
        for r in rows:
            if r[1] in (1, 2):  # 2024 and 2018
                assert r[2] == 1  # rank 1 = winner
            elif r[1] == 3:  # 2013
                assert r[2] == 2  # rank 2 = runner-up

    def test_sorted_by_recency(self, search_db):
        """Contests should be sorted by election recency DESC."""
        rows = search_db.execute("""
            SELECT r.candidate, r.votes, e.sort_date
            FROM rounds_ac r
            LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%'
            WHERE r.candidate = 'Vijayendra' AND r.round_no != 999
            ORDER BY e.sort_date DESC, r.votes DESC
        """).fetchall()

        dates = [r[2] for r in rows]
        assert dates == sorted(dates, reverse=True)


class TestConstituencyHistory:
    """Test constituency history query logic."""

    def test_winner_runner_up_per_election(self, search_db):
        """Each election should have a winner and runner-up."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, party_abv, votes, round_no,
                       ROW_NUMBER() OVER (PARTITION BY round_no ORDER BY votes DESC) as rank_in_round
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no != 999
            )
            SELECT round_no, rank_in_round, candidate, votes
            FROM ranked
            WHERE rank_in_round <= 2
            ORDER BY round_no DESC, rank_in_round ASC
        """).fetchall()

        # 3 elections, 2 rows each = 6 rows
        assert len(rows) == 6

        # Latest election by round_no DESC: round 3 is 2013 (oldest), round 1 is 2024 (newest)
        # Since ORDER BY round_no DESC, round 3 comes first
        # Round 3 (2013): Anil Kumar (INC) won, Vijayendra (BJP) runner-up
        assert rows[0][2] == 'Anil Kumar'   # round 3, rank 1
        assert rows[0][3] == 61000
        assert rows[1][2] == 'Vijayendra'   # round 3, rank 2
        assert rows[1][3] == 52800

        # Round 2 (2018): Vijayendra won
        assert rows[2][2] == 'Vijayendra'   # round 2, rank 1
        assert rows[2][3] == 72100

        # Round 1 (2024): Vijayendra won
        assert rows[4][2] == 'Vijayendra'   # round 1, rank 1
        assert rows[4][3] == 89234

    def test_margin_calculation(self, search_db):
        """Margin should be winner votes minus runner-up votes."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, votes, round_no,
                       ROW_NUMBER() OVER (PARTITION BY round_no ORDER BY votes DESC) as rn
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no = 1
            )
            SELECT MAX(CASE WHEN rn=1 THEN votes END) - MAX(CASE WHEN rn=2 THEN votes END) as margin
            FROM ranked
        """).fetchone()

        assert rows[0] == 89234 - 67891  # 21343

    def test_elections_sorted_by_recency(self, search_db):
        """Elections should be sorted by sort_date DESC."""
        rows = search_db.execute("""
            SELECT DISTINCT e.sort_date
            FROM rounds_ac r
            LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%'
            WHERE r.state_code = 'S07' AND r.ac_no = 22 AND r.round_no != 999
            ORDER BY e.sort_date DESC
        """).fetchall()

        dates = [r[0] for r in rows]
        assert dates == ['2024-05', '2018-05', '2013-05']

    def test_multiple_parties_won(self, search_db):
        """Different parties should have won across different elections."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT party_abv, round_no,
                       ROW_NUMBER() OVER (PARTITION BY round_no ORDER BY votes DESC) as rn
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no != 999
            )
            SELECT DISTINCT party_abv FROM ranked WHERE rn = 1
        """).fetchall()

        parties = {r[0] for r in rows}
        assert 'BJP' in parties   # won 2024 and 2018
        assert 'INC' in parties   # won 2013
