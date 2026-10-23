#!/bin/bash
# Real-microphone mode (default):  bash run_realtime.sh [--device N]
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: installing (needs internet once)..."
  bash ./setup_ubuntu.sh || exit 1
fi
# Qt 5 on a Wayland session: use XWayland unless the user chose a platform
if [ -n "$WAYLAND_DISPLAY" ] && [ -n "$DISPLAY" ] && [ -z "$QT_QPA_PLATFORM" ]; then export QT_QPA_PLATFORM=xcb; fi
exec .venv/bin/python ans_cvs_realtime.py "$@"
