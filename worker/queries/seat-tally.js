import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';
import { getPartiesWithSymbols } from './parties.js';
import { getElectionById } from './elections.js';

export async function handleSeatTally(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state') || '';
  const electionId = url.searchParams.get('election_id') || '';

  // Build state filter
  let sf = '';
  const params = [];
  const params2 = [];  // for second CTE copy

  if (electionId) {
    const election = await getElectionById(env, electionId);
    if (election) {
      const stateList = JSON.parse(election.states);
      if (stateList.length === 1) {
        sf = 'AND r.state_code = ?';
        params.push(stateList[0]);
        params2.push(stateList[0]);
      } else {
        const ph = stateList.map(() => '?').join(',');
        sf = `AND r.state_code IN (${ph})`;
        params.push(...stateList);
        params2.push(...stateList);
      }
    }
  } else if (state) {
    sf = 'AND r.state_code = ?';
    params.push(state);
    params2.push(state);
  }

  // Main seat-tally query (mirrors server.py CTEs)
  const query = `
    WITH latest_rounds AS (
      SELECT lr.state_code, lr.ac_no, lr.max_round
      FROM latest_rounds_ac lr
      JOIN constituency_status cs
        ON lr.state_code = cs.state_code AND lr.ac_no = cs.ac_no
      WHERE cs.status = 'DONE'
    ),
    ac_winners AS (
      SELECT state_code, ac_no, winner_abv
      FROM (
        SELECT lr.state_code, lr.ac_no, p.abv as winner_abv,
               ROW_NUMBER() OVER (
                 PARTITION BY lr.state_code, lr.ac_no
                 ORDER BY r.votes DESC
               ) as rn
        FROM rounds_ac r
        JOIN latest_rounds lr
          ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
          AND r.round_no = lr.max_round
        JOIN parties p ON r.party_abv = p.abv
        WHERE 1=1 ${sf}
      ) WHERE rn = 1
    ),
    ac_totals AS (
      SELECT r.state_code, r.ac_no, SUM(r.votes) as total_votes
      FROM rounds_ac r
      JOIN latest_rounds lr
        ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
        AND r.round_no = lr.max_round
      GROUP BY r.state_code, r.ac_no
    ),
    party_best AS (
      SELECT state_code, ac_no, party_abv, party_name,
             votes, ac_declared, winner_abv, total_votes
      FROM (
        SELECT r.state_code, r.ac_no,
               p.abv as party_abv, p.name as party_name,
               r.votes, cs.won as ac_declared,
               aw.winner_abv, at.total_votes,
               ROW_NUMBER() OVER (
                 PARTITION BY r.state_code, r.ac_no, p.abv
                 ORDER BY r.votes DESC
               ) as rn
        FROM rounds_ac r
        JOIN latest_rounds lr
          ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no
          AND r.round_no = lr.max_round
        JOIN constituency_status cs
          ON r.state_code = cs.state_code AND r.ac_no = cs.ac_no
        JOIN parties p ON r.party_abv = p.abv
        JOIN ac_totals at
          ON r.state_code = at.state_code AND r.ac_no = at.ac_no
        JOIN ac_winners aw
          ON r.state_code = aw.state_code AND r.ac_no = aw.ac_no
        WHERE 1=1 ${sf}
      ) WHERE rn = 1
    )
    SELECT
      party_abv,
      MAX(party_name) as party_name,
      SUM(CASE WHEN party_abv = winner_abv AND ac_declared = 1 THEN 1 ELSE 0 END) as won_seats,
      SUM(CASE WHEN party_abv = winner_abv AND ac_declared = 0 THEN 1 ELSE 0 END) as leading_seats,
      SUM(CASE WHEN party_abv != winner_abv AND ac_declared = 1
               AND votes * 6 >= total_votes THEN 1 ELSE 0 END) as lost_no_deposit,
      SUM(CASE WHEN party_abv != winner_abv AND ac_declared = 1
               AND votes * 6 < total_votes THEN 1 ELSE 0 END) as lost_deposit,
      SUM(votes) as total_votes
    FROM party_best
    GROUP BY party_abv
    ORDER BY won_seats DESC
  `;

  // Flatten params (each appears twice in the query)
  const allParams = [...params, ...params2];
  const rows = await env.DB.prepare(query).bind(...allParams).all();

  // Check if won status is populated
  let checkQ = 'SELECT SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) as won_count FROM constituency_status';
  const checkParams = [];
  if (state) {
    checkQ += ' WHERE state_code = ?';
    checkParams.push(state);
  }
  const checkRow = await env.DB.prepare(checkQ).bind(...checkParams).first();
  const hasWonData = (checkRow?.won_count || 0) > 0;

  // Get party symbols (cached in KV)
  const symbolRows = await getPartiesWithSymbols(env);
  const symbols = {};
  for (const r of symbolRows) symbols[r.abv] = r.symbol_url;

  const result = rows.results.map(row => {
    let won = row.won_seats;
    let leading = row.leading_seats;
    let lostNoDep = row.lost_no_deposit;
    let lostDep = row.lost_deposit;
    if (!hasWonData) {
      won += leading;
      leading = 0;
      lostNoDep = 0;
      lostDep = 0;
    }
    return {
      party_abv: row.party_abv,
      party_name: row.party_name || row.party_abv,
      won, leading,
      total: won + leading,
      lost_no_deposit: lostNoDep,
      lost_deposit: lostDep,
      total_votes: row.total_votes || 0,
      color: getColor(row.party_abv),
      symbol_url: symbols[row.party_abv] || null,
    };
  });

  // Majority calculation
  let majority = null;
  if (state) {
    const sRow = await env.DB.prepare(
      'SELECT assembly_seats FROM states WHERE state_code = ?'
    ).bind(state).first();
    if (sRow) majority = Math.floor(sRow.assembly_seats / 2) + 1;
  } else if (electionId) {
    // Sum assembly_seats for states in this election only
    const election = await getElectionById(env, electionId);
    if (election) {
      const stateList = JSON.parse(election.states);
      const ph = stateList.map(() => '?').join(',');
      const mRow = await env.DB.prepare(
        `SELECT SUM(assembly_seats) as total_seats FROM states WHERE state_code IN (${ph})`
      ).bind(...stateList).first();
      if (mRow?.total_seats) majority = Math.floor(mRow.total_seats / 2) + 1;
    }
  } else {
    const mRow = await env.DB.prepare(`
      SELECT SUM(s.assembly_seats) as total_seats
      FROM states s
      JOIN (SELECT DISTINCT state_code FROM constituency_status WHERE status = 'DONE') cs
        ON s.state_code = cs.state_code
    `).first();
    if (mRow?.total_seats) majority = Math.floor(mRow.total_seats / 2) + 1;
  }

  return jsonResponse({ parties: result, majority, updated_at: new Date().toISOString() }, 200, {
    'Cache-Control': 'public, max-age=30, stale-while-revalidate=60',
  });
}
