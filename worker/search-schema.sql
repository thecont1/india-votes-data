-- FTS5 Search Schema for LET Live!
-- Trigram tokenizer for fuzzy matching of Indian names and party abbreviations
-- v4: Added symbol_url for party symbols in search results

-- Content table with ranking columns
CREATE TABLE IF NOT EXISTS candidates_search (
    entity_type    TEXT NOT NULL,     -- 'candidate' | 'constituency'
    entity_id      TEXT NOT NULL,     -- composite key for lookups
    name           TEXT NOT NULL,     -- primary searchable text
    context        TEXT DEFAULT '',   -- secondary text (state, party, alliance)
    boost          REAL DEFAULT 1.0,  -- legacy ranking multiplier
    votes          INTEGER DEFAULT 0, -- candidate vote count (final round)
    total_votes    INTEGER DEFAULT 0, -- constituency total votes (final round)
    election_sort  TEXT DEFAULT '',   -- election sort_date for recency weighting
    symbol_url     TEXT DEFAULT ''    -- party symbol URL for candidates
);

-- FTS5 virtual table using trigram tokenizer
-- case_sensitive 0: handles inconsistent ECI casing ("BJP" vs "bjp")
-- content='candidates_search': external content mode for efficient updates
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    entity_type,
    entity_id,
    name,
    context,
    content='candidates_search',
    content_rowid='rowid',
    tokenize='trigram case_sensitive 0'
);

-- Populate: candidates from final round per AC
-- Final round = round 999 (postal ballots) if it exists, else max non-999 round
INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort, symbol_url)
WITH final_rounds AS (
    SELECT
        r.state_code,
        r.ac_no,
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
       CASE WHEN cs.won = 1 THEN 1.5
            WHEN cs.status = 'LIVE' THEN 1.2
            ELSE 1.0 END,
       r.votes,
       0,
       COALESCE(e.sort_date, ''),
       COALESCE(p.symbol_url, '')
FROM rounds_ac r
JOIN final_rounds fr
    ON r.state_code = fr.state_code
    AND r.ac_no = fr.ac_no
    AND r.round_no = fr.final_round
LEFT JOIN constituency_status cs
    ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
LEFT JOIN elections e
    ON e.states LIKE '%' || r.state_code || '%'
LEFT JOIN parties p
    ON r.party_abv = p.abv;

-- Populate: constituencies with total votes cast (final round only)
INSERT INTO candidates_search (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort, symbol_url)
WITH final_rounds AS (
    SELECT
        cs.state_code,
        cs.ac_no,
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
       cs.ac_name,
       s.state_name,
       1.0,
       0,
       COALESCE(tv.total, 0),
       '',
       ''
FROM constituency_status cs
JOIN states s ON cs.state_code = s.state_code
LEFT JOIN final_rounds fr ON cs.state_code = fr.state_code AND cs.ac_no = fr.ac_no
LEFT JOIN (
    SELECT state_code, ac_no, SUM(votes) as total
    FROM rounds_ac
    WHERE round_no IN (
        SELECT COALESCE(
            (SELECT MAX(r2.round_no) FROM rounds_ac r2
             WHERE r2.state_code = rounds_ac.state_code AND r2.ac_no = rounds_ac.ac_no AND r2.round_no = 999),
            (SELECT MAX(r2.round_no) FROM rounds_ac r2
             WHERE r2.state_code = rounds_ac.state_code AND r2.ac_no = rounds_ac.ac_no AND r2.round_no != 999)
        )
        FROM (SELECT DISTINCT state_code, ac_no FROM rounds_ac) rounds_ac
    )
    GROUP BY state_code, ac_no
) tv ON cs.state_code = tv.state_code AND cs.ac_no = tv.ac_no
WHERE cs.ac_name IS NOT NULL;

-- Rebuild FTS index from content table
INSERT INTO search_fts(search_fts) VALUES('rebuild');
