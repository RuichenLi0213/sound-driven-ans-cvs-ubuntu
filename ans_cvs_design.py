"""
ans_cvs_design.py
=================
Derives the model parameters that are not set directly in ans_cvs_config.py:
mu_HR / mu_BP for rest and activation, Cneu_HR (gamma_HR) and the coupling
reference, from the target levels, using the steady-state form of the model
equations.  A short simulation of the full pulsatile model then corrects the
activation mu values so the simulated elevated plateau lands on the targets.

Steady state of the model (Pdot_BP = 0, Faa_BP = MAP_mean):
    Q   = SV * HR                         mean cardiac inflow (mL/s)
    Pv  = P_RA + R_ven * Q                mean venous pressure
    R   = (MAP - Pv) / Q                  required resistance
    R   = R0 + Aneu_BP (SNS_BP - 0.5) - Bneu_BP (PSNS_BP - 0.5)
        = R0 + 2 (SNS_BP - 0.5)           (weights 1, reciprocal: PSNS = 1 - SNS)
    SNS_BP = 1 / (1 + (MAP/mu_BP)^nu_s_BP)          -> mu_BP
    HR  = Aneu_HR SNS_HR - Bneu_HR PSNS_HR + Cneu_HR + Eneu_BP_to_HR
        = 2 SNS_HR - 1 + Cneu_HR + Eneu_BP_to_HR     -> Cneu_HR (rest), SNS_HR (activation)
    SNS_HR = 1 / (1 + (HR/mu_HR)^nu_s_HR)           -> mu_HR
"""
import math
import numpy as np

import ans_cvs_config as C
import ans_cvs_model as M


def _sp(x):
    return max(x, 0.0) + math.log1p(math.exp(-abs(x)))


def base_params():
    """Parameter dict for ans_cvs_model.ANSCVSModel, mu set to placeholders."""
    weights = (C.ANEU_HR, C.BNEU_HR, C.ANEU_BP, C.BNEU_BP)
    if weights != (1.0, 1.0, 1.0, 1.0):
        raise ValueError('The experiment requires all four SNS/PSNS weights = 1.0, got %s' % (weights,))
    p = dict(dt=C.DT,
             mu_HR=1.0, nu_s_HR=-C.NU_HR, nu_p_HR=+C.NU_HR,
             Aneu_HR=C.ANEU_HR, Bneu_HR=C.BNEU_HR, Cneu_HR=0.0, Tneu_HR=C.TNEU_HR,
             mu_BP=100.0, nu_s_BP=+C.NU_BP, nu_p_BP=-C.NU_BP,
             Aneu_BP=C.ANEU_BP, Bneu_BP=C.BNEU_BP, Tneu_BP=C.TNEU_BP, tau_Faa_BP=C.TAU_FAA_BP,
             R0_base=C.R0_BASE,
             K_BP_to_HR=C.K_BP_TO_HR, MAP_knee_BP_to_HR=C.MAP_REST_MMHG + C.KNEE_OFFSET_MMHG,
             w_knee_BP_to_HR=C.W_KNEE_MMHG, tau_rate_BP_to_HR=C.TAU_RATE_S,
             K_rate_BP_to_HR=C.K_RATE, w_rate_BP_to_HR=C.W_RATE,
             mu_ref_coupling=C.MAP_REST_MMHG, use_fixed_coupling_ref=1 if C.USE_FIXED_COUPLING_REF else 0,
             tau_mu=C.TAU_MU, **C.CVS)
    return p


def check_reciprocal(p):
    """Coupled reciprocal mode: equal magnitude, opposite sign, in each pathway."""
    ok_hr = p['nu_s_HR'] == -p['nu_p_HR'] and p['nu_s_HR'] != 0
    ok_bp = p['nu_s_BP'] == -p['nu_p_BP'] and p['nu_s_BP'] != 0
    return ok_hr and ok_bp


