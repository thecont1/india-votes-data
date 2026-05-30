import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';

export async function handleAcRaces(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  if (!state) return jsonResponse({ error: 'state required' }, 400);

  const rows = await env.DB.prepare(`
    WITH latest_rounds AS (
      SELECT lr.state_code, lr.ac_no, lr.max_round
      FROM (
        SELECT state_code, ac_no, MAX(round_no) as max_round
        FROM rounds_ac
        WHERE state_code = ?
        GROUP BY state_code, ac_no
      ) lr
      JOIN constituency_status cs
        ON lr.state_code = cs.state_code AND lr.ac_no = cs.ac_no
      WHERE cs.status = 'DONE'
    ),
    ranked AS (
      SELECT r.state_code, r.ac_no, r.ac_name,
             r.candidate,
             p.abv as party_abv, p.name as party_name,
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
      JOIN parties p ON r.party_abv = p.abv
    )
    SELECT ac_no, ac_name, candidate, party_abv, party_name,
           votes, rank,
           current_round, won, latest_round,
           form20_url, form20_status,
           form20_score, form20_checked_at,
           SUM(votes) OVER (PARTITION BY ac_no) as total_votes
    FROM ranked
    ORDER BY ac_no, rank
  `).bind(state).all();

  // Get party symbols
  const symbolRows = await env.DB.prepare(
    'SELECT abv, symbol_url FROM parties WHERE symbol_url IS NOT NULL'
  ).all();
  const symbols = {};
  for (const r of symbolRows.results) symbols[r.abv] = r.symbol_url;

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
    ac.candidates.push({
      ac_no: row.ac_no, ac_name: row.ac_name,
      candidate: row.candidate,
      party_abv: row.party_abv, party_name: row.party_name,
      votes: row.votes, rank: row.rank,
      color: getColor(row.party_abv),
      symbol_url: symbols[row.party_abv] || null,
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
