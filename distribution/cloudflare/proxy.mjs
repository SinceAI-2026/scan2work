// Stream the prepared public demo through HTTPS. No arbitrary proxy targets.
const PUBLIC_POSTS = ['/api/copilot/diagnose', '/api/work-orders/draft'];
export default {
  async fetch(request, env) {
    if (!['GET', 'HEAD', 'POST'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405 });
    }
    const incoming = new URL(request.url);
    const mcp = incoming.pathname === '/mcp';
    if (mcp && !['GET', 'POST'].includes(request.method)) return new Response('Method not allowed', {status: 405});
    if (mcp && !env.MCP_ORIGIN) return new Response('MCP is not configured', {status: 503});
    const posts = env.ASSISTANT === '1' ? [...PUBLIC_POSTS, '/api/field-jobs'] : PUBLIC_POSTS;
    if (request.method === 'POST' && !mcp && !posts.includes(incoming.pathname)) {
      return Response.json({error: 'public_demo_read_only'}, {status: 403});
    }
    const origin = new URL(mcp ? env.MCP_ORIGIN : env.ORIGIN);
    // The ordinary origin contains public scene data. MCP carries a private
    // workspace credential, so it must use its separate encrypted origin.
    if (mcp && (origin.protocol !== 'https:' || origin.username || origin.password)) {
      return new Response('MCP requires a secure origin', {status: 503});
    }
    origin.pathname = incoming.pathname;
    origin.search = mcp ? '' : incoming.search;
    const headers = new Headers();
    if (mcp) {
      const authorization = request.headers.get('authorization') || '';
      if (!/^Bearer [^\s]{1,512}$/i.test(authorization)) return new Response('Unauthorized', {status: 401});
      headers.set('authorization', authorization);
      for (const name of ['mcp-protocol-version', 'mcp-session-id']) {
        if (request.headers.has(name)) headers.set(name, request.headers.get(name));
      }
      headers.set('x-forwarded-for', request.headers.get('cf-connecting-ip') || 'cloudflare');
    }
    for (const key of ['accept', 'content-type', 'range', 'if-range', 'if-none-match', 'if-modified-since']) {
      if (request.headers.has(key)) headers.set(key, request.headers.get(key));
    }
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
