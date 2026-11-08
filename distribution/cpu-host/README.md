# Portable CPU runtime

This package runs the prepared Scan2Work demo on Ubuntu 24.04 with Python 3.12.
It does not provision a VM, create billing resources, change DNS, copy credentials,
start services, or rebuild the scene. The package is intended for a single CPU
host with 2 vCPUs, 4 GiB RAM, a 20 GiB boot disk and a separately retained 30 GiB
data disk. This is initial sizing, not a load-tested capacity guarantee. There is
no CUDA, GPU, torch, model download or runtime inference API requirement.

The browser renders the splats and plans navigation. The CPU serves prepared
assets, deterministic diagnostics, shared inspection jobs, the authenticated MCP
endpoint and Arduino integration. Voice audio processing remains in ElevenLabs.

## Filesystem and processes

| Path | Purpose |
| --- | --- |
| `/opt/scan2work/current` | Symlink to one verified immutable code snapshot |
| `/opt/scan2work/venv` | Python 3.12 environment from `requirements-demo.lock.txt` |
| `/var/lib/scan2work` | Separately mounted, retained data disk |
| `/var/lib/scan2work/data/baseline` | Original 504-object dataset and shared `field_log.json` |
| `/var/lib/scan2work/data/extraction` | Separate 1,933-region dataset; hosted read-only |
| `/etc/scan2work/runtime.env` | Non-secret runtime flags and public voice configuration |
| `/etc/scan2work/mcp-token` | Private MCP bearer credential; no prefix or surrounding quotes |
| `/etc/scan2work/arduino-token` | Private board credential; no prefix or surrounding quotes |

The three supplied systemd services bind to loopback: baseline on 9080, review
on 9081 and MCP on 9090. Main and MCP use the **same** baseline directory and job
log. Its POSIX file lock and atomic replacement synchronize these two processes.
Do not place them on separate replicas or an object-storage mount. Cloud Run
replicas or multiple VMs would first need a transactional shared job-store adapter.

The `scan2work` service user needs read access to code, data and its token files,
and write access to the baseline directory for the job log and lock. Units
restrict writes to that directory; the review service has no writable dataset.
The wrapper defaults assistant/Arduino opt-ins off. The reviewed hosted profile
below enables both on the baseline only; review explicitly clears both and voice.

## Prepare before activation

1. Mount the retained disk at `/var/lib/scan2work`. Confirm it is a distinct mount
   and that VM deletion will not delete it. Do not rely on a boot-disk directory
   with the same name. The provisioning package handles the disk separately.
2. Transfer checksum-verified prepared data. Existing release 0.1.1 contains both
   data archives; use the standard installer to validate extraction and inventories
   in a staging directory before copying its `data/baseline` and `data/extraction`
   into the paths above. Never copy a developer's unrelated job log into production.
3. Unpack a verified current source snapshot into a new `/opt/scan2work/code/<revision>`
   directory and point `current` to it. Keep the previous snapshot for rollback.
   Install CPU binary wheels into the shared environment:

   ```sh
   python3.12 -m venv /opt/scan2work/venv
   /opt/scan2work/venv/bin/pip install --only-binary=:all: \
     -r /opt/scan2work/current/requirements-demo.lock.txt
   /opt/scan2work/venv/bin/pip check
   ```

4. Create the dedicated `scan2work` user and private configuration directory.
   Place the existing credentials through a private transfer, **never** in Git,
   shell history, public storage, the JavaScript widget or this environment file.
   Token files should be owned by `scan2work`, mode 600; the directory should be
   root-owned, group `scan2work`, mode 750. The non-secret `runtime.env` can be
   root-owned, mode 600; systemd reads it before dropping privileges:

   ```dotenv
   VALMET_PUBLIC_ASSISTANT=1
   VALMET_PUBLIC_ARDUINO=1
   VALMET_VOICE_AGENT_ID=agent_5501m6v02k69e2r8fwc0hznmfda5
   VALMET_VOICE_AGENT_NAME=Ilkka
   VALMET_VOICE_MCP_URL=https://scan2work.app/mcp
   ```

   The board need not be connected to start the app. Without real heartbeats its
   panel remains offline; do not manufacture a heartbeat for deployment testing.
   Optional Tiger/Snowflake credentials are not required. They store board events,
   not the shared inspection-job log.

