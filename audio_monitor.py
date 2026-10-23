"""
audio_monitor.py
================
Real-microphone sound level and trigger detection.

Level metric
    A-weighted (IEC 61672 analogue A-weighting, bilinear-transformed) RMS over a
    20 ms rectangular window, updated every 10 ms:  L_Aeq,20ms.
    L_dBFS = 10 log10(mean square) + 3.01   (0 dBFS = RMS of a full-scale sine)
    L_SPL  = L_dBFS + calibration offset     (dB(A) re 20 uPa)
    Without a saved calibration the offset is an assumption and the level is
    an ESTIMATE ("Estimated sound level / Uncalibrated").

Trigger (onset)
    L >= threshold (85 dB(A)) while armed and not already inside a sound.
Sound end
    L < threshold - hysteresis (79 dB(A)) continuously for RELEASE_HOLD_S
    (150 ms).  The end event is issued when that hold is complete.
Debouncing
    One sound = one onset.  A new onset is possible only after the sound-end
    criterion has been met.  After (re)arming, the detector first waits for
    the sound-end criterion so a sound already in progress is not counted.
"""
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime

import numpy as np
from scipy import signal

import ans_cvs_config as C


def a_weighting_sos(fs):
    """Digital A-weighting filter (second-order sections), 0 dB at 1 kHz.
    Analogue IEC 61672 poles, pre-warped for the bilinear transform."""
    f1, f2, f3, f4 = 20.598997, 107.65265, 737.86223, 12194.217
    w = [2 * fs * np.tan(np.pi * f / fs) for f in (f1, f2, f3, f4)]
    z = [0.0] * 4
    p = [-w[0], -w[0], -w[1], -w[2], -w[3], -w[3]]
    k = 1.0
    zd, pd, kd = signal.bilinear_zpk(z, p, k, fs)
    sos = signal.zpk2sos(zd, pd, kd)
    _, h = signal.sosfreqz(sos, worN=[1000.0], fs=fs)
    sos[0, :3] /= abs(h[0])
    return sos


class LevelDetector:
    """Block-wise level computation and onset/end detection (no audio I/O)."""

    def __init__(self, fs, offset_db, threshold_db=C.TRIGGER_THRESHOLD_DB,
                 hysteresis_db=C.HYSTERESIS_DB, release_hold_s=C.RELEASE_HOLD_S,
                 window_s=C.WINDOW_S, weighting=C.FREQUENCY_WEIGHTING):
        self.fs = fs
        self.offset_db = offset_db
        self.threshold_db = threshold_db
        self.release_db = threshold_db - hysteresis_db
        self.release_hold_s = release_hold_s
        self.window_s = window_s
        self.sos = a_weighting_sos(fs) if weighting == 'A' else None
        self.zi = np.zeros((self.sos.shape[0], 2)) if self.sos is not None else None
        self.ms_hist = []            # (n_samples, sum of squares) of recent blocks
        self.in_sound = False
        self.below_since = None
        self.armed = False
        self.need_quiet = True
        self.level_db = -np.inf
        self.level_dbfs = -np.inf
        self.last_block = (0, 0.0)
        self.sound_max_db = -np.inf    # max level of the current/last detected sound

    def arm(self):
        """Enable onsets; a sound in progress must end before the next onset."""
        self.armed = True
        self.need_quiet = True
        self.in_sound = True
        self.below_since = None

    def disarm(self):
        self.armed = False

    def process(self, x, t_end):
        """x: mono float block; t_end: wall time of the last sample.
        Returns list of events (kind, t, level_db)."""
        if self.sos is not None:
            y, self.zi = signal.sosfilt(self.sos, x, zi=self.zi)
        else:
            y = x
        self.last_block = (len(y), float(np.dot(y, y)))
        self.ms_hist.append(self.last_block)
        n_win = int(round(self.window_s * self.fs))
        tot_n, tot_s, keep = 0, 0.0, []
        for n, s2 in reversed(self.ms_hist):
            keep.append((n, s2))
            tot_n += n
            tot_s += s2
            if tot_n >= n_win:
                break
        self.ms_hist = list(reversed(keep))
        ms = tot_s / max(tot_n, 1)
        self.level_dbfs = 10.0 * np.log10(ms + 1e-20) + 3.0103
        self.level_db = self.level_dbfs + self.offset_db
        events = []
        if not self.armed:
            return events
        if self.in_sound and not self.need_quiet:
            self.sound_max_db = max(self.sound_max_db, self.level_db)
        if self.in_sound:
            if self.level_db < self.release_db:
                if self.below_since is None:
                    self.below_since = t_end
                elif t_end - self.below_since >= self.release_hold_s:
                    self.in_sound = False
                    self.below_since = None
                    if self.need_quiet:
                        self.need_quiet = False      # arming wait finished, no event
                    else:
                        events.append(('end', t_end, self.sound_max_db))   # level = max of the sound
            else:
                self.below_since = None
        elif self.level_db >= self.threshold_db:
            self.in_sound = True
            self.below_since = None
            self.sound_max_db = self.level_db
            events.append(('onset', t_end, self.level_db))
        return events


def mic_help():
    """Where to look when the microphone is blocked or silent (depends on the OS)."""
    if sys.platform == 'darwin':
        return ('macOS: System Settings > Privacy & Security > Microphone - allow the app that runs Python '
                '(e.g. Terminal).')
    if sys.platform.startswith('win'):
        return ('Windows: Settings > Privacy & security > Microphone - turn on "Microphone access" and '
                '"Let desktop apps access your microphone"; check that the input is not muted '
                '(Settings > System > Sound > Input).')
    return ('Linux: check that the input is not muted and is the selected device (Settings > Sound > Input, '
            'or pavucontrol > Input Devices / Recording).')


