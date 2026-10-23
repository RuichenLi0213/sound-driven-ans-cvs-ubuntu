"""
ans_cvs_realtime.py
===================
Microphone-driven real-time ANS-CVS simulation and display.

    python ans_cvs_realtime.py              real-microphone mode (default)
    python ans_cvs_realtime.py --test-mode  simulated stimuli from buttons, microphone unused

HR, pulsatile BP and the four ANS activities are virtual signals computed by
the coupled ANS-CVS model (ans_cvs_model.py); they are not measurements of the
person at the computer.
"""
import argparse
import collections
import sys
import threading
import time

import numpy as np

import ans_cvs_config as C
import ans_cvs_design as D
import ans_cvs_model as M
from stimulus_controller import (StimulusController, REST, RISE, SUSTAINED, RECOVERY,
                                 PAUSED, ENDED, INIT)


def _windows_timer_resolution(enable):
    """Windows with Python < 3.11: 1 ms sleep resolution for the model loop (default is ~15.6 ms)."""
    if sys.platform.startswith('win') and sys.version_info < (3, 11):
        try:
            import ctypes
            winmm = ctypes.windll.winmm
            (winmm.timeBeginPeriod if enable else winmm.timeEndPeriod)(1)
        except Exception:
            pass


# =====================================================================
# Real-time engine (model thread)
# =====================================================================
class RingBuffer:
    """Bounded buffer of model samples (rows of M.OUT_COLS)."""

    def __init__(self, n_rows, n_cols):
        self.a = np.full((n_rows, n_cols), np.nan)
        self.n = 0
        self.i = 0
        self.lock = threading.Lock()

    def extend(self, rows):
        with self.lock:
            for r in rows:
                self.a[self.i] = r
                self.i = (self.i + 1) % len(self.a)
                self.n = min(self.n + 1, len(self.a))

    def snapshot(self):
        with self.lock:
            if self.n < len(self.a):
                return self.a[:self.n].copy()
            return np.concatenate([self.a[self.i:], self.a[:self.i]])


