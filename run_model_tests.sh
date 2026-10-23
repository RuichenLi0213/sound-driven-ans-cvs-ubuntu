#!/bin/bash
# Automated tests that do NOT use the microphone (about 1 minute).  Runs headless (Qt offscreen).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  bash ./setup_ubuntu.sh || exit 1
fi
FAIL=0
run() { echo; echo "=============== $*"; .venv/bin/python "$@" || FAIL=1; }
run test_simulated_stimulus.py
run test_realtime_engine.py
run test_audio_detector.py
run test_gui_smoke.py
echo
if [ "$FAIL" = "0" ]; then echo "ALL TESTS PASSED (reports and figures in test_output/)"; else echo "SOME TESTS FAILED - see the output above"; fi
exit $FAIL