def analytic_design(p, hr_r, map_r, hr_a, map_a, sns_hr_rest):
    """Closed-form steady-state design.  Returns dict of derived values."""
    nu_hr, nu_bp = abs(p['nu_p_HR']), abs(p['nu_s_BP'])
    A_bp, B_bp = p['Aneu_BP'], p['Bneu_BP']
    A_hr, B_hr = p['Aneu_HR'], p['Bneu_HR']

    def R_needed(hr_bps, MAP):
        Q = p['SV'] * hr_bps
        Pv = p['P_RA'] + p['R_ven'] * Q
        return (MAP - Pv) / Q

    def mu_bp_for(hr_bps, MAP):
        R = R_needed(hr_bps, MAP)
        s = 0.5 + (R - p['R0_base']) / (A_bp + B_bp)      # SNS_BP at steady state
        if not 0.0 < s < 1.0:
            raise ValueError('target requires SNS_BP outside (0,1)')
        return MAP / (1.0 / s - 1.0) ** (1.0 / nu_bp), s, R

    h_r, h_a = hr_r / 60.0, hr_a / 60.0
    mu_bp_r, sbp_r, R_r = mu_bp_for(h_r, map_r)
    mu_bp_a, sbp_a, R_a = mu_bp_for(h_a, map_a)

    knee, w = p['MAP_knee_BP_to_HR'], p['w_knee_BP_to_HR']

    def coup(MAP, mu_bp):
        ref = p['mu_ref_coupling'] if p['use_fixed_coupling_ref'] else mu_bp
        return -p['K_BP_to_HR'] * w / 60.0 * (_sp((MAP - knee) / w) - _sp((ref - knee) / w))

    e_r, e_a = coup(map_r, mu_bp_r), coup(map_a, mu_bp_a)
    s_r = sns_hr_rest
    c_hr = h_r - (A_hr * s_r - B_hr * (1 - s_r)) - e_r
    s_a = (h_a - c_hr - e_a + B_hr) / (A_hr + B_hr)
    if not 0.0 < s_a < 1.0:
        raise ValueError('elevated HR target requires SNS_HR outside (0,1)')
    mu_hr_r = h_r / (s_r / (1 - s_r)) ** (1.0 / nu_hr)
    mu_hr_a = h_a / (s_a / (1 - s_a)) ** (1.0 / nu_hr)
    return dict(mu_HR_rest=mu_hr_r, mu_HR_act=mu_hr_a, mu_BP_rest=mu_bp_r, mu_BP_act=mu_bp_a,
                Cneu_HR=c_hr, SNS_HR_rest=s_r, SNS_HR_act=s_a, SNS_BP_rest=sbp_r, SNS_BP_act=sbp_a,
                R_rest=R_r, R_act=R_a, Ecoup_rest_bpm=e_r * 60, Ecoup_act_bpm=e_a * 60,
                slope_direct_rest=(A_hr + B_hr) * nu_hr * s_r * (1 - s_r) / h_r,
                slope_direct_act=(A_hr + B_hr) * nu_hr * s_a * (1 - s_a) / h_a)


def rest_model(p, d, spinup_s=None, settle_s=None):
    """Model initialised at the resting targets, CVS spin-up, then ANS active."""
    spinup_s = C.SPINUP_S if spinup_s is None else spinup_s
    settle_s = C.SETTLE_S if settle_s is None else settle_s
    q = dict(p, mu_HR=d['mu_HR_rest'], mu_BP=d['mu_BP_rest'], Cneu_HR=d['Cneu_HR'])
    mod = M.ANSCVSModel(q)
    h = C.HR_REST_BPM / 60.0
    Q = q['SV'] * h
    Pv = q['P_RA'] + q['R_ven'] * Q
    mod.init_state(hr_bps=h, R=d['R_rest'], P_art=C.MAP_REST_MMHG, P_ven=Pv)
    mod.spin_up(int(round(spinup_s / q['dt'])))
    out = mod.run(int(round(settle_s / q['dt'])), active=True, rec_every=C.SAMPLE_EVERY_STEPS)
    return mod, out


def _clone(mod):
    c = M.ANSCVSModel(mod.params)
    c.P[:] = mod.P
    c.S[:] = mod.S
    c.I[:] = mod.I
    c.p_buf[:] = mod.p_buf
    c.v_buf[:] = mod.v_buf
    c.q_buf[:] = mod.q_buf
    return c


def _plateau(mod_rest, mu_hr, mu_bp, hold_s=6.0, avg_s=2.0):
    m2 = _clone(mod_rest)
    m2.set_mu(mu_hr, mu_bp)
    out = m2.run(int(round(hold_s / m2.dt)), True, C.SAMPLE_EVERY_STEPS)
    t = out[:, 0]
    sel = t > t[-1] - avg_s
    return out[sel, M.OC['HR_bpm']].mean(), out[sel, M.OC['MAP_mean']].mean()


def derive(verbose=False, refine=True):
    """Full design: analytic values + simulation-based refinement of the activation mu."""
    p = base_params()
    if not check_reciprocal(p):
        raise ValueError('slopes violate coupled reciprocal mode')
    d = analytic_design(p, C.HR_REST_BPM, C.MAP_REST_MMHG, C.HR_ELEVATED_BPM, C.MAP_ELEVATED_MMHG,
                        C.SNS_HR_REST)
    d['analytic_mu_HR_act'], d['analytic_mu_BP_act'] = d['mu_HR_act'], d['mu_BP_act']
    mod, out = rest_model(p, d, spinup_s=10.0, settle_s=20.0)
    if refine:
        target = np.array([C.HR_ELEVATED_BPM, C.MAP_ELEVATED_MMHG])
        x = np.array([d['mu_HR_act'], d['mu_BP_act']])
        for it in range(4):
            f = np.array(_plateau(mod, *x)) - target
            if verbose:
                print('refine it=%d mu=(%.5f, %.4f) plateau err HR %.4f bpm MAP %.4f mmHg' % (it, x[0], x[1], f[0], f[1]))
            if np.all(np.abs(f) < 0.005):
                break
            J = np.zeros((2, 2))
            for j, hstep in enumerate((-0.002 * x[0], 0.002 * x[1])):
                xp = x.copy()
                xp[j] += hstep
                J[:, j] = (np.array(_plateau(mod, *xp)) - target - f) / hstep
            x = x - np.linalg.solve(J, f)
        d['mu_HR_act'], d['mu_BP_act'] = float(x[0]), float(x[1])
    params = dict(p, mu_HR=d['mu_HR_rest'], mu_BP=d['mu_BP_rest'], Cneu_HR=d['Cneu_HR'])
    return params, d


if __name__ == '__main__':
    params, d = derive(verbose=True)
    for k, v in d.items():
        print('%-22s %.6f' % (k, v))
