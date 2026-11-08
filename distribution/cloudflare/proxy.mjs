// Stream the prepared public demo through HTTPS. No arbitrary proxy targets.
const PUBLIC_POSTS = ['/api/copilot/diagnose', '/api/work-orders/draft'];
const BOARD_READS = ['/api/live/board', '/api/live/board/events'];
const BOARD_POSTS = ['/api/live/board/claim', '/api/live/board/heartbeat', '/api/live/board/action'];
const BOARD_AUTH = ['/api/live/board/claim', '/api/live/board/heartbeat'];

// Only immutable scene payloads belong on the public asset origin. Runtime
// manifests, job logs, JSON records and every API remain on the CPU service.
export function isScenePayload(pathname) {
  let path;
  try { path = decodeURIComponent(pathname); } catch { return false; }
  if (path.includes('\\') || path.includes('\0') || path.split('/').some(p => p === '.' || p === '..')) return false;
  if (new Set(['/data/boiler_room_points.bin','/data/splat_instance.bin','/data/splat_source_rows.npy',
    '/data/source_members.npz','/data/floorplan_scan.png',
    '/data/navigation_map_preview.png','/data/overlay_walk.mp4']).has(path)) return true;
  return /^\/data\/(?:photos|walk_frames|take_apart|library)\/.+\.(?:jpg|jpeg|png|webp|mp4|webm|glb|ply|gspl)$/i.test(path);
}

export default {
  async fetch(request, env) {
    if (!['GET', 'HEAD', 'POST'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405 });
    }
    const incoming = new URL(request.url);
    const mcp = incoming.pathname === '/mcp';
    const board = incoming.pathname.startsWith('/api/live/');
    if (board) {
      if (env.ARDUINO !== '1') return Response.json({error: 'public_demo_read_only'}, {status: 403});
      if (!['GET', 'POST'].includes(request.method)) return new Response('Method not allowed', {status: 405});
      const allowed = request.method === 'GET' ? BOARD_READS : BOARD_POSTS;
      if (!allowed.includes(incoming.pathname)) return Response.json({error: 'unknown_live_endpoint'}, {status: 404});
      if (!env.ARDUINO_ORIGIN) return new Response('Arduino is not configured', {status: 503});
    }
    if (mcp && !['GET', 'POST'].includes(request.method)) return new Response('Method not allowed', {status: 405});
    if (mcp && !env.MCP_ORIGIN) return new Response('MCP is not configured', {status: 503});
    const posts = env.ASSISTANT === '1' ? [...PUBLIC_POSTS, '/api/field-jobs'] : PUBLIC_POSTS;
    if (request.method === 'POST' && !mcp && !board && !posts.includes(incoming.pathname)) {
      return Response.json({error: 'public_demo_read_only'}, {status: 403});
    }
    const staticPayload = ['GET','HEAD'].includes(request.method) && env.STATIC_ORIGIN && isScenePayload(incoming.pathname);
    const configuredOrigin = mcp ? env.MCP_ORIGIN : board ? env.ARDUINO_ORIGIN : staticPayload ? env.STATIC_ORIGIN : env.ORIGIN;
    let origin;
    try { origin = new URL(configuredOrigin); } catch { return new Response('Service origin is not configured', {status:503}); }
    // The ordinary origin contains public scene data. MCP carries a private
    // workspace credential, so it must use its separate encrypted origin.
    if ((mcp || board || staticPayload) && (origin.protocol !== 'https:' || origin.username || origin.password)) {
      return new Response('Credentialed services require a secure origin', {status: 503});
    }
    if (!['http:','https:'].includes(origin.protocol) || origin.username || origin.password) return new Response('Invalid service origin', {status:503});
    // A single stable HTTPS CPU origin can route /main, /review and /board to
    // separate listeners. Preserve that configured prefix; visitors cannot set it.
    origin.pathname = origin.pathname.replace(/\/$/, '') + incoming.pathname;
    origin.search = mcp || board ? '' : incoming.search;
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
    if (board && request.method === 'POST' && BOARD_AUTH.includes(incoming.pathname)) {
      const token = request.headers.get('x-live-token') || '';
      if (!/^[^\s]{1,512}$/.test(token)) return new Response('Unauthorized', {status: 401});
      headers.set('x-live-token', token);
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
