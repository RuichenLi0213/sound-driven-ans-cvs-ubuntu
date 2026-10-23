"""
ans_cvs_config.py
=================
CONFIGURATION SECTION for the real-time ANS-CVS program.
Every adjustable target level, sound threshold, calibration offset and timing
parameter lives here.  Values derived from these (mu_HR, mu_BP, Cneu_HR, the
coupling reference) are computed at start-up by ans_cvs_design.py.

Theory.pdf symbols -> code variables
    alpha_HR  -> Aneu_HR     SNS weight, HR pathway         (Eq. 10)
    beta_HR   -> Bneu_HR     PSNS weight, HR pathway        (Eq. 10)
    alpha_BP  -> Aneu_BP     SNS weight, BP (resistance)    (Eq. 10)
    beta_BP   -> Bneu_BP     PSNS weight, BP (resistance)   (Eq. 10)
    gamma_HR  -> Cneu_HR     basal level, HR pathway (bps)
    gamma_BP  -> R0_base     basal level, BP pathway; the code writes Eq. (10) as
                             R0_base + Aneu_BP*(SNS-0.5) - Bneu_BP*(PSNS-0.5),
                             which equals Eq. (10) with gamma_BP = R0_base when
                             Aneu_BP = Bneu_BP
    nu_{i,S}, nu_{i,P} -> nu_s_*, nu_p_*  (Eq. 7, 8)
    mu_i      -> mu_HR (bps), mu_BP (mmHg)
    tau_i     -> Tneu_HR, Tneu_BP  (Eq. 11)
"""

# =====================================================================
# 1. TARGET LEVELS  (values determined earlier in this project)
# =====================================================================
HR_REST_BPM = 80          # regulated resting HR of the original model (icase=4 run)
MAP_REST_MMHG = 96.40        # regulated resting MAP of the original model
SBP_REST_REF = 104.65        # original-model resting SBP (display reference only)
DBP_REST_REF = 85.36         # original-model resting DBP (display reference only)
HR_ELEVATED_BPM = 100.0       # elevated HR chosen in the experimental plan
MAP_ELEVATED_MMHG = 113.0    # elevated MAP chosen in the experimental plan

# =====================================================================
# 2. ANS WEIGHTS  (fixed by the experiment specification: all four = 1.0)
# =====================================================================
ANEU_HR = 1.0                # alpha_HR, SNS weight, HR pathway
BNEU_HR = 1.0                # beta_HR, PSNS weight, HR pathway
ANEU_BP = 1.0                # alpha_BP, SNS weight, BP pathway
BNEU_BP = 1.0                # beta_BP, PSNS weight, BP pathway

# =====================================================================
# 3. COUPLED RECIPROCAL MODE  (equal slope magnitude, opposite sign, per pathway)
#    HR pathway: nu_s_HR = -NU_HR, nu_p_HR = +NU_HR  (same signs as the Fortran)
#    BP pathway: nu_s_BP = +NU_BP, nu_p_BP = -NU_BP  (same signs as the Fortran)
# =====================================================================
NU_HR = 2.0                  # |slope|, HR pathway   (Fortran: 7.0, see README)
NU_BP = 14.0                 # |slope|, BP pathway   (Fortran: 7.0; Theory tested 3.5-14)
SNS_HR_REST = 0.40           # resting SNS_HR operating point; sets Cneu_HR and mu_HR

# =====================================================================
# 4. TIME CONSTANTS (s)
# =====================================================================
DT = 5.0e-5                  # integration step (same as the Fortran)
TNEU_HR = 0.12               # HR effector time constant      (Fortran 3.5 s)
TNEU_BP = 0.05               # resistance effector time const (Fortran 0.5 s)
TAU_FAA_BP = 0.03            # MAP afferent relaxation time   (Fortran 0.2 s)
TAU_MU = 0.05                # mu transition time constant (added; 0 = step)

