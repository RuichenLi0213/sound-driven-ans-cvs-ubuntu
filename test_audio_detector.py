"""
test_audio_detector.py
======================
Part A (always): the sound-level / trigger logic with SYNTHETIC signals, fed to
the same LevelDetector class the microphone path uses (no audio hardware).
Part B (--live): REAL MICROPHONE check.  Opens the selected input for a few
seconds, reports device, sample rate and measured levels.  Nothing is
injected: an onset is reported only if the room sound really exceeds the
threshold.

Run:  python test_audio_detector.py            (Part A)
      python test_audio_detector.py --live 10  (Part A + 10 s of live microphone)
"""
import argparse
import sys
import time

import numpy as np
from scipy import signal

import ans_cvs_config as C
from audio_monitor import LevelDetector, a_weighting_sos, AudioMonitor, list_input_devices

FS = 48000
BLOCK = int(C.BLOCK_S * FS)
OFFSET = 120.0            # test offset: 0 dBFS (full-scale sine RMS) = 120 dB SPL


def amp_for_level(db_spl):
    """Peak amplitude of a 1 kHz sine giving db_spl with OFFSET (A-weight = 0 dB at 1 kHz)."""
    return 10 ** ((db_spl - OFFSET) / 20.0)


def run(det, x, t0=0.0):
    evs, levels = [], []
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        t_end = t0 + (i + BLOCK) / FS
        evs += det.process(x[i:i + BLOCK], t_end)
        levels.append((t_end, det.level_db))
    return evs, np.array(levels)


def tone(db, dur, f=1000.0):
    t = np.arange(int(dur * FS)) / FS
    return amp_for_level(db) * np.sin(2 * np.pi * f * t)


def silence(dur, db=40.0):
    rng = np.random.default_rng(0)
    return rng.normal(0, 1, int(dur * FS)) * 10 ** ((db - OFFSET - 3.0103) / 20.0)   # ~db dB(Z) noise


