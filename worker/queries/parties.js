import { jsonResponse } from '../shared/cors.js';

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
  return jsonResponse({ parties: result });
}
