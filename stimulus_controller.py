"""
stimulus_controller.py
======================
Phase state machine shared by the real-time program and the simulated-stimulus
tests.  The source of stimulus events (microphone or test injection) is the
only thing that differs between them.

Timing convention (all times are model time, seconds)
-----------------------------------------------------
* A valid stimulus onset is latched into one complete response event:
  mu_HR / mu_BP targets switch to their activation values immediately.
* The activation is held for at least RISE_MIN_S (1 s) even if the sound has
  already ended ("rise" state).
* If the sound is still above threshold when the rise is complete, activation
  continues ("sustained activation") until the sound-end criterion is met.
* Recovery (mu targets back to rest) starts at the later of
  "rise completed" and "sound ended".
* State returns to "rest" when HR and MAP stay within RECOVERY_BAND of the
  stimulus-induced increase for RECOVERY_HOLD_S.
* A new, independent onset during recovery re-activates from the current
  state (no reset to rest).  The detector only reports a new onset after the
  previous sound has ended, so one sound is never counted twice.
"""
import ans_cvs_config as C

REST, RISE, SUSTAINED, RECOVERY, PAUSED, ENDED, INIT = (
    'rest', 'activation (rise)', 'sustained activation', 'recovery', 'paused', 'ended', 'initialising')


class StimulusController:
    def __init__(self, mu_rest, mu_act, hr_rest, map_rest, hr_act, map_act):
        self.mu_rest = mu_rest          # (mu_HR, mu_BP)
        self.mu_act = mu_act
        self.hr_rest, self.map_rest = hr_rest, map_rest
        self.inc_hr, self.inc_map = hr_act - hr_rest, map_act - map_rest
        self.state = REST
        self.sound_active = False
        self.t_onset = None
        self.t_rise_done = None
        self.t_recovery = None
        self.t_in_band = None
        self.log = []                   # (t_model, event, detail)

    # ------------------------------------------------------------------ events
    def on_sound_onset(self, t, level_db=None):
        """A sound crossed the trigger threshold (detector already debounced)."""
        self.sound_active = True
        if self.state in (REST, RECOVERY):
            prev = self.state
            self.state = RISE
            self.t_onset = t
            self.t_rise_done = t + C.RISE_MIN_S
            self.t_recovery = None
            self.t_in_band = None
            self.log.append((t, 'stimulus', 'onset %s; from %s' % (_lvl(level_db), prev)))
            return 'trigger'
        self.log.append((t, 'stimulus-during-activation', 'onset %s; no reset' % _lvl(level_db)))
        return 'ignored'

    def on_sound_end(self, t):
        self.sound_active = False
        self.log.append((t, 'sound-end', ''))
        if self.state == SUSTAINED:
            self._start_recovery(t)

    # ------------------------------------------------------------------ update
    def update(self, t, hr_bpm, map_mmhg):
        """Called once per integration chunk.  Returns the mu targets to apply."""
        if self.state == RISE and t >= self.t_rise_done:
            if self.sound_active:
                self.state = SUSTAINED
                self.log.append((t, 'sustained', 'sound still above threshold'))
            else:
                self._start_recovery(t)
        elif self.state == RECOVERY:
            in_band = (abs(hr_bpm - self.hr_rest) <= C.RECOVERY_BAND * self.inc_hr and
                       abs(map_mmhg - self.map_rest) <= C.RECOVERY_BAND * self.inc_map)
            if in_band:
                if self.t_in_band is None:
                    self.t_in_band = t
                elif t - self.t_in_band >= C.RECOVERY_HOLD_S:
                    self.state = REST
                    self.log.append((t, 'rest', 'within %.0f%% band since %.3f s after recovery onset'
                                     % (100 * C.RECOVERY_BAND, self.t_in_band - self.t_recovery)))
            else:
                self.t_in_band = None
        return self.mu_targets()

    def mu_targets(self):
        return self.mu_act if self.state in (RISE, SUSTAINED) else self.mu_rest

    def _start_recovery(self, t):
        self.state = RECOVERY
        self.t_recovery = t
        self.t_in_band = None
        self.log.append((t, 'recovery-onset', ''))


def _lvl(level_db):
    return '' if level_db is None else '%.1f dB' % level_db
