import { jsonResponse } from '../shared/cors.js';

export async function handleConstituency(stateCode, acNo, env) {
  const rows = await env.DB.prepare(`
    SELECT round_no, candidate, party_abv, votes
    FROM rounds_ac
    WHERE state_code = ? AND ac_no = ?
    ORDER BY round_no, votes DESC
  `).bind(stateCode, acNo).all();

  return jsonResponse({
    state_code: stateCode,
    ac_no: parseInt(acNo),
    rounds: rows.results,
  });
}
