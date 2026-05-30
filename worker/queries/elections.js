import { jsonResponse } from '../shared/cors.js';

export async function handleElections(env) {
  const rows = await env.DB.prepare(
    'SELECT election_id, name, states, sort_date FROM elections ORDER BY sort_date DESC'
  ).all();
  return jsonResponse({ elections: rows.results.map(r => ({
    ...r,
    states: JSON.parse(r.states),
  })) });
}

export async function handleCurrentElection(env) {
  const row = await env.DB.prepare(
    'SELECT election_id, name, states, sort_date FROM elections ORDER BY sort_date DESC LIMIT 1'
  ).first();
  if (!row) return jsonResponse({ election: null });
  return jsonResponse({ election: { ...row, states: JSON.parse(row.states) } });
}

export function getElectionById(env, electionId) {
  return env.DB.prepare(
    'SELECT election_id, name, states, sort_date FROM elections WHERE election_id = ?'
  ).bind(electionId).first();
}
