#!/usr/bin/env bash
# Copy the fault panel app to a UNO Q on USB-C and (re)start it, without the App Lab UI.
# Uses the adb that App Lab installs. Usage: arduino/deploy.sh [--logs]
set -euo pipefail
ADB="${ADB:-$HOME/Library/Arduino15/packages/arduino/tools/adb/32.0.0/adb}"
command -v "$ADB" >/dev/null || ADB=adb
APP="$(cd "$(dirname "$0")" && pwd)/fault-panel"
DEST=/home/arduino/ArduinoApps/fault-panel

"$ADB" get-state >/dev/null  # fails if no board is attached
"$ADB" shell "mkdir -p $DEST"
"$ADB" push "$APP/." "$DEST/" | tail -1
# adb shells set TMPDIR=/data/local/tmp, which does not exist on the UNO Q; the CLI needs a real one.
"$ADB" shell "export TMPDIR=/tmp; arduino-app-cli app stop user:fault-panel >/dev/null 2>&1; arduino-app-cli app start user:fault-panel 2>&1 | grep -v 'Download progress' | tail -5"
if [[ "${1:-}" == "--logs" ]]; then "$ADB" shell "docker logs -f fault-panel-main-1"; fi
