"""
test_simulated_stimulus.py
==========================
MODEL TESTING WITH SIMULATED STIMULI (no microphone).

Uses the same ANS-CVS model, the same start-up (stable rest), the same
StimulusController and the same Engine.apply_event()/_advance_to() code as the
real-time program.  Only the source of the stimulus event differs: the event
"an acoustic stimulus above 85 dB has been detected" is injected at chosen
model times, followed by a sound-end event issued the way the detector issues
it (RELEASE_HOLD_S after the sound stops).

Run:  python test_simulated_stimulus.py
Writes test_output/simulated_stimulus_report.txt, .json and .png
"""
import json
import os
import sys

import numpy as np

import ans_cvs_config as C
import ans_cvs_design as D
import ans_cvs_model as M
from ans_cvs_realtime import Engine
from stimulus_controller import REST, RISE, SUSTAINED, RECOVERY

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'test_output')
INC_HR = C.HR_ELEVATED_BPM - C.HR_REST_BPM
INC_MAP = C.MAP_ELEVATED_MMHG - C.MAP_REST_MMHG
O = M.OC


class Harness:
    """Engine driven in model time (not wall-clock) with a full-resolution recorder."""

    def __init__(self, params_override=None):
        self.eng = Engine(audio=None, test_mode=True)
        if params_override:
            self._override = params_override
            orig = D.base_params

            def patched():
                p = orig()
                p.update(params_override)
                return p
            D.base_params = patched
            try:
                self.eng._initialise()
            finally:
                D.base_params = orig
        else:
            self.eng._initialise()
        self.rows = []
        orig_record = self.eng._record

        def rec(rows):
            orig_record(rows)
            if len(rows):
                r = rows.copy()
                r[:, 0] -= self.eng.t0_model
                self.rows.append(r)
        self.eng._record = rec
        self.states = []          # (t_rel, state) after every chunk
        orig_check = self.eng._check_transition

        def chk():
            orig_check()
            self.states.append((self.eng.model.get('t') - self.eng.t0_model, self.eng.ctrl.state))
        self.eng._check_transition = chk

    @property
    def t(self):
        return self.eng.model.get('t') - self.eng.t0_model

    def run_until(self, t_rel):
        while self.t < t_rel - 0.5 * C.DT:
            self.eng._advance_to(self.eng.t0_model + t_rel)

    def event(self, kind, t_rel):
        self.run_until(t_rel)
        self.eng.apply_event(kind, self.eng.t0_model + t_rel, None)

    def stimulus(self, t_on, sound_dur):
        """Simulated detection at t_on; detector-style end RELEASE_HOLD_S after the sound stops."""
        self.event('onset', t_on)
        self.event('end', t_on + sound_dur + C.RELEASE_HOLD_S)

    def data(self):
        return np.vstack(self.rows)

    def log(self):
        return [(t - self.eng.t0_model, e, d) for t, e, d in self.eng.ctrl.log]


# ---------------------------------------------------------------------------------- metrics
def first_time(t, cond):
    i = np.nonzero(cond)[0]
    return float(t[i[0]]) if len(i) else float('nan')


