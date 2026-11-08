#!/usr/bin/env bash
# GCE startup: prepare retained storage and a private backup timer, not the app.
set -euo pipefail
DISK=/dev/disk/by-id/google-scan2work-data
MOUNT=/var/lib/scan2work
for attempt in $(seq 1 30); do [[ -b "$DISK" ]] && break; sleep 2; done
[[ -b "$DISK" ]] || { echo 'Retained data disk is absent; refusing startup' >&2; exit 1; }
FSTYPE=$(blkid -s TYPE -o value "$DISK" || true)
if [[ -z "$FSTYPE" ]]; then
  # Never format a disk containing a partition table or another recognizable signature.
  [[ -z "$(wipefs --no-act --noheadings --output TYPE "$DISK")" ]] || { echo 'Unrecognized data disk; manual review required' >&2; exit 1; }
  mkfs.ext4 -L scan2work-data "$DISK"
elif [[ "$FSTYPE" != ext4 ]]; then
  echo 'Data disk is not ext4; refusing to reformat it' >&2; exit 1
fi
mkdir -p "$MOUNT"
DISK_UUID=$(blkid -s UUID -o value "$DISK")
if ! grep -Fq "UUID=$DISK_UUID " /etc/fstab; then
  # Without nofail: data must never silently fall back to the disposable boot disk.
  printf 'UUID=%s %s ext4 defaults 0 2\n' "$DISK_UUID" "$MOUNT" >> /etc/fstab
fi
mountpoint -q "$MOUNT" || mount "$MOUNT"
[[ "$(findmnt -n -o UUID --target "$MOUNT")" == "$DISK_UUID" ]] || { echo 'Wrong filesystem mounted at data path' >&2; exit 1; }
id scan2work >/dev/null 2>&1 || useradd --system --no-create-home --home-dir "$MOUNT" --shell /usr/sbin/nologin scan2work
install -d -o scan2work -g scan2work -m 0750 "$MOUNT/data" "$MOUNT/data/baseline" "$MOUNT/data/extraction"
install -d -m 0755 /usr/local/lib/scan2work
cat > /usr/local/lib/scan2work/backup-state.py <<'PYTHON'
#!/usr/bin/env python3
"""Copy only the shared job log to GCS; never collect tokens, env files or code."""
import datetime
import fcntl
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.parse
import urllib.request
import uuid

ROOT = Path('/var/lib/scan2work/data/baseline')
LOG = ROOT / 'field_log.json'
META = 'http://metadata.google.internal/computeMetadata/v1/'

def metadata(path):
    req = urllib.request.Request(META + path, headers={'Metadata-Flavor': 'Google'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read().decode().strip()

def main():
    if not LOG.exists():
        print('No shared job log yet; no backup required.')
        return
    # The app also locks this exact path before replacing its JSON file.
    with (ROOT / 'field_log.json.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        payload = LOG.read_bytes()
        json.loads(payload)  # Refuse a malformed backup instead of recording success.
    now = datetime.datetime.now(datetime.timezone.utc)
    manifest = json.dumps({'created_utc': now.isoformat(), 'files': {
        'baseline/field_log.json': {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}
    }}).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as tar:
        for name, data in [('baseline/field_log.json', payload), ('manifest.json', manifest)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            info.mtime = int(now.timestamp())
            tar.addfile(info, io.BytesIO(data))
    bucket = metadata('instance/attributes/scan2work-backup-bucket')
    token = json.loads(metadata('instance/service-accounts/default/token'))['access_token']
    name = now.strftime('jobs/%Y/%m/%d/%H%M%S-') + uuid.uuid4().hex + '.tar.gz'
    query = urllib.parse.urlencode({'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'})
    url = 'https://storage.googleapis.com/upload/storage/v1/b/' + urllib.parse.quote(bucket, safe='') + '/o?' + query
    req = urllib.request.Request(url, data=archive.getvalue(), method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'application/gzip'})
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.load(response)
    print('Backed up shared job log:', result['name'], 'bytes:', len(payload))

if __name__ == '__main__':
    main()
PYTHON
chmod 0755 /usr/local/lib/scan2work/backup-state.py
cat > /etc/systemd/system/scan2work-state-backup.service <<'UNIT'
[Unit]
Description=Back up Scan2Work shared job log to private Cloud Storage
Wants=network-online.target
After=network-online.target
RequiresMountsFor=/var/lib/scan2work

[Service]
Type=oneshot
User=scan2work
Group=scan2work
ExecStart=/usr/bin/python3 /usr/local/lib/scan2work/backup-state.py
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/scan2work/data/baseline
UNIT
cat > /etc/systemd/system/scan2work-state-backup.timer <<'UNIT'
[Unit]
Description=Nightly Scan2Work shared job log backup

[Timer]
OnCalendar=*-*-* 03:15:00 UTC
RandomizedDelaySec=600
Persistent=true
Unit=scan2work-state-backup.service

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now scan2work-state-backup.timer
echo 'Retained disk and nightly backup timer prepared. Application is not installed or activated.'
