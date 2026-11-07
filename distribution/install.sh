#!/bin/sh
# Download the versioned installer. No sudo or system Python modification.
set -eu
base=${SCAN2WORK_DOWNLOAD_BASE:-https://assets.scan2work.app/releases/@RELEASE@}
python_cmd=''
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys;sys.exit(sys.version_info[:2] != (3,12))' 2>/dev/null; then
    python_cmd=$candidate
    break
  fi
done
if [ -z "$python_cmd" ]; then
  echo 'Scan2Work needs Python 3.12. macOS: brew install python@3.12. Ubuntu 24.04: install python3.12 and python3.12-venv.' >&2
  exit 1
fi
temp_dir=$(mktemp -d)
trap 'rm -rf "$temp_dir"' EXIT HUP INT TERM
curl --fail --silent --show-error --location "$base/install.py" -o "$temp_dir/install.py"
"$python_cmd" "$temp_dir/install.py" --manifest "$base/manifest.json" "$@"