class Engine(threading.Thread):
    """Initialises stable rest, then integrates the model in step with wall-clock time.
    Stimulus events come from an AudioMonitor (real microphone) or from
    inject_test_stimulus() (test mode only)."""

    def __init__(self, audio=None, test_mode=False):
        super().__init__(daemon=True)
        self.audio = audio
        self.test_mode = test_mode
        self.state_text = INIT
        self.status = 'initialising model'
        self.error = None
        self.ready = False
        self._stop_evt = threading.Event()
        self._paused = threading.Event()
        self._pause_wall = None
        self.paused_total = 0.0
        self.test_events = collections.deque()
        self.buf = RingBuffer(int(C.BUFFER_S / (C.SAMPLE_EVERY_STEPS * C.DT)), M.NOUT)
        self.beats = collections.deque(maxlen=400)       # (t_SBP, SBP, t_DBP, DBP, MAP_cycle)
        self.markers = collections.deque(maxlen=200)     # (t, 'stimulus' | 'recovery')
        self.log_lines = collections.deque(maxlen=300)
        self.log_count = 0
        self.t0_model = 0.0
        self.lag_s = 0.0
        self._last_cycle = -1
        self._bmax = self._bmin = None
        self._log_seen = 0

    # ---------------------------------------------------------------- control
    def stop(self):
        self._stop_evt.set()

    def pause(self):
        if not self._paused.is_set():
            self._pause_wall = time.perf_counter()
            self._paused.set()
            if self.audio:
                self.audio.disarm()          # no stimuli accepted while paused
            self.log('paused')

    def resume(self):
        if self._paused.is_set():
            self.paused_total += time.perf_counter() - self._pause_wall
            self.test_events.clear()
            if self.audio:
                self.audio.arm()             # discards queued audio; waits for quiet first
            self._paused.clear()
            self.log('resumed (audio received while paused was discarded)')

    @property
    def paused(self):
        return self._paused.is_set()

    def inject_test_stimulus(self, duration_s):
        """TEST MODE ONLY: a simulated '>= 85 dB stimulus detected' event."""
        if not self.test_mode:
            raise RuntimeError('simulated stimuli are only available in --test-mode')
        if self.ready and not self.paused:
            now = time.perf_counter()
            self.test_events.append(('onset', now, None))
            self.test_events.append(('end', now + max(duration_s, 0.0) + C.RELEASE_HOLD_S, None))

    def log(self, text, t=None):
        tt = '' if t is None else '[t=%7.2f s] ' % t
        self.log_lines.append(time.strftime('%H:%M:%S ') + tt + text)
        self.log_count += 1

    # ---------------------------------------------------------------- thread
    def run(self):
        try:
            self._initialise()
        except Exception as e:  # report instead of failing silently
            self.error = 'Initialisation failed: %s' % e
            self.state_text = ENDED
            return
        _windows_timer_resolution(True)
        try:
            self._loop()
        except Exception as e:
            self.error = 'Model loop stopped: %s' % e
        finally:
            _windows_timer_resolution(False)
            self.state_text = ENDED
            self.ready = False

    def _initialise(self):
        self.status = 'deriving parameters (first run also compiles the model)'
        params, d = D.derive()
        self.design = d
        self.status = 'establishing stable rest (spin-up %.0f s + ANS-active settle %.0f s)' % (C.SPINUP_S, C.SETTLE_S)
        mod, out = D.rest_model(params, d)
        t = out[:, 0]
        w = t > t[-1] - C.REST_CHECK_WINDOW_S
        hr, mp = out[w, M.OC['HR_bpm']], out[w, M.OC['MAP_mean']]
        checks = dict(hr_err=abs(hr.mean() - C.HR_REST_BPM), hr_rng=np.ptp(hr),
                      map_err=abs(mp.mean() - C.MAP_REST_MMHG), map_rng=np.ptp(mp))
        if not (checks['hr_err'] < C.REST_TOL_HR_BPM and checks['hr_rng'] < C.REST_TOL_HR_BPM and
                checks['map_err'] < C.REST_TOL_MAP_MMHG and checks['map_rng'] < C.REST_TOL_MAP_MMHG):
            raise RuntimeError('rest not stable: %s' % checks)
        self.model = mod
        self.ctrl = StimulusController((d['mu_HR_rest'], d['mu_BP_rest']), (d['mu_HR_act'], d['mu_BP_act']),
                                       C.HR_REST_BPM, C.MAP_REST_MMHG, C.HR_ELEVATED_BPM, C.MAP_ELEVATED_MMHG)
        self.t0_model = mod.get('t')
        self._chunk = int(round(C.CHUNK_S / C.DT))
        self._prev_state = self.ctrl.state
        self.log('stable rest: HR %.2f bpm, MAP %.2f mmHg (|err| %.3f / %.3f)'
                 % (hr.mean(), mp.mean(), checks['hr_err'], checks['map_err']))
        self.log('weights Aneu_HR=%.1f Bneu_HR=%.1f Aneu_BP=%.1f Bneu_BP=%.1f; slopes HR %+.1f/%+.1f, BP %+.1f/%+.1f'
                 % (mod.params['Aneu_HR'], mod.params['Bneu_HR'], mod.params['Aneu_BP'], mod.params['Bneu_BP'],
                    mod.params['nu_s_HR'], mod.params['nu_p_HR'], mod.params['nu_s_BP'], mod.params['nu_p_BP']))
        self.ready = True
        self.state_text = REST
        self.status = 'running'
        if self.audio:
            self.audio.arm()
            self.log('detector armed: trigger >= %.0f dB(A), %s' % (
                C.TRIGGER_THRESHOLD_DB, 'calibrated' if self.audio.calibrated else 'UNCALIBRATED estimate'))
        else:
            self.log('TEST MODE: stimuli only from the test buttons')

    def _next_events(self, wall_now):
        evs = []
        if self.audio:
            while True:
                try:
                    evs.append(self.audio.events.get_nowait())
                except Exception:
                    break
        while self.test_events and self.test_events[0][1] <= wall_now:
            evs.append(self.test_events.popleft())
        return evs

    def _loop(self):
        mod, ctrl = self.model, self.ctrl
        wall0 = time.perf_counter()
        while not self._stop_evt.is_set():
            if self._paused.is_set():
                self.state_text = PAUSED
                time.sleep(0.01)
                continue
            now = time.perf_counter()
            target = self.t0_model + (now - wall0 - self.paused_total)
            for kind, t_wall, level in self._next_events(now):
                self.apply_event(kind, self.t0_model + (t_wall - wall0 - self.paused_total), level)
            self._advance_to(target)
            self.lag_s = target - mod.get('t')
            self.state_text = ctrl.state
            time.sleep(0.004)

    def apply_event(self, kind, t_ev, level=None):
        """Integrate up to the event's model time, then pass it to the controller.
        Used by the real-time loop and by the simulated-stimulus tests."""
        mod, ctrl = self.model, self.ctrl
        self._advance_to(t_ev)
        t_model = mod.get('t')
        if kind == 'onset':
            r = ctrl.on_sound_onset(t_model, level)
            if r == 'trigger':
                self.markers.append((t_model, 'stimulus'))
            self.log('%s (%s), applied %.0f ms after detection'
                     % ('STIMULUS' if r == 'trigger' else 'stimulus during activation (no reset)',
                        'simulated' if level is None else '%.1f dB(A)' % level,
                        max(0.0, t_model - t_ev) * 1e3), t_model - self.t0_model)
        else:
            ctrl.on_sound_end(t_model)
            self.log('sound ended%s' % ('' if level is None else ' (max %.1f dB(A))' % level),
                     t_model - self.t0_model)
        self._check_transition()

    def _advance_to(self, t_target, max_chunks=50):
        """Integrate in CHUNK_S pieces until model time reaches t_target.
        The controller updates the mu targets before every chunk."""
        mod, ctrl = self.model, self.ctrl
        n = 0
        while mod.get('t') < t_target - 0.5 * C.DT and n < max_chunks and not self._stop_evt.is_set():
            steps = min(self._chunk, max(1, int(np.ceil((t_target - mod.get('t')) / C.DT))))
            mu = ctrl.update(mod.get('t'), mod.get('HR_bps') * 60.0, mod.get('MAP_mean'))
            mod.set_mu(*mu)
            self._check_transition()
            self._record(mod.run(steps, True, C.SAMPLE_EVERY_STEPS))
            n += 1

    def _check_transition(self):
        st = self.ctrl.state
        if st != self._prev_state:
            t = self.model.get('t')
            if self._prev_state in (RISE, SUSTAINED) and st == RECOVERY:
                self.markers.append((t, 'recovery'))
                self.log('recovery onset', t - self.t0_model)
            elif st == SUSTAINED:
                self.log('sustained activation (sound still above threshold)', t - self.t0_model)
            elif self._prev_state == RECOVERY and st == REST:
                self.log('back at rest', t - self.t0_model)
            self._prev_state = st

    def _record(self, rows):
        if len(rows) == 0:
            return
        rows = rows.copy()
        rows[:, 0] -= self.t0_model
        self.buf.extend(rows)
        O = M.OC
        for r in rows:
            c = r[O['cycle_index']]
            if c != self._last_cycle:
                if self._last_cycle >= 0 and self._bmax is not None:
                    # SBP/DBP are the model's beat values; marker times are where they occurred
                    self.beats.append((self._bmax[1], r[O['SBP']], self._bmin[1], r[O['DBP']], r[O['MAP_cycle']]))
                self._last_cycle = c
                self._bmax, self._bmin = (r[O['P_art']], r[0]), (r[O['P_art']], r[0])
            else:
                if self._bmax is None or r[O['P_art']] > self._bmax[0]:
                    self._bmax = (r[O['P_art']], r[0])
                if self._bmin is None or r[O['P_art']] < self._bmin[0]:
                    self._bmin = (r[O['P_art']], r[0])


