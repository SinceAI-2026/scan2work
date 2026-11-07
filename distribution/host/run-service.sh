#!/bin/bash
# Foreground programs for the separate, explicitly public Scan2Work demo.
set -euo pipefail
service="${1:?Choose baseline, review, mcp or proxy}"
case "$service" in baseline|review|mcp|proxy) ;; *) echo 'Unknown Scan2Work service' >&2; exit 2 ;; esac

install_dir=/workspace/scan2work/releases/0.1.1
# A verified source snapshot can update hosted code without modifying a release.
code_dir="${SCAN2WORK_CODE_DIR:-$install_dir/app}"
deploy_dir=/workspace/scan2work/deploy
export PROC_NAME="scan2work-${service}"
# Use the image's portal logging support, but do not source shared credentials
# or register this public application with the management/auth portal.
. /opt/supervisor-scripts/utils/logging.sh "/var/log/portal/${PROC_NAME}.log"

if [[ "$service" == mcp ]]; then
  # Same implementation as the dedicated supervised MCP service; no second listener.
  exec /bin/bash "$deploy_dir/mcp-service.sh" server
elif [[ "$service" == proxy ]]; then
  # A second Caddy must not reuse the management process's autosave/storage.
  export XDG_CONFIG_HOME=/workspace/scan2work/proxy/config
  export XDG_DATA_HOME=/workspace/scan2work/proxy/data
  mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME"
  pty /opt/instance-tools/bin/caddy run --config "$deploy_dir/Caddyfile" --adapter caddyfile
else
  python_cmd="$install_dir/.venv/bin/python"
  launcher="$code_dir/scripts/serve_public_demo.py"
  install_url=https://assets.scan2work.app/releases/0.1.1/index.html
  cd "$install_dir"
  if [[ "$service" == baseline ]]; then
    assistant_args=()
    if [[ -f "$deploy_dir/assistant.env" ]] && grep -qx 'VALMET_PUBLIC_ASSISTANT=1' "$deploy_dir/assistant.env"; then
      assistant_args=(--assistant)
    fi
    if [[ -f "$deploy_dir/assistant.env" ]] && grep -qx 'VALMET_PUBLIC_ARDUINO=1' "$deploy_dir/assistant.env"; then
      export VALMET_PUBLIC_ARDUINO=1
      export LIVE_TOKEN_FILE=/workspace/scan2work/private/arduino-token
    fi
    pty "$python_cmd" "$launcher" --data-dir "$install_dir/data/baseline" "${assistant_args[@]}" \
      --install-url "$install_url" --proposal-url https://review.scan2work.app/ --port 9080
  else
    pty "$python_cmd" "$launcher" --data-dir "$install_dir/data/extraction" \
      --install-url "$install_url" --port 9081
  fi
fi
