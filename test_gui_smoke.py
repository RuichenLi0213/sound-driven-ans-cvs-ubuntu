"""
test_gui_smoke.py
=================
Drives the real GUI programmatically (Start / Pause / Resume / End) and saves
screenshots to test_output/.  Runs with Qt's offscreen platform unless
--show is given.

    python test_gui_smoke.py           TEST MODE window: simulated stimuli via the test buttons
    python test_gui_smoke.py --mic     REAL MICROPHONE window: opens the microphone, nothing injected;
                                       checks Start defaults, level display, and that End releases it
"""
import argparse
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument('--mic', action='store_true')
ap.add_argument('--show', action='store_true')
a = ap.parse_args()
if not a.show:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtWidgets, QtCore  # noqa: E402
import ans_cvs_realtime as R  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'test_output')
os.makedirs(OUT, exist_ok=True)
app = QtWidgets.QApplication(sys.argv)
checks = {}


def pump(seconds):
    t_end = time.time() + seconds
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.01)


def wait_ready(w, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pump(0.1)
        if w.engine is not None and (w.engine.ready or w.engine.error):
            return w.engine.ready
    return False


if not a.mic:
    w = R.create_window(R.parse_args(['--test-mode']))
    w.resize(1250, 900)
    w.show()
    pump(0.3)
    checks['test-mode label shown'] = 'TEST MODE' in w.mode_lbl.text()
    checks['inject buttons disabled before Start'] = not w.btn_inj_short.isEnabled()
    w.btn_start.click()
    checks['engine became ready (stable rest)'] = wait_ready(w)
    pump(3.0)
    checks['no stimulus before injection'] = not any(m[1] == 'stimulus' for m in w.engine.markers)
    w.btn_inj_short.click()
    pump(0.7)
    s07 = w.engine.state_text
    pump(2.5)
    w.btn_inj_long.click()
    pump(1.5)
    s_long = w.engine.state_text
    pump(6.0)
    w.grab().save(os.path.join(OUT, 'gui_test_mode.png'))
    checks['brief injection -> activation (rise)'] = s07 == R.RISE
    checks['3 s injection -> sustained activation at 1.5 s'] = s_long == R.SUSTAINED
    checks['back at rest afterwards'] = w.engine.state_text == R.REST
    w.btn_pause.click()
    pump(0.5)
    checks['pause -> paused state'] = w.engine.state_text == R.PAUSED and w.btn_pause.text() == 'Resume'
    w.btn_pause.click()
    pump(0.5)
    checks['resume -> running'] = w.engine.state_text != R.PAUSED
    w.btn_end.click()
    pump(0.5)
    checks['End stops the engine and keeps the plots'] = (w.engine is None and len(w.c_hr.getData()[0]) > 100)
    import ans_cvs_config as Cfg
    import glob
    Cfg.SESSIONS_DIR = os.path.join(OUT, 'sessions_smoke_test')
    before = set(glob.glob(os.path.join(Cfg.SESSIONS_DIR, '*')))
    w.btn_save.click()
    pump(0.3)
    new = sorted(set(glob.glob(os.path.join(Cfg.SESSIONS_DIR, '*'))) - before)
    csvs = [f for f in new if f.endswith('.csv') and not f.endswith('_beats.csv')]
    ok_save = len(new) == 3 and len(csvs) == 1
    if ok_save:
        import numpy as np
        arr = np.genfromtxt(csvs[0], delimiter=',', names=True)
        ok_save = len(arr) > 1000 and 'HR_bpm' in arr.dtype.names and 'SNS_BP' in arr.dtype.names
    checks['Save data after End writes CSV, beats and event log'] = ok_save
    w.grab().save(os.path.join(OUT, 'gui_test_mode_after_end.png'))
    w.btn_start.click()
    checks['Start again after End re-initialises stable rest'] = wait_ready(w) and w.engine.state_text == R.REST
    w.close()
    pump(0.3)
    checks['closing the window stops the engine'] = w.engine is None
else:
    w = R.create_window(R.parse_args([]))
    w.resize(1250, 900)
    w.show()
    pump(0.3)
    checks['real-microphone mode is the default (no flag)'] = 'REAL MICROPHONE' in w.mode_lbl.text() and not w.test_mode
    checks['no test inject buttons in real mode'] = not hasattr(w, 'btn_inj_short')
    checks['uncalibrated label before calibration'] = ('Uncalibrated' in w.cal_lbl.text()) or w.calibrated
    w.btn_start.click()
    pump(0.5)
    checks['microphone opened'] = w.audio is not None and w.audio.stream is not None
    checks['engine became ready (stable rest)'] = wait_ready(w)
    pump(6.0)
    stim = [m for m in w.engine.markers if m[1] == 'stimulus']
    print('level display:', w.level_lbl.text())
    print('state:', w.state_lbl.text())
    print('stimuli detected from real room sound during 6 s:', len(stim))
    print('event log:\n  ' + w.log_box.toPlainText().replace('\n', '\n  '))
    checks['level displayed with threshold'] = 'threshold 85' in w.level_lbl.text() and 'dB(A)' in w.level_lbl.text()
    checks['no stimulus injected at start (only real crossings logged)'] = all(
        'simulated' not in ln for ln in w.engine.log_lines)
    w.grab().save(os.path.join(OUT, 'gui_real_microphone.png'))
    stream = w.audio.stream
    w.btn_end.click()
    pump(0.5)
    checks['End releases the microphone'] = w.audio is None and stream.closed
    w.close()

for k, v in checks.items():
    print('[%s] %s' % ('PASS' if v else 'FAIL', k))
sys.exit(1 if not all(checks.values()) else 0)
