#!/usr/bin/env bash
# Portable prepared-data runtime. No GPU, cloud CLI, model download or host-specific helpers.
set -euo pipefail

service="${1:?Choose baseline, review or mcp}"
case "$service" in baseline|review|mcp) ;; *) echo 'Unknown Scan2Work service' >&2; exit 2 ;; esac
if [[ $# -ne 1 ]]; then echo 'Expected exactly one service name' >&2; exit 2; fi
code_dir="${SCAN2WORK_CODE_DIR:-/opt/scan2work/current}"
python_cmd="${SCAN2WORK_PYTHON:-/opt/scan2work/venv/bin/python}"
data_root="${SCAN2WORK_DATA_ROOT:-/var/lib/scan2work/data}"
config_dir="${SCAN2WORK_CONFIG_DIR:-/etc/scan2work}"
install_url="${SCAN2WORK_INSTALL_URL:-https://assets.scan2work.app/releases/0.1.1/index.html}"
review_url="${SCAN2WORK_REVIEW_URL:-https://review.scan2work.app/}"
assistant="${VALMET_PUBLIC_ASSISTANT:-0}"
arduino="${VALMET_PUBLIC_ARDUINO:-0}"
[[ -x "$python_cmd" && -r "$code_dir/server.py" && -r "$code_dir/mcp_server.py" ]] || {
  echo 'Scan2Work code or Python environment is missing' >&2; exit 1;
}

export PYTHONUNBUFFERED=1 VALMET_PUBLIC_DEMO=1 VALMET_OFFLINE=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Dedicated files are authoritative; never inherit an unrelated host token.
unset FIELD_ATLAS_MCP_TOKEN LIVE_TOKEN
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
# The public baseline and MCP intentionally share this one persistent job log.
export VALMET_DATA_DIR="$data_root/baseline"
export VALMET_PUBLIC_ASSISTANT="$assistant"
cd "$code_dir"

if [[ "$service" == mcp ]]; then
  export FIELD_ATLAS_MCP_PUBLIC=1
  export FIELD_ATLAS_MCP_TOKEN_FILE="$config_dir/mcp-token"
  [[ -r "$FIELD_ATLAS_MCP_TOKEN_FILE" && -s "$FIELD_ATLAS_MCP_TOKEN_FILE" ]] || {
    echo 'Private MCP token file is missing or unreadable' >&2; exit 1;
  }
  exec "$python_cmd" "$code_dir/mcp_server.py" "${SCAN2WORK_MCP_PORT:-9090}"
fi

args=(--install-url "$install_url")
if [[ "$service" == baseline ]]; then
  [[ "$assistant" != 1 ]] || args+=(--assistant)
  export VALMET_PUBLIC_ARDUINO="$arduino"
  if [[ "$arduino" == 1 ]]; then
    export LIVE_TOKEN_FILE="$config_dir/arduino-token"
    [[ -r "$LIVE_TOKEN_FILE" && -s "$LIVE_TOKEN_FILE" ]] || {
      echo 'Private Arduino token file is missing or unreadable' >&2; exit 1;
    }
  fi
  args+=(--data-dir "$data_root/baseline" --proposal-url "$review_url" --port "${SCAN2WORK_MAIN_PORT:-9080}")
else
  # Review must never inherit baseline voice, job or board opt-ins.
  export VALMET_PUBLIC_ASSISTANT=0 VALMET_PUBLIC_ARDUINO=0 VALMET_VOICE_AGENT_ID="" VALMET_VOICE_MCP_URL=""
  unset LIVE_TOKEN LIVE_TOKEN_FILE FIELD_ATLAS_MCP_TOKEN FIELD_ATLAS_MCP_TOKEN_FILE
  args+=(--data-dir "$data_root/extraction" --port "${SCAN2WORK_REVIEW_PORT:-9081}")
fi
exec "$python_cmd" "$code_dir/scripts/serve_public_demo.py" "${args[@]}"