# =====================================================================
# 5. BP -> HR COUPLING (rise-sensitive baroreflex arm)
# =====================================================================
# Reference term of the pressure coupling.  False = Fortran form: the term uses the
# dynamic mu_BP (shifts with the activation, i.e. baroreflex resetting).  True = a
# separate fixed reference at MAP_REST_MMHG.  Both pass the simulated-stimulus tests
# with the values below (see README); the Fortran form is kept as default because
# separation was not necessary.
USE_FIXED_COUPLING_REF = False
K_BP_TO_HR = 0.5             # pressure gain above the knee, bpm/mmHg (Fortran 3.3)
KNEE_OFFSET_MMHG = 1.626     # knee = reference + offset (Fortran: 86.286 - 84.66)
W_KNEE_MMHG = 1.0            # recruitment width (Fortran 1.0)
TAU_RATE_S = 0.05            # rate sensitivity of the coupling afferent (Fortran 0.15)
K_RATE = 0.05                # rising-pressure rate gain, bpm per mmHg/s (Fortran 2.0)
W_RATE = 0.05                # rate rectifier width, mmHg/s (Fortran 0.05)

# =====================================================================
# 6. CARDIOVASCULAR SYSTEM (unchanged from the Fortran)
# =====================================================================
R0_BASE = 1.08453            # gamma_BP, basal resistance (mmHg s/mL)
CVS = dict(t_systole=0.30, SV=70.0, k_reflect_base=0.30, tau_reflect=0.25,
           C_art=1.5, C_ven=100.0, R_ven=0.04, P_RA=2.0)

# =====================================================================
# 7. STIMULUS RESPONSE TIMING
# =====================================================================
RISE_MIN_S = 1.0             # every trigger is latched for at least this long
RISE_CRITERION = 0.95        # fraction of the target increase required within RISE_MIN_S
RECOVERY_LIMIT_S = 5.0       # recovery criterion must be met within this time
RECOVERY_BAND = 0.05         # |x - rest| <= 5 % of the stimulus-induced increase
RECOVERY_HOLD_S = 0.5        # band must hold this long before the state returns to "rest"

# =====================================================================
# 8. START-UP: STABLE REST BEFORE DETECTION IS ENABLED
# =====================================================================
SPINUP_S = 20.0              # CVS spin-up, HR and R held (as in the Fortran)
SETTLE_S = 30.0              # ANS active, no stimulus
REST_CHECK_WINDOW_S = 5.0    # last part of the settle period that is checked
REST_TOL_HR_BPM = 0.1        # |HR - HR_REST| and HR range must be below this
REST_TOL_MAP_MMHG = 0.1      # |MAP - MAP_REST| and MAP range must be below this

# =====================================================================
# 9. SOUND LEVEL AND TRIGGER
# =====================================================================
TRIGGER_THRESHOLD_DB = 85.0  # trigger level, dB SPL, A-weighted (dB(A))
HYSTERESIS_DB = 6.0          # sound ends below threshold - hysteresis (79 dB(A))
RELEASE_HOLD_S = 0.15        # ... continuously for this long (sound-end criterion)
FREQUENCY_WEIGHTING = 'A'    # IEC 61672 A-weighting (digital approximation)
BLOCK_S = 0.010              # level update interval (10 ms)
WINDOW_S = 0.020             # analysis window: 20 ms rectangular RMS (L_Aeq,20ms)
# Calibration: L_SPL = L_dBFS + CAL_OFFSET_DB, where 0 dBFS = RMS of a full-scale sine.
# The default below is a PROVISIONAL ASSUMPTION, not a measurement.  A raw MEMS
# microphone spec (94 dB SPL -> -26 dBFS) would give 120 dB, but with the OS input
# gain the MacBook Pro microphone (development machine) read room sound at a median of -38.7 dBFS
# during testing (120 dB would call that 81 dB(A) and trigger on everyday sounds).
# 100 dB maps that room sound to about 61 dB(A).  Other computers / operating systems
# differ: calibrate on the computer you use.  Until a calibration is saved the
# GUI labels the level "Estimated sound level / Uncalibrated".
CAL_OFFSET_DB_DEFAULT = 100.0
CALIBRATION_FILE = 'calibration.json'   # written by the GUI "Calibrate" dialog
PEAK_HOLD_S = 2.0            # display only: highest level of the last 2 s (brief claps)
SAMPLE_RATE = None           # None = device default
INPUT_DEVICE = None          # None = system default input; can be chosen in the GUI

# =====================================================================
# 10. DISPLAY
# =====================================================================
PLOT_WINDOW_S = 40.0         # rolling window (30-60 s)
PLOT_FPS = 30
SAMPLE_EVERY_STEPS = 100     # one plotted sample per 5 ms of model time
BUFFER_S = 60.0              # bounded ring buffers hold the last 60 s
CHUNK_S = 0.01               # model integrated in 10 ms chunks (200 steps)
SESSIONS_DIR = 'sessions'    # "Save data" writes CSV/log files here
