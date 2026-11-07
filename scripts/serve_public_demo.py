#!/usr/bin/env python3
"""Run a prepared, read-only hosted demo behind an existing HTTPS reverse proxy.

This script creates no accounts, tunnels, credentials, datasets or cloud resources.
Use a separate release installation; it always binds the app to loopback.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan2work_launcher import LaunchError, check_dataset


def https_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError('Use an absolute HTTPS URL without credentials')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--install-url', type=https_url, required=True,
                        help='Published release download/instructions page')
    parser.add_argument('--proposal-url', type=https_url,
                        help='Optional separately hosted full extraction review')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--assistant', action='store_true',
                        help='Show the voice assistant and keep a shared inspection job log (run mcp_server.py with the same --data-dir)')
    parser.add_argument('--check', action='store_true', help='Check prepared files, then exit')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    data = args.data_dir.expanduser().resolve()
    try:
        count = check_dataset(data, thorough=False)
    except (OSError, ValueError, LaunchError) as error:
        parser.exit(1, f'Prepared dataset failed preflight: {error}\n')
    print(f'Public demo: {count} objects; offline; ' + ('voice assistant on, inspection jobs shared, other writes disabled.'
          if args.assistant else 'shared writes disabled.'), flush=True)
    if args.check:
        return 0
    env = {**os.environ, 'VALMET_PUBLIC_DEMO': '1', 'VALMET_OFFLINE': '1',
           'VALMET_DATA_DIR': str(data), 'VALMET_BIND_HOST': '127.0.0.1',
           'VALMET_INSTALL_URL': args.install_url,
           'VALMET_PROPOSAL_VIEW_URL': args.proposal_url or '',
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
           'VALMET_PUBLIC_ASSISTANT': '1' if args.assistant else '0'}
    server = Path(__file__).resolve().parents[1] / 'server.py'
    os.execve(sys.executable, [sys.executable, str(server), str(args.port)], env)


if __name__ == '__main__':
    raise SystemExit(main())
