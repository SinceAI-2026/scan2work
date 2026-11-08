# Small CPU origin for Scan2Work

This is a fresh infrastructure plan for the prepared demo. It does not rebuild
the scan, run models, transfer credentials, install the app or change public DNS.
The browser renders the Gaussians; serving these prepared assets needs no GPU.
The former H100 origin also ran the Python API and MCP, so removing it removed
those services even while the Cloudflare Worker remained deployed.

## Resources and boundaries

| Resource | Configuration |
| --- | --- |
| Dedicated project | Explicit `--project`; never the gcloud default project |
| VM | One `scan2work-cpu`, Ubuntu 24.04, `e2-medium` (4 GiB), `europe-north1-b`; standard/non-Spot, no GPU |
| Boot disk | 20 GiB `pd-standard`; disposable installation/code |
| State disk | Separate 30 GiB `scan2work-data`, `pd-standard`, attached with `auto-delete=no`; mounted at `/var/lib/scan2work` |
| Network | Dedicated VPC/subnet; public TCP 80/443; SSH only from Google's IAP range; no raw API/MCP ports |
| Access | OS Login, project SSH keys blocked, explicit human operator; VM deletion protection |
| Runtime identity | Dedicated `scan2work-runtime` service account; only `storage.objectCreator` on its backup bucket; no project-wide Editor or default Compute identity |
| Backup | Private regional GCS bucket; nightly shared-job snapshot; 30-day retention, delete lifecycle after 45 days, 7-day soft delete |

The disk can survive VM deletion, but it is not an off-VM backup by itself. The
nightly backup copies **only** `data/baseline/field_log.json`, with the app's lock
held while reading, plus a checksum manifest. Tokens, environment files and
private configuration are never collected. Prepared release assets remain in
R2; preserve a separate recoverable copy of the private credentials. No backup
can recover shared jobs that were only on the destroyed server.

The app layout expected by [CPU hosting](../cpu-host/README.md) is:

```text
/opt/scan2work/current                   # immutable code snapshot symlink
/opt/scan2work/venv                      # runtime Python environment
/var/lib/scan2work/data/baseline         # original 504-object dataset + shared log
/var/lib/scan2work/data/extraction       # separate 1,933-region review dataset
/etc/scan2work/runtime.env               # private configuration; not metadata
/etc/scan2work/mcp-token                 # private MCP credential
/etc/scan2work/arduino-token             # distinct private board credential
```

`startup.sh` formats only a disk with no recognized filesystem/signature,
mounts the data disk, creates the service user and installs the backup timer.
It refuses a non-ext4 disk rather than reformatting it. The mount is mandatory:
services must not silently write state to the boot disk when it is absent.

## Review, then explicitly apply

Requirements: authenticated `gcloud`, Python 3, permission to create the
dedicated project (or use an explicitly selected existing one), link its billing
account, enable APIs, create Compute/Storage resources and change their IAM.
The operator receives OS Admin Login and IAP tunnel access in this project, and
permission to use this runtime service account for SSH. The provisioning identity
needs appropriate project/billing administration permissions; the VM does not.

The default is **plan only, with no cloud calls**. Choose globally available
project and bucket names. The following identifiers are examples, not embedded
account defaults:

```sh
bash distribution/gcp/provision.sh \
  --project scan2work-2026-demo \
  --operator user:YOUR_EMAIL \
  --create-project --link-billing --billing-account YOUR_BILLING_ACCOUNT \
  --budget-amount 45 --budget-currency EUR
```

After reviewing the printed plan, repeat it with `--apply`. For an existing,
dedicated, billing-enabled project, omit `--create-project --link-billing`.
`--billing-account` is still needed when creating a budget alert. Organization
projects can specify `--folder ID` or `--organization ID`. `--zone` permits
another zone in Finland if capacity requires it, without changing VM size.

Before paid resource allocation, the script checks the E2 machine type and
regional quotas for CPUs, VM count, IPs and disk capacity. Actual zonal capacity
is determined when Google accepts the VM creation request. This script does
not silently upgrade to a more expensive machine or use a different project.

