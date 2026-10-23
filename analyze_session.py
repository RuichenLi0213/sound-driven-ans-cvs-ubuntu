"""
analyze_session.py
==================
Evaluates a session saved with the "Save data" button (sessions/session_*.csv).
For every detected stimulus in the saved window it reports the same metrics as
the simulated-stimulus test: time to 95 % of the target increase (HR, cycle-mean
MAP, one-cycle running MAP, beat MAP), values at 1 s, peaks, recovery time to the
5 % band, residual error 5 s after recovery onset, and SBP/DBP.

    python analyze_session.py                       newest session in sessions/
    python analyze_session.py sessions/session_20261020_141500.csv
"""
import glob
import os
import sys

import numpy as np

import ans_cvs_config as C
from test_simulated_stimulus import response_metrics


def load(path):
    data = np.genfromtxt(path, delimiter=',', skip_header=1)
    events = path[:-4] + '_events.txt'
    markers = []
    with open(events, encoding='utf-8') as f:
        lines = f.read().splitlines()
    if 'markers (t, kind):' in lines:
        for ln in lines[lines.index('markers (t, kind):') + 1:]:
            if ',' in ln:
                t, kind = ln.split(',', 1)
                markers.append((float(t), kind.strip()))
    log = [ln for ln in lines if ln[:2].isdigit()]
    return data, markers, log


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        files = sorted(f for f in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         C.SESSIONS_DIR, 'session_*.csv'))
                       if not f.endswith('_beats.csv'))
        if not files:
            print('no saved session found in %s/' % C.SESSIONS_DIR)
            return 1
        path = files[-1]
    data, markers, log = load(path)
    t = data[:, 0]
    print('session: %s  (%.1f s of data, t = %.1f to %.1f s)' % (os.path.basename(path), t[-1] - t[0], t[0], t[-1]))
    print('event log lines with stimuli / sound end:')
    for ln in log:
        if 'STIMULUS' in ln or 'sound ended' in ln or 'stimulus during' in ln:
            print('   ' + ln)
    stims = [m[0] for m in markers if m[1] == 'stimulus' and m[0] >= t[0]]
    recs = [m[0] for m in markers if m[1] == 'recovery']
    if not stims:
        print('no stimulus inside the saved window')
        return 0
    inc_hr = C.HR_ELEVATED_BPM - C.HR_REST_BPM
    inc_map = C.MAP_ELEVATED_MMHG - C.MAP_REST_MMHG
    print('\ncriteria: 95%% of increase within %.1f s; within %.0f%% band <= %.1f s after recovery onset'
          % (C.RISE_MIN_S, 100 * C.RECOVERY_BAND, C.RECOVERY_LIMIT_S))
    print('%-8s %-8s %-7s %-7s %-7s %-7s %-7s %-7s %-7s %-7s' % ('stim t', 'rec t', 'HRt95', 'MAPt95', 'MAPrun',
                                                                 'beatMAP', 'HR@1s', 'MAP@1s', 'HRrec', 'MAPrec'))
    for i, ts in enumerate(stims):
        tr = [r for r in recs if r > ts]
        if not tr:
            print('%-8.2f recovery not in saved window' % ts)
            continue
        t_next = stims[i + 1] if i + 1 < len(stims) else None
        if t_next is not None and tr[0] > t_next:
            print('%-8.2f re-triggered before recovery' % ts)
            continue
        m = response_metrics(data, ts, tr[0], t_next=t_next)
        ok = (m['HR_t95_s'] <= C.RISE_MIN_S and m['MAP_t95_s'] <= C.RISE_MIN_S and
              m['HR_recovery_s'] <= C.RECOVERY_LIMIT_S and m['MAP_recovery_s'] <= C.RECOVERY_LIMIT_S)
        print('%-8.2f %-8.2f %-7.3f %-7.3f %-7.3f %-7.3f %-7.2f %-7.2f %-7.2f %-7.2f %s'
              % (ts, tr[0], m['HR_t95_s'], m['MAP_t95_s'], m['MAP_run_t95_s'], m['beat_MAP_t95_s'], m['HR_at_1s'],
                 m['MAP_at_1s'], m['HR_recovery_s'], m['MAP_recovery_s'], 'PASS' if ok else 'CHECK'))
        print('         peaks HR %.2f MAP %.2f | SBP/DBP before %.1f/%.1f, max %.1f/%.1f | error 5 s after recovery: '
              'HR %+.3f MAP %+.3f' % (m['HR_peak'], m['MAP_peak'], m['SBP_rest'], m['DBP_rest'], m['SBP_max_activation'],
                                      m['DBP_max_activation'], m['HR_err_5s_after_recovery'], m['MAP_err_5s_after_recovery']))
    print('\n(nan = not reached before recovery onset; times in s; MAP = model cycle-mean MAP; '
          'targets HR %.2f -> %.2f bpm, MAP %.2f -> %.2f mmHg, increase %.2f / %.2f)'
          % (C.HR_REST_BPM, C.HR_ELEVATED_BPM, C.MAP_REST_MMHG, C.MAP_ELEVATED_MMHG, inc_hr, inc_map))
    return 0


if __name__ == '__main__':
    sys.exit(main())