def response_metrics(d, t_on, t_rec, t_next=None):
    """Rise and recovery metrics for one stimulus (times relative to t_on / t_rec)."""
    t = d[:, O['t']]
    res = {}
    end = t_next if t_next is not None else t[-1]
    for name, col, x_r, inc in (('HR', 'HR_bpm', C.HR_REST_BPM, INC_HR), ('MAP', 'MAP_mean', C.MAP_REST_MMHG, INC_MAP),
                                ('MAP_run', 'MAP_run', C.MAP_REST_MMHG, INC_MAP)):
        x = d[:, O[col]]
        act = (t >= t_on) & (t <= t_rec)
        res[name + '_t95_s'] = first_time(t[act] - t_on, x[act] - x_r >= C.RISE_CRITERION * inc)
        res[name + '_at_1s'] = float(np.interp(t_on + C.RISE_MIN_S, t, x))
        res[name + '_peak'] = float(x[act].max())
        res[name + '_overshoot_pct'] = 100.0 * (x[act].max() - (x_r + inc)) / inc
        rec = (t >= t_rec) & (t < end)
        outside = np.nonzero(np.abs(x[rec] - x_r) > C.RECOVERY_BAND * inc)[0]
        tr = t[rec] - t_rec
        res[name + '_recovery_s'] = float(tr[outside[-1]] + (tr[1] - tr[0])) if len(outside) else 0.0
        res[name + '_undershoot_pct'] = 100.0 * max(0.0, x_r - x[rec].min()) / inc
        t5 = t_rec + C.RECOVERY_LIMIT_S
        res[name + '_err_5s_after_recovery'] = (float(np.interp(t5, t, x) - x_r)
                                                if t5 <= end else float('nan'))   # nan: next stimulus came first
    # beat-averaged MAP and SBP/DBP
    cyc = d[:, O['cycle_index']]
    ch = np.nonzero(np.diff(cyc))[0] + 1
    bt, bmap, bsbp, bdbp = t[ch], d[ch, O['MAP_cycle']], d[ch, O['SBP']], d[ch, O['DBP']]
    sel = (bt > t_on) & (bt <= t_rec + 0.001)
    res['beat_MAP_t95_s'] = first_time(bt[sel] - t_on, bmap[sel] - C.MAP_REST_MMHG >= C.RISE_CRITERION * INC_MAP)
    pre = (bt < t_on) & (bt > t_on - 3)
    res['SBP_rest'], res['DBP_rest'] = float(bsbp[pre].mean()), float(bdbp[pre].mean())
    res['SBP_max_activation'] = float(bsbp[sel].max()) if sel.any() else float('nan')
    res['DBP_max_activation'] = float(bdbp[sel].max()) if sel.any() else float('nan')
    return res


def integrity(d, params):
    t = d[:, O['t']]
    dt = np.diff(t)
    chk = {}
    chk['finite'] = bool(np.all(np.isfinite(d[:, :17])))
    chk['max_abs_SNS_HR+PSNS_HR-1'] = float(np.max(np.abs(d[:, O['SNS_HR']] + d[:, O['PSNS_HR']] - 1)))
    chk['max_abs_SNS_BP+PSNS_BP-1'] = float(np.max(np.abs(d[:, O['SNS_BP']] + d[:, O['PSNS_BP']] - 1)))
    chk['weights'] = {k: params[k] for k in ('Aneu_HR', 'Bneu_HR', 'Aneu_BP', 'Bneu_BP')}
    chk['slopes'] = {k: params[k] for k in ('nu_s_HR', 'nu_p_HR', 'nu_s_BP', 'nu_p_BP')}
    chk['reciprocal'] = (params['nu_s_HR'] == -params['nu_p_HR'] and params['nu_s_BP'] == -params['nu_p_BP'])
    chk['max_HR_step_bpm_per_5ms'] = float(np.max(np.abs(np.diff(d[:, O['HR_bpm']]))))
    chk['max_MAPmean_step_mmHg_per_5ms'] = float(np.max(np.abs(np.diff(d[:, O['MAP_mean']]))))
    chk['max_Part_step_mmHg_per_5ms'] = float(np.max(np.abs(np.diff(d[:, O['P_art']]))))
    chk['sample_interval_ok'] = bool(np.allclose(dt, C.SAMPLE_EVERY_STEPS * C.DT, atol=1e-9))
    chk['HR_range_bpm'] = (float(d[:, O['HR_bpm']].min()), float(d[:, O['HR_bpm']].max()))
    chk['Part_range_mmHg'] = (float(d[:, O['P_art']].min()), float(d[:, O['P_art']].max()))
    # cardiac phase: beat intervals vs 1/HR at the beat
    cyc = d[:, O['cycle_index']]
    ch = np.nonzero(np.diff(cyc))[0] + 1
    bt = t[ch]
    ibi = np.diff(bt)
    hr_at = d[ch[1:], O['HR_bpm']]
    chk['max_beat_interval_mismatch_s'] = float(np.max(np.abs(ibi - 60.0 / hr_at)))
    return chk


