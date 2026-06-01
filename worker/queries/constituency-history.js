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

  // Get all rounds for this constituency, ranked by votes
  // Do NOT join elections via LIKE (causes duplication). Fetch separately.
  const rows = await env.DB.prepare(`
    WITH ranked AS (
      SELECT
        r.state_code, r.ac_no, r.ac_name,
        r.round_no, r.candidate, r.party_abv, r.votes,
        ROW_NUMBER() OVER (
          PARTITION BY r.state_code, r.ac_no, r.round_no
          ORDER BY r.votes DESC
        ) as rank_in_round
      FROM rounds_ac r
      WHERE r.state_code = ? AND r.ac_no = ? AND r.round_no != 999
    )
    SELECT candidate, party_abv, votes, round_no, rank_in_round
    FROM ranked
    WHERE rank_in_round <= 2
    ORDER BY round_no DESC, rank_in_round ASC
  `).bind(stateCode, acNo).all();

  // Fetch elections for this state separately
  const electionRows = await env.DB.prepare(
    'SELECT name, sort_date FROM elections WHERE states LIKE ? ORDER BY sort_date DESC'
  ).bind(`%${stateCode}%`).all();

  // Group rows into elections by round_no
  const roundMap = new Map();
  for (const row of rows.results) {
    if (!roundMap.has(row.round_no)) {
      roundMap.set(row.round_no, { winner: null, runner_up: null });
    }
    const round = roundMap.get(row.round_no);
    const entry = {
      candidate: row.candidate,
      party: row.party_abv,
      votes: row.votes,
    };
    if (row.rank_in_round === 1) {
      round.winner = entry;
    } else if (row.rank_in_round === 2) {
      round.runner_up = entry;
    }
  }

  // Build elections list: match round_no to election (oldest round = oldest election)
  // Sort round numbers ascending = oldest first, then reverse for display (newest first)
  const sortedRounds = [...roundMap.entries()].sort((a, b) => a[0] - b[0]);
  const elections = [];
  const electionList = [...electionRows.results].reverse(); // oldest first to match ascending round_no

  for (let i = 0; i < sortedRounds.length; i++) {
    const [roundNo, { winner, runner_up }] = sortedRounds[i];
    const election = electionList[i] || null;
    const margin = winner && runner_up ? winner.votes - runner_up.votes : (winner ? winner.votes : 0);
    elections.push({
      election_name: election?.name || `Round ${roundNo}`,
      sort_date: election?.sort_date || '',
      winner,
      runner_up,
      margin,
    });
  }

  // Reverse so newest election is first
  elections.reverse();

  // Build summary
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
