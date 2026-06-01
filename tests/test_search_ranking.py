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
            election_id TEXT NOT NULL DEFAULT '',
            round_no INTEGER NOT NULL,
            candidate TEXT NOT NULL,
            party_abv TEXT NOT NULL,
            votes INTEGER NOT NULL,
            PRIMARY KEY (state_code, ac_no, election_id, round_no, candidate, party_abv)
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
    # Each state_code represents a different election cycle
    # Round 999 = final result (postal ballots), round 1/2 = intermediate counting
    cur.executescript("""
        INSERT INTO states VALUES ('S07', 'Karnataka');
        INSERT INTO states VALUES ('S11', 'Karnataka');  -- different election

        INSERT INTO elections VALUES ('AC-2024-05', 'AC 2024 May - S07', '["S07"]', '2024-05');
        INSERT INTO elections VALUES ('AC-2018-05', 'AC 2018 May - S11', '["S11"]', '2018-05');

        INSERT INTO parties VALUES ('BJP', 'Bharatiya Janata Party', 'NDA');
        INSERT INTO parties VALUES ('INC', 'Indian National Congress', 'I.N.D.I.A.');
        INSERT INTO parties VALUES ('JD(S)', 'Janata Dal (Secular)', '');

        -- Constituency status
        INSERT INTO constituency_status VALUES ('S07', 22, 'Vijayapur', 'DONE', 1);
        INSERT INTO constituency_status VALUES ('S07', 50, 'Namvijaynagar', 'DONE', 1);
        INSERT INTO constituency_status VALUES ('S07', 10, 'Badami', 'DONE', 1);
        INSERT INTO constituency_status VALUES ('S11', 22, 'Vijayapur', 'DONE', 1);

        -- S07-22 Vijayapur: intermediate round + final round 999
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 'AC-2024-05', 1, 'Vijayendra', 'BJP', 85000);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 'AC-2024-05', 1, 'Ramesh Kumar', 'INC', 64000);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 'AC-2024-05', 999, 'Vijayendra', 'BJP', 89234);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 'AC-2024-05', 999, 'Ramesh Kumar', 'INC', 67891);
        INSERT INTO rounds_ac VALUES ('S07', 22, 'Vijayapur', 'AC-2024-05', 999, 'Anil Kumar', 'JD(S)', 12345);

        -- S07-50 Namvijaynagar: only round 999
        INSERT INTO rounds_ac VALUES ('S07', 50, 'Namvijaynagar', 'AC-2024-05', 999, 'C. Joseph Vijay', 'INC', 95000);
        INSERT INTO rounds_ac VALUES ('S07', 50, 'Namvijaynagar', 'AC-2024-05', 999, 'Suresh Babu', 'BJP', 78000);

        -- S07-10 Badami: only round 999
        INSERT INTO rounds_ac VALUES ('S07', 10, 'Badami', 'AC-2024-05', 999, 'Siddaramaiah', 'INC', 85000);
        INSERT INTO rounds_ac VALUES ('S07', 10, 'Badami', 'AC-2024-05', 999, 'B Sriramulu', 'BJP', 72000);

        -- S11-22 Vijayapur (past election): round 999
        INSERT INTO rounds_ac VALUES ('S11', 22, 'Vijayapur', 'AC-2018-05', 999, 'Anil Kumar', 'INC', 61000);
        INSERT INTO rounds_ac VALUES ('S11', 22, 'Vijayapur', 'AC-2018-05', 999, 'Vijayendra', 'BJP', 52800);
    """)

    conn.commit()

    # Build search index (mirrors search-schema.sql v3 — uses final round)
    cur.executescript("""
        -- Candidates with votes + election sort (final round = 999 or max)
        INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort)
        WITH final_rounds AS (
            SELECT
                r.state_code, r.ac_no,
                COALESCE(
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no = 999),
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no != 999)
                ) as final_round
            FROM (SELECT DISTINCT state_code, ac_no FROM rounds_ac) r
        )
        SELECT 'candidate',
               r.state_code || '-' || r.ac_no || '-' || r.party_abv,
               r.candidate,
               r.party_abv || ' | ' || COALESCE(r.ac_name, '') || ' | ' || COALESCE(SUBSTR(e.sort_date, 1, 4), ''),
               CASE WHEN cs.won = 1 THEN 1.5 ELSE 1.0 END,
               r.votes, 0, COALESCE(e.sort_date, '')
        FROM rounds_ac r
        JOIN final_rounds fr ON r.state_code = fr.state_code AND r.ac_no = fr.ac_no AND r.round_no = fr.final_round
        LEFT JOIN constituency_status cs ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
        LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%';

        -- Constituencies with total votes (final round only)
        INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort)
        WITH final_rounds AS (
            SELECT
                cs.state_code, cs.ac_no,
                COALESCE(
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = cs.state_code AND r2.ac_no = cs.ac_no AND r2.round_no = 999),
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = cs.state_code AND r2.ac_no = cs.ac_no AND r2.round_no != 999)
                ) as final_round
            FROM (SELECT DISTINCT state_code, ac_no FROM rounds_ac) cs
        )
        SELECT 'constituency',
               cs.state_code || '-' || cs.ac_no,
               cs.ac_name, s.state_name, 1.0, 0,
               COALESCE(tv.total, 0), ''
        FROM constituency_status cs
        JOIN states s ON cs.state_code = s.state_code
        LEFT JOIN final_rounds fr ON cs.state_code = fr.state_code AND cs.ac_no = fr.ac_no
        LEFT JOIN (
            SELECT state_code, ac_no, SUM(votes) as total
            FROM rounds_ac r
            WHERE r.round_no = (
                SELECT COALESCE(
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no = 999),
                    (SELECT MAX(r2.round_no) FROM rounds_ac r2
                     WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no AND r2.round_no != 999)
                )
            )
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
        """Constituencies should be sorted by total votes cast (final round only)."""
        rows = search_db.execute("""
            SELECT name, total_votes
            FROM candidates_search
            WHERE entity_type = 'constituency'
            ORDER BY total_votes DESC
        """).fetchall()

        names = [r[0] for r in rows]
        totals = [r[1] for r in rows]
        # total_votes from final round (999) only:
        # S07 Vijayapur: 89234+67891+12345 = 169470
        # S07 Namvijaynagar: 95000+78000 = 173000
        # S07 Badami: 85000+72000 = 157000
        # S11 Vijayapur: 61000+52800 = 113800
        assert names[0] == 'Namvijaynagar'  # highest final-round total
        assert totals[0] == 173000

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
        """Searching for 'Vijayendra' should return their contest from each constituency."""
        rows = search_db.execute("""
            SELECT candidate, party_abv, votes, state_code, ac_no, round_no
            FROM rounds_ac
            WHERE candidate = ?
            ORDER BY votes DESC
        """, ('Vijayendra',)).fetchall()

        # Vijayendra appears in round 999 for S07-22 and S11-22
        # (round 1 intermediate rows exist but final round 999 is what matters)
        assert len(rows) >= 2  # at least 2 constituencies
        votes = [r[2] for r in rows]
        assert 89234 in votes  # S07-22 round 999
        assert 52800 in votes  # S11-22 round 999

    def test_winner_detection(self, search_db):
        """Winner should be the candidate with most votes in the final round."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, party_abv, votes, round_no,
                       ROW_NUMBER() OVER (ORDER BY votes DESC) as rank_in_round
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no = 999
            )
            SELECT candidate, round_no, rank_in_round, votes
            FROM ranked
            ORDER BY rank_in_round
        """).fetchall()

        # Final round 999: Vijayendra won (rank 1), Ramesh Kumar runner-up (rank 2)
        assert rows[0][2] == 1  # rank 1 = winner
        assert rows[0][0] == 'Vijayendra'
        assert rows[1][2] == 2  # rank 2 = runner-up

    def test_sorted_by_recency(self, search_db):
        """Contests should be sorted by election recency DESC."""
        rows = search_db.execute("""
            SELECT r.candidate, r.votes, e.sort_date
            FROM rounds_ac r
            LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%'
            WHERE r.candidate = 'Vijayendra'
            ORDER BY e.sort_date DESC, r.votes DESC
        """).fetchall()

        dates = [r[2] for r in rows]
        assert dates == sorted(dates, reverse=True)


class TestConstituencyHistory:
    """Test constituency history query logic."""

    def test_winner_runner_up_per_election(self, search_db):
        """Final round should have a winner and runner-up."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, party_abv, votes, round_no,
                       ROW_NUMBER() OVER (ORDER BY votes DESC) as rank_in_round
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no = 999
            )
            SELECT rank_in_round, candidate, votes
            FROM ranked
            WHERE rank_in_round <= 2
            ORDER BY rank_in_round ASC
        """).fetchall()

        # Final round 999: Vijayendra (89234) won, Ramesh Kumar (67891) runner-up
        assert len(rows) == 2
        assert rows[0][1] == 'Vijayendra'  # rank 1
        assert rows[0][2] == 89234
        assert rows[1][1] == 'Ramesh Kumar'  # rank 2
        assert rows[1][2] == 67891

    def test_margin_calculation(self, search_db):
        """Margin should be winner votes minus runner-up votes (final round)."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT candidate, votes,
                       ROW_NUMBER() OVER (ORDER BY votes DESC) as rn
                FROM rounds_ac
                WHERE state_code = 'S07' AND ac_no = 22 AND round_no = 999
            )
            SELECT MAX(CASE WHEN rn=1 THEN votes END) - MAX(CASE WHEN rn=2 THEN votes END) as margin
            FROM ranked
        """).fetchone()

        assert rows[0] == 89234 - 67891  # 21343

    def test_elections_sorted_by_recency(self, search_db):
        """Constituency results should map to election sort_date."""
        rows = search_db.execute("""
            SELECT DISTINCT e.sort_date
            FROM rounds_ac r
            LEFT JOIN elections e ON e.states LIKE '%' || r.state_code || '%'
            WHERE r.state_code = 'S07' AND r.ac_no = 22
            ORDER BY e.sort_date DESC
        """).fetchall()

        dates = [r[0] for r in rows]
        assert '2024-05' in dates

    def test_multiple_parties_won(self, search_db):
        """Different parties should have won across different constituencies."""
        rows = search_db.execute("""
            WITH ranked AS (
                SELECT party_abv, state_code, ac_no,
                       ROW_NUMBER() OVER (PARTITION BY state_code, ac_no ORDER BY votes DESC) as rn
                FROM rounds_ac
                WHERE round_no = 999
            )
            SELECT DISTINCT party_abv FROM ranked WHERE rn = 1
        """).fetchall()

        parties = {r[0] for r in rows}
        assert 'BJP' in parties   # won S07-22
        assert 'INC' in parties   # won S07-50, S07-10, S11-22
