// Stream the prepared public demo through HTTPS. No arbitrary proxy targets.
// With MCP_ORIGIN set, /mcp goes to the voice assistant's MCP server (mcp_server.py), keeping the Authorization
// bearer token ElevenLabs sends; every other path still drops visitor credentials. With ASSISTANT = "1",
// POST /api/field-jobs is allowed so the viewer's job card can save checks and notes (the server must also run
// with --assistant). Both are off unless set in wrangler.toml.
const PUBLIC_POSTS = ['/api/copilot/diagnose', '/api/work-orders/draft'];
export default {
  async fetch(request, env) {
    if (!['GET', 'HEAD', 'POST'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405 });
    }
    const incoming = new URL(request.url);
    const mcp = incoming.pathname === '/mcp' && !!env.MCP_ORIGIN;
    const posts = env.ASSISTANT === '1' ? [...PUBLIC_POSTS, '/api/field-jobs'] : PUBLIC_POSTS;
    if (request.method === 'POST' && !mcp && !posts.includes(incoming.pathname)) {
      return Response.json({error: 'public_demo_read_only'}, {status: 403});
    }
    const origin = new URL(mcp ? env.MCP_ORIGIN : env.ORIGIN);
    origin.pathname = incoming.pathname;
    origin.search = incoming.search;
    const headers = new Headers();
    const forward = ['accept', 'content-type', 'range', 'if-range', 'if-none-match', 'if-modified-since',
      ...(mcp ? ['authorization', 'mcp-session-id', 'mcp-protocol-version'] : [])];
    for (const key of forward) {
      if (request.headers.has(key)) headers.set(key, request.headers.get(key));
    }
    // mcp_server.py requires the bearer token for any request that arrives through a proxy.
    if (mcp) headers.set('x-forwarded-for', request.headers.get('cf-connecting-ip') || 'cloudflare');
    let body;
    if (request.method === 'POST') {
      if (Number(request.headers.get('content-length') || 0) > 16384) return new Response('Request too large', {status: 413});
      const reader = request.body?.getReader();
      const chunks = []; let size = 0;
      if (reader) for (;;) {
        const {value, done} = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 16384) { await reader.cancel(); return new Response('Request too large', {status: 413}); }
        chunks.push(value);
      }
      body = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) {body.set(chunk, offset); offset += chunk.length;}
    }
    try {
      const response = await fetch(origin, {method: request.method, headers, body, redirect: 'manual'});
      const out = new Response(response.body, response);
      out.headers.set('X-Content-Type-Options', 'nosniff');
      out.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
      out.headers.set('Cache-Control', 'no-store');
      return out;
    } catch {
      return new Response('The demo is restarting. Please try again shortly.', {status: 503, headers: {'Retry-After': '15'}});
    }
  }
};
