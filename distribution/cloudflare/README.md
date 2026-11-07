# HTTPS browser demo edge

`proxy.mjs` streams the isolated public backend, including video range responses.
For scene content it forwards GET/HEAD and the bounded offline diagnosis/draft
POST endpoints; visitor cookies and authorization are not forwarded. The separate
`/mcp` route accepts authenticated MCP GET/POST only when `MCP_ORIGIN` is set to
an HTTPS origin. Only that route forwards its bearer credential; it strips
query strings, never follows redirects and enforces the same 16 KiB request cap. The backend independently
rejects scene/library mutations. Worker `ASSISTANT="1"` also permits the
field-job POST route, but the backend must separately enable `--assistant`. The fixed `ORIGIN` is deployment configuration, never a
visitor-supplied URL. Each prepared release runs in its own environment; the edge
uses no model keys.

Cloudflare account: `ee189a77308f43cac6e888da636d3604` (the account hosting
`scan2work.app`). After an authorized `wrangler login`, from this directory:

```sh
CLOUDFLARE_ACCOUNT_ID=ee189a77308f43cac6e888da636d3604 wrangler deploy
CLOUDFLARE_ACCOUNT_ID=ee189a77308f43cac6e888da636d3604 wrangler deploy --env review
```

The current origins use the verified provider DNS hostname and preallocated public
ports of the team's existing instance. Cloudflare Workers cannot fetch a raw IP
address directly; keep an origin hostname that resolves to the host. [Host configuration](../host/README.md) runs both apps on loopback with
a separate Caddy proxy. Origin traffic is public HTTP; this deployment serves
only the approved demo data and synthetic records. A future operational service
needs authenticated users and an encrypted private origin connection. No
management or training service is exposed by these Workers.

The instance filesystem is ephemeral on recycle/destroy. Keep published release
archives in R2 and reinstall on a persistent CPU host before retiring the current
instance; no GPU is needed for this viewer. Update the two origin URLs when moving.

`assets.scan2work.app` connects directly to the `scan2work-downloads` R2 bucket.
Use explicit `/releases/<version>/index.html` links; R2 does not provide directory
indexes. Downloads do not depend on the demo host. See
[release publication](../../docs/cloudflare-r2.md) for immutable uploads.

Run `node --test distribution/cloudflare/proxy.test.mjs` from the repository root.
After deploying, verify the manifest, 206 video range response, enabled job
actions, blocked scene/library writes, actual room rendering, navigation, and
the installer download URL. The review site's job writes remain disabled.

## Optional public MCP route

`MCP_ORIGIN` must be a dedicated HTTPS origin serving only the MCP process. Never
point it at the ordinary HTTP scene origin: the connection carries a workspace
bearer credential. The backend verifies that credential independently and serves
read tools by default, or shared inspection tools when the host explicitly
enables `VALMET_PUBLIC_ASSISTANT=1`. With no `MCP_ORIGIN`, `/mcp` returns 503.

The current demo uses a separately supervised Cloudflare quick tunnel to the
loopback MCP service. Restarting that tunnel may change its hostname; update
`MCP_ORIGIN` and re-run authenticated initialization, tool listing and denied-write
checks. This is a demo transport, not a persistent named tunnel. The existing
management tunnel, Caddy and scene proxy do not need changes.

See [voice/MCP setup](../../docs/voice-mcp.md) for the ElevenLabs handoff.

## Arduino fault panel

Set `ARDUINO="1"` and `ARDUINO_ORIGIN` to the dedicated HTTPS tunnel serving the
baseline web process. The backend separately requires `VALMET_PUBLIC_ARDUINO=1`
and a private `LIVE_TOKEN_FILE`. Public board actions also use the shared job
workflow, so enable `ASSISTANT="1"` with matching viewer/MCP settings.

The Worker accepts only GET `board` / `board/events` and POST `board/claim`,
`board/heartbeat`, `board/action` under `/api/live/`. The device's `X-Live-Token`
goes only to the HTTPS claim/heartbeat routes; cookies, bearer credentials and
query strings are discarded. No secret belongs in Worker plaintext vars or
browser runtime data. The existing MCP and ordinary scene origins remain
separate. If the dedicated board tunnel restarts, update `ARDUINO_ORIGIN`.

Verify a device without its token is rejected, public state says offline until
a real heartbeat arrives, and the panel's job controls work. Exercise successful
heartbeat handling against an isolated fixture rather than impersonating a
connected device on the live demo. The board's Python client sets a descriptive
User-Agent because Cloudflare may reject default automated client headers.
