"""
ans_cvs_model.py
================
Python/numba port of ans_cvs_coupled_single_case.f90 (coupled ANS-CVS model),
advanced in chunks so it can run in real time.

Ported unchanged from the Fortran (same equations, same update order per step):
  neuro_bp()          MAP afferent -> SNS_BP / PSNS_BP -> resistance target -> R_vb
                      cycle-mean pressure MAP_mean, Pdot_BP, BP->HR coupling terms
  neuro_hr()          HR direct pathway (Theory Eq. 7, 8, 10) + BP->HR coupling,
                      Eq. (11) for HR integrated with RK4
  cardiac_inflow()    half-sine ejection + delayed reflected wave
  update_cvs()        two-compartment Windkessel (arterial / venous)
  map_running_mean()  running one-cycle MAP and venous pressure
  update_summary()    beat-by-beat SBP / DBP / MAP_cycle

Structural differences from the Fortran (explained in README_realtime.md):
  1. mu_HR and mu_BP are model inputs: the phase controller sets their targets
     between integration chunks, and the sigmoids use mu values that follow the
     targets with a first-order lag tau_mu (tau_mu = 0: instantaneous step).
     The Fortran holds mu constant.
  2. Option (off by default): the reference term of the BP->HR pressure
     coupling can use a separate fixed parameter mu_ref_coupling
     (use_fixed_coupling_ref = 1).  Default (= 0) uses the dynamic mu_BP
     exactly as the Fortran does.
  3. The step counter never resets (the Fortran restarts nstep after the CVS
     spin-up); the reflected-wave buffer index therefore stays continuous.
  4. No CSV / batch statistics inside the kernel; decimated samples are
     returned to the caller instead.
"""
import math
import numpy as np

try:
    from numba import njit
    HAVE_NUMBA = True
except Exception:  # pragma: no cover - fallback keeps the module importable
    HAVE_NUMBA = False

    def njit(*a, **k):
        def deco(f):
            return f
        return deco if not (a and callable(a[0])) else a[0]

NPB = 80000  # running-mean pressure buffer (4 s at dt = 5e-5 s), as in the Fortran

# ---------------------------------------------------------------- parameter indices
(P_DT, P_MU_HR, P_NU_S_HR, P_NU_P_HR, P_A_HR, P_B_HR, P_C_HR, P_T_HR,
 P_MU_BP, P_NU_S_BP, P_NU_P_BP, P_A_BP, P_B_BP, P_T_BP, P_TAU_FAA, P_R0,
 P_K, P_KNEE, P_W, P_TAU_RATE, P_KR, P_WR, P_MU_REF, P_FIXED_REF,
 P_TSYS, P_SV, P_KREF, P_CART, P_CVEN, P_RVEN, P_PRA, P_TAU_REFLECT, P_TAU_MU) = range(33)
NPAR = 33

# ---------------------------------------------------------------- state indices
(S_T, S_HR, S_TDUR, S_TCR, S_PART, S_PVEN, S_RVB, S_FAA, S_MAPMEAN, S_PVENRUN,
 S_MAPRUN, S_PSUM, S_VSUM, S_SNS_HR, S_PSNS_HR, S_SNS_BP, S_PSNS_BP, S_ED, S_EP,
 S_ER, S_PDOT, S_FC, S_REQ, S_QIN, S_SBP, S_DBP, S_MAPCYC, S_PMAX, S_PMIN,
 S_PSUMC, S_PCNTC, S_WRAP, S_REC, S_MU_HR, S_MU_BP) = range(35)
NSTATE = 35
(I_NSTEP, I_PCOUNT, I_PWIN, I_PFILL, I_PRIMED, I_CYCLE, I_NDELAY, I_NBUF) = range(8)
NISTATE = 8