5. Check both datasets and configuration before enabling anything:

   ```sh
   /opt/scan2work/venv/bin/python /opt/scan2work/current/scripts/serve_public_demo.py \
     --data-dir /var/lib/scan2work/data/baseline \
     --install-url https://assets.scan2work.app/releases/0.1.1/index.html --check
   /opt/scan2work/venv/bin/python /opt/scan2work/current/scripts/serve_public_demo.py \
     --data-dir /var/lib/scan2work/data/extraction \
     --install-url https://assets.scan2work.app/releases/0.1.1/index.html --check
   bash -n /opt/scan2work/current/distribution/cpu-host/run-service.sh
   systemd-analyze verify /opt/scan2work/current/distribution/cpu-host/units/*.service
   caddy validate --config /opt/scan2work/current/distribution/cpu-host/Caddyfile --adapter caddyfile
   ```

   Run the installer/launcher thorough doctor too when preparing the data; the
   service preflight checks file structure, membership, evidence and model assets,
   while the thorough doctor also hashes the native PLY.

## HTTPS routing and final activation

Replace `cpu.scan2work.app` in `Caddyfile` with the chosen stable origin hostname.
Point its DNS at the CPU host and allow HTTPS certificate issuance on ports 80/443.
The Python ports stay bound to loopback. Use the distribution's Caddy service;
no Vast portal, `pty` helper, ephemeral quick tunnel or GPU-host proxy is involved.

| Worker origin setting | CPU route | Local process |
| --- | --- | --- |
| Main `ORIGIN=https://cpu.scan2work.app/main` | `/main/*`, prefix removed | 9080 |
| Review `ORIGIN=https://cpu.scan2work.app/review` | `/review/*`, prefix removed | 9081 |
| `MCP_ORIGIN=https://cpu.scan2work.app` | Exact `/mcp`, unchanged | 9090 |
| `ARDUINO_ORIGIN=https://cpu.scan2work.app/board` | `/board/api/live/*`, `/board` removed | 9080 |

The supplied Worker preserves the origin's base path before appending the
incoming path. Deploy this version with the CPU routes; older Worker versions
that overwrite `origin.pathname` do not support these prefixes.
Keep bearer credentials on the encrypted MCP route and board credentials only on
claim/heartbeat. The public URLs and existing ElevenLabs connection remain
`scan2work.app`, `review.scan2work.app` and `scan2work.app/mcp`.

Once the prepared resource plan is approved, copy only these three units into
`/etc/systemd/system`, install the reviewed Caddy configuration, reload systemd,
and enable/start `scan2work-baseline`, `scan2work-review`, `scan2work-mcp` and Caddy.
These instructions do not authorize creating cloud resources by themselves.

Verify loopback first, then the HTTPS origin, then the public Workers: catalogs
504/1,933; 30 mapped records each; main voice/jobs/Arduino enabled and review off;
source video range responses; native washer export unchanged; new navigation and
metrics files; authenticated MCP ten tools with shared jobs; unauthenticated MCP
and board heartbeat denied. Use a disposable dataset for write tests. Browser QA
must check the widget, metrics, route, rendering and the existing local edits.

## State and backups

The destroyed GPU instance had no persistent volume. Its latest shared-job hash
does not match the local logs checked, and no complete matching backup was found.
Restoring scene archives restores geometry, evidence and workbook records; it
**does not recover inspection notes or completed checks**. Bootstrap a fresh demo
job log only if no verified production backup exists, and label that reset in the
deployment report. Do not imply recovered job history or import local QA jobs.
RAM-only board task/event state, temporary fault simulations and draft work orders
also reset. Visitor-local room edits survive in the same browser if the public
origin and dataset IDs remain unchanged.

Keep the data disk independent of VM deletion and make an off-host backup of
`/var/lib/scan2work/data/baseline/field_log.json` plus its checksum and timestamp.
Read or copy the complete file under its sibling `field_log.json.lock` using the
same POSIX lock convention. Do not back up private tokens into the public release
bucket. The cloud provisioning/backup package defines scheduling and private
storage. Test restore to a disposable directory before relying on it.

For code updates, swap only `current` after verifying a new immutable snapshot,
then restart the three application services deliberately. Keep source assets,
credentials, flags and job state unchanged. To roll back, restore the previous
`current` symlink and restart those services; do not restore an old job log over
newer technician work.

## Validation

`python -m unittest tests.test_cpu_host` checks shell/unit contracts and launches
all three real processes against disposable copies of prepared data when those
local datasets are installed. It exercises HTTP/MCP shared jobs only in the
throwaway directory; it sends no board heartbeat and makes no model/provider call.
Set `VALMET_CPU_TEST_BASELINE` and `VALMET_CPU_TEST_EXTRACTION` for alternate local
prepared copies. Linux `systemd-analyze` and Caddy validation must also run on the
actual target; passing portable tests is not a claim that a VM has been deployed.