def direction_checks(d, t_on, t_rec, t_end):
    t = d[:, O['t']]
    pre = (t > t_on - 2) & (t < t_on)
    act = (t > t_on + 0.05) & (t <= t_rec)
    late = (t > t_end - 2) & (t <= t_end)
    rec = (t > t_rec + 0.05) & (t < t_rec + 1.5)
    r = {}
    for s in ('SNS_HR', 'PSNS_HR', 'SNS_BP', 'PSNS_BP'):
        x = d[:, O[s]]
        r[s] = dict(rest=float(x[pre].mean()), activation_mean=float(x[act].mean()),
                    activation_extreme=float(x[act].max() if s.startswith('SNS') else x[act].min()),
                    recovery_mean=float(x[rec].mean()), final=float(x[late].mean()))
    ok = (r['SNS_HR']['activation_mean'] > r['SNS_HR']['rest'] and r['PSNS_HR']['activation_mean'] < r['PSNS_HR']['rest']
          and r['SNS_BP']['activation_mean'] > r['SNS_BP']['rest'] and r['PSNS_BP']['activation_mean'] < r['PSNS_BP']['rest']
          and r['SNS_HR']['recovery_mean'] < r['SNS_HR']['activation_mean']
          and r['PSNS_HR']['recovery_mean'] > r['PSNS_HR']['activation_mean']
          and r['SNS_BP']['recovery_mean'] < r['SNS_BP']['activation_mean']
          and r['PSNS_BP']['recovery_mean'] > r['PSNS_BP']['activation_mean']
          and all(abs(r[s]['final'] - r[s]['rest']) < 0.005 for s in r))
    return r, ok


