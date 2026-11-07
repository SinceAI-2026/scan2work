#!/bin/bash
# Separate authenticated MCP deployment; read-only unless explicitly enabled.
set -euo pipefail
service="${1:?Choose server or tunnel}"
case "$service" in server|tunnel) ;; *) echo 'Unknown MCP service' >&2; exit 2 ;; esac
export PROC_NAME="scan2work-mcp"
[[ "$service" == tunnel ]] && export PROC_NAME="scan2work-mcp-tunnel"
. /opt/supervisor-scripts/utils/logging.sh "/var/log/portal/${PROC_NAME}.log"

if [[ "$service" == tunnel ]]; then
  # Do not inherit the host's named tunnel credentials or management config.
  # The resulting HTTPS origin changes if this quick tunnel restarts. Update
  # the edge Worker's MCP_ORIGIN before declaring the endpoint healthy again.
  pty env -i PATH=/usr/bin:/bin /opt/instance-tools/bin/cloudflared tunnel \
    --config /dev/null --no-autoupdate --metrics 127.0.0.1:0 \
    --url http://127.0.0.1:9090
else
  code_dir="${SCAN2WORK_MCP_CODE:?Set the immutable MCP code snapshot path}"
  install_dir=/workspace/scan2work/releases/0.1.1
  token_file=/workspace/scan2work/private/mcp-token
  assistant_mode=0
  # Read this one exact flag, never source shell code or credentials from it.
  if [[ -f /workspace/scan2work/deploy/assistant.env ]] && \
      grep -qx 'VALMET_PUBLIC_ASSISTANT=1' /workspace/scan2work/deploy/assistant.env; then
    assistant_mode=1
  fi
  [[ -r "$code_dir/mcp_server.py" && -s "$token_file" ]] || exit 1
  cd "$code_dir"
  pty env -i PATH=/usr/bin:/bin PYTHONUNBUFFERED=1 \
    VALMET_DATA_DIR="$install_dir/data/baseline" \
    VALMET_OFFLINE=1 VALMET_PUBLIC_DEMO=1 FIELD_ATLAS_MCP_PUBLIC=1 \
    VALMET_PUBLIC_ASSISTANT="$assistant_mode" \
    FIELD_ATLAS_MCP_TOKEN_FILE="$token_file" \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    "$install_dir/.venv/bin/python" "$code_dir/mcp_server.py" 9090
fi
