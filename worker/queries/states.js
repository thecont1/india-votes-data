import { jsonResponse } from '../shared/cors.js';

export async function handleStates(env) {
  const rows = await env.DB.prepare(
    'SELECT state_code, state_code_std, state_name FROM states ORDER BY state_code'
  ).all();
  return jsonResponse({ states: rows.results });
}
