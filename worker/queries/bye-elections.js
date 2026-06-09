import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';
import { getPartiesWithSymbols } from './parties.js';

/**
 * GET /api/bye-elections?election_id=BYE-2026-05&state=S10
 *
 * Returns bye-election constituencies with their final results.
 * Optional state filter. Optional election_id filter (defaults to latest bye-election).
 * Output matches ac-races format (candidates with color, symbol_url, party_name).
 */
export async function handleByeElections(request, env) {
  const url = new URL(request.url);
  const stateFilter = url.searchParams.get('state')?.trim();
  let electionId = url.searchParams.get('election_id')?.trim();

  // If no election_id specified, find the latest bye-election
  if (!electionId) {
    const latest = await env.DB.prepare(
      `SELECT election_id FROM elections WHERE election_id LIKE 'BYE-%' ORDER BY sort_date DESC LIMIT 1`
    ).first();
    if (!latest) {
      return jsonResponse({ bye_elections: [], election_id: null });
    }
    electionId = latest.election_id;
  }

  // Get all candidates for this bye-election's final round
  let query = `
    WITH final_rounds AS (
      SELECT state_code, ac_no,
        COALESCE(
          (SELECT MAX(round_no) FROM rounds_ac r2
           WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no
             AND r2.election_id = ? AND r2.round_no = 999),
          (SELECT MAX(round_no) FROM rounds_ac r2
           WHERE r2.state_code = r.state_code AND r2.ac_no = r.ac_no
             AND r2.election_id = ? AND r2.round_no != 999)
        ) as final_round
      FROM rounds_ac r
      WHERE r.election_id = ?
      GROUP BY state_code, ac_no
    )
    SELECT
      r.state_code, r.ac_no, r.ac_name, r.candidate, r.party_abv, r.votes,
      s.state_name,
      p.name as party_name,
      ROW_NUMBER() OVER (PARTITION BY r.state_code, r.ac_no ORDER BY r.votes DESC) as rank,
      SUM(r.votes) OVER (PARTITION BY r.state_code, r.ac_no) as total_votes
    FROM rounds_ac r
    JOIN final_rounds fr
      ON r.state_code = fr.state_code AND r.ac_no = fr.ac_no AND r.round_no = fr.final_round
    LEFT JOIN states s ON r.state_code = s.state_code
    LEFT JOIN parties p ON r.party_abv = p.abv
    WHERE r.election_id = ?
      ${stateFilter ? 'AND r.state_code = ?' : ''}
    ORDER BY r.state_code, r.ac_no, rank
  `;

  const binds = [electionId, electionId, electionId, electionId];
  if (stateFilter) binds.push(stateFilter);

  const rows = await env.DB.prepare(query).bind(...binds).all();

  // Get party symbols (cached in KV)
  const symbolRows = await getPartiesWithSymbols(env);
  const symbols = {};
  for (const r of symbolRows) symbols[r.abv] = r.symbol_url;

  // Group by constituency
  const acMap = new Map();
  for (const row of rows.results) {
    const key = `${row.state_code}-${row.ac_no}`;
    if (!acMap.has(key)) {
      acMap.set(key, {
        state_code: row.state_code,
        state_name: row.state_name || '',
        ac_no: row.ac_no,
        ac_name: row.ac_name || '',
        total_votes: row.total_votes,
        margin: 0,
        candidates: [],
      });
    }
    const ac = acMap.get(key);
    ac.candidates.push({
      ac_no: row.ac_no,
      ac_name: row.ac_name,
      candidate: row.candidate,
      party_abv: row.party_abv,
      party_name: row.party_name || row.party_abv,
      votes: row.votes,
      rank: row.rank,
      color: getColor(row.party_abv),
      symbol_url: symbols[row.party_abv] || null,
    });
  }

  // Compute margin, winner, runner_up
  const byeElections = [...acMap.values()];
  for (const ac of byeElections) {
    const cands = ac.candidates;
    ac.winner = cands.find(c => c.rank === 1) || null;
    ac.runner_up = cands.find(c => c.rank === 2) || null;
    if (cands.length >= 2) ac.margin = cands[0].votes - cands[1].votes;
    else if (cands.length === 1) ac.margin = cands[0].votes;
  }
  byeElections.sort((a, b) => b.margin - a.margin);

  return jsonResponse({
    election_id: electionId,
    bye_elections: byeElections,
    total: byeElections.length,
  }, 200, {
    'Cache-Control': 'public, max-age=30, stale-while-revalidate=60',
  });
}
