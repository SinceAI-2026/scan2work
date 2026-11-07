# Isolated public deployment on the supplied Vast instance

These templates serve the prepared 0.1.1 release. They contain no credentials and
do not create DNS, public storage, tunnels or accounts. Read
`/etc/vast-agents-guide.md` on the actual instance before using them. They require
its existing supervisor, Caddy binary and portal logging helper, plus Python 3.12.

The application is explicitly public and read-only by default, with separate
opt-ins for shared inspection jobs and the Arduino panel. It uses its
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
templates and any shared inspection job log off-host before retiring the instance.
Use a new immutable release directory for updates and retain the previous one
until verification passes. The templates' versioned paths must be changed
deliberately when deploying another release.

For an existing deployment, install and verify the new release first, then update
only `run-service.sh` and restart `scan2work-baseline` and `scan2work-review`.
Keep the previous wrapper for rollback. The Caddy process, ports and Workers
need no change when the upstream listeners stay the same.

### Update hosted code without replacing release data

For a code-only fix, unpack a checksum-verified source archive into a **new**
directory under `/workspace/scan2work/services/`. The archive's source revision
and inventory must identify that snapshot. Set `SCAN2WORK_CODE_DIR` to its code
root in each web program's supervisor `environment` line. The wrapper then uses
that code with the existing 0.1.1 Python environment and selected data directory;
the release's `app/`, archives and datasets stay unchanged. Check any new Python
requirements before using this approach.

For the main site's read-only ElevenLabs widget, add these non-secret settings
to **only** `scan2work-baseline` (combine them with its existing `PROC_NAME` and
`SCAN2WORK_CODE_DIR` values):

```ini
VALMET_VOICE_AGENT_ID="agent_5501m6v02k69e2r8fwc0hznmfda5",VALMET_VOICE_AGENT_NAME="Ilkka",VALMET_VOICE_MCP_URL="https://scan2work.app/mcp"
```

Set `VALMET_VOICE_AGENT_ID=""` on the extraction review to leave voice off there.
The MCP bearer token never belongs in these public widget settings. The explicit
agent ID enables the widget without `--assistant`, shared jobs or backend model
calls. Configure the cloud agent separately as described below.

Back up the deployed wrapper and supervisor configuration first. Validate the
wrapper with `bash -n`, then apply only the changed web programs:

```sh
supervisorctl reread
supervisorctl update scan2work-baseline scan2work-review
```

Verify the main manifest's `runtime.voice.enabled=true` and
`runtime.voice.service_mode=read_only` (or `shared_jobs` when the assistant flag
is enabled), the review's voice setting remains false,
and both catalogs retain their expected counts. Reload the public page and check
the visible widget as well as a text interaction; a manifest flag alone is not
proof that ElevenLabs loaded. To roll back, restore both backups and apply those
same two supervisor programs. Do not restart the proxy or MCP tunnel for a web
code update.

## Voice assistant (Ilkka) and shared inspection jobs

Off by default. The dedicated MCP process uses the same prepared baseline as
its viewer. Read-only mode exposes six reading tools; explicitly enabling
`VALMET_PUBLIC_ASSISTANT=1` gives both processes the shared inspection job log
and ten MCP tools. Scene edits and library writes stay read-only in either mode.

1. Deploy code containing these changes (a new release or an immutable source
   snapshot). The 0.1.1 archives remain unchanged; they do not contain this
   shared-assistant integration.
2. Store the random MCP bearer credential in
   `/workspace/scan2work/private/mcp-token`, mode 600, with its directory mode 700.
   The private value belongs in ElevenLabs as a workspace secret too.
3. Install `mcp-service.sh` and `supervisor-mcp.conf`. Substitute the immutable
   code path for `MCP_CODE_SNAPSHOT`; do not register another `scan2work-mcp`
   block. These define the single MCP listener at `127.0.0.1:9090` and its
   separate supervised HTTPS quick tunnel. Existing Caddy/management routing
   stays unchanged; no raw public MCP HTTP port is needed.
4. For shared inspection jobs, create `/workspace/scan2work/deploy/assistant.env`
   with `VALMET_PUBLIC_ASSISTANT=1`. The baseline wrapper and dedicated MCP
   wrapper both read that flag. Set Worker `ASSISTANT = "1"` to permit
   `/api/field-jobs` POSTs at the edge. Leave these off for read-only service.
5. Set Worker `MCP_ORIGIN` to the dedicated **HTTPS** tunnel origin. It forwards
   only `/mcp` credentials there. A quick-tunnel restart may change that
   hostname; update the binding and re-test before declaring the service ready.
6. In ElevenLabs, point the public agent's MCP connection at
   `https://scan2work.app/mcp`, set the matching bearer secret and refresh its
   tool list. See [voice setup](../../docs/voice-mcp.md) for the widget's dynamic
   variable and browser client tool. An explicit voice agent ID is optional;
   Wael's `--assistant` mode retains the original Ilkka integration.

Restart only the named application services whose settings changed. Verify
unauthenticated MCP calls return 401, authenticated initialization/tool calls
work, and write tools are rejected in read-only mode. In shared mode, test a
synthetic job through both the viewer and MCP against a disposable dataset,
including safety-first checks and the 1,000-character field limit. Do not reset
an existing log while validating the deployment.

Shared mode deliberately exposes one demonstration job log to every visitor.
For a fresh scenario use a separate prepared data copy and keep the old log.
Never enable it accidentally by changing the general scene-write setting.

## Public Arduino connection

The hardware module displays synthetic workbook fault status and accepts the
board's A0 touch/button input. It does not control plant equipment. To enable it
on the main hosted app, add this second exact line to `deploy/assistant.env`:

```text
VALMET_PUBLIC_ASSISTANT=1
VALMET_PUBLIC_ARDUINO=1
```

Keep a distinct random device token (at least 32 characters) in
`/workspace/scan2work/private/arduino-token`, mode 600, parent directory mode 700.
The baseline wrapper exports its path as `LIVE_TOKEN_FILE`; the secret is never
embedded in the scene, browser config, public download or repository. The
server refuses public Arduino startup without this credential. Give the same
value privately to the board in its `board.env`, with
`PLANT_API=https://scan2work.app`. It is not the ElevenLabs MCP credential.

Copy `board-tunnel.sh` to the deployment directory, validate it with `bash -n`,
and install `supervisor-board.conf` as a separate supervisor configuration.
Register only `scan2work-board-tunnel`. It provides encrypted access to the
baseline listener, allowing the device token to travel over HTTPS on both
legs. Set Worker `ARDUINO="1"` and `ARDUINO_ORIGIN` to its returned HTTPS URL.
Keep the existing MCP tunnel running. If the board tunnel restarts, its URL may
change and the Worker binding must be updated.

The panel should stay visible with **Board offline** until a real authenticated
heartbeat arrives. Its latest touch input and heartbeat age describe the board;
the plant signal bars remain workbook-derived. Optional Tiger Data / Snowflake
stores show unconfigured until supplied separately. The review process does not
receive Arduino flags or the private token.

For rollback, restore the backed-up web/MCP supervisor configuration, wrappers
and `assistant.env`, then apply only the named services that changed. Restore
the matching Worker bindings and remove only the newly added board tunnel if
rolling back Arduino. Preserve the shared job log, device token and old source
snapshots; never reset operator history as part of rollback.
