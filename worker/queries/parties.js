import { jsonResponse } from '../shared/cors.js';

/**
 * Fetch parties with symbol_url from KV cache (1-hour TTL).
 * Used by seat-tally, roundwise, bye-elections.
 */
export async function getPartiesWithSymbols(env) {
  const KV_KEY = 'parties:symbols';
  const cached = await env.KV?.get(KV_KEY, { type: 'json' });
  if (cached) return cached;

  const { results } = await env.DB.prepare(
    'SELECT abv, symbol_url FROM parties WHERE symbol_url IS NOT NULL'
  ).all();

  if (env.KV) {
    await env.KV.put(KV_KEY, JSON.stringify(results), { expirationTtl: 3600 });
  }
  return results;
}

/**
 * Fetch all party info (abv, name, symbol_url) from KV cache.
 * Used by ac-races.
 */
export async function getPartiesFull(env) {
  const KV_KEY = 'parties:full';
  const cached = await env.KV?.get(KV_KEY, { type: 'json' });
  if (cached) return cached;

  const { results } = await env.DB.prepare(
    'SELECT abv, name, symbol_url FROM parties'
  ).all();

  if (env.KV) {
    await env.KV.put(KV_KEY, JSON.stringify(results), { expirationTtl: 3600 });
  }
  return results;
}

export async function handleParties(env) {
  const rows = await env.DB.prepare(`
    SELECT abv, name, chief, founded,
           seats_loksabha, seats_rajyasabha, seats_assembly,
           wikipedia_url, alliance, symbol_url
    FROM parties ORDER BY abv
  `).all();

  const result = {};
  for (const row of rows.results) {
    result[row.abv] = row;
  }
  return jsonResponse({ parties: result }, 200, {
    'Cache-Control': 'public, max-age=300, stale-while-revalidate=600',
  });
}