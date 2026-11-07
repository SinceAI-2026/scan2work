#!/usr/bin/env python3
"""Install a versioned Scan2Work release using only Python's standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = 'Scan2WorkInstaller/0.1'


class InstallError(RuntimeError):
    pass


def checked_url(url, allow_http=False):
    parsed = urlparse(url)
    if parsed.scheme not in ({'https', 'http'} if allow_http else {'https'}):
        raise InstallError('Release downloads require HTTPS; use --allow-http only for an explicit test/mirror.')
    if not parsed.netloc or parsed.username or parsed.password:
        raise InstallError('Release URLs must have a host and no embedded credentials.')
    return url


class ReleaseRedirect(HTTPRedirectHandler):
    def __init__(self, allow_http):
        self.allow_http = allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, self.allow_http)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def remote_open(url, allow_http=False):
    checked_url(url, allow_http)
    return build_opener(ReleaseRedirect(allow_http)).open(
        Request(url, headers={'User-Agent': USER_AGENT}), timeout=60)


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise InstallError('Invalid relative artifact path.')
    parsed = urlparse(value)
    path = PurePosixPath(unquote(parsed.path))
    if parsed.scheme or parsed.netloc or path.is_absolute() or '..' in path.parts or ':' in str(path):
        raise InstallError('Artifact paths must stay inside the release directory.')
    return path


def read_manifest(source, allow_http=False):
    if urlparse(str(source)).scheme:
        with remote_open(str(source), allow_http) as response:
            raw = response.read(1024 * 1024 + 1)
    else:
        with Path(source).expanduser().open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise InstallError('Release manifest is unexpectedly large.')
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise InstallError('Release manifest is not valid JSON.') from error
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise InstallError('Unsupported manifest schema; expected schema_version 1.')
    if not isinstance(manifest.get('release'), str) or not manifest['release'].strip():
        raise InstallError('Manifest has no release identifier.')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, dict) or not {'code', 'baseline'} <= set(artifacts):
        raise InstallError('Manifest must include code and baseline artifacts.')
    for name, spec in artifacts.items():
        if not isinstance(spec, dict):
            raise InstallError(f'Invalid artifact specification: {name}')
        safe_relative(spec.get('path'))
        if not isinstance(spec.get('sha256'), str) or not re.fullmatch('[a-fA-F0-9]{64}', spec['sha256']):
            raise InstallError(f'Invalid SHA256 for {name}')
        for key in ('bytes', 'unpacked_bytes'):
            if type(spec.get(key)) is not int or spec[key] <= 0:
                raise InstallError(f'Invalid {key} for {name}')
    return manifest


def artifact_source(manifest_source, relative, allow_http=False):
    path = safe_relative(relative)
    if urlparse(str(manifest_source)).scheme:
        return checked_url(urljoin(str(manifest_source), relative), allow_http)
    base = Path(manifest_source).expanduser().resolve().parent
    source = (base / str(path)).resolve()
    if base not in source.parents:
        raise InstallError('Artifact resolves outside the release directory.')
    return source


def download_artifact(source, destination, spec, allow_http=False):
    digest = hashlib.sha256(); size = 0
    stream = remote_open(str(source), allow_http) if isinstance(source, str) and urlparse(source).scheme else Path(source).open('rb')
    with stream, Path(destination).open('xb') as out:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if size > spec['bytes']:
                raise InstallError('Downloaded artifact exceeds its declared size.')
            digest.update(block); out.write(block)
    if size != spec['bytes']:
        raise InstallError('Downloaded artifact size does not match the manifest.')
    if digest.hexdigest() != spec['sha256'].lower():
        raise InstallError('Downloaded artifact SHA256 does not match the manifest.')


def extract_archive(archive, destination, expected_bytes):
    """Extract regular files/directories only, without following archive links."""
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=False)
    seen = set(); total = 0
    with tarfile.open(archive, 'r|gz') as bundle:
        for member in bundle:
            name = member.name
            path = PurePosixPath(name)
            if ('\\' in name or '\x00' in name or path.is_absolute()
                    or '..' in path.parts or ':' in name):
                raise InstallError(f'Unsafe archive member: {name!r}')
            if not member.isdir() and not member.isfile():
                raise InstallError(f'Archive links and special files are not supported: {name!r}')
            if not path.parts or str(path) == '.':
                if member.isdir():
                    continue
                raise InstallError('Archive root must be a directory.')
            if str(path) in seen:
                raise InstallError(f'Duplicate archive member: {name!r}')
            seen.add(str(path))
            if len(seen) > 200_000:
                raise InstallError('Archive contains too many members.')
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.size < 0 or total + member.size > expected_bytes:
                raise InstallError('Archive expands beyond its declared size.')
            total += member.size
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise InstallError(f'Cannot read archive member: {name!r}')
            with source, target.open('xb') as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            if target.stat().st_size != member.size:
                raise InstallError('Truncated archive member.')
            target.chmod(0o644 | (member.mode & 0o111))
    if total != expected_bytes:
        raise InstallError('Archive unpacked size does not match the manifest.')


def supported_python():
    candidates = [sys.executable, shutil.which('python3.12'), shutil.which('python3')]
    for candidate in dict.fromkeys(c for c in candidates if c):
        try:
            result = subprocess.run([candidate, '-c', 'import sys;print("%d.%d"%sys.version_info[:2])'],
                                    text=True, capture_output=True, check=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.stdout.strip() == '3.12':
            return str(candidate)
    raise InstallError('Python 3.12 is required for this tested release. Install Python 3.12 and rerun the installer.')


def venv_python(root):
    return Path(root) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare_environment(stage, python):
    subprocess.run([python, '-m', 'venv', str(stage / '.venv')], check=True)
    lock = stage / 'app' / 'requirements-demo.lock.txt'
    launcher = stage / 'app' / 'scripts' / 'scan2work_launcher.py'
    if not lock.is_file() or not launcher.is_file() or not (stage / 'app' / 'server.py').is_file():
        raise InstallError('Code artifact lacks server.py, requirements-demo.lock.txt or scripts/scan2work_launcher.py.')
    subprocess.run([str(venv_python(stage)), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-input', '--only-binary=:all:', '-r', str(lock)], check=True)
    subprocess.run([str(venv_python(stage)), '-m', 'pip', 'check'], check=True)
    shutil.copy2(launcher, stage / 'start.py')


def validate_install(stage):
    subprocess.run([str(venv_python(stage)), str(stage / 'start.py'), '--doctor'], check=True)


def relocate_venv_scripts(stage, destination):
    """Virtualenv entrypoint shebangs/activation scripts contain their build prefix."""
    folder = stage / '.venv' / ('Scripts' if os.name == 'nt' else 'bin')
    for file in [stage / '.venv' / 'pyvenv.cfg', *folder.iterdir()]:
        if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
            continue
        try:
            text = file.read_text()
        except UnicodeError:
            continue
        if str(stage) in text:
            file.write_text(text.replace(str(stage), str(destination)))


def publish(stage, destination):
    # Reserve a previously absent destination; never replace another installation,
    # even an empty directory. The populated tree is published in one rename.
    destination.mkdir(exist_ok=False)
    reservation = destination.stat()
    try:
        os.replace(stage, destination)
    except BaseException:
        if destination.exists() and destination.stat().st_ino == reservation.st_ino:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise


def install(manifest_source, install_dir, with_extraction=False, allow_http=False):
    destination = Path(install_dir).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise InstallError(f'Install directory already exists and was not changed: {destination}')
    manifest = read_manifest(manifest_source, allow_http)
    names = ['code', 'baseline'] + (['extraction'] if with_extraction else [])
    if any(name not in manifest['artifacts'] for name in names):
        raise InstallError('This release does not include the requested extraction artifact.')
    python = supported_python()
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = sum(manifest['artifacts'][name]['bytes'] + manifest['artifacts'][name]['unpacked_bytes']
                   for name in names) + 1024 ** 3
    if shutil.disk_usage(destination.parent).free < required:
        raise InstallError(f'Not enough free disk space: approximately {required / 1024**3:.1f} GiB is needed during installation.')
    lock_path = destination.parent / f'.{destination.name}.install.lock'
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise InstallError(f'Another installation may be running: {lock_path}') from error
    os.close(lock_fd)
    try:
        with tempfile.TemporaryDirectory(prefix=f'.{destination.name}.stage-', dir=destination.parent) as temp:
            stage = Path(temp) / 'install'; stage.mkdir()
            downloads = Path(temp) / 'downloads'; downloads.mkdir()
            for name in names:
                spec = manifest['artifacts'][name]
                print(f'Installing {name} ({spec["bytes"] / 1024**2:.1f} MiB)…', flush=True)
                archive = downloads / f'{name}.tar.gz'
                download_artifact(artifact_source(manifest_source, spec['path'], allow_http), archive, spec, allow_http)
                target = stage / 'app' if name == 'code' else stage / 'data' / name
                extract_archive(archive, target, spec['unpacked_bytes'])
            receipt = {'schema_version': 1, 'release': manifest['release'],
                       'profiles': names[1:], 'python': '3.12',
                       'artifacts': {name: manifest['artifacts'][name] for name in names}}
            (stage / 'install.json').write_text(json.dumps(receipt, indent=2) + '\n')
            prepare_environment(stage, python)
            validate_install(stage)
            relocate_venv_scripts(stage, destination)
            publish(stage, destination)
    finally:
        lock_path.unlink(missing_ok=True)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, help='Local manifest.json or HTTPS release URL')
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--with-extraction', action='store_true')
    parser.add_argument('--allow-http', action='store_true', help='Allow an explicit HTTP test server/mirror')
    parser.add_argument('--launch', action='store_true', help='Start the installed demo and open the browser')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--extraction-port', type=int, default=8768)
    args = parser.parse_args(argv)
    if not all(1 <= port <= 65535 for port in (args.port, args.extraction_port)):
        parser.error('Ports must be between 1 and 65535.')
    try:
        folder = install(args.manifest, args.install_dir, args.with_extraction, args.allow_http)
        print(f'Installed successfully: {folder}\nStart: "{venv_python(folder)}" "{folder / "start.py"}"', flush=True)
        if args.launch:
            return subprocess.call([str(venv_python(folder)), str(folder / 'start.py'), '--open',
                '--port', str(args.port), '--extraction-port', str(args.extraction_port)], cwd=folder)
        return 0
    except KeyboardInterrupt:
        print('Stopped. Any completed installation was preserved.', file=sys.stderr)
        return 130
    except (InstallError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f'Installation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
