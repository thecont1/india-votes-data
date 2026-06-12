import { jsonResponse } from '../shared/cors.js';

export async function handleConstituencyHistory(request, env) {
  const url = new URL(request.url);
  const stateCode = url.searchParams.get('state')?.trim();
  const acNo = parseInt(url.searchParams.get('ac'));

  if (!stateCode || isNaN(acNo)) {
    return jsonResponse({ error: 'state and ac parameters required' }, 400);
  }

  // Get constituency name + state name
  const meta = await env.DB.prepare(`
    SELECT cs.ac_name, s.state_name
    FROM constituency_status cs
    JOIN states s ON cs.state_code = s.state_code
    WHERE cs.state_code = ? AND cs.ac_no = ?
  `).bind(stateCode, acNo).first();

  // Get the final round for this constituency, per election.
  // Uses a grouped MAX on rounds_ac with the composite index —
  // correct per-election max, no latest_rounds_ac cross-election risk.
  const rows = await env.DB.prepare(`
    WITH election_finals AS (
      SELECT cc.election_id,
             MAX(r.round_no) as final_round
      FROM (SELECT DISTINCT election_id, state_code, ac_no FROM rounds_ac
            WHERE state_code = ? AND ac_no = ?) cc
      JOIN rounds_ac r
        ON r.election_id = cc.election_id
       AND r.state_code  = cc.state_code
       AND r.ac_no       = cc.ac_no
      GROUP BY cc.election_id
    ),
    ranked AS (
      SELECT
        r.election_id, r.candidate, r.party_abv, r.votes,
        e.name as election_name, e.sort_date,
        ROW_NUMBER() OVER (PARTITION BY r.election_id ORDER BY r.votes DESC) as rank_in_round
      FROM rounds_ac r
      JOIN election_finals ef
        ON r.election_id = ef.election_id
        AND r.round_no = ef.final_round
      LEFT JOIN elections e ON r.election_id = e.election_id
      WHERE r.state_code = ? AND r.ac_no = ?
    )
    SELECT election_id, election_name, sort_date, candidate, party_abv, votes, rank_in_round
    FROM ranked
    WHERE rank_in_round <= 2
    ORDER BY sort_date DESC, rank_in_round ASC
  `).bind(stateCode, acNo, stateCode, acNo).all();

  // Group by election
  const electionMap = new Map();
  for (const row of rows.results) {
    if (!electionMap.has(row.election_id)) {
      electionMap.set(row.election_id, {
        election_id: row.election_id,
        election_name: row.election_name || 'Unknown',
        sort_date: row.sort_date || '',
        winner: null,
        runner_up: null,
        margin: 0,
      });
    }
    const election = electionMap.get(row.election_id);
    const entry = { candidate: row.candidate, party: row.party_abv, votes: row.votes };
    if (row.rank_in_round === 1) election.winner = entry;
    else if (row.rank_in_round === 2) election.runner_up = entry;
  }

  const elections = [];
  for (const [eid, election] of electionMap) {
    if (election.winner && election.runner_up) {
      election.margin = election.winner.votes - election.runner_up.votes;
    } else if (election.winner) {
      election.margin = election.winner.votes;
    }
    elections.push(election);
  }

  const partiesWon = [...new Set(elections.map(e => e.winner?.party).filter(Boolean))];
  const lastWinner = elections[0]?.winner;

  return jsonResponse({
    constituency: meta?.ac_name || '',
    state: meta?.state_name || '',
    state_code: stateCode,
    ac_no: acNo,
    elections,
    summary: {
      total_elections: elections.length,
      parties_won: partiesWon,
      last_winner: lastWinner ? `${lastWinner.candidate} (${lastWinner.party})` : '',
    },
  });
}
