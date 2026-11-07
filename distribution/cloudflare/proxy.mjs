// Stream the prepared public demo through HTTPS. No arbitrary proxy targets.
export default {
  async fetch(request, env) {
    if (!['GET', 'HEAD', 'POST'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405 });
    }
    const incoming = new URL(request.url);
    if (request.method === 'POST' && !['/api/copilot/diagnose', '/api/work-orders/draft'].includes(incoming.pathname)) {
      return Response.json({error: 'public_demo_read_only'}, {status: 403});
    }
    const origin = new URL(env.ORIGIN);
    origin.pathname = incoming.pathname;
    origin.search = incoming.search;
    const headers = new Headers();
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
