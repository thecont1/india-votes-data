-- FTS5 Search Schema for LET Live!
-- Trigram tokenizer for fuzzy matching of Indian names and party abbreviations

-- Content table with ranking boost column
CREATE TABLE IF NOT EXISTS candidates_search (
    entity_type  TEXT NOT NULL,     -- 'candidate' | 'constituency' | 'party'
    entity_id    TEXT NOT NULL,     -- composite key for lookups
    name         TEXT NOT NULL,     -- primary searchable text
    context      TEXT DEFAULT '',   -- secondary text (state, party, alliance)
    boost        REAL DEFAULT 1.0   -- ranking multiplier
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

-- Populate: parties (boost 2.0 — most commonly searched)
INSERT INTO candidates_search (entity_type, entity_id, name, context, boost)
SELECT 'party', abv, name, COALESCE(alliance, ''), 2.0 FROM parties;

-- Populate: constituencies (boost 1.0)
INSERT INTO candidates_search (entity_type, entity_id, name, context, boost)
SELECT 'constituency',
       cs.state_code || '-' || cs.ac_no,
       cs.ac_name,
       s.state_name,
       1.0
FROM constituency_status cs
JOIN states s ON cs.state_code = s.state_code
WHERE cs.ac_name IS NOT NULL;

-- Populate: candidates from latest round per AC (boost by status)
INSERT INTO candidates_search (entity_type, entity_id, name, context, boost)
SELECT 'candidate',
       r.state_code || '-' || r.ac_no || '-' || r.party_abv,
       r.candidate,
       r.party_abv || ' | ' || COALESCE(r.ac_name, ''),
       CASE WHEN cs.won = 1 THEN 1.5
            WHEN cs.status = 'LIVE' THEN 1.2
            ELSE 1.0 END
FROM rounds_ac r
INNER JOIN (
    SELECT state_code, ac_no, MAX(round_no) as max_round
    FROM rounds_ac
    WHERE round_no != 999
    GROUP BY state_code, ac_no
) lr ON r.state_code = lr.state_code
    AND r.ac_no = lr.ac_no
    AND r.round_no = lr.max_round
LEFT JOIN constituency_status cs
    ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
WHERE r.round_no != 999;

-- Rebuild FTS index from content table
INSERT INTO search_fts(search_fts) VALUES('rebuild');
