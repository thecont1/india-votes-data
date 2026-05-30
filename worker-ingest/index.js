// Ingestion Worker — writes only, auth required
// Accepts scraper data and writes to D1 in batches

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
  const { state_code, ac_no, ac_name, round_no, candidates } = body;

  if (!state_code || !ac_no || !candidates?.length) {
    return Response.json(
      { error: 'Missing required fields: state_code, ac_no, candidates' },
      { status: 400, headers: corsHeaders }
    );
  }

  const stmts = [];

  // Insert each candidate
  for (const c of candidates) {
    stmts.push(
      env.DB.prepare(`
        INSERT INTO rounds_ac (state_code, ac_no, ac_name, round_no, candidate, party_abv, votes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (state_code, ac_no, round_no, candidate, party_abv)
        DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name
      `).bind(state_code, ac_no, ac_name || null, round_no, c.candidate, c.party_abv, c.votes)
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

  // Update FTS content table for new/changed candidates
  for (const c of candidates) {
    stmts.push(
      env.DB.prepare(`
        INSERT OR REPLACE INTO candidates_search (entity_type, entity_id, name, context, boost)
        VALUES ('candidate', ?, ?, ?, 1.0)
      `).bind(
        `${state_code}-${ac_no}-${c.party_abv}`,
        c.candidate,
        `${c.party_abv} | ${ac_name || ''}`
      )
    );
  }

  // Execute all in one batch
  await env.DB.batch(stmts);

  // Rebuild FTS index
  await env.DB.prepare("INSERT INTO search_fts(search_fts) VALUES('rebuild')").run();

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
    const { state_code, ac_no, ac_name, round_no, candidates } = round;
    if (!state_code || !ac_no || !candidates?.length) continue;

    for (const c of candidates) {
      stmts.push(
        env.DB.prepare(`
          INSERT INTO rounds_ac (state_code, ac_no, ac_name, round_no, candidate, party_abv, votes)
          VALUES (?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (state_code, ac_no, round_no, candidate, party_abv)
          DO UPDATE SET votes = EXCLUDED.votes, ac_name = EXCLUDED.ac_name
        `).bind(state_code, ac_no, ac_name || null, round_no, c.candidate, c.party_abv, c.votes)
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

    for (const c of candidates) {
      stmts.push(
        env.DB.prepare(`
          INSERT OR REPLACE INTO candidates_search (entity_type, entity_id, name, context, boost)
          VALUES ('candidate', ?, ?, ?, 1.0)
        `).bind(
          `${state_code}-${ac_no}-${c.party_abv}`,
          c.candidate,
          `${c.party_abv} | ${ac_name || ''}`
        )
      );
    }
  }

  // D1 batch limit is ~100 statements; split if needed
  const BATCH_SIZE = 80;
  for (let i = 0; i < stmts.length; i += BATCH_SIZE) {
    await env.DB.batch(stmts.slice(i, i + BATCH_SIZE));
  }

  // Rebuild FTS once at the end
  await env.DB.prepare("INSERT INTO search_fts(search_fts) VALUES('rebuild')").run();

  return Response.json(
    { ok: true, rounds: rounds.length, candidates: totalCandidates },
    { headers: corsHeaders }
  );
}
