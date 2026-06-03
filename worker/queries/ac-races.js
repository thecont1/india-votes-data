import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';

export async function handleAcRaces(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const electionId = url.searchParams.get('election_id')?.trim();
  if (!state) return jsonResponse({ error: 'state required' }, 400);

  const electionFilter = electionId ? 'AND r.election_id = ?' : '';
  const binds = electionId ? [state, electionId, state, electionId] : [state, state];

  const rows = await env.DB.prepare(`
    WITH latest_rounds AS (
      SELECT lr.state_code, lr.ac_no, lr.max_round
      FROM (
        SELECT state_code, ac_no, MAX(round_no) as max_round
        FROM rounds_ac
        WHERE state_code = ? ${electionId ? 'AND election_id = ?' : ''}
        GROUP BY state_code, ac_no
      ) lr
      JOIN constituency_status cs
        ON lr.state_code = cs.state_code AND lr.ac_no = cs.ac_no
      WHERE cs.status = 'DONE'
    ),
    ranked AS (
      SELECT r.state_code, r.ac_no, r.ac_name,
             r.candidate,
             r.party_abv,
             r.votes,
             cs.current_round,
             cs.won,
             lr.max_round as latest_round,
             cs.form20_url,
             cs.form20_status,
             cs.form20_score,
             cs.form20_checked_at,
             ROW_NUMBER() OVER (
               PARTITION BY r.state_code, r.ac_no
               ORDER BY r.votes DESC
             ) as rank
      FROM rounds_ac r
      JOIN latest_rounds lr
        ON r.state_code = lr.state_code
        AND r.ac_no = lr.ac_no
        AND r.round_no = lr.max_round
      JOIN constituency_status cs
        ON r.state_code = cs.state_code
        AND r.ac_no = cs.ac_no
      WHERE r.state_code = ? ${electionFilter}
    )
    SELECT ac_no, ac_name, candidate, party_abv,
           votes, rank,
           current_round, won, latest_round,
           form20_url, form20_status,
           form20_score, form20_checked_at,
           SUM(votes) OVER (PARTITION BY ac_no) as total_votes
    FROM ranked
    ORDER BY ac_no, rank
  `).bind(...binds).all();

  // Get party names and symbols
  const partyRows = await env.DB.prepare(
    'SELECT abv, name, symbol_url FROM parties'
  ).all();
  const partyInfo = {};
  for (const r of partyRows.results) {
    partyInfo[r.abv] = { name: r.name, symbol_url: r.symbol_url };
  }

  // Group by AC
  const acMap = new Map();
  for (const row of rows.results) {
    if (!acMap.has(row.ac_no)) {
      acMap.set(row.ac_no, {
        ac_no: row.ac_no,
        ac_name: row.ac_name,
        total_votes: row.total_votes,
        status: 'PENDING',
        current_round: row.current_round || 0,
        won: row.won || 0,
        latest_round: row.latest_round,
        form20_url: row.form20_url,
        form20_status: row.form20_status || 'UNAVAILABLE',
        form20_score: row.form20_score,
        form20_checked_at: row.form20_checked_at,
        margin: 0,
        candidates: [],
      });
    }
    const ac = acMap.get(row.ac_no);
    const pi = partyInfo[row.party_abv] || {};
    ac.candidates.push({
      ac_no: row.ac_no, ac_name: row.ac_name,
      candidate: row.candidate,
      party_abv: row.party_abv, party_name: pi.name || row.party_abv,
      votes: row.votes, rank: row.rank,
      color: getColor(row.party_abv),
      symbol_url: pi.symbol_url || null,
    });
  }

  // Compute status and margin
  const result = [...acMap.values()];
  for (const ac of result) {
    const cands = ac.candidates;
    if (ac.total_votes > 0 && cands.length > 1) ac.status = 'DONE';
    if (cands.length >= 2) ac.margin = cands[0].votes - cands[1].votes;
    else if (cands.length === 1) ac.margin = cands[0].votes;
  }
  result.sort((a, b) => b.margin - a.margin);

  return jsonResponse({ races: result, state });
}