This is deliberately not an updater: existing named resources cause a stop.
If a command fails after some resources were created, inspect that partial
installation and resume only the outstanding commands from the printed plan.
**Do not delete the retained data disk or backup bucket to rerun the script.**
For later app releases, update code and services using the CPU-host instructions.

## Verify infrastructure before routing visitors

```sh
gcloud --project=YOUR_PROJECT compute ssh scan2work-cpu \
  --zone=europe-north1-b --tunnel-through-iap
```

On the VM:

```sh
sudo journalctl -u google-startup-scripts.service --no-pager -n 80
findmnt /var/lib/scan2work
systemctl list-timers scan2work-state-backup.timer
```

Confirm the disk's attachment has `autoDelete: false` using `gcloud compute
instances describe`. Install the CPU services, restored data and TLS before
changing Worker origins. `cpu.scan2work.app` will terminate TLS and route
`/main/`, `/review/`, `/board/` and `/mcp`; those paths belong to the CPU-host
and Worker configuration, not this provisioning script.

After a legitimate demo job exists, run and inspect its first backup:

```sh
sudo systemctl start scan2work-state-backup.service
sudo journalctl -u scan2work-state-backup.service --no-pager -n 30
```

The runtime identity can create a new backup but cannot list, download,
overwrite or delete older objects. Use the provisioning/operator identity with
appropriate Storage access to list and download a selected backup. Verify its
manifest checksum and JSON, then stop baseline and MCP together before
restoring `baseline/field_log.json` into the shared data directory. Restore
ownership to `scan2work:scan2work`; retain the replaced log separately. Test a
restore to a disposable directory first. Backups are private recovery files,
not files to upload into public R2 downloads.

The timer runs daily at 03:15 UTC with up to ten minutes of jitter, and catches
up after downtime. A nightly schedule permits up to roughly a day of job loss;
run a manual backup before maintenance. Check timer failures operationally;
there is no external backup-failure notification configured by this script.

## Cost and retention

Budget approximately **US$35–45/month** for this small Finland origin at low API
traffic, subject to the selected account's actual regional prices. The official
Iowa example is $24.46/month for the VM plus about $2 for 50 GiB standard disks
and $3.65 for one attached IPv4 (730 hours). Finland pricing, traffic, backups,
tax and voice/LLM usage are additional considerations. Public scan/video
downloads should remain in R2 to avoid Google internet egress charges.
[Compute prices](https://cloud.google.com/products/compute/pricing/general-purpose?hl=en),
[disk prices](https://cloud.google.com/compute/disks-image-pricing),
[IPv4/network prices](https://cloud.google.com/vpc/network-pricing),
[R2 prices](https://developers.cloudflare.com/r2/pricing/).

`--budget-amount 45 --budget-currency EUR` creates project-filtered monthly alerts at 50%, 80% and 100%
for the billing account's default alert recipients. The currency must match the
billing account; this account uses EUR (also the script default). **An alert is not a spending
cap and does not stop services.** Budget creation requires billing budget
permissions. Stop the CPU VM to stop compute charges; disks and a reserved IP
still cost money. The bucket retention policy is not permanently locked; it
prevents deleting fresh backups for 30 days. Its lifecycle schedules deletion
after 45 days, with another seven days of soft-delete recovery/storage billing.

## Why this uses a VM first

The existing job store relies on POSIX file locks shared between viewer and
MCP. Cloud Run and Cloudflare Containers have ephemeral writable filesystems;
GCS FUSE does not provide those locks. A later serverless migration should move
shared jobs to a transactional store first. The small CPU origin preserves the
current behavior while separating compute, durable state and downloadable scans.
[Cloud Run filesystem](https://docs.cloud.google.com/run/docs/container-contract),
[Cloudflare Container storage](https://developers.cloudflare.com/containers/faq/),
[GCS FUSE limitations](https://docs.cloud.google.com/storage/docs/cloud-storage-fuse/overview).