# ---------------------------------------------------------------- output columns
OUT_COLS = ['t', 'HR_bpm', 'P_art', 'MAP_mean', 'MAP_run', 'SBP', 'DBP', 'MAP_cycle',
            'SNS_HR', 'PSNS_HR', 'SNS_BP', 'PSNS_BP', 'R_vb', 'Eneu_HR_direct_bpm',
            'Eneu_BP_to_HR_bpm', 'Pdot_BP', 'cycle_index', 'mu_HR', 'mu_BP', 'Faa_BP']
NOUT = len(OUT_COLS)
OC = {c: i for i, c in enumerate(OUT_COLS)}


@njit(cache=True)
def softplus(x):
    return max(x, 0.0) + math.log(1.0 + math.exp(-abs(x)))


@njit(cache=True)
def hr_direct(faa_hr, mu_hr, P):
    """Theory Eq. (7), (8), (10) for the HR pathway; returns (SNS, PSNS, sigma_HR)."""
    ratio = faa_hr / mu_hr
    ns = 1.0 / (1.0 + ratio ** P[P_NU_S_HR])
    npp = 1.0 / (1.0 + ratio ** P[P_NU_P_HR])
    return ns, npp, P[P_A_HR] * ns - P[P_B_HR] * npp + P[P_C_HR]


