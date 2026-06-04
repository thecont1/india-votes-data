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

  // Phase 1: counting rounds (exclude 999) — ALL party votes per AC
  const rows = await env.DB.prepare(`
    SELECT ac_no, round_no, party_abv, votes
    FROM rounds_ac
    WHERE state_code = ? AND round_no != 999 ${electionFilter}
    ORDER BY ac_no, round_no, party_abv
  `).bind(...binds).all();

  // Build per-AC sorted round data
  // acData: ac_no -> Map(round_no -> [{party, votes}])
  // acRoundKeys: ac_no -> [round_no] sorted unique
  const acData = new Map();
  const acRoundKeys = new Map();
  const allParties = new Set();

  for (const row of rows.results) {
    if (!acData.has(row.ac_no)) {
      acData.set(row.ac_no, new Map());
      acRoundKeys.set(row.ac_no, []);
    }
    const acRounds = acData.get(row.ac_no);
    if (!acRounds.has(row.round_no)) {
      acRounds.set(row.round_no, []);
      acRoundKeys.get(row.ac_no).push(row.round_no);
    }
    acRounds.get(row.round_no).push({ party: row.party_abv, votes: row.votes });
    allParties.add(row.party_abv);
  }

  // Sort round keys for each AC
  for (const rounds of acRoundKeys.values()) {
    rounds.sort((a, b) => a - b);
  }

  // Distinct target rounds
  const allRoundNos = new Set();
  for (const rounds of acRoundKeys.values()) {
    for (const rn of rounds) allRoundNos.add(rn);
  }
  const distinctRounds = [...allRoundNos].sort((a, b) => a - b);

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

  // For each target round, compute party totals (all parties, not just leader)
  const countingData = new Map();
  for (const targetRn of distinctRounds) {
    const partyTotals = new Map();
    for (const [acNo, rounds] of acRoundKeys) {
      const idx = bisectRight(rounds, targetRn) - 1;
      if (idx >= 0) {
        const roundNo = rounds[idx];
        for (const { party, votes } of acData.get(acNo).get(roundNo)) {
          partyTotals.set(party, (partyTotals.get(party) || 0) + votes);
        }
      }
    }
    countingData.set(targetRn, partyTotals);
  }

  // Phase 2: F round (999) — ALL party votes
  const fRows = await env.DB.prepare(`
    SELECT party_abv, SUM(votes) as total_votes
    FROM rounds_ac
    WHERE state_code = ? AND round_no = 999 ${electionFilter}
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
