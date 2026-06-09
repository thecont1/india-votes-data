// Ingestion Worker — writes only, auth required
// Accepts scraper data and writes to D1 in batches

// Party name normalization — must match load-json-to-d1.py HARDCODED_PARTIES
const PARTY_ABBREV = {
  'Janata Dal  (Secular)': 'JD(S)', 'Janata Dal (Secular)': 'JD(S)',
  'Janata Dal  (United)': 'JD(U)', 'Janata Dal (United)': 'JD(U)',
  'None of the Above': 'NOTA', 'Bharat Rashtra Samithi': 'BRS',
  'Nationalist Congress Party': 'NCP',
  'Nationalist Congress Party - Sharadchandra Pawar': 'NCP-SP',
  'Nationalist Congress Party \u2013 Sharadchandra Pawar': 'NCP-SP',
  'Shiv Sena': 'SHS', 'ShivSena': 'SHS', 'SHIVSS': 'SHS',
  'Shiv Sena (Uddhav Balasaheb Thackeray)': 'SS(UBT)',
  'Rashtriya Janata Dal': 'RJD', 'Rashtriya Lok Dal': 'RLD',
  'Jammu & Kashmir National Conference': 'JKNC',
  'Jammu and Kashmir National Conference': 'JKNC',
  'Jammu & Kashmir Peoples Democratic Party': 'JKPDP',
  'Indian National Lok Dal': 'INLD', 'Haryana Lokhit Party': 'HLP',
  'Jannayak Janta Party': 'JJP',
  'All India Majlis-E-Ittehadul Muslimeen': 'AIMIM',
  'All India Majlis-e-Ittehadul Muslimeen': 'AIMIM',
  'Communist Party of India (Marxist-Leninist) (Liberation)': 'CPI(ML)(L)',
};

function normalizeParty(name) {
  if (!name) return name;
  const trimmed = name.trim();
  return PARTY_ABBREV[trimmed] || trimmed;
}

export default {
  async fetch(request, env) {
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    // Auth check
    const auth = request.headers.get('Authorization');
    if (!auth || auth !== `Bearer ${env.INGEST_TOKEN}`) {
      return new Response('Unauthorized', { status: 401, headers: corsHeaders });
    }

    const url = new URL(request.url);
    try {
      // Health check
      if (url.pathname === '/health') {
        return Response.json({ status: 'ok', worker: 'election-ingest' }, { headers: corsHeaders });
      }

      // Round snapshot ingestion
      if (request.method === 'POST' && url.pathname === '/ingest/round') {
        return handleRoundIngest(request, env, corsHeaders);
      }

      // Batch round snapshots (multiple ACs in one request)
      if (request.method === 'POST' && url.pathname === '/ingest/batch') {
        return handleBatchIngest(request, env, corsHeaders);
      }

      return Response.json({ error: 'Not found' }, { status: 404, headers: corsHeaders });
    } catch (err) {
      console.error('Ingest error:', err);
      return Response.json(
        { error: 'Internal error: ' + err.message },
        { status: 500, headers: corsHeaders }
      );
    }
  }
};


async function handleRoundIngest(request, env, corsHeaders) {
  const body = await request.json();
  const { state_code, ac_no, ac_name, round_no, candidates, election_id } = body;

  if (!state_code || !ac_no || !candidates?.length) {
    return Response.json(
      { error: 'Missing required fields: state_code, ac_no, candidates' },
      { status: 400, headers: corsHeaders }
    );
  }

  const eid = election_id || '';
  const stmts = [];

  // Insert each candidate
  for (const c of candidates) {
    c.party_abv = normalizeParty(c.party_abv);
    stmts.push(
      env.DB.prepare(`
        INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv)
        DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name
      `).bind(state_code, ac_no, ac_name || null, eid, round_no, c.candidate, c.party_abv, c.votes)
    );
  }

  // Update constituency_status
  const status = round_no === 999 ? 'DONE' : 'LIVE';
  stmts.push(
    env.DB.prepare(`
      INSERT INTO constituency_status (state_code, ac_no, ac_name, status, current_round)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT (state_code, ac_no) DO UPDATE SET
        ac_name = COALESCE(excluded.ac_name, constituency_status.ac_name),
        status = excluded.status,
        current_round = excluded.current_round,
        error_count = CASE
          WHEN excluded.status = 'ERROR' THEN constituency_status.error_count + 1
          ELSE 0
        END
    `).bind(state_code, ac_no, ac_name || null, status, round_no)
  );

  // Look up state standard code for search context (e.g. S25 -> WB)
  const stateRow = await env.DB.prepare(
    "SELECT state_code_std FROM states WHERE state_code = ?"
  ).bind(state_code).first();
  const stateStd = stateRow?.state_code_std || state_code;

  // Update FTS content table for new/changed candidates
  for (const c of candidates) {
    const entityId = `${state_code}-${ac_no}-${eid ? eid + '-' : ''}${c.party_abv}|${c.candidate}`;
    // Delete existing row to avoid duplicates (no UNIQUE constraint on entity_id)
    stmts.push(
      env.DB.prepare(`DELETE FROM candidates_search WHERE entity_id = ?`).bind(entityId)
    );
    stmts.push(
      env.DB.prepare(`
        INSERT INTO candidates_search
        (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort, symbol_url)
        VALUES ('candidate', ?, ?, ?, 1.0, ?, 0,
                COALESCE((SELECT sort_date FROM elections WHERE election_id = ?), ''),
                COALESCE((SELECT symbol_url FROM parties WHERE abv = ?), ''))
      `).bind(
        entityId,
        c.candidate,
        `${c.party_abv} | ${ac_name || ''}, ${stateStd}`,
        c.votes,
        eid,
        c.party_abv
      )
    );
  }

  // Execute all in one batch
  await env.DB.batch(stmts);

  // Maintain latest_rounds_ac summary table
  await env.DB.prepare(`
    INSERT INTO latest_rounds_ac (state_code, ac_no, max_round)
    SELECT state_code, ac_no, MAX(round_no)
    FROM rounds_ac
    WHERE state_code = ?
    GROUP BY state_code, ac_no
    ON CONFLICT(state_code, ac_no) DO UPDATE SET max_round = excluded.max_round
  `).bind(state_code).run();

  return Response.json(
    { ok: true, candidates: candidates.length, state_code, ac_no, round_no },
    { headers: corsHeaders }
  );
}


