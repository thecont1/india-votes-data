import { jsonResponse } from '../shared/cors.js';

export async function handleSearch(request, env) {
  const url = new URL(request.url);
  const q = url.searchParams.get('q')?.trim();
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '50'), 50);

  if (!q || q.length < 2) {
    return jsonResponse({ results: { candidate: [], constituency: [] }, query: q });
  }

  // Two separate FTS queries with different sort orders:
  // - Candidates: sort by votes DESC (importance by vote count)
  // - Constituencies: sort by election_sort DESC (most recent first), then total_votes DESC
  const candidateLimit = Math.min(limit, 20);
  const constituencyLimit = Math.min(limit, 10);

  const [candidateRows, constituencyRows] = await Promise.all([
    env.DB.prepare(`
      SELECT cs.entity_type, cs.entity_id, cs.name, cs.context, cs.boost,
             cs.votes, cs.total_votes, cs.election_sort, cs.symbol_url,
             highlight(search_fts, 2, '<mark>', '</mark>') as highlighted_name
      FROM search_fts sf
      JOIN candidates_search cs ON sf.rowid = cs.rowid
      WHERE sf.search_fts MATCH ?
        AND cs.entity_type = 'candidate'
      ORDER BY cs.votes DESC
      LIMIT ?
    `).bind(q, candidateLimit).all(),

    env.DB.prepare(`
      SELECT cs.entity_type, cs.entity_id, cs.name, cs.context, cs.boost,
             cs.votes, cs.total_votes, cs.election_sort, cs.symbol_url,
             highlight(search_fts, 2, '<mark>', '</mark>') as highlighted_name
      FROM search_fts sf
      JOIN candidates_search cs ON sf.rowid = cs.rowid
      WHERE sf.search_fts MATCH ?
        AND cs.entity_type = 'constituency'
      ORDER BY cs.election_sort DESC, cs.total_votes DESC
      LIMIT ?
    `).bind(q, constituencyLimit).all(),
  ]);

  const grouped = { candidate: [], constituency: [] };

  for (const row of candidateRows.results) {
    grouped.candidate.push({
      entity_id: row.entity_id,
      name: row.name,
      context: row.context,
      highlighted_name: row.highlighted_name,
      boost: row.boost,
      votes: row.votes,
      total_votes: row.total_votes,
      election_sort: row.election_sort,
      symbol_url: row.symbol_url || '',
    });
  }

  for (const row of constituencyRows.results) {
    grouped.constituency.push({
      entity_id: row.entity_id,
      name: row.name,
      context: row.context,
      highlighted_name: row.highlighted_name,
      boost: row.boost,
      votes: row.votes,
      total_votes: row.total_votes,
      election_sort: row.election_sort,
      symbol_url: row.symbol_url || '',
    });
  }

  return jsonResponse({ results: grouped, query: q });
}
