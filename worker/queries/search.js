import { jsonResponse } from '../shared/cors.js';

export async function handleSearch(request, env) {
  const url = new URL(request.url);
  const q = url.searchParams.get('q')?.trim();
  const limit = Math.min(parseInt(url.searchParams.get('limit') || '50'), 50);

  if (!q || q.length < 2) {
    return jsonResponse({ results: { candidate: [], constituency: [] }, query: q });
  }

  // Get current election's sort_date for recency boost
  const currentElection = await env.DB.prepare(
    'SELECT sort_date FROM elections ORDER BY sort_date DESC LIMIT 1'
  ).first();
  const currentDate = currentElection?.sort_date || '';

  // FTS5 trigram search with recency + votes ranking
  // Candidates: current-election gets +1M head start, then sort by votes DESC
  // Constituencies: sort by total_votes DESC
  const rows = await env.DB.prepare(`
    SELECT cs.entity_type, cs.entity_id, cs.name, cs.context, cs.boost,
           cs.votes, cs.total_votes, cs.election_sort,
           highlight(search_fts, 2, '<mark>', '</mark>') as highlighted_name
    FROM search_fts sf
    JOIN candidates_search cs ON sf.rowid = cs.rowid
    WHERE sf.search_fts MATCH ?
      AND cs.entity_type IN ('candidate', 'constituency')
    ORDER BY
      CASE cs.entity_type
        WHEN 'candidate' THEN
          (CASE WHEN cs.election_sort >= ? THEN 1000000 ELSE 0 END) + cs.votes
        WHEN 'constituency' THEN cs.total_votes
        ELSE 0
      END DESC
    LIMIT ?
  `).bind(q, currentDate, limit).all();

  // Group by entity_type
  const grouped = { candidate: [], constituency: [] };
  for (const row of rows.results) {
    const entry = {
      entity_id: row.entity_id,
      name: row.name,
      context: row.context,
      highlighted_name: row.highlighted_name,
      boost: row.boost,
      votes: row.votes,
      total_votes: row.total_votes,
      election_sort: row.election_sort,
    };
    if (grouped[row.entity_type]) {
      grouped[row.entity_type].push(entry);
    }
  }

  return jsonResponse({ results: grouped, query: q });
}
