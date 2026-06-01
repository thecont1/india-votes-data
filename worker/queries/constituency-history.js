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

  // Get the final round for this constituency:
  //   - If round 999 exists (postal ballots), that's the final count
  //   - Otherwise, the highest round_no (counting still ongoing)
  // Then get the top 2 candidates from that round.
  const rows = await env.DB.prepare(`
    WITH final_round AS (
      SELECT
        COALESCE(
          (SELECT MAX(round_no) FROM rounds_ac
           WHERE state_code = ? AND ac_no = ? AND round_no = 999),
          (SELECT MAX(round_no) FROM rounds_ac
           WHERE state_code = ? AND ac_no = ? AND round_no != 999)
        ) as rnd
    ),
    ranked AS (
      SELECT
        r.candidate, r.party_abv, r.votes, r.round_no,
        ROW_NUMBER() OVER (ORDER BY r.votes DESC) as rank_in_round
      FROM rounds_ac r, final_round fr
      WHERE r.state_code = ? AND r.ac_no = ?
        AND r.round_no = fr.rnd
    )
    SELECT candidate, party_abv, votes, round_no, rank_in_round
    FROM ranked
    WHERE rank_in_round <= 2
    ORDER BY rank_in_round ASC
  `).bind(stateCode, acNo, stateCode, acNo, stateCode, acNo).all();

  // Get the election for this state
  const election = await env.DB.prepare(
    'SELECT name, sort_date FROM elections WHERE states LIKE ? ORDER BY sort_date DESC LIMIT 1'
  ).bind(`%${stateCode}%`).first();

  const winner = rows.results.find(r => r.rank_in_round === 1) || null;
  const runnerUp = rows.results.find(r => r.rank_in_round === 2) || null;
  const margin = winner && runnerUp ? winner.votes - runnerUp.votes : (winner ? winner.votes : 0);

  const electionData = winner ? [{
    election_name: election?.name || `Round ${rows.results[0]?.round_no || '?'}`,
    sort_date: election?.sort_date || '',
    winner: { candidate: winner.candidate, party: winner.party_abv, votes: winner.votes },
    runner_up: runnerUp ? { candidate: runnerUp.candidate, party: runnerUp.party_abv, votes: runnerUp.votes } : null,
    margin,
  }] : [];

  const lastWinner = winner;

  return jsonResponse({
    constituency: meta?.ac_name || '',
    state: meta?.state_name || '',
    state_code: stateCode,
    ac_no: acNo,
    elections: electionData,
    summary: {
      total_elections: electionData.length,
      parties_won: winner ? [winner.party_abv] : [],
      last_winner: lastWinner ? `${lastWinner.candidate} (${lastWinner.party_abv})` : '',
    },
  });
}
