import { jsonResponse } from '../shared/cors.js';

export async function handleConstituency(stateCode, acNo, env, request) {
  const url = new URL(request.url);
  const electionId = url.searchParams.get('election_id')?.trim();

  let query = `
    SELECT round_no, candidate, party_abv, votes
    FROM rounds_ac
    WHERE state_code = ? AND ac_no = ?
  `;
  const binds = [stateCode, acNo];

  if (electionId) {
    query += ' AND election_id = ?';
    binds.push(electionId);
  }

  query += ' ORDER BY round_no, votes DESC';

  const rows = await env.DB.prepare(query).bind(...binds).all();

  return jsonResponse({
    state_code: stateCode,
    ac_no: parseInt(acNo),
    rounds: rows.results,
  });
}
