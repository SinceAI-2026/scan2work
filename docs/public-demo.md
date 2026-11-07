# Hosted browser preview

The hosted preview uses a separate prepared release installation. It does not
expose the author's workstation, editing session, model services or credentials.
The full installation remains writable; hosted mode is explicit and opt-in.

Start the app using the Python environment created by the installer:

```sh
./.venv/bin/python app/scripts/serve_public_demo.py \
  --data-dir ./data/baseline \
  --install-url https://assets.scan2work.app/releases/0.1.1/ \
  --port 8080
```

Use `--check` for a file preflight without starting the server. An optional
`--proposal-url https://…/` links a separately hosted extraction review. With no
proposal URL, the full-review link is hidden. The launcher does not create a
tunnel, alter DNS or provision infrastructure. A process supervisor can run this
foreground command, and an existing HTTPS reverse proxy should forward only the
application's `127.0.0.1:8080` port. Do not proxy SSH, management ports or other
services on the host. The download URL must point to the actual published release.

The launcher sets `VALMET_PUBLIC_DEMO=1`, `VALMET_OFFLINE=1`,
`VALMET_BIND_HOST=127.0.0.1`, `VALMET_DATA_DIR`, `VALMET_INSTALL_URL` and the optional
`VALMET_PROPOSAL_VIEW_URL`. Public mode forces offline operation before importing
configuration that could discover credentials, even if the host inherited
`VALMET_OFFLINE=0`. Its runtime flags are served as a manifest projection; the
packaged scene and provenance are not rewritten.

Visitors can browse the room, search and inspect objects, navigate, view BIM and
AR evidence, and download existing library artifacts. Client-side geometry and
IFC exports remain available. Room edits in the hosted UI are browser-local
simulations. They are not maintenance updates to the shared model.

By default, the server rejects scene-edit, field-job, library-generation and library-removal
requests with `403 public_demo_read_only`. It serves an empty operator log/edit
state and a fresh read-only inspection queue; direct static log, edit and cache
files are unavailable. Directory listings and symlinks escaping the web/data
roots are disabled. Install the downloadable release to save jobs or create new
native object-library exports. The optional Arduino panel is hidden, and all
`/api/live/` endpoints return 403 before importing hardware stores or discovering
their credentials. Installed local mode retains the upstream hardware module.

The copilot uses the prepared synthetic records, keyword routing and local
retrieval. It does not call models. Requests are limited to 16 KiB, questions to
2,000 characters and notes to 4,000 characters; at most two copilot/archive-build
operations run concurrently. A maintenance draft is returned only to that
request with `status=DEMO_DRAFT` and `persisted=false`; no draft or visitor note
is added to shared history. Existing library downloads do not start object
reconstruction jobs.

This is a small demonstration server, not a multi-tenant service. Keep the
dataset isolated and read-only at the filesystem/deployment layer where possible,
use the HTTPS proxy's request/rate controls, and supervise the process. There are
no visitor accounts or server-persisted visitor sessions. Do not add live
operational or confidential data to this public installation.

Validation: `python -m unittest tests.test_public_demo tests.test_installed_server
tests.test_media_ranges tests.test_runtime_compatibility` exercises the hosted
write boundary, stateless drafts, blocked operator history, local copilot,
manifest preservation, launcher preflight, normal writable mode, active-dataset
video ranges and original online/offline runtime compatibility. This is distinct
from browser interaction QA and deployment connectivity checks.

## Optional voice connection

The later [voice/MCP integration](voice-mcp.md) allows an explicitly configured
ElevenLabs widget while backend inference remains offline and shared writes stay
disabled. No voice configuration means no external widget load. The current
0.1.1 package has voice disabled; the newer source requires Wael's cloud-agent
configuration before activation. The separate public MCP service exposes only
reading tools and requires a bearer credential over its encrypted origin.

Wael's explicit `--assistant` option is retained in the follow-up source. It
allows shared field jobs and the original Ilkka widget while scene/library
writes remain blocked. The matching MCP service can opt into the same shared
log with `VALMET_PUBLIC_ASSISTANT=1`; Worker `ASSISTANT="1"` allows only the
additional field-job POST route. Keep all three settings aligned. This opt-in
is separate from explicit read-only `runtime.voice` configuration.