# ---------------------------------------------------------------------------------- tests
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    report, results, passes = [], {}, {}

    def say(*a):
        line = ' '.join(str(x) for x in a)
        print(line)
        report.append(line)

    say('MODEL TESTING WITH SIMULATED STIMULI (no microphone involved)')
    say('=' * 72)

    # ---------- T1: stable rest, then one brief stimulus (clap-like), then a sustained one,
    #            then a re-trigger during recovery, then long rest
    h = Harness()
    params = h.eng.model.params
    dsg = h.eng.design
    say('derived: mu_HR rest %.5f bps (%.2f bpm), act %.5f bps (%.2f bpm); mu_BP rest %.3f, act %.3f mmHg; '
        'Cneu_HR %.5f bps' % (dsg['mu_HR_rest'], 60 * dsg['mu_HR_rest'], dsg['mu_HR_act'], 60 * dsg['mu_HR_act'],
                              dsg['mu_BP_rest'], dsg['mu_BP_act'], dsg['Cneu_HR']))
    h.run_until(30.0)
    d = h.data()
    t = d[:, 0]
    w = (t > 10) & (t <= 30)
    rest = dict(HR_mean=float(d[w, O['HR_bpm']].mean()), HR_range=float(np.ptp(d[w, O['HR_bpm']])),
                MAP_mean=float(d[w, O['MAP_mean']].mean()), MAP_range=float(np.ptp(d[w, O['MAP_mean']])),
                MAP_run_mean=float(d[w, O['MAP_run']].mean()))
    results['T1_rest'] = rest
    passes['T1 rest HR/MAP at targets and stable (|err|,range < 0.1)'] = (
        abs(rest['HR_mean'] - C.HR_REST_BPM) < 0.1 and rest['HR_range'] < 0.1 and
        abs(rest['MAP_mean'] - C.MAP_REST_MMHG) < 0.1 and rest['MAP_range'] < 0.1)
    say('\nT1 rest (t = 10-30 s after detection enabled): HR %.3f bpm (range %.4f), MAP %.3f mmHg (range %.4f), '
        'MAP_run %.3f' % (rest['HR_mean'], rest['HR_range'], rest['MAP_mean'], rest['MAP_range'], rest['MAP_run_mean']))

    # T2 brief stimulus at 30 s (sound lasts 0.05 s)
    t_on2 = 30.0
    h.stimulus(t_on2, 0.05)
    h.run_until(50.0)
    lg = h.log()
    t_rec2 = [x[0] for x in lg if x[1] == 'recovery-onset' and x[0] > t_on2][0]
    # T3 sustained stimulus at 50 s (sound lasts 4 s)
    t_on3 = 50.0
    h.stimulus(t_on3, 4.0)
    h.run_until(70.0)
    lg = h.log()
    t_rec3 = [x[0] for x in lg if x[1] == 'recovery-onset' and x[0] > t_on3][0]
    # T4 re-trigger during recovery: clap at 70 s, second clap 0.4 s after its recovery onset
    t_on4 = 70.0
    h.stimulus(t_on4, 0.05)
    h.run_until(t_on4 + C.RISE_MIN_S + 0.4)
    lg = h.log()
    t_rec4a = [x[0] for x in lg if x[1] == 'recovery-onset' and x[0] > t_on4][0]
    t_on4b = t_rec4a + 0.4
    hr_before_retrigger = h.eng.model.get('HR_bps') * 60
    h.stimulus(t_on4b, 0.05)
    h.run_until(95.0)
    lg = h.log()
    t_rec4b = [x[0] for x in lg if x[1] == 'recovery-onset' and x[0] > t_on4b][0]
    # T5 long rest afterwards
    h.run_until(215.0)
    d = h.data()
    t = d[:, 0]

    m2 = response_metrics(d, t_on2, t_rec2, t_next=t_on3)
    m3 = response_metrics(d, t_on3, t_rec3, t_next=t_on4)
    m4 = response_metrics(d, t_on4b, t_rec4b, t_next=None)
    results['T2_brief'] = dict(t_on=t_on2, recovery_onset_after_stimulus_s=t_rec2 - t_on2, **m2)
    results['T3_sustained'] = dict(t_on=t_on3, recovery_onset_after_stimulus_s=t_rec3 - t_on3, **m3)
    sel3 = (t > t_rec3 - 1.0) & (t <= t_rec3)
    results['T3_sustained']['plateau_HR'] = float(d[sel3, O['HR_bpm']].mean())
    results['T3_sustained']['plateau_MAP'] = float(d[sel3, O['MAP_mean']].mean())
    results['T3_sustained']['plateau_MAP_run'] = float(d[sel3, O['MAP_run']].mean())
    cyc = d[:, O['cycle_index']]
    ch = np.nonzero(np.diff(cyc))[0] + 1
    bsel = (t[ch] > t_rec3 - 2.0) & (t[ch] <= t_rec3)
    results['T3_sustained']['plateau_SBP'] = float(d[ch[bsel], O['SBP']].mean())
    results['T3_sustained']['plateau_DBP'] = float(d[ch[bsel], O['DBP']].mean())
    results['T4_retrigger'] = dict(first_recovery_onset=t_rec4a, second_onset=t_on4b,
                                   HR_at_retrigger=hr_before_retrigger,
                                   state_after_second_onset=[s for tt, s in h.states if tt >= t_on4b][0],
                                   **m4)
    i_rt = np.searchsorted(t, t_on4b)
    results['T4_retrigger']['HR_step_at_retrigger_bpm'] = float(abs(d[i_rt, O['HR_bpm']] - d[i_rt - 1, O['HR_bpm']]))
    w5 = (t > 195) & (t <= 215)
    results['T5_long_rest'] = dict(HR_mean=float(d[w5, O['HR_bpm']].mean()), HR_range=float(np.ptp(d[w5, O['HR_bpm']])),
                                   MAP_mean=float(d[w5, O['MAP_mean']].mean()), MAP_range=float(np.ptp(d[w5, O['MAP_mean']])),
                                   HR_drift_vs_T1=float(d[w5, O['HR_bpm']].mean() - rest['HR_mean']),
                                   MAP_drift_vs_T1=float(d[w5, O['MAP_mean']].mean() - rest['MAP_mean']))
    dirs2, ok_dir2 = direction_checks(d, t_on2, t_rec2, t_on3)
    dirs3, ok_dir3 = direction_checks(d, t_on3, t_rec3, t_on4)
    results['ANS_directions_brief'] = dirs2
    results['ANS_directions_sustained'] = dirs3
    results['integrity'] = integrity(d, params)
    results['controller_log'] = [(round(a, 4), b, c) for a, b, c in lg]

    for lab, m in (('T2 brief stimulus (clap-like)', m2), ('T3 sustained stimulus (4 s)', m3),
                   ('T4 re-trigger during recovery', m4)):
        say('\n' + lab)
        say('  rise   : 95%% of increase reached after HR %.3f s, MAP(cycle-mean) %.3f s, MAP(one-cycle running) %.3f s, '
            'beat MAP %.3f s' % (m['HR_t95_s'], m['MAP_t95_s'], m['MAP_run_t95_s'], m['beat_MAP_t95_s']))
        say('  at 1 s : HR %.2f bpm, MAP %.2f mmHg   peak: HR %.2f (overshoot %.1f%%), MAP %.2f (overshoot %.1f%%)'
            % (m['HR_at_1s'], m['MAP_at_1s'], m['HR_peak'], m['HR_overshoot_pct'], m['MAP_peak'], m['MAP_overshoot_pct']))
        say('  recov. : within 5%% band after HR %.3f s, MAP %.3f s, MAP_run %.3f s; undershoot HR %.1f%%, MAP %.1f%%; '
            'error 5 s after recovery onset HR %+.3f bpm, MAP %+.3f mmHg'
            % (m['HR_recovery_s'], m['MAP_recovery_s'], m['MAP_run_recovery_s'], m['HR_undershoot_pct'],
               m['MAP_undershoot_pct'], m['HR_err_5s_after_recovery'], m['MAP_err_5s_after_recovery']))
        say('  SBP/DBP: 3 s before onset %.2f / %.2f, max during the latched/sustained activation %.2f / %.2f'
            % (m['SBP_rest'], m['DBP_rest'], m['SBP_max_activation'], m['DBP_max_activation']))
    r3 = results['T3_sustained']
    say('  T3 plateau (last 1 s of sustained activation): HR %.3f bpm, MAP %.3f mmHg (MAP_run %.3f), beat SBP/DBP %.2f / %.2f'
        % (r3['plateau_HR'], r3['plateau_MAP'], r3['plateau_MAP_run'], r3['plateau_SBP'], r3['plateau_DBP']))
    say('  T2 recovery onset %.3f s after stimulus (latched rise); T3 recovery onset %.3f s after stimulus '
        '(sound 4 s + %.2f s end hold)' % (t_rec2 - t_on2, t_rec3 - t_on3, C.RELEASE_HOLD_S))
    r4 = results['T4_retrigger']
    say('  T4: second onset %.2f s after first recovery onset; HR at re-trigger %.2f bpm (not reset to rest); '
        'state -> %s; HR step across re-trigger %.4f bpm' % (t_on4b - t_rec4a, r4['HR_at_retrigger'],
                                                          r4['state_after_second_onset'], r4['HR_step_at_retrigger_bpm']))
    r5 = results['T5_long_rest']
    say('\nT5 long rest (120-140 s after the last stimulus): HR %.3f (range %.4f, drift vs T1 %+.4f), MAP %.3f '
        '(range %.4f, drift %+.4f)' % (r5['HR_mean'], r5['HR_range'], r5['HR_drift_vs_T1'], r5['MAP_mean'],
                                       r5['MAP_range'], r5['MAP_drift_vs_T1']))
    say('\nANS activities (rest -> activation mean / extreme -> recovery mean -> final):')
    for lab, dd in (('brief', dirs2), ('sustained', dirs3)):
        for s, v in dd.items():
            say('  %-9s %-8s %.3f -> %.3f / %.3f -> %.3f -> %.3f' % (lab, s, v['rest'], v['activation_mean'],
                                                                   v['activation_extreme'], v['recovery_mean'], v['final']))
    ig = results['integrity']
    say('\nIntegrity: finite=%s, weights=%s, slopes=%s, reciprocal=%s' % (ig['finite'], ig['weights'], ig['slopes'],
                                                                       ig['reciprocal']))
    say('  max|SNS+PSNS-1|: HR %.1e, BP %.1e;  max step per 5 ms: HR %.3f bpm, MAP %.3f mmHg, P_art %.3f mmHg'
        % (ig['max_abs_SNS_HR+PSNS_HR-1'], ig['max_abs_SNS_BP+PSNS_BP-1'], ig['max_HR_step_bpm_per_5ms'],
           ig['max_MAPmean_step_mmHg_per_5ms'], ig['max_Part_step_mmHg_per_5ms']))
    say('  ranges: HR %.2f-%.2f bpm, P_art %.2f-%.2f mmHg; max |beat interval - 60/HR| %.4f s'
        % (ig['HR_range_bpm'] + ig['Part_range_mmHg'] + (ig['max_beat_interval_mismatch_s'],)))

    for lab, m in (('T2', m2), ('T3', m3), ('T4', m4)):
        passes[lab + ' HR: >=95% of increase within 1 s'] = m['HR_t95_s'] <= C.RISE_MIN_S
        passes[lab + ' MAP (cycle-mean): >=95% of increase within 1 s'] = m['MAP_t95_s'] <= C.RISE_MIN_S
        passes[lab + ' HR: within 5% band <= 5 s after recovery onset'] = m['HR_recovery_s'] <= C.RECOVERY_LIMIT_S
        passes[lab + ' MAP: within 5% band <= 5 s after recovery onset'] = m['MAP_recovery_s'] <= C.RECOVERY_LIMIT_S
    passes['T3 plateau at elevated targets (HR +-0.1 bpm, MAP +-0.1 mmHg)'] = (
        abs(r3['plateau_HR'] - C.HR_ELEVATED_BPM) < 0.1 and abs(r3['plateau_MAP'] - C.MAP_ELEVATED_MMHG) < 0.1)
    passes['T2 brief stimulus reaches elevated level by 1 s (HR, MAP within 5% of increase)'] = (
        abs(m2['HR_at_1s'] - C.HR_ELEVATED_BPM) <= 0.05 * INC_HR and abs(m2['MAP_at_1s'] - C.MAP_ELEVATED_MMHG) <= 0.05 * INC_MAP)
    passes['T4 re-trigger re-activates from current state (no reset, HR continuous)'] = (
        r4['state_after_second_onset'] == RISE and r4['HR_step_at_retrigger_bpm'] < 0.5 and
        r4['HR_at_retrigger'] > C.HR_REST_BPM + 0.05 * INC_HR)
    passes['T5 remains at rest after stimuli (|drift| < 0.05, range < 0.05)'] = (
        abs(r5['HR_drift_vs_T1']) < 0.05 and abs(r5['MAP_drift_vs_T1']) < 0.05 and r5['HR_range'] < 0.05 and r5['MAP_range'] < 0.05)
    passes['ANS directions (SNS up / PSNS down in activation, reverse in recovery, return to rest)'] = ok_dir2 and ok_dir3
    passes['coupled reciprocal mode throughout (signs, SNS+PSNS=1 per pathway)'] = (
        ig['reciprocal'] and ig['max_abs_SNS_HR+PSNS_HR-1'] < 1e-9 and ig['max_abs_SNS_BP+PSNS_BP-1'] < 1e-9)
    passes['all four weights = 1.0'] = all(v == 1.0 for v in ig['weights'].values())
    passes['no divergence / discontinuity (finite, HR step < 1 bpm/5 ms, beat timing consistent)'] = (
        ig['finite'] and ig['max_HR_step_bpm_per_5ms'] < 1.0 and ig['max_beat_interval_mismatch_s'] < 0.03)
    passes['no substantial oscillation (overshoot and undershoot < 10% of increase)'] = all(
        m[k] < 10.0 for m in (m2, m3, m4) for k in ('HR_overshoot_pct', 'MAP_overshoot_pct', 'HR_undershoot_pct',
                                                    'MAP_undershoot_pct'))

    say('\nACCEPTANCE (model with simulated stimuli)')
    for k, v in passes.items():
        say('  [%s] %s' % ('PASS' if v else 'FAIL', k))
    say('\nNot a criterion, reported for transparency: the one-cycle running MAP and the beat-averaged MAP '
        'lag the cycle-mean MAP by about half a beat to one beat (see rise times above).')

    # ---------- design justification runs (not pass/fail)
    say('\nDESIGN CHECKS (not pass/fail)')
    alt = 0 if C.USE_FIXED_COUPLING_REF else 1
    say('  mu_BP step %.3f -> %.3f mmHg shifts the coupling reference term by %+.2f bpm (dynamic-reference form); '
        'Eneu_BP_to_HR at the elevated plateau %.2f bpm' % (dsg['mu_BP_rest'], dsg['mu_BP_act'],
                                                           _ref_shift(params, dsg), dsg['Ecoup_act_bpm']))
    try:
        hl = Harness(params_override=dict(use_fixed_coupling_ref=alt))
        hl.run_until(10.0)
        hl.stimulus(10.0, 0.05)
        hl.run_until(25.0)
        dl = hl.data()
        ml = response_metrics(dl, 10.0, 10.0 + C.RISE_MIN_S)
        say('  alternative coupling reference (%s): HR t95 %.3f s, MAP t95 %.3f s, HR peak %.2f bpm (overshoot %.1f%%), '
            'HR undershoot %.1f%%, recovery HR %.2f s / MAP %.2f s'
            % ('fixed at MAP_rest' if alt else 'dynamic mu_BP, Fortran form', ml['HR_t95_s'], ml['MAP_t95_s'],
               ml['HR_peak'], ml['HR_overshoot_pct'], ml['HR_undershoot_pct'], ml['HR_recovery_s'], ml['MAP_recovery_s']))
        results['design_check_alternative_coupling_ref'] = ml
    except Exception as e:
        say('  alternative coupling reference: initialisation failed (%s)' % e)
    for nu in (7.0, 3.5):
        try:
            p = D.base_params()
            p.update(nu_s_HR=-nu, nu_p_HR=nu)
            dd = D.analytic_design(p, C.HR_REST_BPM, C.MAP_REST_MMHG, C.HR_ELEVATED_BPM, C.MAP_ELEVATED_MMHG,
                                   C.SNS_HR_REST)
            q = dict(p, mu_HR=dd['mu_HR_rest'], mu_BP=dd['mu_BP_rest'], Cneu_HR=dd['Cneu_HR'])
            ends = []
            for pert in (+0.05, -0.05):
                mod = M.ANSCVSModel(q)
                hh = (C.HR_REST_BPM + pert) / 60.0
                Q = q['SV'] * hh
                mod.init_state(hr_bps=hh, R=dd['R_rest'], P_art=C.MAP_REST_MMHG, P_ven=q['P_RA'] + q['R_ven'] * Q)
                mod.spin_up(int(20.0 / q['dt']))
                mod.run(int(30.0 / q['dt']), True, 0)
                ends.append(mod.get('HR_bps') * 60.0)
            say('  |nu_HR| = %.1f with weights 1: dSigma_HR/dHR = %.2f at the designed rest (>1: unstable through the HR '
                'afferent); starting 0.05 bpm above / below rest, HR ends at %.1f / %.1f bpm after 30 s'
                % (nu, dd['slope_direct_rest'], ends[0], ends[1]))
        except Exception as e:
            say('  |nu_HR| = %.1f: no valid design (%s)' % (nu, e))
    say('  |nu_HR| = %.1f (used): dSigma_HR/dHR = %.2f at rest, %.2f at the elevated state'
        % (abs(params['nu_p_HR']), dsg['slope_direct_rest'], dsg['slope_direct_act']))

    with open(os.path.join(OUT_DIR, 'simulated_stimulus_report.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(report) + '\n')
    with open(os.path.join(OUT_DIR, 'simulated_stimulus_results.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(results=results, passes=passes), f, indent=1, default=str)
    _plot(d, [(t_on2, t_rec2), (t_on3, t_rec3), (t_on4, t_rec4a), (t_on4b, t_rec4b)])
    n_fail = sum(not v for v in passes.values())
    say('\n%d criteria, %d failed. Output in %s' % (len(passes), n_fail, OUT_DIR))
    return n_fail


def _ref_shift(params, dsg):
    import math
    sp = lambda x: max(x, 0.0) + math.log1p(math.exp(-abs(x)))
    k, w = params['MAP_knee_BP_to_HR'], params['w_knee_BP_to_HR']
    return params['K_BP_to_HR'] * w * (sp((dsg['mu_BP_act'] - k) / w) - sp((dsg['mu_BP_rest'] - k) / w))


def _plot(d, events):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t = d[:, 0]
    sel = (t > 25) & (t < 92)
    fig, ax = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    ax[0].plot(t[sel], d[sel, O['HR_bpm']], color='#962020', lw=1.6)
    ax[0].axhline(C.HR_REST_BPM, ls='--', color='gray', lw=0.8)
    ax[0].axhline(C.HR_ELEVATED_BPM, ls='--', color='#dc7800', lw=0.8)
    ax[0].set_ylabel('Heart rate [bpm]')
    ax[1].plot(t[sel], d[sel, O['P_art']], color='#285aaa', lw=0.8, label='arterial pressure')
    ax[1].plot(t[sel], d[sel, O['MAP_mean']], color='k', ls='--', lw=1.2, label='MAP (cycle-mean)')
    ax[1].set_ylabel('Blood pressure [mmHg]')
    ax[1].legend(loc='upper right', fontsize=8)
    for s, c, ls in (('SNS_HR', '#c82828', '-'), ('PSNS_HR', '#2850c8', '-'), ('SNS_BP', '#c82828', '--'),
                     ('PSNS_BP', '#2850c8', '--')):
        ax[2].plot(t[sel], d[sel, O[s]], color=c, ls=ls, lw=1.3, label=s)
    ax[2].set_ylim(0, 1)
    ax[2].set_ylabel('ANS activity [-]')
    ax[2].legend(loc='upper right', ncol=4, fontsize=8)
    ax[2].set_xlabel('Model time after detection enabled [s]')
    for a in ax:
        for on, rec in events:
            a.axvline(on, color='r', lw=0.8)
            a.axvline(rec, color='g', lw=0.8)
        a.grid(alpha=0.3)
    ax[0].set_title('Simulated-stimulus test: brief (30 s), sustained 4 s (50 s), re-trigger during recovery (70 s); '
                    'red = stimulus, green = recovery onset', fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, 'simulated_stimulus_test.png'), dpi=110)


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