async function handleBatchIngest(request, env, corsHeaders) {
  const body = await request.json();
  const { rounds } = body;  // Array of { state_code, ac_no, ac_name, round_no, candidates }

  if (!Array.isArray(rounds) || rounds.length === 0) {
    return Response.json(
      { error: 'Missing rounds array' },
      { status: 400, headers: corsHeaders }
    );
  }

  const stmts = [];
  let totalCandidates = 0;

  for (const round of rounds) {
    const { state_code, ac_no, ac_name, round_no, candidates, election_id } = round;
    if (!state_code || !ac_no || !candidates?.length) continue;

    const eid = election_id || '';
    for (const c of candidates) {
      c.party_abv = normalizeParty(c.party_abv);
      stmts.push(
        env.DB.prepare(`
          INSERT INTO rounds_ac (state_code, ac_no, ac_name, election_id, round_no, candidate, party_abv, votes)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (state_code, ac_no, election_id, round_no, candidate, party_abv)
          DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name
        `).bind(state_code, ac_no, ac_name || null, eid, round_no, c.candidate, c.party_abv, c.votes)
      );
      totalCandidates++;
    }

    const status = round_no === 999 ? 'DONE' : 'LIVE';
    stmts.push(
      env.DB.prepare(`
        INSERT INTO constituency_status (state_code, ac_no, ac_name, status, current_round)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (state_code, ac_no) DO UPDATE SET
          ac_name = COALESCE(excluded.ac_name, constituency_status.ac_name),
          status = excluded.status,
          current_round = excluded.current_round,
          error_count = CASE
            WHEN excluded.status = 'ERROR' THEN constituency_status.error_count + 1
            ELSE 0
          END
      `).bind(state_code, ac_no, ac_name || null, status, round_no)
    );

    // Look up state standard code for search context (e.g. S25 -> WB)
    const stateRow = await env.DB.prepare(
      "SELECT state_code_std FROM states WHERE state_code = ?"
    ).bind(state_code).first();
    const stateStd = stateRow?.state_code_std || state_code;

    for (const c of candidates) {
      const entityId = `${state_code}-${ac_no}-${eid ? eid + '-' : ''}${c.party_abv}|${c.candidate}`;
      stmts.push(
        env.DB.prepare(`DELETE FROM candidates_search WHERE entity_id = ?`).bind(entityId)
      );
      stmts.push(
        env.DB.prepare(`
          INSERT INTO candidates_search
          (entity_type, entity_id, name, context, boost, votes, total_votes, election_sort, symbol_url)
          VALUES ('candidate', ?, ?, ?, 1.0, ?, 0,
                  COALESCE((SELECT sort_date FROM elections WHERE election_id = ?), ''),
                  COALESCE((SELECT symbol_url FROM parties WHERE abv = ?), ''))
        `).bind(
          entityId,
          c.candidate,
          `${c.party_abv} | ${ac_name || ''}, ${stateStd}`,
          c.votes,
          eid,
          c.party_abv
        )
      );
    }
  }

  // D1 batch limit is ~100 statements; split if needed
  const BATCH_SIZE = 50;
  for (let i = 0; i < stmts.length; i += BATCH_SIZE) {
    await env.DB.batch(stmts.slice(i, i + BATCH_SIZE));
  }

  // Maintain latest_rounds_ac summary table for affected states
  const affectedStates = [...new Set(rounds.map(r => r.state_code).filter(Boolean))];
  for (const sc of affectedStates) {
    await env.DB.prepare(`
      INSERT INTO latest_rounds_ac (state_code, ac_no, max_round)
      SELECT state_code, ac_no, MAX(round_no)
      FROM rounds_ac
      WHERE state_code = ?
      GROUP BY state_code, ac_no
      ON CONFLICT(state_code, ac_no) DO UPDATE SET max_round = excluded.max_round
    `).bind(sc).run();
  }

  // Rebuild FTS only if explicitly requested (caller passes ?rebuild_fts=1 on final batch)
  const rebuildFts = new URL(request.url).searchParams.get('rebuild_fts') === '1';
  if (rebuildFts) {
    await env.DB.prepare("INSERT INTO search_fts(search_fts) VALUES('rebuild')").run();
  }

  return Response.json(
    { ok: true, rounds: rounds.length, candidates: totalCandidates },
    { headers: corsHeaders }
  );
}