def save_session(eng, test_mode, audio, calibrated, offset_db):
    """Write the buffered samples (last BUFFER_S s), beats and the event log."""
    import json
    import os
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), C.SESSIONS_DIR)
    os.makedirs(folder, exist_ok=True)
    stem = os.path.join(folder, time.strftime('session_%Y%m%d_%H%M%S'))
    data = eng.buf.snapshot()
    np.savetxt(stem + '.csv', data, delimiter=',', header=','.join(M.OUT_COLS), comments='', fmt='%.6g')
    beats = np.array(list(eng.beats)) if eng.beats else np.zeros((0, 5))
    np.savetxt(stem + '_beats.csv', beats, delimiter=',', header='t_SBP,SBP,t_DBP,DBP,MAP_cycle', comments='',
               fmt='%.6g')
    with open(stem + '_events.txt', 'w', encoding='utf-8') as f:
        meta = dict(mode='test mode (simulated stimuli)' if test_mode else 'real microphone',
                    calibrated=bool(calibrated), calibration_offset_dB=offset_db,
                    input_device=getattr(audio, 'device_name', None) if audio else None,
                    threshold_dBA=C.TRIGGER_THRESHOLD_DB, hysteresis_dB=C.HYSTERESIS_DB,
                    release_hold_s=C.RELEASE_HOLD_S, weighting=C.FREQUENCY_WEIGHTING, window_s=C.WINDOW_S,
                    targets=dict(HR_rest=C.HR_REST_BPM, MAP_rest=C.MAP_REST_MMHG, HR_elevated=C.HR_ELEVATED_BPM,
                                 MAP_elevated=C.MAP_ELEVATED_MMHG),
                    design={k: float(v) for k, v in getattr(eng, 'design', {}).items()},
                    time_zero='t = 0 when detection was enabled (model time, s)')
        f.write(json.dumps(meta, indent=1) + '\n\n')
        f.write('\n'.join(eng.log_lines) + '\n')
        f.write('\nmarkers (t, kind):\n')
        for tm, kind in eng.markers:
            f.write('%.4f,%s\n' % (tm - eng.t0_model, kind))
    return stem + '.csv'


