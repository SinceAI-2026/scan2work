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
