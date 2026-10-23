#!/bin/bash
# TEST MODE: simulated stimuli from buttons, the microphone is NOT opened.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: installing (needs internet once)..."
  bash ./setup_ubuntu.sh || exit 1
fi
if [ -n "$WAYLAND_DISPLAY" ] && [ -n "$DISPLAY" ] && [ -z "$QT_QPA_PLATFORM" ]; then export QT_QPA_PLATFORM=xcb; fi
exec .venv/bin/python ans_cvs_realtime.py --test-mode "$@"
