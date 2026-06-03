import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';

export async function handleRoundwise(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const electionId = url.searchParams.get('election_id')?.trim();
  if (!state) return jsonResponse({ error: 'state required' }, 400);

  const electionFilter = electionId ? 'AND election_id = ?' : '';
  const binds = electionId ? [state, electionId] : [state];
  const fBinds = electionId ? [state, electionId] : [state];

  // Phase 1: counting rounds (exclude 999)
  const rows = await env.DB.prepare(`
    WITH ranked AS (
      SELECT state_code, ac_no, round_no, party_abv, votes,
             ROW_NUMBER() OVER (
               PARTITION BY state_code, ac_no, round_no
               ORDER BY votes DESC
             ) as rn
      FROM rounds_ac
      WHERE state_code = ? AND round_no != 999 ${electionFilter}
    )
    SELECT ac_no, round_no, party_abv, votes
    FROM ranked WHERE rn = 1
    ORDER BY ac_no, round_no
  `).bind(...binds).all();

  // Build per-AC sorted round data
  const acData = new Map();    // ac_no -> [{round, party, votes}]
  const acRoundKeys = new Map(); // ac_no -> [round_no] sorted
  const allParties = new Set();

  for (const row of rows.results) {
    if (!acData.has(row.ac_no)) {
      acData.set(row.ac_no, []);
      acRoundKeys.set(row.ac_no, []);
    }
    acData.get(row.ac_no).push({ round: row.round_no, party: row.party_abv, votes: row.votes });
    acRoundKeys.get(row.ac_no).push(row.round_no);
    allParties.add(row.party_abv);
  }

  // Distinct target rounds
  const distinctRounds = [...new Set(rows.results.map(r => r.round_no))].sort((a, b) => a - b);

  // Binary search helper
  function bisectRight(arr, target) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] <= target) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  // For each target round, compute party totals
  const countingData = new Map();
  for (const targetRn of distinctRounds) {
    const partyTotals = new Map();
    for (const [acNo, rounds] of acRoundKeys) {
      const idx = bisectRight(rounds, targetRn) - 1;
      if (idx >= 0) {
        const { party, votes } = acData.get(acNo)[idx];
        partyTotals.set(party, (partyTotals.get(party) || 0) + votes);
      }
    }
    countingData.set(targetRn, partyTotals);
  }

  // Phase 2: F round (999)
  const fRows = await env.DB.prepare(`
    WITH ranked AS (
      SELECT r.state_code, r.ac_no,
             r.party_abv, r.votes,
             ROW_NUMBER() OVER (
               PARTITION BY r.state_code, r.ac_no
               ORDER BY r.votes DESC
             ) as rank
      FROM rounds_ac r
      WHERE r.state_code = ? AND r.round_no = 999 ${electionFilter}
    )
    SELECT party_abv, SUM(votes) as total_votes
    FROM ranked WHERE rank = 1
    GROUP BY party_abv
  `).bind(...fBinds).all();

  const fData = new Map();
  for (const row of fRows.results) {
    fData.set(row.party_abv, row.total_votes);
    allParties.add(row.party_abv);
  }

  // Build all_rounds
  const allRounds = [...distinctRounds];
  const hasF = fData.size > 0;
  if (hasF) allRounds.push(999);

  // Cumulative series
  const cumulativeSeries = new Map();
  for (const rn of allRounds) {
    if (rn === 999) {
      cumulativeSeries.set(rn, new Map(fData));
    } else {
      cumulativeSeries.set(rn, new Map(countingData.get(rn) || new Map()));
    }
  }

  // Sort parties by final vote count
  const finalKey = hasF ? 999 : allRounds[allRounds.length - 1];
  const finalVotes = new Map();
  for (const p of allParties) {
    finalVotes.set(p, cumulativeSeries.get(finalKey)?.get(p) || 0);
  }
  const sortedParties = [...allParties].sort((a, b) => finalVotes.get(b) - finalVotes.get(a));

  // Get party symbols
  const symbolRows = await env.DB.prepare(
    'SELECT abv, symbol_url FROM parties WHERE symbol_url IS NOT NULL'
  ).all();
  const symbols = {};
  for (const r of symbolRows.results) symbols[r.abv] = r.symbol_url;

  const series = sortedParties
    .filter(party => finalVotes.get(party) > 0)
    .map(party => ({
      party_abv: party,
      party_name: party,
      color: getColor(party),
      symbol_url: symbols[party] || null,
      data: allRounds.map(rn => cumulativeSeries.get(rn)?.get(party) || 0),
    }));

  return jsonResponse({ state, rounds: allRounds, series });
}
