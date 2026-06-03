// Public API Worker — reads only, serves dashboard
// Ported from server.py (FastAPI) to Cloudflare Workers + D1

import { CORS, jsonResponse, errorResponse } from './shared/cors.js';
import { handleSeatTally } from './queries/seat-tally.js';
import { handleParties } from './queries/parties.js';
import { handleAcRaces } from './queries/ac-races.js';
import { handleRoundwise } from './queries/roundwise.js';
import { handleStatus } from './queries/status.js';
import { handleConstituency } from './queries/constituency.js';
import { handleElections, handleCurrentElection } from './queries/elections.js';
import { handleStates } from './queries/states.js';
import { handleSearch } from './queries/search.js';
import { handleCandidateHistory } from './queries/candidate-history.js';
import { handleConstituencyHistory } from './queries/constituency-history.js';
import { handleDownload } from './queries/download.js';
import { handleTvChannels } from './queries/tv-channels.js';
import { handleByeElections } from './queries/bye-elections.js';

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      // Health check
      if (path === '/health') {
        return jsonResponse({ status: 'ok', worker: 'election-api' });
      }

      // API routes
      if (path === '/api/seat-tally') return handleSeatTally(request, env);
      if (path === '/api/parties') return handleParties(env);
      if (path === '/api/ac-races') return handleAcRaces(request, env);
      if (path === '/api/roundwise') return handleRoundwise(request, env);
      if (path === '/api/status') return handleStatus(request, env);
      if (path === '/api/elections') return handleElections(env);
      if (path === '/api/elections/current') return handleCurrentElection(env);
      if (path === '/api/states') return handleStates(env);
      if (path === '/api/search') return handleSearch(request, env);
      if (path === '/api/candidate-history') return handleCandidateHistory(request, env);
      if (path === '/api/constituency-history') return handleConstituencyHistory(request, env);
      if (path === '/api/download') return handleDownload(request, env);
      if (path === '/api/tv-channels') return handleTvChannels();
      if (path === '/api/bye-elections') return handleByeElections(request, env);

      // Constituency detail: /api/constituency/:state/:ac
      const constMatch = path.match(/^\/api\/constituency\/([^/]+)\/(\d+)$/);
      if (constMatch) {
        return handleConstituency(constMatch[1], constMatch[2], env, request);
      }

      return errorResponse('Not found', 404);
    } catch (err) {
      console.error('Worker error:', err);
      return errorResponse('Internal server error: ' + err.message, 500);
    }
  }
};
