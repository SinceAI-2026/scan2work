#!/usr/bin/env python3
"""Publish only immutable public scene media to a fresh R2 prefix. Dry run by default.

Runtime JSON, inspection jobs and credentials are deliberately excluded. Credentials
come from an existing AWS CLI profile; no bucket, DNS or access policy is changed.
"""
import argparse
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

EXACT = {'boiler_room_points.bin', 'splat_instance.bin', 'splat_source_rows.npy',
         'source_members.npz', 'floorplan_scan.png',
         'navigation_map_preview.png', 'overlay_walk.mp4'}
MEDIA_DIRS = {'photos', 'walk_frames', 'take_apart', 'library'}
MEDIA_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.mp4', '.webm', '.glb', '.ply', '.gspl'}


def public_files(folder):
    folder = Path(folder).resolve()
    inventory = json.loads((folder / 'asset-inventory.json').read_text())
    expected = {item['path']: item for item in inventory['files']}
    files = []
    for p in sorted(folder.rglob('*')):
        rel = p.relative_to(folder)
        allowed = (rel.as_posix() in EXACT or
                   (rel.parts[0] in MEDIA_DIRS and p.suffix.lower() in MEDIA_EXT))
        if not allowed:
            continue
        if p.is_symlink() or any((folder / Path(*rel.parts[:i])).is_symlink()
                                 for i in range(1, len(rel.parts))):
            raise ValueError(f'Linked media is not publishable: {rel}')
        if not p.is_file() or any(part.startswith('.') for part in rel.parts):
            continue
        item = expected.get(rel.as_posix())
        if not item or item['bytes'] != p.stat().st_size:
            raise ValueError(f'Media not in the verified release inventory: {rel}')
        with p.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if sha != item['sha256']:
            raise ValueError(f'Media checksum changed: {rel}')
        files.append((rel.as_posix(), p, item))
    if 'boiler_room_points.bin' not in {x[0] for x in files}:
        raise ValueError('Incomplete scene payload')
    return files


def publish_rest(args, datasets, prefix):
    """Reuse an explicitly selected Wrangler login without printing its credential.

    The API's 300 MB limit is checked first. Native full-scan exports and review
    thumbnails stay on the CPU; the original complete archive remains in R2.
    """
    config = args.wrangler_config.expanduser()
    token = tomllib.loads(config.read_text())['oauth_token']
    base = f'https://api.cloudflare.com/client/v4/accounts/{args.account_id}/r2/buckets/{args.bucket}/objects'

    def request(url, data=None, content_type='application/json'):
        for attempt in range(5):
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': content_type}
            if data is not None:
                headers['Cache-Control'] = 'public,max-age=31536000,immutable'
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method='PUT' if data is not None else 'GET')
            try:
                with urllib.request.urlopen(req, timeout=180) as response:
                    result = json.load(response)
                if not result.get('success'):
                    raise RuntimeError('R2 rejected the publication request')
                return result.get('result')
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise RuntimeError(f'R2 HTTP {error.code}; no completion marker published') from None
                time.sleep(min(30, 2 ** (attempt + 1)))
        raise RuntimeError('R2 retry limit reached')

    files = [(name, rel, src, item) for name, group in datasets.items() for rel, src, item in group]
    if any(item['bytes'] > 300_000_000 for _, _, _, item in files):
        raise ValueError('REST upload limit exceeded; use the S3 profile')
    if request(base + '?' + urllib.parse.urlencode({'prefix': prefix + '/', 'per_page': 1})):
        raise RuntimeError('Prefix already exists. Use a new version; no overwrite attempted.')
    manifest = {'schema_version': 1, 'version': args.version,
                'datasets': {name: [f[2] for f in group] for name, group in datasets.items()}}
    for index, (name, rel, src, item) in enumerate(files, 1):
        data = src.read_bytes()
        result = request(base + '/' + urllib.parse.quote(f'{prefix}/{name}/data/{rel}', safe='/'),
                         data, mimetypes.guess_type(rel)[0] or 'application/octet-stream')
        if int(result['size']) != item['bytes']:
            raise RuntimeError('Uploaded object size mismatch')
        if index % 50 == 0:
            print(f'Uploaded {index}/{len(files)} verified media files', flush=True)
        time.sleep(.3)  # Stay below the account API rate limit even on small images.
    request(base + '/' + prefix + '/media-manifest.json',
            (json.dumps(manifest, separators=(',', ':')) + '\n').encode())
    print('Published verified media and completion manifest.', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', type=Path, required=True)
    ap.add_argument('--extraction', type=Path, required=True)
    ap.add_argument('--version', required=True)
    ap.add_argument('--account-id', required=True)
    ap.add_argument('--bucket', default='scan2work-downloads')
    ap.add_argument('--profile', default='scan2work-r2')
    ap.add_argument('--wrangler-config', type=Path,
                    help='Use this existing Wrangler login via REST instead of an AWS profile')
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', args.version):
        ap.error('Version must be one safe path segment')
    if not re.fullmatch(r'[0-9a-f]{32}', args.account_id):
        ap.error('Expected a Cloudflare account ID')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+', args.bucket):
        ap.error('Expected an R2 bucket name')
    datasets = {name: public_files(getattr(args, name)) for name in ('baseline', 'extraction')}
    prefix = f'demo/{args.version}'
    cli = ['aws', '--profile', args.profile, '--region', 'auto', '--endpoint-url',
           f'https://{args.account_id}.r2.cloudflarestorage.com']
    report = {name: {'files': len(files), 'bytes': sum(f[2]['bytes'] for f in files)}
              for name, files in datasets.items()}
    print(json.dumps({'prefix': prefix, 'datasets': report, 'execute': args.execute}), flush=True)
    if not args.execute:
        return
    if args.wrangler_config:
        publish_rest(args, datasets, prefix)
        return
    result = subprocess.run(cli + ['s3api', 'list-objects-v2', '--bucket', args.bucket,
        '--prefix', prefix + '/', '--max-keys', '1', '--output', 'json'],
        capture_output=True, text=True, check=True)
    if json.loads(result.stdout).get('KeyCount', 0):
        raise RuntimeError('Prefix already exists. Use a new version; existing assets are never overwritten.')
    # Materialize only verified media, so recursive upload cannot include runtime state.
    with tempfile.TemporaryDirectory(prefix='scan2work-public-media-') as staging:
        root = Path(staging)
        manifest = {'schema_version': 1, 'version': args.version, 'datasets': {}}
        for name, files in datasets.items():
            manifest['datasets'][name] = [f[2] for f in files]
            for rel, src, _ in files:
                dest = root / name / 'data' / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.link(src, dest)
        subprocess.run(cli + ['s3', 'cp', str(root), f's3://{args.bucket}/{prefix}/',
            '--recursive', '--only-show-errors', '--cache-control',
            'public,max-age=31536000,immutable'], check=True)
        # Completion marker is published last, never before all payloads succeeded.
        marker = root / 'media-manifest.json'
        marker.write_text(json.dumps(manifest, separators=(',', ':')) + '\n')
        subprocess.run(cli + ['s3', 'cp', str(marker), f's3://{args.bucket}/{prefix}/media-manifest.json',
            '--only-show-errors', '--content-type', 'application/json', '--cache-control',
            'public,max-age=31536000,immutable'], check=True)
    print('Published verified media; runtime JSON and private state were excluded.', flush=True)


if __name__ == '__main__':
    main()