def clap(db_peak_window, dur=0.006):
    """Clap-like burst: exponentially decaying broadband noise (main pulse ~6 ms)."""
    rng = np.random.default_rng(1)
    n = int(0.05 * FS)
    t = np.arange(n) / FS
    x = rng.normal(0, 1, n) * np.exp(-t / (dur / 3))
    b, a = signal.butter(2, [400, 4000], btype='band', fs=FS)
    return signal.lfilter(b, a, x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', type=float, default=0.0, help='seconds of live microphone test (Part B)')
    ap.add_argument('--device', default=None)
    args = ap.parse_args()
    checks = {}

    print('PART A: trigger logic with synthetic signals (no microphone)')
    sos = a_weighting_sos(FS)
    f = np.array([31.5, 100.0, 1000.0, 4000.0, 10000.0])
    _, h = signal.sosfreqz(sos, worN=f, fs=FS)
    gains = 20 * np.log10(np.abs(h))
    ref = np.array([-39.4, -19.1, 0.0, 1.0, -2.5])        # IEC 61672 A-weighting (dB)
    print('  A-weighting gain at %s Hz: %s dB (IEC: %s)' % (f.tolist(), np.round(gains, 2).tolist(), ref.tolist()))
    checks['A-weighting within 0.5 dB of IEC at 31.5 Hz-4 kHz, 1 dB at 10 kHz'] = (
        np.all(np.abs(gains[:4] - ref[:4]) < 0.5) and abs(gains[4] - ref[4]) < 1.0)

    det = LevelDetector(FS, OFFSET)
    _, lv = run(det, tone(94.0, 0.5))
    print('  1 kHz tone generated at 94.0 dB -> measured %.2f dB(A)' % lv[-1, 1])
    checks['level of a 1 kHz tone reproduced within 0.1 dB'] = abs(lv[-1, 1] - 94.0) < 0.1

    # not armed -> no events
    det = LevelDetector(FS, OFFSET)
    ev, _ = run(det, np.concatenate([silence(0.3), tone(95, 0.3), silence(0.5)]))
    checks['no events while not armed'] = len(ev) == 0

    # sustained tone above threshold: exactly one onset and one end, end after release hold
    det = LevelDetector(FS, OFFSET)
    det.arm()
    x = np.concatenate([silence(0.5), tone(90, 2.0), silence(1.0)])
    ev, lv = run(det, x)
    kinds = [e[0] for e in ev]
    print('  sustained 90 dB tone 0.5-2.5 s: events %s' % [(k, round(t, 3)) for k, t, _ in ev])
    checks['sustained tone: one onset, one end'] = kinds == ['onset', 'end']
    if kinds == ['onset', 'end']:
        checks['onset within 20 ms of tone start'] = 0.5 <= ev[0][1] <= 0.52
        lag = ev[1][1] - 2.5
        print('  sound-end event %.3f s after the tone stopped (release hold %.2f s + 20 ms window + block)' % (lag, C.RELEASE_HOLD_S))
        checks['end issued release hold + window (+ one block) after the sound stops'] = (
            C.RELEASE_HOLD_S <= lag <= C.RELEASE_HOLD_S + C.WINDOW_S + 2 * C.BLOCK_S + 1e-6)

    # below threshold tone: nothing
    det = LevelDetector(FS, OFFSET)
    det.arm()
    ev, _ = run(det, np.concatenate([silence(0.5), tone(83, 2.0), silence(0.5)]))
    checks['83 dB tone (below 85) never triggers'] = len(ev) == 0

    # fluctuating around the threshold: hysteresis prevents chatter
    det = LevelDetector(FS, OFFSET)
    det.arm()
    t = np.arange(int(3.0 * FS)) / FS
    env_db = 85.0 + 2.5 * np.sin(2 * np.pi * 3.0 * t)             # 82.5 - 87.5 dB, 3 Hz
    x = 10 ** ((env_db - OFFSET) / 20.0) * np.sin(2 * np.pi * 1000 * t)
    ev, _ = run(det, np.concatenate([silence(0.5), x, silence(1.0)]))
    print('  level oscillating 82.5-87.5 dB for 3 s: %d onsets, %d ends' % ([e[0] for e in ev].count('onset'),
                                                                          [e[0] for e in ev].count('end')))
    checks['threshold chatter (+-2.5 dB, 3 Hz) counted as one sound'] = [e[0] for e in ev] == ['onset', 'end']

    # clap-like bursts
    burst = clap(0)
    det = LevelDetector(FS, OFFSET)
    _, lv = run(det, np.concatenate([np.zeros(BLOCK * 5), burst / np.max(np.abs(burst))]))
    lmax_full = lv[:, 1].max()              # L_Aeq,20ms of a full-scale-peak burst
    gain_db = 98.0 - lmax_full              # scale so the burst reads 98 dB(A) L_Aeq,20ms
    loud = burst / np.max(np.abs(burst)) * 10 ** (gain_db / 20)
    quiet = loud * 10 ** (-20 / 20)         # 78 dB(A)
    det = LevelDetector(FS, OFFSET)
    det.arm()
    x = np.concatenate([silence(0.5), loud, silence(1.0), quiet, silence(1.0), loud, silence(0.10), loud, silence(1.0),
                        loud, silence(0.40), loud, silence(1.0)])
    ev, lv = run(det, x)
    on = [round(e[1], 3) for e in ev if e[0] == 'onset']
    print('  clap-like bursts (98 dB(A) L_Aeq,20ms; one at 78 dB(A); pairs 0.10 s and 0.40 s apart): onsets at %s' % on)
    checks['single loud clap -> 1 onset; quiet clap -> none'] = len(on) >= 1 and 0.5 <= on[0] <= 0.53
    checks['two claps 0.10 s apart counted as one sound (release hold)'] = len(on) == 4
    checks['two claps 0.40 s apart counted as two sounds'] = len(on) == 4

    # arming while a sound is in progress: waits for quiet first
    det = LevelDetector(FS, OFFSET)
    ev1, _ = run(det, tone(95, 0.5))
    det.arm()
    ev2, _ = run(det, np.concatenate([tone(95, 1.0), silence(0.5), tone(95, 0.5), silence(0.5)]))
    print('  armed during an ongoing 95 dB sound: events %s' % [(k, round(t, 3)) for k, t, _ in ev2])
    checks['sound in progress at arming is not counted; next sound is'] = [e[0] for e in ev2] == ['onset', 'end']

    # end event carries the maximum level of the sound
    det = LevelDetector(FS, OFFSET)
    det.arm()
    ev, _ = run(det, np.concatenate([silence(0.5), tone(88, 0.3), tone(96, 0.2), tone(88, 0.3), silence(0.5)]))
    checks['sound-end event reports the max level of the sound (96 dB)'] = (
        [e[0] for e in ev] == ['onset', 'end'] and abs(ev[1][2] - 96.0) < 0.3)

    # AudioMonitor callback path with synthetic blocks (no audio device is opened)
    mon = AudioMonitor(device=None, offset_db=OFFSET)
    mon.fs = FS
    mon.detector = LevelDetector(FS, OFFSET)
    mon.arm()
    x = np.concatenate([silence(0.5), tone(92, 0.3), silence(0.6)]).astype(np.float32)
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        mon._callback(x[i:i + BLOCK, None], BLOCK, None, None)
    evs = []
    while not mon.events.empty():
        evs.append(mon.events.get())
    checks['monitor callback path: one onset + one end, peak hold ~92 dB'] = (
        [e[0] for e in evs] == ['onset', 'end'] and abs(mon.peak_db - 92.0) < 0.5)

    for k, v in checks.items():
        print('  [%s] %s' % ('PASS' if v else 'FAIL', k))
    n_fail = sum(not v for v in checks.values())

    if args.live > 0:
        print('\nPART B: REAL MICROPHONE (%.0f s, nothing injected)' % args.live)
        try:
            devs, default_in = list_input_devices()
            print('  input devices: %s; default input index %s' % ([(i, n) for i, n, _ in devs], default_in))
            dev = int(args.device) if args.device and args.device.isdigit() else args.device
            mon = AudioMonitor(device=dev)
            name, fs = mon.start()
            print('  opened "%s" at %d Hz; calibration offset %.1f dB (%s)'
                  % (name, fs, mon.offset_db, 'calibrated' if mon.calibrated else 'UNCALIBRATED default'))
            mon.arm()
            lv, t_end = [], time.time() + args.live
            while time.time() < t_end:
                time.sleep(0.05)
                lv.append((mon.level_dbfs, mon.level_db))
            mon.stop()
            lv = np.array([x for x in lv if np.isfinite(x[0])])
            evs = []
            while not mon.events.empty():
                evs.append(mon.events.get())
            print('  blocks received %d, all-zero blocks at end %d, overflows %d, warning: %s'
                  % (mon.blocks, mon.zero_blocks, mon.overflows, mon.warning))
            if len(lv):
                print('  L_Aeq,20ms: %.1f to %.1f dBFS (median %.1f)  ->  estimated %.1f to %.1f dB(A) with offset %.1f'
                      % (lv[:, 0].min(), lv[:, 0].max(), np.median(lv[:, 0]), lv[:, 1].min(), lv[:, 1].max(), mon.offset_db))
            print('  trigger events during the live test: %s' % [(k, round(l, 1)) for k, _, l in evs])
        except Exception as e:
            print('  live microphone test could not run: %s' % e)
            n_fail += 1
    return n_fail


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
