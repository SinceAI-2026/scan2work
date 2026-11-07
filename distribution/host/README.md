# Isolated public deployment on the supplied Vast instance

These templates serve the prepared 0.1.1 release. They contain no credentials and
do not create DNS, public storage, tunnels or accounts. Read
`/etc/vast-agents-guide.md` on the actual instance before using them. They require
its existing supervisor, Caddy binary and portal logging helper, plus Python 3.12.

The application is explicitly public and read-only at the server. It uses its
own release installation, logs, supervisor programs and Caddy process. The
instance's management `caddy`, `instance_portal`, `tunnel_manager`, existing jobs,
`/etc/portal.yaml` and shared environment must remain untouched.

| Program | Listener | Purpose |
| --- | --- | --- |
| `scan2work-baseline` | `127.0.0.1:9080` | Original 504-object room |
| `scan2work-review` | `127.0.0.1:9081` | Separate 1,933-region extraction review |
| `scan2work-proxy` | `:10100`, `:10200` | Public HTTP origins for the HTTPS edge |

The assigned host mapping was verified as 10100→20229 and 10200→20250, but must
be checked again on any replacement instance. Caddy's admin API and automatic TLS
are disabled. Its configuration/data homes are separate from management Caddy.
The external edge must supply HTTPS for `scan2work.app` and `review.scan2work.app`.
The download CTA points to
`https://assets.scan2work.app/releases/0.1.1/index.html`; configure and verify that
release independently. These addresses are deployment targets, not proof of
publication.

## Install and verify before activation

Transfer an intact, checksum-verified release folder to
`/workspace/scan2work/incoming-0.1.1/`, including the installers, manifest, checksum
list and all archives. Do not install while a transfer is incomplete.

```sh
python3.12 /workspace/scan2work/incoming-0.1.1/install.py \
  --manifest /workspace/scan2work/incoming-0.1.1/manifest.json \
  --install-dir /workspace/scan2work/releases/0.1.1 --with-extraction
python3.12 /workspace/scan2work/releases/0.1.1/start.py --doctor
```

Copy this directory's `Caddyfile` and `run-service.sh` to
`/workspace/scan2work/deploy/`. Validate before loading supervisor:

```sh
bash -n /workspace/scan2work/deploy/run-service.sh
caddy validate --config /workspace/scan2work/deploy/Caddyfile --adapter caddyfile
```

Confirm all four listener ports are free and there are no existing supervisor
programs using these names. Only then copy `scan2work.conf` to
`/etc/supervisor/conf.d/scan2work.conf`. Register **only these three programs**:

```sh
supervisorctl reread
supervisorctl update scan2work-baseline scan2work-review scan2work-proxy
supervisorctl status scan2work-baseline scan2work-review scan2work-proxy
```

Verify `/data/digital_twin.json` on both loopback ports reports
`runtime.public_demo=true`, `offline=true` and `shared_writes_enabled=false`, with
the expected 504/1,933 catalogs. Verify the baseline review URL, existing source
photos, video byte-range responses, grounded diagnosis and `403` for shared
mutations. Test both origin ports and the final HTTPS domains separately. No
API or model rebuild is required. For stopping or restarting, name only these
three Scan2Work programs; never stop all supervisor services.

This instance reports `workspace_is_volume=false`. Files survive a normal
stop/start but are lost on recycle/destroy. Keep release archives and deployment
templates off-host; the public preview has no irreplaceable visitor records.
Use a new immutable release directory for updates and retain the previous one
until verification passes. The templates' versioned paths must be changed
deliberately when deploying another release.

For an existing deployment, install and verify the new release first, then update
only `run-service.sh` and restart `scan2work-baseline` and `scan2work-review`.
Keep the previous wrapper for rollback. The Caddy process, ports and Workers
need no change when the upstream listeners stay the same.

## Voice assistant (Ilkka) and shared inspection jobs

Off by default. When enabled, scan2work.app shows the Ilkka voice widget, and both the visitor's job card and
the assistant (through `mcp_server.py`) save checks and notes to one shared job log in the baseline data
directory. Scene edits and library writes stay read-only.

1. **Token on the host.** Create `/workspace/scan2work/deploy/mcp.env` (never commit it):
   `FIELD_ATLAS_MCP_TOKEN=<the same value as the ElevenLabs workspace secret>`, with no trailing newline.
2. **Release.** Deploy a release that contains these changes (0.1.2 or later), and update `install_dir` in
   `run-service.sh` to match.
3. **Services.** Install the updated `scan2work.conf` and `run-service.sh`, then reload supervisor. It adds
   `scan2work-mcp` (127.0.0.1:9082) and starts the baseline viewer with `--assistant`.
4. **Caddy.** Install the updated `Caddyfile`: port 10300 forwards to the MCP server. Map a public port to
   10300 the same way 20229 maps to 10100.
5. **Worker.** In `distribution/cloudflare/wrangler.toml`, set `MCP_ORIGIN` to that public port and
   `ASSISTANT = "1"`, then deploy the Worker. `/mcp` is forwarded with the visitor's `Authorization` header;
   every other path still drops credentials.
6. **ElevenLabs.** In Agents → Integrations → MCP servers → "Field Atlas", change the server URL to
   `https://scan2work.app/mcp`. Keep the same secret token.

Check before switching ElevenLabs over (expect 401 without the token, then a JSON-RPC result with it):

```sh
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://scan2work.app/mcp -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
curl -s -X POST https://scan2work.app/mcp -H "Authorization: Bearer $FIELD_ATLAS_MCP_TOKEN" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

The public job log is shared by every visitor. Anyone on the site can start jobs and add notes, limited to
1,000 characters per field. To clear it, stop `scan2work-baseline` and `scan2work-mcp`, write
`{"jobs":{}}` to `data/baseline/field_log.json`, and start both again.
