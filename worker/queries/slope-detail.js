import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';

export async function handleSlopeDetail(request, env) {
  const url = new URL(request.url);
  const pathParts = url.pathname.split('/');
  const stateCode = pathParts[pathParts.length - 1];
  if (!stateCode) return jsonResponse({ error: 'state_code required' }, 400);

  const rows = await env.DB.prepare(`
    WITH all_pairs AS (
      SELECT DISTINCT party_abv, ac_no
      FROM rounds_ac
      WHERE state_code = ? AND round_no IN (998, 999)
    ),
    evm AS (
      SELECT party_abv, ac_no, SUM(votes) as votes
      FROM rounds_ac
      WHERE state_code = ? AND round_no = 998
      GROUP BY party_abv, ac_no
    ),
    total AS (
      SELECT party_abv, ac_no, SUM(votes) as votes
      FROM rounds_ac
      WHERE state_code = ? AND round_no = 999
      GROUP BY party_abv, ac_no
    )
    SELECT
      a.party_abv,
      a.ac_no,
      COALESCE(e.votes, 0) as evm_votes,
      COALESCE(t.votes, 0) as total_votes,
      COALESCE(t.votes, 0) - COALESCE(e.votes, 0) as postal_votes
    FROM all_pairs a
    LEFT JOIN evm e ON a.party_abv = e.party_abv AND a.ac_no = e.ac_no
    LEFT JOIN total t ON a.party_abv = t.party_abv AND a.ac_no = t.ac_no
    ORDER BY a.party_abv, a.ac_no
  `).bind(stateCode, stateCode, stateCode).all();

  // Aggregate per party
  const partyMap = new Map();
  for (const row of rows.results) {
    if (!partyMap.has(row.party_abv)) {
      partyMap.set(row.party_abv, {
        party_abv: row.party_abv,
        total_evm: 0,
        total_combined: 0,
        pts: [],
      });
    }
    const p = partyMap.get(row.party_abv);
    p.total_evm += row.evm_votes;
    p.total_combined += row.total_votes;
    p.pts.push({
      ac: row.ac_no,
      evm: row.evm_votes,
      postal: row.postal_votes,
      total: row.total_votes,
    });
  }

  // Get symbols
  const symbolRows = await env.DB.prepare(
    'SELECT abv, symbol_url FROM parties WHERE symbol_url IS NOT NULL'
  ).all();
  const symbols = {};
  for (const r of symbolRows.results) symbols[r.abv] = r.symbol_url;

  // Sort by total EVM descending
  const parties = [...partyMap.values()]
    .sort((a, b) => b.total_evm - a.total_evm)
    .map(p => ({
      party_abv: p.party_abv,
      color: getColor(p.party_abv),
      symbol_url: symbols[p.party_abv] || null,
      total_evm: p.total_evm,
      total_combined: p.total_combined,
      pts: p.pts,
    }));

  return jsonResponse({ state: stateCode, parties });
}
