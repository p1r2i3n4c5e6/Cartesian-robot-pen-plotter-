#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_DIR="${SCRIPT_DIR}/logs"

mkdir -p "${LOG_DIR}"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        🤖 Robot + cncjs Raspberry Pi Launcher          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "[INFO] The PyQt app will auto-start cncjs, open localhost,"
echo "[INFO] and auto-connect the machine if your settings allow it."
echo ""

exec env -i \
  HOME="${HOME:-}" \
  USER="${USER:-}" \
  LOGNAME="${LOGNAME:-}" \
  SHELL="${SHELL:-/bin/bash}" \
  PATH="${PATH:-/usr/bin:/bin:/usr/local/bin}" \
  LANG="${LANG:-C.UTF-8}" \
  LC_ALL="${LC_ALL:-}" \
  DISPLAY="${DISPLAY:-}" \
  WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" \
  XDG_SESSION_TYPE="${XDG_SESSION_TYPE:-}" \
  XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-}" \
  DESKTOP_SESSION="${DESKTOP_SESSION:-}" \
  XAUTHORITY="${XAUTHORITY:-}" \
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
  DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" \
  TERM="${TERM:-xterm-256color}" \
  COLORTERM="${COLORTERM:-truecolor}" \
  "${PYTHON_BIN}" "${SCRIPT_DIR}/robot.py" \
  > "${LOG_DIR}/robot1.log" 2>&1
