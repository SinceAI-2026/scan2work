#!/usr/bin/env python3
"""Launch an installed Scan2Work release; --doctor validates files and startup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse
from urllib.request import urlopen
import webbrowser
import zipfile


class LaunchError(RuntimeError):
    pass


def local_file(folder, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise LaunchError('Invalid packaged file path.')
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or ':' in relative:
        raise LaunchError(f'Packaged path escapes its dataset: {relative}')
    result = (Path(folder) / str(path)).resolve()
    if Path(folder).resolve() not in result.parents or not result.is_file():
        raise LaunchError(f'Missing packaged file: {relative}')
    return result


def sha256(file):
    digest = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inventory(folder, inventory_path, thorough=True):
    inventory = json.loads(local_file(folder, inventory_path).read_text())
    if inventory.get('schema_version') != 1 or not isinstance(inventory.get('files'), list):
        raise LaunchError('Invalid asset inventory.')
    for entry in inventory['files']:
        # These are legitimate user edits, not immutable release files.
        name = entry['path']
        if name in {'scene_edits.json', 'field_log.json'} or name.startswith('library/'):
            continue
        file = local_file(folder, name)
        if file.stat().st_size != entry['bytes']:
            raise LaunchError(f'Packaged file size changed: {name}')
        if thorough and sha256(file) != entry['sha256']:
            raise LaunchError(f'Packaged file checksum changed: {name}')


def evidence_files(asset):
    photo = asset.get('photo')
    if isinstance(photo, str):
        yield photo
    elif isinstance(photo, dict) and photo.get('url'):
        yield photo['url']
    for evidence in asset.get('evidence') or []:
        for key in ('imageUrl', 'cropUrl', 'renderedCropUrl', 'renderedcropUrl'):
            if evidence.get(key):
                yield evidence[key]


def check_evidence(folder, asset):
    for reference in evidence_files(asset):
        url = urlparse(reference)
        if url.scheme or url.netloc:
            continue  # external evidence is not downloaded or required for offline startup
        path = unquote(url.path).removeprefix('./')
        if path.startswith('data/'):
            local_file(folder, path[5:])


def check_take_apart(folder):
    """Optional illustrated models are separate from native source membership."""
    root = Path(folder) / 'take_apart'
    index = root / 'index.json'
    if not index.is_file():
        return
    doc = json.loads(local_file(root, 'index.json').read_text())
    if not isinstance(doc, dict):
        raise LaunchError('Invalid optional take-apart model index.')
    for section in ('models', 'ai3d'):
        entries = doc.get(section, {})
        if not isinstance(entries, dict):
            raise LaunchError('Invalid optional take-apart model entries.')
        for entry in entries.values():
            if not isinstance(entry, dict):
                raise LaunchError('Invalid optional take-apart model entry.')
            file = local_file(root, entry.get('file'))
            with file.open('rb') as stream:
                header = stream.read(12)
            if (len(header) != 12 or struct.unpack('<4sII', header) != (b'glTF', 2, file.stat().st_size)
                    or file.stat().st_size < 20):
                raise LaunchError(f'Invalid optional GLB model: {entry.get("file")}')


def check_dataset(folder, thorough=True):
    twin = json.loads(local_file(folder, 'digital_twin.json').read_text())
    assets = twin.get('assets')
    if not isinstance(assets, list) or not assets:
        raise LaunchError('Scene has no asset catalog.')
    for key in ('id', 'asset_id'):
        values = [asset.get(key) for asset in assets]
        if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
            raise LaunchError(f'Scene contains missing or duplicate {key} values.')
    if not isinstance(twin.get('navigation', {}).get('grid_meta'), dict):
        raise LaunchError('Scene is missing its navigation grid metadata.')
    splats = local_file(folder, 'boiler_room_points.bin')
    with splats.open('rb') as stream:
        header = stream.read(16)
    if len(header) != 16:
        raise LaunchError('Truncated scene renderer header.')
    magic, _, count, stride = struct.unpack('<4sIII', header)
    if magic != b'GSPL' or count == 0 or stride not in (16, 40) or splats.stat().st_size != 16 + count * stride:
        raise LaunchError('Scene renderer size/header is invalid.')
    owners = local_file(folder, 'splat_instance.bin')
    with owners.open('rb') as stream:
        header = stream.read(12)
    if len(header) != 12 or struct.unpack('<4sII', header) != (b'GSID', 1, count) or owners.stat().st_size != 12 + count * 2:
        raise LaunchError('Object ownership does not match the displayed scan.')
    scene = twin.get('scene') or {}
    if scene.get('membership_mode') == 'exact-source-rows':
        source = local_file(folder, scene.get('source_ply'))
        rows = local_file(folder, scene.get('source_rows_file'))
        members = local_file(folder, scene.get('members_file'))
        with rows.open('rb') as stream:
            if stream.read(6) != b'\x93NUMPY':
                raise LaunchError('Invalid source row map.')
        try:
            with zipfile.ZipFile(members) as archive:
                if archive.testzip() is not None or not {'source_rows.npy', 'offsets.npy', 'object_ids.npy'} <= set(archive.namelist()):
                    raise LaunchError('Invalid exact object membership archive.')
        except zipfile.BadZipFile as error:
            raise LaunchError('Invalid exact object membership archive.') from error
        if thorough and sha256(source) != scene.get('source_sha256'):
            raise LaunchError('Native source PLY checksum changed; exact exports would be unsafe.')
    for asset in assets:
        check_evidence(folder, asset)
    check_take_apart(folder)
    library_index = Path(folder) / 'library' / 'library.json'
    if library_index.is_file():
        library = json.loads(library_index.read_text())
        for entry in library.get('saved', []):
            for reference in (entry.get('files') or {}).values():
                if reference:
                    local_file(Path(folder) / 'library', reference)
            check_evidence(folder, entry)
    review = twin.get('proposal_review')
    if review:
        catalog_path = review['catalog_url'].removeprefix('./').removeprefix('data/')
        catalog = json.loads(local_file(folder, catalog_path).read_text())
        for region in catalog.get('regions', []):
            check_evidence(folder, region)
        projected = local_file(folder, review['projected_instance_url'].removeprefix('./').removeprefix('data/'))
        with projected.open('rb') as stream:
            header = stream.read(12)
        if len(header) != 12 or struct.unpack('<4sII', header) != (b'GSID', 1, count) or projected.stat().st_size != 12 + 2 * count:
            raise LaunchError('Candidate ownership overlay does not match the baseline renderer.')
    return len(assets)


def venv_python(root):
    return Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def preflight(root, thorough=False):
    receipt = json.loads(local_file(root, 'install.json').read_text())
    if receipt.get('schema_version') != 1 or receipt.get('profiles') not in (['baseline'], ['baseline', 'extraction']):
        raise LaunchError('Unsupported installation receipt or profile selection.')
    if not venv_python(root).is_file():
        raise LaunchError('Installed Python environment is missing; rerun the installer into a new directory.')
    local_file(root / 'app', 'server.py')
    for name in ['code', *receipt['profiles']]:
        folder = root / 'app' if name == 'code' else root / 'data' / name
        inventory = receipt['artifacts'][name].get('inventory')
        if inventory:
            verify_inventory(folder, inventory, thorough)
        if name != 'code':
            count = check_dataset(folder, thorough)
            print(f'{name}: {count} objects; packaged assets checked.', flush=True)
    return receipt['profiles']


def server_environment(root, profile, extraction_port=None):
    env = os.environ.copy()
    env.update(VALMET_OFFLINE='1', VALMET_BIND_HOST='127.0.0.1',
               VALMET_DATA_DIR=str((root / 'data' / profile).resolve()),
               VALMET_PROPOSAL_VIEW_URL=f'http://127.0.0.1:{extraction_port}/' if profile == 'baseline' and extraction_port else '',
               PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    return env


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def start_server(root, profile, port, extraction_port=None, timeout=45):
    logs = root / 'logs'; logs.mkdir(exist_ok=True)
    log_path = logs / f'{profile}.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen([str(venv_python(root)), str(root / 'app' / 'server.py'), str(port)],
                                   cwd=root / 'app', env=server_environment(root, profile, extraction_port),
                                   stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            text = log_path.read_text(errors='replace')
            if process.poll() is not None:
                raise LaunchError(f'{profile} server failed to start. See {log_path}\n{text[-1500:]}')
            match = re.search(r'listening on (http://127\.0\.0\.1:(\d+))', text)
            if match:
                url = match[1]
                with urlopen(url + '/api/assets?mapped=true', timeout=5) as response:
                    result = json.load(response)
                if not isinstance(result.get('assets'), list):
                    raise LaunchError(f'{profile} server returned an invalid health response.')
                print(f'{profile}: {url}/', flush=True)
                return process, url
            time.sleep(.1)
        raise LaunchError(f'{profile} server startup timed out. See {log_path}')
    except BaseException:
        stop_process(process)
        raise


def check_port(port):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as error:
            raise LaunchError(f'Port {port} is already in use. Choose --port / --extraction-port.') from error


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--doctor', action='store_true', help='Verify checksums, dependencies and ephemeral-port server startup, then exit')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--extraction-port', type=int, default=8768)
    parser.add_argument('--open', action='store_true', help='Open the baseline in your browser')
    args = parser.parse_args(argv)
    if not all(1 <= port <= 65535 for port in (args.port, args.extraction_port)):
        parser.error('Ports must be between 1 and 65535.')
    root = Path(root or __file__).resolve()
    if root.is_file():
        root = root.parent
    processes = []
    try:
        profiles = preflight(root, thorough=args.doctor)
        if args.doctor:
            for profile in profiles:
                process, _ = start_server(root, profile, 0)
                stop_process(process)
            print('Doctor passed: assets, offline dependencies and HTTP startup are working.', flush=True)
            return 0
        if 'extraction' in profiles and args.port == args.extraction_port:
            raise LaunchError('Baseline and extraction need different ports.')
        for port in [args.port] + ([args.extraction_port] if 'extraction' in profiles else []):
            check_port(port)
        if 'extraction' in profiles:
            process, _ = start_server(root, 'extraction', args.extraction_port)
            processes.append(process)
        process, url = start_server(root, 'baseline', args.port,
                                    args.extraction_port if 'extraction' in profiles else None)
        processes.append(process)
        if args.open:
            webbrowser.open(url + '/')
        print('Press Ctrl-C to stop the demo.', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
        raise LaunchError('A demo server stopped. Check the installation logs directory.')
    except KeyboardInterrupt:
        return 0
    except (LaunchError, OSError, ValueError, KeyError, struct.error) as error:
        print(f'Cannot start Scan2Work: {error}', file=sys.stderr)
        return 1
    finally:
        for process in processes:
            stop_process(process)


if __name__ == '__main__':
    raise SystemExit(main())
