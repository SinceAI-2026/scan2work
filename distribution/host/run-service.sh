#!/bin/bash
# Foreground programs for the separate, explicitly public Scan2Work demo.
set -euo pipefail
service="${1:?Choose baseline, review, mcp or proxy}"
case "$service" in baseline|review|mcp|proxy) ;; *) echo 'Unknown Scan2Work service' >&2; exit 2 ;; esac

install_dir=/workspace/scan2work/releases/0.1.1
deploy_dir=/workspace/scan2work/deploy
export PROC_NAME="scan2work-${service}"
# Use the image's portal logging support, but do not source shared credentials
# or register this public application with the management/auth portal.
. /opt/supervisor-scripts/utils/logging.sh "/var/log/portal/${PROC_NAME}.log"

if [[ "$service" == mcp ]]; then
  # The voice assistant's MCP server (ElevenLabs calls it through the Worker at https://scan2work.app/mcp).
  # The token lives only on this host, in deploy/mcp.env: FIELD_ATLAS_MCP_TOKEN=<the ElevenLabs workspace secret>.
  # It writes the same inspection job log as the baseline viewer, so both use the baseline data directory.
  set -a; . "$deploy_dir/mcp.env"; set +a
  : "${FIELD_ATLAS_MCP_TOKEN:?Set FIELD_ATLAS_MCP_TOKEN in $deploy_dir/mcp.env}"
  export VALMET_DATA_DIR="$install_dir/data/baseline" VALMET_OFFLINE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
  cd "$install_dir"
  pty "$install_dir/.venv/bin/python" "$install_dir/app/mcp_server.py" 9082
elif [[ "$service" == proxy ]]; then
  # A second Caddy must not reuse the management process's autosave/storage.
  export XDG_CONFIG_HOME=/workspace/scan2work/proxy/config
  export XDG_DATA_HOME=/workspace/scan2work/proxy/data
  mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
  pty /opt/instance-tools/bin/caddy run --config "$deploy_dir/Caddyfile" --adapter caddyfile
else
  python_cmd="$install_dir/.venv/bin/python"
  launcher="$install_dir/app/scripts/serve_public_demo.py"
  install_url=https://assets.scan2work.app/releases/0.1.1/index.html
  cd "$install_dir"
  if [[ "$service" == baseline ]]; then
    pty "$python_cmd" "$launcher" --data-dir "$install_dir/data/baseline" --assistant \
      --install-url "$install_url" --proposal-url https://review.scan2work.app/ --port 9080
  else
    pty "$python_cmd" "$launcher" --data-dir "$install_dir/data/extraction" \
      --install-url "$install_url" --port 9081
  fi
fi
