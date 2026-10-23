"""
test_realtime_engine.py
=======================
Headless test of the real-time Engine thread (TEST MODE, no microphone, no GUI):
  * model time stays synchronised with wall-clock time
  * a simulated stimulus injected through the test-mode path is applied with low latency
  * Pause: model time and recovery stop, injected stimuli are rejected,
    Resume continues from the same state without skipping the paused interval
  * ring buffers stay bounded
  * stop() ends the thread
Run:  python test_realtime_engine.py      (takes about 25 s of wall-clock time)
"""
import sys
import time

import numpy as np

import ans_cvs_config as C
import ans_cvs_model as M
from ans_cvs_realtime import Engine
from stimulus_controller import REST, RECOVERY, PAUSED


def main():
    results = {}
    eng = Engine(audio=None, test_mode=True)
    t_start = time.perf_counter()
    eng.start()
    while not eng.ready and eng.error is None and time.perf_counter() - t_start < 120:
        time.sleep(0.05)
    if eng.error:
        print('FAIL: engine error', eng.error)
        return 1
    print('engine ready after %.1f s (parameter derivation, JIT compile, spin-up and settle)' % (time.perf_counter() - t_start))
    wall0 = time.perf_counter()
    model0 = eng.model.get('t')

    # 1. synchronisation over 4 s
    time.sleep(4.0)
    dw, dm = time.perf_counter() - wall0, eng.model.get('t') - model0
    results['sync_error_after_4s_ms'] = (dw - dm) * 1e3
    results['state_before_stimulus'] = eng.state_text

    # 2. simulated stimulus (brief) through the test-mode injection
    eng.inject_test_stimulus(0.0)
    t_inj_model = eng.model.get('t')
    time.sleep(0.9)                                   # inside the 1 s rise window
    hr_09 = eng.model.get('HR_bps') * 60
    time.sleep(0.3)                                   # now past the 1 s latch -> recovery
    state_12 = eng.state_text
    # 3. pause during recovery
    eng.pause()
    time.sleep(0.05)
    tm_p = eng.model.get('t')
    hr_p = eng.model.get('HR_bps') * 60
    eng.inject_test_stimulus(0.0)                     # must be ignored while paused
    time.sleep(2.0)
    tm_p2 = eng.model.get('t')
    results['model_time_advance_during_2s_pause_ms'] = (tm_p2 - tm_p) * 1e3
    results['HR_change_during_pause_bpm'] = eng.model.get('HR_bps') * 60 - hr_p
    results['state_while_paused'] = eng.state_text
    eng.resume()
    time.sleep(0.2)
    tm_r = eng.model.get('t')
    results['model_time_advance_200ms_after_resume_ms'] = (tm_r - tm_p2) * 1e3
    time.sleep(5.0)
    results['state_6s_after_resume'] = eng.state_text
    results['HR_after_resume_recovery'] = eng.model.get('HR_bps') * 60
    stimuli = [m for m in eng.markers if m[1] == 'stimulus']
    results['n_stimuli_applied'] = len(stimuli)
    results['stimulus_latency_ms'] = (stimuli[0][0] - t_inj_model) * 1e3 if stimuli else float('nan')
    results['HR_0.9s_after_injection'] = hr_09
    results['state_1.2s_after_injection'] = state_12
    # 4. sync after pause: model time = wall time minus paused time
    dw, dm = time.perf_counter() - wall0 - eng.paused_total, eng.model.get('t') - model0
    results['sync_error_excluding_pause_ms'] = (dw - dm) * 1e3
    results['buffer_rows'] = eng.buf.n
    results['buffer_capacity_rows'] = len(eng.buf.a)
    eng.stop()
    eng.join(timeout=3.0)
    results['thread_stopped'] = not eng.is_alive()
    for k, v in results.items():
        print('%-42s %s' % (k, v))
    checks = {
        'sync error < 50 ms': abs(results['sync_error_after_4s_ms']) < 50 and abs(results['sync_error_excluding_pause_ms']) < 50,
        'rest before stimulus': results['state_before_stimulus'] == REST,
        'stimulus applied once (pause injection rejected)': results['n_stimuli_applied'] == 1,
        'injection latency < 20 ms': results['stimulus_latency_ms'] < 20,
        'HR at >= 95% of the increase 0.9 s after injection (within the 1 s rise)':
            results['HR_0.9s_after_injection'] > C.HR_REST_BPM + 0.95 * (C.HR_ELEVATED_BPM - C.HR_REST_BPM),
        'recovery after the 1 s latch': results['state_1.2s_after_injection'] == RECOVERY,
        'model frozen while paused': results['model_time_advance_during_2s_pause_ms'] < 1.0 and
                                     abs(results['HR_change_during_pause_bpm']) < 1e-9 and results['state_while_paused'] == PAUSED,
        'resume continues without skipping (advance ~200 ms, not 2.2 s)':
            100 < results['model_time_advance_200ms_after_resume_ms'] < 400,
        'back at rest after resume': results['state_6s_after_resume'] == REST and
                                     abs(results['HR_after_resume_recovery'] - C.HR_REST_BPM) < 0.05,
        'buffers bounded': results['buffer_rows'] <= results['buffer_capacity_rows'],
        'thread stopped': results['thread_stopped'],
    }
    print()
    for k, v in checks.items():
        print('[%s] %s' % ('PASS' if v else 'FAIL', k))
    return sum(not v for v in checks.values())


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
