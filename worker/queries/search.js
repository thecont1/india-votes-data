import { jsonResponse } from '../shared/cors.js';

export async function handleSearch(request, env) {
  const url = new URL(request.url);
  const q = url.searchParams.get('q')?.trim();
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '20'), 50);

  if (!q || q.length < 2) {
    return jsonResponse({ results: [], query: q });
  }

  // FTS5 trigram search with boost-aware ranking
  const rows = await env.DB.prepare(`
    SELECT cs.entity_type, cs.entity_id, cs.name, cs.context, cs.boost,
           highlight(search_fts, 2, '<mark>', '</mark>') as highlighted_name
    FROM search_fts sf
    JOIN candidates_search cs ON sf.rowid = cs.rowid
    WHERE sf.search_fts MATCH ?
    ORDER BY sf.rank / cs.boost
    LIMIT ?
  `).bind(q, limit).all();

  // Group by entity_type
  const grouped = { party: [], constituency: [], candidate: [] };
  for (const row of rows.results) {
    const entry = {
      entity_id: row.entity_id,
      name: row.name,
      context: row.context,
      highlighted_name: row.highlighted_name,
      boost: row.boost,
    };
    if (grouped[row.entity_type]) {
      grouped[row.entity_type].push(entry);
    }
  }

  return jsonResponse({ results: grouped, query: q });
}
