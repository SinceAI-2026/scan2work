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

The stable HTTPS origin is `cpu.scan2work.app`, hosted by the dedicated Google
Cloud CPU VM. Caddy strips `/main`, `/review` and `/board` before forwarding to
loopback listeners; `/mcp` is preserved. The Worker preserves these configured
path prefixes. See [CPU runtime](../cpu-host/README.md) and
[provisioning and recovery](../gcp/README.md). There are no GPU dependencies or
quick tunnels in this deployment.

The CPU runs the Python APIs and shared job log. A separately retained disk and
private nightly backups protect that mutable state. Cloudflare Workers are the
public edge, not the Python backend. Deleting the CPU VM still interrupts APIs;
retaining its data disk and backups makes recovery possible.

`STATIC_ORIGIN` optionally serves a strict allowlist of heavy scene files from
a versioned R2 prefix. JSON manifests, APIs and job state always use the CPU.
Only GET/HEAD may use R2; visitor credentials are stripped. The public Worker
keeps responses uncached to avoid mixing runtime versions. The R2 payload keys
are immutable. `publish_demo_assets.py` validates each file against the release
inventory and refuses an existing prefix. Its completion manifest is uploaded
last; verify it before switching `STATIC_ORIGIN`.

The frequent render buffers, photos, video and GLBs use R2. Full native PLY
exports and the less-used proposal-review thumbnails remain on the CPU; the
complete native scan is also retained in the immutable release archive. No
geometry or evidence is dropped by this split.

Publish an installed, verified release with an existing AWS R2 profile, or reuse
an explicitly selected Wrangler login (`wrangler whoami` refreshes it first):

```sh
python3 distribution/publish_demo_assets.py \
  --baseline /absolute/path/data/baseline \
  --extraction /absolute/path/data/extraction \
  --version 0.1.1-cpu1 --account-id ee189a77308f43cac6e888da636d3604 \
  --wrangler-config ~/.wrangler/config/default.toml
```

This prints a dry-run summary. Add `--execute` only for the reviewed upload.
The REST transport checks the 300 MB per-object limit and throttles API requests.
The explicit login file stays local; its credential is sent only to Cloudflare's
API and is never printed or archived. Without `--wrangler-config`, the publisher
uses the `scan2work-r2` AWS profile instead. A partial upload requires a fresh
version; never point the Worker at a prefix without its completion manifest.

`assets.scan2work.app` connects directly to the `scan2work-downloads` R2 bucket.
Use explicit `/releases/<version>/index.html` links; R2 does not provide directory
indexes. Downloads do not depend on the demo host. See
[release publication](../../docs/cloudflare-r2.md) for immutable uploads.

Run `node --test distribution/cloudflare/proxy.test.mjs` from the repository root.
After deploying, verify the manifest, 206 video range response, enabled job
actions, blocked scene/library writes, actual room rendering, navigation, and
the installer download URL. The review site's job writes remain disabled.

## Optional public MCP route

`MCP_ORIGIN` must be an HTTPS origin whose `/mcp` route serves only the MCP process. Never
point it at the ordinary HTTP scene origin: the connection carries a workspace
bearer credential. The backend verifies that credential independently and serves
read tools by default, or shared inspection tools when the host explicitly
enables `VALMET_PUBLIC_ASSISTANT=1`. With no `MCP_ORIGIN`, `/mcp` returns 503.

The CPU host exposes the authenticated MCP process at its stable HTTPS `/mcp`
route. It shares the baseline job directory with the main viewer. The existing
ElevenLabs endpoint remains `https://scan2work.app/mcp`.

See [voice/MCP setup](../../docs/voice-mcp.md) for the ElevenLabs handoff.

## Arduino fault panel

Set `ARDUINO="1"` and `ARDUINO_ORIGIN` to the dedicated HTTPS `/board` route serving the
baseline web process. The backend separately requires `VALMET_PUBLIC_ARDUINO=1`
and a private `LIVE_TOKEN_FILE`. Public board actions also use the shared job
workflow, so enable `ASSISTANT="1"` with matching viewer/MCP settings.

The Worker accepts only GET `board` / `board/events` and POST `board/claim`,
`board/heartbeat`, `board/action` under `/api/live/`. The device's `X-Live-Token`
goes only to the HTTPS claim/heartbeat routes; cookies, bearer credentials and
query strings are discarded. No secret belongs in Worker plaintext vars or
browser runtime data. The existing MCP and ordinary scene origins remain
separate. Keep `ARDUINO_ORIGIN` pointed at the `/board` prefix.

Verify a device without its token is rejected, public state says offline until
a real heartbeat arrives, and the panel's job controls work. Exercise successful
heartbeat handling against an isolated fixture rather than impersonating a
connected device on the live demo. The board's Python client sets a descriptive
User-Agent because Cloudflare may reject default automated client headers.
