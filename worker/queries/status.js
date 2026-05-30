import { jsonResponse } from '../shared/cors.js';
import { getElectionById } from './elections.js';

export async function handleStatus(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const electionId = url.searchParams.get('election_id');

  let stateFilter = '';
  const params = [];

  if (state) {
    stateFilter = 'AND cs.state_code = ?';
    params.push(state);
  } else if (electionId) {
    const election = await getElectionById(env, electionId);
    if (election) {
      const stateList = JSON.parse(election.states);
      const placeholders = stateList.map(() => '?').join(',');
      stateFilter = `AND cs.state_code IN (${placeholders})`;
      params.push(...stateList);
    }
  }

  const stmt = env.DB.prepare(`
    SELECT COALESCE(cs.status, 'PENDING') as effective_status, COUNT(*) as cnt
    FROM constituency_status cs
    WHERE cs.state_code IN (SELECT DISTINCT state_code FROM rounds_ac)
    ${stateFilter}
    GROUP BY effective_status
  `).bind(...params);

  const rows = await stmt.all();
  const statuses = {};
  for (const row of rows.results) {
    statuses[row.effective_status] = row.cnt;
  }

  return jsonResponse({
    statuses,
    active_states: state ? 1 : Object.keys(statuses).length,
    updated_at: new Date().toISOString(),
  });
}