@njit(cache=True)
def _step(S, I, P, p_buf, v_buf, q_buf, active):
    dt = P[P_DT]
    I[I_NSTEP] += 1
    nstep = I[I_NSTEP]

    # ===================== reference transition (added) =====================
    # P[P_MU_HR], P[P_MU_BP] are the targets set by the phase controller; the
    # mu values used by the sigmoids follow them with time constant tau_mu
    # (tau_mu = 0 reproduces an instantaneous step, i.e. the Fortran).
    if P[P_TAU_MU] > 0.0:
        S[S_MU_HR] += dt * (P[P_MU_HR] - S[S_MU_HR]) / P[P_TAU_MU]
        S[S_MU_BP] += dt * (P[P_MU_BP] - S[S_MU_BP]) / P[P_TAU_MU]
    else:
        S[S_MU_HR] = P[P_MU_HR]
        S[S_MU_BP] = P[P_MU_BP]
    mu_hr = S[S_MU_HR]
    mu_bp = S[S_MU_BP]

    # ===================== neuro_bp =====================
    S[S_FAA] += dt * (S[S_MAPMEAN] - S[S_FAA]) / P[P_TAU_FAA]
    x = S[S_FAA] / mu_bp
    S[S_SNS_BP] = 1.0 / (1.0 + x ** P[P_NU_S_BP])
    S[S_PSNS_BP] = 1.0 / (1.0 + x ** P[P_NU_P_BP])
    # Theory Eq. (10) for R, written in the Fortran's centred form
    S[S_REQ] = P[P_R0] + P[P_A_BP] * (S[S_SNS_BP] - 0.5) - P[P_B_BP] * (S[S_PSNS_BP] - 0.5)
    if active:
        S[S_RVB] += dt * (S[S_REQ] - S[S_RVB]) / P[P_T_BP]          # Theory Eq. (11), X = R
    S[S_PDOT] = (P[P_SV] * S[S_HR] - (S[S_MAPMEAN] - S[S_PVENRUN]) / S[S_RVB]) / P[P_CART]
    S[S_MAPMEAN] += dt * S[S_PDOT]
    S[S_FC] = S[S_MAPMEAN] + P[P_TAU_RATE] * S[S_PDOT]
    w = P[P_W]
    knee = P[P_KNEE]
    S[S_REC] = 1.0 / (1.0 + math.exp(-(S[S_FC] - knee) / w))
    mu_ref = P[P_MU_REF] if P[P_FIXED_REF] > 0.5 else mu_bp
    S[S_EP] = -P[P_K] * w / 60.0 * (softplus((S[S_FC] - knee) / w) - softplus((mu_ref - knee) / w))
    wr = P[P_WR]
    S[S_ER] = -P[P_KR] * wr / 60.0 * (softplus(S[S_PDOT] / wr) - math.log(2.0))

    # ===================== neuro_hr =====================
    ebp = S[S_EP] + S[S_ER]
    h = S[S_HR]
    ns, npp, ed = hr_direct(h, mu_hr, P)
    S[S_SNS_HR] = ns
    S[S_PSNS_HR] = npp
    S[S_ED] = ed
    if active:                                                       # Theory Eq. (11), X = HR
        T = P[P_T_HR]
        e1 = hr_direct(h, mu_hr, P)[2] + ebp
        k1 = dt * (-h + e1) / T
        e2 = hr_direct(h + 0.5 * k1, mu_hr, P)[2] + ebp
        k2 = dt * (-(h + 0.5 * k1) + e2) / T
        e3 = hr_direct(h + 0.5 * k2, mu_hr, P)[2] + ebp
        k3 = dt * (-(h + 0.5 * k2) + e3) / T
        e4 = hr_direct(h + k3, mu_hr, P)[2] + ebp
        k4 = dt * (-(h + k3) + e4) / T
        h = h + (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
    S[S_TDUR] = 1.0 / h
    S[S_HR] = 1.0 / S[S_TDUR]

    # ===================== cardiac_inflow =====================
    S[S_TCR] += dt
    if S[S_TCR] >= S[S_TDUR]:
        S[S_TCR] -= S[S_TDUR]
        S[S_WRAP] = 1.0
    else:
        S[S_WRAP] = 0.0
    kref = P[P_KREF]
    q_peak = P[P_SV] * math.pi / (2.0 * P[P_TSYS] * (1.0 + kref))
    if S[S_TCR] < P[P_TSYS]:
        q_fwd = q_peak * math.sin(math.pi * S[S_TCR] / P[P_TSYS])
    else:
        q_fwd = 0.0
    nbuf = I[I_NBUF]
    ndelay = I[I_NDELAY]
    q_buf[(nstep - 1) % nbuf] = q_fwd
    if nstep > ndelay or I[I_PRIMED] == 1:
        q_ref = kref * q_buf[(nstep - 1 - ndelay) % nbuf]
    else:
        q_ref = 0.0
    S[S_QIN] = q_fwd + q_ref

    # ===================== update_cvs =====================
    q_out = (S[S_PART] - S[S_PVEN]) / S[S_RVB]
    q_ret = (S[S_PVEN] - P[P_PRA]) / P[P_RVEN]
    if q_ret < 0.0:
        q_ret = 0.0
    S[S_PART] += dt * (S[S_QIN] - q_out) / P[P_CART]
    S[S_PVEN] += dt * (q_out - q_ret) / P[P_CVEN]

    # ===================== map_running_mean =====================
    I[I_PCOUNT] += 1
    k = I[I_PCOUNT] % NPB
    p_buf[k] = S[S_PART]
    v_buf[k] = S[S_PVEN]
    S[S_PSUM] += S[S_PART]
    S[S_VSUM] += S[S_PVEN]
    I[I_PWIN] += 1
    I[I_PFILL] = min(I[I_PFILL] + 1, NPB)
    n_cycle = max(1, int(round(S[S_TDUR] / dt)))
    while I[I_PWIN] > n_cycle:
        j = (k - I[I_PWIN] + 1) % NPB
        S[S_PSUM] -= p_buf[j]
        S[S_VSUM] -= v_buf[j]
        I[I_PWIN] -= 1
    while I[I_PWIN] < n_cycle and I[I_PWIN] < I[I_PFILL]:
        I[I_PWIN] += 1
        j = (k - I[I_PWIN] + 1) % NPB
        S[S_PSUM] += p_buf[j]
        S[S_VSUM] += v_buf[j]
    if I[I_PCOUNT] % 200000 == 0:                    # exact re-summation (round-off)
        ps = 0.0
        vs = 0.0
        for i in range(I[I_PWIN]):
            j = (k - i) % NPB
            ps += p_buf[j]
            vs += v_buf[j]
        S[S_PSUM] = ps
        S[S_VSUM] = vs
    S[S_MAPRUN] = S[S_PSUM] / I[I_PWIN]
    S[S_PVENRUN] = S[S_VSUM] / I[I_PWIN]

    # ===================== update_summary =====================
    if S[S_PART] > S[S_PMAX]:
        S[S_PMAX] = S[S_PART]
    if S[S_PART] < S[S_PMIN]:
        S[S_PMIN] = S[S_PART]
    S[S_PSUMC] += S[S_PART]
    S[S_PCNTC] += 1.0
    if S[S_WRAP] > 0.5:
        S[S_SBP] = S[S_PMAX]
        S[S_DBP] = S[S_PMIN]
        if S[S_PCNTC] > 0.0:
            S[S_MAPCYC] = S[S_PSUMC] / S[S_PCNTC]
        I[I_CYCLE] += 1
        S[S_PMAX] = S[S_PART]
        S[S_PMIN] = S[S_PART]
        S[S_PSUMC] = 0.0
        S[S_PCNTC] = 0.0
    S[S_T] += dt


@njit(cache=True)
def advance(S, I, P, p_buf, v_buf, q_buf, nsteps, active, rec_every, out):
    """Advance nsteps time steps.  Every rec_every steps one row is written to out.
    Returns the number of rows written."""
    nrow = 0
    for n in range(nsteps):
        _step(S, I, P, p_buf, v_buf, q_buf, active)
        if rec_every > 0 and (I[I_NSTEP] % rec_every) == 0 and nrow < out.shape[0]:
            o = out[nrow]
            o[0] = S[S_T]
            o[1] = S[S_HR] * 60.0
            o[2] = S[S_PART]
            o[3] = S[S_MAPMEAN]
            o[4] = S[S_MAPRUN]
            o[5] = S[S_SBP]
            o[6] = S[S_DBP]
            o[7] = S[S_MAPCYC]
            o[8] = S[S_SNS_HR]
            o[9] = S[S_PSNS_HR]
            o[10] = S[S_SNS_BP]
            o[11] = S[S_PSNS_BP]
            o[12] = S[S_RVB]
            o[13] = S[S_ED] * 60.0
            o[14] = (S[S_EP] + S[S_ER]) * 60.0
            o[15] = S[S_PDOT]
            o[16] = I[I_CYCLE]
            o[17] = S[S_MU_HR]
            o[18] = S[S_MU_BP]
            o[19] = S[S_FAA]
            nrow += 1
    return nrow


# ======================================================================
# Python-side model object
# ======================================================================
class ANSCVSModel:
    """Holds the state of one coupled ANS-CVS simulation.

    params: dict with the keys of PARAM_KEYS (see ans_cvs_config.MODEL_PARAMS).
    """
    PARAM_KEYS = ['dt', 'mu_HR', 'nu_s_HR', 'nu_p_HR', 'Aneu_HR', 'Bneu_HR', 'Cneu_HR', 'Tneu_HR',
                  'mu_BP', 'nu_s_BP', 'nu_p_BP', 'Aneu_BP', 'Bneu_BP', 'Tneu_BP', 'tau_Faa_BP', 'R0_base',
                  'K_BP_to_HR', 'MAP_knee_BP_to_HR', 'w_knee_BP_to_HR', 'tau_rate_BP_to_HR',
                  'K_rate_BP_to_HR', 'w_rate_BP_to_HR', 'mu_ref_coupling', 'use_fixed_coupling_ref',
                  't_systole', 'SV', 'k_reflect_base', 'C_art', 'C_ven', 'R_ven', 'P_RA', 'tau_reflect', 'tau_mu']

    def __init__(self, params):
        self.params = dict(params)
        self.params.setdefault('tau_mu', 0.0)
        self.P = np.array([float(self.params[k]) for k in self.PARAM_KEYS], dtype=np.float64)
        self.S = np.zeros(NSTATE, dtype=np.float64)
        self.I = np.zeros(NISTATE, dtype=np.int64)
        self.p_buf = np.zeros(NPB, dtype=np.float64)
        self.v_buf = np.zeros(NPB, dtype=np.float64)
        dt = self.P[P_DT]
        ndelay = max(1, int(self.P[P_TAU_REFLECT] / dt))
        nbuf = ndelay + 10
        self.q_buf = np.zeros(nbuf, dtype=np.float64)
        self.I[I_NDELAY] = ndelay
        self.I[I_NBUF] = nbuf

    # ------------------------------------------------------------------
    @property
    def dt(self):
        return self.P[P_DT]

    def set_mu(self, mu_HR=None, mu_BP=None):
        """Set the mu targets; the model's mu follows with time constant tau_mu."""
        if mu_HR is not None:
            self.P[P_MU_HR] = mu_HR
        if mu_BP is not None:
            self.P[P_MU_BP] = mu_BP

    def get(self, name):
        idx = {'t': S_T, 'HR_bps': S_HR, 'P_art': S_PART, 'P_ven': S_PVEN, 'R_vb': S_RVB,
               'Faa_BP': S_FAA, 'MAP_mean': S_MAPMEAN, 'MAP_run': S_MAPRUN, 'Pven_run': S_PVENRUN,
               'SNS_HR': S_SNS_HR, 'PSNS_HR': S_PSNS_HR, 'SNS_BP': S_SNS_BP, 'PSNS_BP': S_PSNS_BP,
               'SBP': S_SBP, 'DBP': S_DBP, 'MAP_cycle': S_MAPCYC, 'Pdot_BP': S_PDOT,
               'Eneu_HR_direct': S_ED, 'Eneu_BP_to_HR_P': S_EP, 'Eneu_BP_to_HR_R': S_ER,
               'mu_HR': S_MU_HR, 'mu_BP': S_MU_BP, 'R_eq': S_REQ}[name]
        return float(self.S[idx])

    # ------------------------------------------------------------------
    def init_state(self, hr_bps, R, P_art, P_ven, MAP_mean=None, Faa_BP=None):
        """Initial state as in parameterset(): pressures, afferents, HR and R."""
        S = self.S
        S[:] = 0.0
        S[S_HR] = hr_bps
        S[S_TDUR] = 1.0 / hr_bps
        S[S_TCR] = 0.0
        S[S_PART] = P_art
        S[S_PVEN] = P_ven
        S[S_RVB] = R
        S[S_REQ] = R
        m = P_art if MAP_mean is None else MAP_mean
        S[S_MAPMEAN] = m
        S[S_MAPRUN] = P_art
        S[S_FAA] = m if Faa_BP is None else Faa_BP
        S[S_FC] = m
        S[S_PVENRUN] = P_ven
        S[S_SNS_HR] = S[S_PSNS_HR] = S[S_SNS_BP] = S[S_PSNS_BP] = 0.5
        S[S_SBP] = S[S_DBP] = S[S_MAPCYC] = P_art
        S[S_PMAX] = S[S_PMIN] = P_art
        S[S_MU_HR] = self.P[P_MU_HR]
        S[S_MU_BP] = self.P[P_MU_BP]
        self.I[:I_NDELAY] = 0
        self.p_buf[:] = 0.0
        self.v_buf[:] = 0.0
        self.q_buf[:] = 0.0

    def spin_up(self, nsteps):
        """CVS spin-up with HR and R held (ANS inactive), as in run_case()."""
        nbuf = int(self.I[I_NBUF])
        n = (max(nsteps, nbuf) // nbuf) * nbuf          # multiple of nbuf, as in the Fortran
        dummy = np.zeros((1, NOUT))
        advance(self.S, self.I, self.P, self.p_buf, self.v_buf, self.q_buf, n, False, 0, dummy)
        self.I[I_PRIMED] = 1
        return n

    def run(self, nsteps, active=True, rec_every=100):
        """Advance nsteps; return decimated samples (rows x NOUT)."""
        rows = nsteps // rec_every + 2 if rec_every > 0 else 1
        out = np.empty((rows, NOUT))
        n = advance(self.S, self.I, self.P, self.p_buf, self.v_buf, self.q_buf,
                    int(nsteps), bool(active), int(rec_every), out)
        return out[:n]
