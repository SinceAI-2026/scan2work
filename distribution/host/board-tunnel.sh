#!/bin/bash
# Dedicated encrypted transport for authenticated Arduino requests.
set -euo pipefail
export PROC_NAME=scan2work-board-tunnel
. /opt/supervisor-scripts/utils/logging.sh "/var/log/portal/${PROC_NAME}.log"
# The main application's board API checks LIVE_TOKEN before accepting a heartbeat.
# Keep this separate from MCP and management tunnels. On restart, update the
# Worker's ARDUINO_ORIGIN to the new HTTPS hostname and recheck the public API.
pty env -i PATH=/usr/bin:/bin /opt/instance-tools/bin/cloudflared tunnel \
  --config /dev/null --no-autoupdate --metrics 127.0.0.1:0 \
  --url http://127.0.0.1:9080
