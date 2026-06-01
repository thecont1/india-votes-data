import { jsonResponse } from '../shared/cors.js';

export async function handleCandidateHistory(request, env) {
  const url = new URL(request.url);
  const name = url.searchParams.get('name')?.trim();

  if (!name) {
    return jsonResponse({ error: 'name parameter required' }, 400);
  }

  // For each (election_id, state_code, ac_no) the candidate contested,
  // get only the final round. Compute winner's votes and margin.
  const rows = await env.DB.prepare(`
    WITH candidate_contests AS (
      SELECT DISTINCT election_id, state_code, ac_no
      FROM rounds_ac
      WHERE candidate = ?
    ),
    final_rounds AS (
      SELECT
        cc.election_id,
        cc.state_code,
        cc.ac_no,
        COALESCE(
          (SELECT MAX(r2.round_no) FROM rounds_ac r2
           WHERE r2.election_id = cc.election_id AND r2.state_code = cc.state_code AND r2.ac_no = cc.ac_no AND r2.round_no = 999),
          (SELECT MAX(r2.round_no) FROM rounds_ac r2
           WHERE r2.election_id = cc.election_id AND r2.state_code = cc.state_code AND r2.ac_no = cc.ac_no AND r2.round_no != 999)
        ) as final_round
      FROM candidate_contests cc
    ),
    ranked AS (
      SELECT
        r.election_id,
        r.state_code,
        r.ac_no,
        r.ac_name,
        r.candidate,
        r.party_abv,
        r.votes,
        r.round_no,
        s.state_name,
        s.state_code_std,
        p.symbol_url,
        e.name as election_name,
        e.sort_date,
        ROW_NUMBER() OVER (
          PARTITION BY r.election_id, r.state_code, r.ac_no
          ORDER BY r.votes DESC
        ) as rank_in_round,
        MAX(r.votes) OVER (
          PARTITION BY r.election_id, r.state_code, r.ac_no
        ) as winner_votes
      FROM rounds_ac r
      JOIN final_rounds fr
        ON r.election_id = fr.election_id
        AND r.state_code = fr.state_code
        AND r.ac_no = fr.ac_no
        AND r.round_no = fr.final_round
      LEFT JOIN states s ON r.state_code = s.state_code
      LEFT JOIN parties p ON r.party_abv = p.abv
      LEFT JOIN elections e ON r.election_id = e.election_id
    )
    SELECT election_id, election_name, sort_date, state_code, ac_no, ac_name,
           candidate, party_abv, votes, state_name, state_code_std,
           round_no, rank_in_round, winner_votes, symbol_url
    FROM ranked
    WHERE candidate = ?
    ORDER BY sort_date DESC, votes DESC
  `).bind(name, name).all();

  if (!rows.results.length) {
    return jsonResponse({ candidate: name, contests: [], summary: null });
  }

  const contests = rows.results.map(r => {
    const margin = r.winner_votes - r.votes;
    return {
      election_id: r.election_id || '',
      election_name: r.election_name || 'Unknown',
      sort_date: r.sort_date || '',
      state: r.state_name || '',
      state_code: r.state_code,
      state_abbrev: r.state_code_std || r.state_code,
      ac_no: r.ac_no,
      constituency: r.ac_name || '',
      party: r.party_abv || '',
      symbol_url: r.symbol_url || '',
      votes: r.votes,
      rank: r.rank_in_round,
      winner_votes: r.winner_votes,
      margin,
    };
  });

  // Build summary
  const wins = contests.filter(c => c.rank === 1).length;
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