# =====================================================================
# GUI
# =====================================================================
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--test-mode', action='store_true',
                    help='simulated stimuli from buttons; the microphone is NOT used')
    ap.add_argument('--device', default=None, help='input device index or name (default: system default)')
    return ap.parse_args(argv)


def create_window(args):
    """Build the main window (requires an existing QApplication)."""
    from PyQt5 import QtCore, QtGui, QtWidgets
    import pyqtgraph as pg

    pg.setConfigOptions(antialias=True, background='w', foreground='k')

    COL_SNS, COL_PSNS = (200, 40, 40), (40, 80, 200)
    COL_HR, COL_BP, COL_MAP = (150, 30, 30), (40, 90, 170), (20, 20, 20)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            self.test_mode = args.test_mode
            self.engine = None
            self.audio = None
            self.setWindowTitle('ANS-CVS real-time model  -  %s' % ('TEST MODE (simulated stimuli)' if self.test_mode
                                                                  else 'real microphone'))
            self.resize(1250, 900)
            cw = QtWidgets.QWidget()
            self.setCentralWidget(cw)
            v = QtWidgets.QVBoxLayout(cw)

            # ---- row 1: buttons and audio settings
            r1 = QtWidgets.QHBoxLayout()
            self.btn_start = QtWidgets.QPushButton('Start')
            self.btn_pause = QtWidgets.QPushButton('Pause')
            self.btn_end = QtWidgets.QPushButton('End')
            self.btn_save = QtWidgets.QPushButton('Save data')
            self.btn_save.setToolTip('Write the last %.0f s of model signals and the event log to the %s/ folder'
                                     % (C.BUFFER_S, C.SESSIONS_DIR))
            self.btn_pause.setEnabled(False)
            self.btn_end.setEnabled(False)
            self.btn_save.setEnabled(False)
            for b in (self.btn_start, self.btn_pause, self.btn_end, self.btn_save):
                b.setMinimumWidth(90)
                r1.addWidget(b)
            r1.addSpacing(20)
            self.mode_lbl = QtWidgets.QLabel()
            r1.addWidget(self.mode_lbl)
            r1.addStretch(1)
            if not self.test_mode:
                r1.addWidget(QtWidgets.QLabel('Input:'))
                self.dev_combo = QtWidgets.QComboBox()
                self.dev_combo.setMinimumWidth(260)
                r1.addWidget(self.dev_combo)
                r1.addWidget(QtWidgets.QLabel('Cal. offset (dB):'))
                self.off_spin = QtWidgets.QDoubleSpinBox()
                self.off_spin.setRange(40.0, 200.0)
                self.off_spin.setDecimals(1)
                self.off_spin.setSingleStep(0.5)
                r1.addWidget(self.off_spin)
                self.btn_cal = QtWidgets.QPushButton('Calibrate...')
                r1.addWidget(self.btn_cal)
            else:
                self.btn_inj_short = QtWidgets.QPushButton('Inject simulated stimulus (brief)')
                self.btn_inj_long = QtWidgets.QPushButton('Inject simulated stimulus (3 s)')
                for b in (self.btn_inj_short, self.btn_inj_long):
                    b.setEnabled(False)
                    b.setStyleSheet('color: #b35900; font-weight: bold;')
                    r1.addWidget(b)
            v.addLayout(r1)

            # ---- row 2: level, calibration, state
            r2 = QtWidgets.QHBoxLayout()
            self.level_lbl = QtWidgets.QLabel('Sound level: -')
            self.level_lbl.setMinimumWidth(600)
            self.level_bar = QtWidgets.QProgressBar()
            self.level_bar.setRange(20, 120)
            self.level_bar.setTextVisible(False)
            self.level_bar.setMaximumWidth(220)
            self.cal_lbl = QtWidgets.QLabel()
            self.state_lbl = QtWidgets.QLabel('State: -')
            f = self.state_lbl.font()
            f.setBold(True)
            self.state_lbl.setFont(f)
            r2.addWidget(self.level_lbl)
            r2.addWidget(self.level_bar)
            r2.addSpacing(10)
            r2.addWidget(self.cal_lbl)
            r2.addStretch(1)
            r2.addWidget(self.state_lbl)
            v.addLayout(r2)
            self.warn_lbl = QtWidgets.QLabel('')
            self.warn_lbl.setStyleSheet('color: #b00000;')
            v.addWidget(self.warn_lbl)

            # ---- plots
            glw = pg.GraphicsLayoutWidget()
            v.addWidget(glw, 1)
            self.p_hr = glw.addPlot(row=0, col=0)
            self.p_bp = glw.addPlot(row=1, col=0)
            self.p_ans = glw.addPlot(row=2, col=0)
            for p, lab in ((self.p_hr, 'Heart rate [bpm]'), (self.p_bp, 'Blood pressure [mmHg]'),
                           (self.p_ans, 'ANS activity [-]')):
                p.setLabel('left', lab)
                p.showGrid(x=True, y=True, alpha=0.25)
                p.setMouseEnabled(x=False, y=False)
                p.getAxis('left').setWidth(70)
            self.p_bp.setXLink(self.p_hr)
            self.p_ans.setXLink(self.p_hr)
            self.p_ans.setLabel('bottom', 'Model time [s]')
            self.p_hr.setYRange(70, 105)
            self.p_bp.setYRange(70, 135)
            self.p_ans.setYRange(0, 1)

            dash = QtCore.Qt.DashLine
            self.c_hr = self.p_hr.plot(pen=pg.mkPen(COL_HR, width=2.5))
            for y, txt, col in ((C.HR_REST_BPM, 'rest %.1f' % C.HR_REST_BPM, (110, 110, 110)),):
                self.p_hr.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(col, width=1.2, style=dash),
                                                  label=txt, labelOpts=dict(position=0.0, color=col,
                                                                            anchors=[(0, 1), (0, 1)])))
            self.hr_txt = pg.TextItem(anchor=(1, 0), color=COL_HR)
            self.p_hr.addItem(self.hr_txt, ignoreBounds=True)

            self.c_p = self.p_bp.plot(pen=pg.mkPen(COL_BP, width=1.5), name='arterial pressure')
            self.c_map = self.p_bp.plot(pen=pg.mkPen(COL_MAP, width=2, style=dash), name='MAP (cycle-mean)')
            self.s_sbp = pg.ScatterPlotItem(symbol='t1', size=7, brush=pg.mkBrush(200, 60, 0), pen=None)
            self.s_dbp = pg.ScatterPlotItem(symbol='t', size=7, brush=pg.mkBrush(0, 140, 90), pen=None)
            self.p_bp.addItem(self.s_sbp)
            self.p_bp.addItem(self.s_dbp)
            for y, col in ((C.MAP_REST_MMHG, (110, 110, 110)), (C.MAP_ELEVATED_MMHG, (220, 120, 0))):
                self.p_bp.addItem(pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(col, width=1, style=QtCore.Qt.DotLine)))
            leg = self.p_bp.addLegend(offset=(10, 5))
            leg.addItem(self.s_sbp, 'SBP (beat)')
            leg.addItem(self.s_dbp, 'DBP (beat)')
            self.bp_txt = pg.TextItem(anchor=(1, 0), color=COL_BP)
            self.p_bp.addItem(self.bp_txt, ignoreBounds=True)

            self.p_ans.addLegend(offset=(-10, 5), colCount=4)
            self.c_sns_hr = self.p_ans.plot(pen=pg.mkPen(COL_SNS, width=2), name='SNS_HR')
            self.c_psns_hr = self.p_ans.plot(pen=pg.mkPen(COL_PSNS, width=2), name='PSNS_HR')
            self.c_sns_bp = self.p_ans.plot(pen=pg.mkPen(COL_SNS, width=2, style=dash), name='SNS_BP')
            self.c_psns_bp = self.p_ans.plot(pen=pg.mkPen(COL_PSNS, width=2, style=dash), name='PSNS_BP')
            self.marker_items = []

            # ---- event log
            self.log_box = QtWidgets.QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setMaximumBlockCount(300)
            self.log_box.setFixedHeight(110)
            v.addWidget(self.log_box)
            self._n_log = 0

            self.btn_start.clicked.connect(self.on_start)
            self.btn_pause.clicked.connect(self.on_pause)
            self.btn_end.clicked.connect(self.on_end)
            self.btn_save.clicked.connect(self.on_save)
            if self.test_mode:
                self.btn_inj_short.clicked.connect(lambda: self.engine and self.engine.inject_test_stimulus(0.0))
                self.btn_inj_long.clicked.connect(lambda: self.engine and self.engine.inject_test_stimulus(3.0))
            else:
                self.btn_cal.clicked.connect(self.on_calibrate)
                self.off_spin.valueChanged.connect(self.on_offset_changed)
                self._populate_devices()
                from audio_monitor import load_calibration
                off, cal, info = load_calibration()
                self.calibrated, self.cal_info = cal, info
                self.off_spin.blockSignals(True)
                self.off_spin.setValue(off)
                self.off_spin.blockSignals(False)
            self._update_mode_labels()
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.refresh)
            self.timer.start(int(1000 / C.PLOT_FPS))

        # ------------------------------------------------------------ helpers
        def _populate_devices(self):
            try:
                from audio_monitor import list_input_devices
                devs, default_in = list_input_devices()
            except Exception as e:
                devs, default_in = [], None
                self.warn_lbl.setText('Audio devices could not be listed: %s' % e)
            self.dev_combo.clear()
            self.dev_combo.addItem('System default input', None)
            for i, name, fs in devs:
                self.dev_combo.addItem('%d: %s (%.0f Hz)%s' % (i, name, fs, '  [default]' if i == default_in else ''), i)
            if args.device is not None:
                idx = self.dev_combo.findText(str(args.device), QtCore.Qt.MatchContains)
                if idx >= 0:
                    self.dev_combo.setCurrentIndex(idx)

        def _update_mode_labels(self):
            if self.test_mode:
                self.mode_lbl.setText('TEST MODE: simulated stimuli, microphone not used')
                self.mode_lbl.setStyleSheet('color: white; background: #b35900; padding: 3px; font-weight: bold;')
                self.cal_lbl.setText('')
            else:
                self.mode_lbl.setText('REAL MICROPHONE MODE')
                self.mode_lbl.setStyleSheet('color: white; background: #1d6b35; padding: 3px; font-weight: bold;')
                if self.calibrated:
                    self.cal_lbl.setText('Calibrated (offset %.1f dB, %s)' % (self.off_spin.value(),
                                                                           (self.cal_info or {}).get('saved', '')))
                    self.cal_lbl.setStyleSheet('color: #1d6b35;')
                else:
                    self.cal_lbl.setText('Estimated sound level / Uncalibrated%s'
                                         % (' (manual offset)' if getattr(self, 'manual_offset', False) else ''))
                    self.cal_lbl.setStyleSheet('color: #b00000; font-weight: bold;')

        def on_offset_changed(self, val):
            self.calibrated, self.cal_info = False, None
            self.manual_offset = True
            if self.audio:
                self.audio.set_offset(val, False)
            self._update_mode_labels()

        # ------------------------------------------------------------ buttons
        def on_start(self):
            if self.engine is not None:
                return
            self.warn_lbl.setText('')
            audio = None
            if not self.test_mode:
                from audio_monitor import AudioMonitor
                dev = self.dev_combo.currentData()
                audio = AudioMonitor(device=dev, offset_db=self.off_spin.value())
                audio.calibrated = self.calibrated
                try:
                    name, fs = audio.start()
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, 'Microphone error', str(e))
                    self.warn_lbl.setText('Microphone could not be opened - the model was not started. '
                                          '(No fallback to simulated audio.)')
                    return
                audio.disarm()                       # detection enabled only after stable rest
                self.audio = audio
                self.dev_combo.setEnabled(False)
                self.log_box.appendPlainText('microphone opened: %s, %d Hz, L_Aeq,20ms every 10 ms' % (name, fs))
            for it in self.marker_items:
                for p in (self.p_hr, self.p_bp, self.p_ans):
                    try:
                        p.removeItem(it)
                    except Exception:
                        pass
            self.marker_items = []
            self.engine = Engine(audio=audio, test_mode=self.test_mode)
            self._n_log = 0
            self.engine.start()
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText('Pause')
            self.btn_end.setEnabled(True)
            self.btn_save.setEnabled(True)

        def on_pause(self):
            if not self.engine or not self.engine.ready:
                return
            if self.engine.paused:
                self.engine.resume()
                self.btn_pause.setText('Pause')
            else:
                self.engine.pause()
                self.btn_pause.setText('Resume')

        def on_end(self):
            self._shutdown()
            self.btn_start.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_end.setEnabled(False)
            if not self.test_mode:
                self.dev_combo.setEnabled(True)
            self.state_lbl.setText('State: ended (plots kept for inspection)')

        def on_save(self):
            eng = self.engine or getattr(self, '_final_engine', None)
            if eng is None:
                return
            try:
                path = save_session(eng, self.test_mode, self.audio, getattr(self, 'calibrated', False),
                                    None if self.test_mode else self.off_spin.value())
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, 'Save failed', str(e))
                return
            self.log_box.appendPlainText('saved: %s (+ _events.txt, _beats.csv)' % path)

        def _shutdown(self):
            if self.engine is not None:
                self.engine.stop()
                self.engine.join(timeout=3.0)
                self._final_engine = self.engine
                self.engine = None
            if self.audio is not None:
                self.audio.stop()                    # releases the microphone
                self.audio = None

        def closeEvent(self, ev):
            self.timer.stop()
            self._shutdown()
            ev.accept()

        def on_calibrate(self):
            from audio_monitor import AudioMonitor, save_calibration
            msg = ('Calibration (A-weighted):\n'
                   '1. Place a reference sound level meter (A-weighting, Leq or Fast) next to the computer microphone.\n'
                   '2. Play a steady sound (pink noise or 1 kHz tone), about 70-90 dB(A) at the microphone.\n'
                   '3. Enter the meter reading below and press OK while the sound is playing.\n'
                   'The program measures the A-weighted level in dBFS for 3 s and stores\n'
                   'offset = meter reading - measured dBFS in calibration.json.')
            ref, ok = QtWidgets.QInputDialog.getDouble(self, 'Calibrate sound level', msg, 80.0, 30.0, 130.0, 1)
            if not ok:
                return
            mon, own = self.audio, False
            try:
                if mon is None:
                    mon = AudioMonitor(device=self.dev_combo.currentData(), offset_db=self.off_spin.value())
                    mon.start()
                    own = True
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                dbfs = mon.measure_leq_dbfs(3.0)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, 'Calibration failed', str(e))
                return
            finally:
                QtWidgets.QApplication.restoreOverrideCursor()
                if own:
                    mon.stop()
            offset = ref - dbfs
            cal = save_calibration(offset, dict(reference_dBA=ref, measured_dBFS=float(dbfs),
                                                device=self.dev_combo.currentText(), weighting='A',
                                                window='Leq over 3 s'))
            self.off_spin.blockSignals(True)
            self.off_spin.setValue(offset)
            self.off_spin.blockSignals(False)
            self.calibrated, self.cal_info = True, cal
            self.manual_offset = False
            if self.audio:
                self.audio.set_offset(offset, True)
            self._update_mode_labels()
            QtWidgets.QMessageBox.information(self, 'Calibration saved',
                                              'Measured %.1f dBFS for %.1f dB(A): offset = %.1f dB' % (dbfs, ref, offset))

        # ------------------------------------------------------------ refresh
        def refresh(self):
            eng = self.engine or getattr(self, '_final_engine', None)
            # sound level
            if not self.test_mode:
                if self.audio is not None:
                    lvl = self.audio.level_db
                    tag = 'Sound level' if self.calibrated else 'Estimated sound level (uncalibrated)'
                    armed = self.audio.detector.armed if self.audio.detector else False
                    self.level_lbl.setText('%s: %5.1f dB(A)   |   max %.0f s: %5.1f   |   threshold %.0f dB(A)   |   detector %s'
                                           % (tag, lvl, C.PEAK_HOLD_S, self.audio.peak_db, C.TRIGGER_THRESHOLD_DB,
                                              'armed' if armed else 'not armed'))
                    self.level_bar.setValue(int(max(20, min(120, lvl if np.isfinite(lvl) else 20))))
                    self.level_bar.setStyleSheet('QProgressBar::chunk { background: %s; }'
                                                 % ('#c62828' if lvl >= C.TRIGGER_THRESHOLD_DB else '#2e7d32'))
                    if self.audio.warning:
                        self.warn_lbl.setText(self.audio.warning)
                else:
                    self.level_lbl.setText('Sound level: microphone closed   |   threshold %.0f dB(A)'
                                           % C.TRIGGER_THRESHOLD_DB)
            else:
                self.level_bar.setVisible(False)
                self.level_lbl.setText('Sound level: not measured in test mode   |   simulated trigger = "above %.0f dB"'
                                       % C.TRIGGER_THRESHOLD_DB)
            if eng is None:
                return
            if eng.error:
                self.warn_lbl.setText(eng.error)
            if self.test_mode:
                for b in (self.btn_inj_short, self.btn_inj_long):
                    b.setEnabled(self.engine is not None and eng.ready and not eng.paused)
            st = eng.state_text if self.engine is not None else ENDED
            extra = ''
            if not eng.ready and self.engine is not None and not eng.error:
                extra = ' - ' + eng.status
            elif eng.ready and eng.lag_s > 0.25:
                extra = '  (model lagging real time by %.2f s)' % eng.lag_s
            if self.engine is not None:
                self.state_lbl.setText('State: %s%s' % (st, extra))
            # log
            cnt = eng.log_count
            if cnt > self._n_log:
                lines = list(eng.log_lines)
                for ln in lines[-min(cnt - self._n_log, len(lines)):]:
                    self.log_box.appendPlainText(ln)
                self._n_log = cnt
            if self.engine is not None and eng.paused:
                return
            data = eng.buf.snapshot()
            if len(data) < 2:
                return
            t = data[:, 0]
            t_now = t[-1]
            sel = t >= t_now - C.PLOT_WINDOW_S
            ts = t[sel]
            O = M.OC
            self.c_hr.setData(ts, data[sel, O['HR_bpm']])
            self.c_p.setData(ts, data[sel, O['P_art']])
            self.c_map.setData(ts, data[sel, O['MAP_mean']])
            self.c_sns_hr.setData(ts, data[sel, O['SNS_HR']])
            self.c_psns_hr.setData(ts, data[sel, O['PSNS_HR']])
            self.c_sns_bp.setData(ts, data[sel, O['SNS_BP']])
            self.c_psns_bp.setData(ts, data[sel, O['PSNS_BP']])
            x0 = max(t_now - C.PLOT_WINDOW_S, t[0])
            x1 = x0 + C.PLOT_WINDOW_S
            beats = [b for b in list(eng.beats) if b[0] >= x0]
            if beats:
                bt = np.array(beats)
                self.s_sbp.setData(bt[:, 0], bt[:, 1])
                self.s_dbp.setData(bt[:, 2], bt[:, 3])
            self.p_hr.setXRange(x0, x1, padding=0.0)
            last = data[-1]
            self.hr_txt.setText('HR %.1f bpm' % last[O['HR_bpm']])
            self.hr_txt.setPos(x1, self.p_hr.viewRange()[1][1])
            sbp_dbp = ('   SBP/DBP %.1f / %.1f' % (beats[-1][1], beats[-1][3])) if beats else ''
            self.bp_txt.setText('MAP %.1f mmHg%s' % (last[O['MAP_mean']], sbp_dbp))
            self.bp_txt.setPos(x1, self.p_bp.viewRange()[1][1])
            # event markers
            have = {(round(it.value(), 4), it.kind) for it in self.marker_items[::3]} if self.marker_items else set()
            for tm, kind in list(eng.markers):
                tm_rel = tm - eng.t0_model
                if tm_rel < x0 or (round(tm_rel, 4), kind) in have:
                    continue
                col = (220, 0, 0) if kind == 'stimulus' else (0, 150, 60)
                for i, p in enumerate((self.p_hr, self.p_bp, self.p_ans)):
                    it = pg.InfiniteLine(pos=tm_rel, angle=90, pen=pg.mkPen(col, width=1.5),
                                         label=(('stimulus' if kind == 'stimulus' else 'recovery')
                                                if i == 0 else None),
                                         labelOpts=dict(position=0.93 if kind == 'stimulus' else 0.80, color=col,
                                                        anchors=[(0, 0.5), (0, 0.5)]))
                    it.kind = kind
                    p.addItem(it)
                    self.marker_items.append(it)
            keep = []
            for k in range(0, len(self.marker_items), 3):
                grp = self.marker_items[k:k + 3]
                if grp[0].value() < x0:
                    for i, p in enumerate((self.p_hr, self.p_bp, self.p_ans)):
                        p.removeItem(grp[i])
                else:
                    keep.extend(grp)
            self.marker_items = keep

    return MainWindow()


def main():
    args = parse_args()
    from PyQt5 import QtCore, QtWidgets
    # crisp scaling on high-DPI screens (Windows / Linux); must be set before QApplication
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QtWidgets.QApplication(sys.argv)
    w = create_window(args)
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()