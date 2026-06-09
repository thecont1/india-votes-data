import { jsonResponse } from '../shared/cors.js';
import { getColor } from '../shared/party-colors.js';
import { getPartiesWithSymbols } from './parties.js';

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

  // Get party symbols (cached in KV)
  const symbolRows = await getPartiesWithSymbols(env);
  const symbols = {};
  for (const r of symbolRows) symbols[r.abv] = r.symbol_url;

  const series = sortedParties
    .filter(party => finalVotes.get(party) > 0)
    .map(party => ({
      party_abv: party,
      party_name: party,
      color: getColor(party),
      symbol_url: symbols[party] || null,
      data: allRounds.map(rn => cumulativeSeries.get(rn)?.get(party) || 0),
    }));

  // Slope detail: per-AC vote breakdowns when rounds are exactly 998+999
  let slopeDetail = null;
  if (allRounds.length === 2 && allRounds.includes(998) && allRounds.includes(999)) {
    const perAcRows = await env.DB.prepare(`
      SELECT ac_no, party_abv, votes, round_no
      FROM rounds_ac
      WHERE state_code = ? AND round_no IN (998, 999) ${electionFilter}
    `).bind(...fBinds).all();

    // Count total ACs from round 999
    const totalACs = new Set(
      perAcRows.results.filter(r => r.round_no === 999).map(r => r.ac_no)
    ).size;
    const threshold = Math.ceil(totalACs * 0.5);

    // Build per-AC maps: party -> Map(ac_no -> votes) for 998 and 999
    const evmByParty = new Map();
    const combinedByParty = new Map();
    for (const row of perAcRows.results) {
      const map = row.round_no === 998 ? evmByParty : combinedByParty;
      if (!map.has(row.party_abv)) map.set(row.party_abv, new Map());
      map.get(row.party_abv).set(row.ac_no, row.votes);
    }

    // Statistics helpers
    const sortedArr = (arr) => [...arr].sort((a, b) => a - b);
    const percentile = (sorted, p) => {
      const idx = (p / 100) * (sorted.length - 1);
      const lo = Math.floor(idx);
      const hi = Math.ceil(idx);
      return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
    };
    const mode = (arr) => {
      if (!arr.length) return null;
      const freq = new Map();
      for (const v of arr) freq.set(v, (freq.get(v) || 0) + 1);
      let best = arr[0], bestCount = 0;
      for (const [v, c] of freq) {
        if (c > bestCount || (c === bestCount && v < best)) { best = v; bestCount = c; }
      }
      return best;
    };

    // Compute per-party stats
    slopeDetail = [];
    for (const party of sortedParties) {
      const evmMap = evmByParty.get(party) || new Map();
      const combMap = combinedByParty.get(party) || new Map();
      if (evmMap.size < threshold) continue;  // 50% threshold

      // Postal = combined - EVM per AC
      const postalArr = [];
      for (const [acNo, combVotes] of combMap) {
        postalArr.push(Math.max(0, combVotes - (evmMap.get(acNo) || 0)));
      }
      const evmArr = [...evmMap.values()];

      const sEvm = sortedArr(evmArr);
      const sPost = sortedArr(postalArr);

      slopeDetail.push({
        party_abv: party,
        count: evmMap.size,
        evm: {
          min: sEvm[0],
          q1: percentile(sEvm, 25),
          mean: Math.round(evmArr.reduce((s, v) => s + v, 0) / evmArr.length),
          median: percentile(sEvm, 50),
          mode: mode(evmArr),
          max: sEvm[sEvm.length - 1],
        },
        postal: {
          min: sPost[0],
          q1: percentile(sPost, 25),
          mean: Math.round(postalArr.reduce((s, v) => s + v, 0) / postalArr.length),
          median: percentile(sPost, 50),
          mode: mode(postalArr),
          max: sPost[sPost.length - 1],
        },
      });
    }
  }

  return jsonResponse({ state, rounds: allRounds, series, slopeDetail }, 200, {
    'Cache-Control': 'public, max-age=30, stale-while-revalidate=60',
  });
}
