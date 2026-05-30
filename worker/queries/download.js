import { CORS } from '../shared/cors.js';
import { getElectionById } from './elections.js';
import * as XLSX from 'xlsx';

export async function handleDownload(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const electionId = url.searchParams.get('election_id');

  // Resolve state codes
  let stateCodes = [];
  if (state) stateCodes.push(state);
  if (electionId) {
    const election = await getElectionById(env, electionId);
    if (election && !state) stateCodes = JSON.parse(election.states);
  }

  // Build state filter fragments
  let sfPlain = '';  // for unaliased columns
  let sfR = '';      // for r.state_code
  const sfParams = [];
  if (stateCodes.length > 0) {
    const ph = stateCodes.map(() => '?').join(',');
    sfPlain = `AND state_code IN (${ph})`;
    sfR = `AND r.state_code IN (${ph})`;
    sfParams.push(...stateCodes);
  }

  // Each CTE that uses sf needs its own copy of params
  // CTEs: latest_non999(sfPlain), all_candidates second half(sfR), postal(sfPlain)
  // That's 3 param sets
  const allParams = [...sfParams, ...sfParams, ...sfParams];

  const rows = await env.DB.prepare(`
    WITH latest_non999 AS (
      SELECT state_code, ac_no, MAX(round_no) as max_round
      FROM rounds_ac
      WHERE round_no != 999 ${sfPlain}
      GROUP BY state_code, ac_no
    ),
    all_candidates AS (
      SELECT DISTINCT r.state_code, r.ac_no, r.ac_name,
             r.candidate, r.party_abv
      FROM rounds_ac r
      JOIN latest_non999 lr
        ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no AND r.round_no = lr.max_round
      UNION
      SELECT DISTINCT r.state_code, r.ac_no, r.ac_name,
             r.candidate, r.party_abv
      FROM rounds_ac r
      WHERE r.round_no = 999 ${sfR}
    ),
    evm AS (
      SELECT r.state_code, r.ac_no, r.candidate, r.party_abv, r.votes as evm_votes
      FROM rounds_ac r
      JOIN latest_non999 lr
        ON r.state_code = lr.state_code AND r.ac_no = lr.ac_no AND r.round_no = lr.max_round
    ),
    postal AS (
      SELECT state_code, ac_no, candidate, party_abv, votes as postal_votes
      FROM rounds_ac
      WHERE round_no = 999 ${sfPlain}
    )
    SELECT ac.state_code, st.state_name, ac.ac_no, ac.ac_name,
           ac.candidate, ac.party_abv,
           COALESCE(evm.evm_votes, 0) as evm_votes,
           COALESCE(postal.postal_votes, 0) as postal_votes,
           COALESCE(evm.evm_votes, 0) + COALESCE(postal.postal_votes, 0) as total_votes
    FROM all_candidates ac
    LEFT JOIN states st ON ac.state_code = st.state_code
    LEFT JOIN evm ON ac.state_code = evm.state_code AND ac.ac_no = evm.ac_no
      AND ac.candidate = evm.candidate AND ac.party_abv = evm.party_abv
    LEFT JOIN postal ON ac.state_code = postal.state_code AND ac.ac_no = postal.ac_no
      AND ac.candidate = postal.candidate AND ac.party_abv = postal.party_abv
    ORDER BY ac.state_code, ac.ac_no, total_votes DESC
  `).bind(...allParams).all();

  // Build XLSX
  const headers = [
    'State Code', 'State Name', 'AC No', 'AC Name', 'Candidate',
    'Party', 'EVM Votes', 'Postal Votes', 'Total Votes',
  ];
  const data = rows.results.map(r => [
    r.state_code, r.state_name || '', r.ac_no, r.ac_name,
    r.candidate, r.party_abv, r.evm_votes, r.postal_votes, r.total_votes,
  ]);

  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet([headers, ...data]);
  XLSX.utils.book_append_sheet(wb, ws, 'Data');

  const buf = XLSX.write(wb, { type: 'array', bookType: 'xlsx' });

  // Filename
  const statePart = stateCodes.length === 1 ? `_${stateCodes[0]}` : '';
  const filename = `election-results${statePart}.xlsx`;

  return new Response(buf, {
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Disposition': `attachment; filename="${filename}"`,
      ...CORS,
    },
  });
}