def import_sounddevice():
    try:
        import sounddevice as sd
    except OSError as e:          # PortAudio shared library missing (Linux)
        raise RuntimeError('sounddevice could not load the PortAudio library (%s).\n'
                           'Ubuntu: sudo apt install libportaudio2' % e)
    return sd


def load_calibration(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), C.CALIBRATION_FILE)
    try:
        with open(path, encoding='utf-8') as f:
            cal = json.load(f)
        return float(cal['offset_db']), True, cal
    except Exception:
        return C.CAL_OFFSET_DB_DEFAULT, False, None


def save_calibration(offset_db, info, path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), C.CALIBRATION_FILE)
    cal = dict(offset_db=float(offset_db), saved=datetime.now().isoformat(timespec='seconds'), **info)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cal, f, indent=2)
    return cal


def list_input_devices():
    """[(index, 'name [host API]', default sample rate)], default input index.
    Windows lists the same microphone once per host API (MME, DirectSound, WASAPI, WDM-KS)."""
    sd = import_sounddevice()
    apis = [h['name'] for h in sd.query_hostapis()]
    devs = []
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0:
            api = apis[d['hostapi']] if 0 <= d['hostapi'] < len(apis) else '?'
            devs.append((i, '%s [%s]' % (d['name'], api), d['default_samplerate']))
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    return devs, default_in


class AudioMonitor:
    """Opens the microphone (sounddevice/PortAudio) and runs LevelDetector on
    every 10 ms block in the audio callback thread.  Events go to self.events."""

    def __init__(self, device=None, offset_db=None):
        self.device = device
        off, calibrated, _ = load_calibration()
        self.offset_db = off if offset_db is None else offset_db
        self.calibrated = calibrated
        self.events = queue.Queue()
        self.stream = None
        self.detector = None
        self.fs = None
        self.error = None
        self.warning = None
        self.lock = threading.Lock()
        self.zero_blocks = 0
        self.overflows = 0
        self.blocks = 0
        self._cal_acc = None
        self.peak_db = -np.inf          # display peak hold (PEAK_HOLD_S)
        self._peak_t = 0.0
        self._nsamp = 0                 # samples received: the detector runs on this sample clock

    # ---------------------------------------------------------------- control
    def start(self):
        sd = import_sounddevice()
        try:
            info = sd.query_devices(self.device, 'input')
        except Exception as e:
            raise RuntimeError('No usable input device (%s). Check that a microphone is connected '
                               'and selected.\n%s' % (e, mic_help()))
        fs = int(C.SAMPLE_RATE or info['default_samplerate'])
        self.fs = fs
        self.detector = LevelDetector(fs, self.offset_db)
        block = int(round(C.BLOCK_S * fs))
        try:
            self.stream = sd.InputStream(device=self.device, channels=1, samplerate=fs,
                                         blocksize=block, dtype='float32', callback=self._callback)
            self.stream.start()
        except Exception as e:
            self.stream = None
            raise RuntimeError('Could not open the microphone "%s": %s\n%s' % (info['name'], e, mic_help()))
        self.device_name = info['name']
        return info['name'], fs

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None

    def arm(self):
        with self.lock:
            if self.detector:
                self.detector.arm()
        self.flush()

    def disarm(self):
        with self.lock:
            if self.detector:
                self.detector.disarm()
        self.flush()

    def flush(self):
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass

    def set_offset(self, offset_db, calibrated):
        with self.lock:
            self.offset_db = offset_db
            self.calibrated = calibrated
            if self.detector:
                self.detector.offset_db = offset_db

    @property
    def level_db(self):
        d = self.detector
        return d.level_db if d else float('nan')

    @property
    def level_dbfs(self):
        d = self.detector
        return d.level_dbfs if d else float('nan')

    # ---------------------------------------------------------------- calibration
    def measure_leq_dbfs(self, seconds=3.0):
        """A-weighted Leq in dBFS over `seconds` of live input (for calibration)."""
        self._cal_acc = [0, 0.0]
        time.sleep(seconds)
        n, s2 = self._cal_acc
        self._cal_acc = None
        if n == 0:
            raise RuntimeError('no audio received during calibration')
        return 10.0 * np.log10(s2 / n + 1e-20) + 3.0103

    # ---------------------------------------------------------------- callback
    def _callback(self, indata, frames, time_info, status):
        t_wall = time.perf_counter()
        self._nsamp += len(indata)
        t_end = self._nsamp / float(self.fs)          # sample clock (s), immune to callback jitter
        if status:
            if status.input_overflow:
                self.overflows += 1
        x = indata[:, 0].astype(np.float64)
        self.blocks += 1
        if not np.any(x):
            self.zero_blocks += 1
            if self.zero_blocks * C.BLOCK_S > 3.0:
                self.warning = 'The microphone delivers only zeros. Access may be blocked. ' + mic_help()
        else:
            self.zero_blocks = 0
            self.warning = None
        with self.lock:
            evs = self.detector.process(x, t_end)
            lvl = self.detector.level_db
            if lvl >= self.peak_db or t_end - self._peak_t > C.PEAK_HOLD_S:
                self.peak_db, self._peak_t = lvl, t_end
            if self._cal_acc is not None:
                n, s2 = self.detector.last_block          # same A-weighted signal as the detector
                self._cal_acc[0] += n
                self._cal_acc[1] += s2
        for kind, t_ev, level in evs:
            # report in wall-clock time for the model thread (event belongs to the current block)
            self.events.put((kind, t_wall - (t_end - t_ev), level))
