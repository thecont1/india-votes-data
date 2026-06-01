import { jsonResponse } from '../shared/cors.js';

export async function handleCandidateHistory(request, env) {
  const url = new URL(request.url);
  const name = url.searchParams.get('name')?.trim();

  if (!name) {
    return jsonResponse({ error: 'name parameter required' }, 400);
  }

  // Find all appearances of this candidate across all rounds
  // Use ROW_NUMBER to determine winner (rank 1 = most votes in that round)
  // Do NOT join elections via LIKE (causes duplication). Fetch separately.
  const rows = await env.DB.prepare(`
    WITH ranked AS (
      SELECT
        r.state_code,
        r.ac_no,
        r.ac_name,
        r.candidate,
        r.party_abv,
        r.votes,
        r.round_no,
        s.state_name,
        ROW_NUMBER() OVER (
          PARTITION BY r.state_code, r.ac_no, r.round_no
          ORDER BY r.votes DESC
        ) as rank_in_round
      FROM rounds_ac r
      LEFT JOIN states s ON r.state_code = s.state_code
      WHERE r.candidate = ?
        AND r.round_no != 999
    )
    SELECT state_code, ac_no, ac_name, candidate, party_abv, votes,
           state_name, round_no, rank_in_round
    FROM ranked
    ORDER BY votes DESC
  `).bind(name).all();

  if (!rows.results.length) {
    return jsonResponse({ candidate: name, contests: [], summary: null });
  }

  // Fetch elections separately and build a lookup: state_code -> sorted elections
  const electionRows = await env.DB.prepare(
    'SELECT election_id, name, states, sort_date FROM elections ORDER BY sort_date DESC'
  ).all();
  const stateElections = new Map();
  for (const e of electionRows.results) {
    let states;
    try { states = JSON.parse(e.states); } catch { states = []; }
    for (const sc of states) {
      if (!stateElections.has(sc)) stateElections.set(sc, []);
      stateElections.get(sc).push({ name: e.name, sort_date: e.sort_date });
    }
  }

  // Match each contest to its election by state_code
  const contests = rows.results.map(r => {
    const elections = stateElections.get(r.state_code) || [];
    // Find the election whose sort_date best matches this round
    // Since we don't have election_id in rounds_ac, assign the first (most recent) election
    // for the state. This is approximate but avoids the LIKE duplication bug.
    const election = elections[0] || null;
    return {
      election_name: election?.name || 'Unknown',
      sort_date: election?.sort_date || '',
      state: r.state_name || '',
      state_code: r.state_code,
      ac_no: r.ac_no,
      constituency: r.ac_name || '',
      party: r.party_abv || '',
      votes: r.votes,
      won: r.rank_in_round === 1,
    };
  });

  // Sort by recency then votes
  contests.sort((a, b) => {
    const d = (b.sort_date || '').localeCompare(a.sort_date || '');
    return d !== 0 ? d : b.votes - a.votes;
  });

  // Build summary
  const wins = contests.filter(c => c.won).length;
  const parties = [...new Set(contests.map(c => c.party))];
  const dates = contests.map(c => c.sort_date).filter(Boolean).sort();

  return jsonResponse({
    candidate: name,
    contests,
    summary: {
      total_contests: contests.length,
      wins,
      parties,
      first_contest: dates[0] || '',
      latest_contest: dates[dates.length - 1] || '',
    },
  });
}
