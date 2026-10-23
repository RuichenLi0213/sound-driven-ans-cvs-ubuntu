# Real-time ANS–CVS model driven by the microphone (Ubuntu version)

This program runs the coupled ANS–CVS model continuously and listens to the computer microphone. When the sound level exceeds **85 dB(A)**, ANS activity changes in the model, and the virtual heart rate and blood pressure rise within 1 s and recover within 5 s.

> HR, pulsatile blood pressure and the four ANS activities shown on screen are **virtual signals computed by the model**. They are not measurements of the person at the computer.

This folder is the Ubuntu port of `ans_cvs_realtime/` (macOS). The model, GUI and trigger logic are the same Python code; only the setup and start scripts, and some operating-system messages, differ ([section 15](#15-differences-from-the-macos-version)). Claude Code is not needed to run it.

---

## Contents

1. [What is in the folder](#1-what-is-in-the-folder)
2. [Requirements](#2-requirements)
3. [Installation (one time)](#3-installation-one-time)
4. ["Compilation"](#4-compilation)
5. [Check the installation without a microphone](#5-check-the-installation-without-a-microphone)
6. [Microphone and sound settings on Ubuntu](#6-microphone-and-sound-settings-on-ubuntu)
7. [Run the program](#7-run-the-program)
8. [Sound-level calibration](#8-sound-level-calibration)
9. [Clap testing: step-by-step protocol](#9-clap-testing-step-by-step-protocol)
10. [Saving and analysing data](#10-saving-and-analysing-data)
11. [Configuration](#11-configuration)
12. [How it works](#12-how-it-works)
13. [What has and has not been tested](#13-what-has-and-has-not-been-tested)
14. [Troubleshooting](#14-troubleshooting)
15. [Differences from the macOS version](#15-differences-from-the-macos-version)

---

## 1. What is in the folder

| File | Purpose |
|---|---|
| `setup_ubuntu.sh` | **One-time installation**: checks and installs system libraries, creates `.venv/`, installs packages, runs the self-check |
| `run_realtime.sh` | **Start the program, real-microphone mode** (default) |
| `run_test_mode.sh` | Start in TEST MODE (simulated stimuli; the microphone is not opened) |
| `run_model_tests.sh` | Automated tests without the microphone (about 1 min, headless) |
| `run_microphone_check.sh` | 10 s live microphone check: device, sample rate, levels |
| `install_desktop_launcher.sh` | Optional: adds the program to the application menu |
| `ans_cvs_realtime.py` | Main program: GUI, real-time engine, microphone trigger |
| `ans_cvs_config.py` | **Configuration section**: targets, threshold, calibration offset, time constants |
| `ans_cvs_model.py` | Python/numba port of `ans_cvs_coupled_single_case.f90` |
| `ans_cvs_design.py` | Derives μ_HR, μ_BP and Cneu_HR from the target levels |
| `stimulus_controller.py` | Phase state machine (rest, rise, sustained activation, recovery) |
| `audio_monitor.py` | Microphone input, A-weighted level, trigger with hysteresis/debouncing, calibration |
| `selfcheck.py` | Installation check used by the setup script |
| `make_calibration_sound.py` | Writes pink-noise and 1 kHz WAV files for calibration |
| `play_sound.py` | Plays a WAV file through the speakers |
| `analyze_session.py` | Evaluates data saved with **Save data** |
| `test_*.py` | Test scripts used by `run_model_tests.sh` / `run_microphone_check.sh` |
| `requirements.txt` | Pinned Python packages |

Created when you use the program:

| File or folder | Created by |
|---|---|
| `.venv/` | Setup |
| `__pycache__/` | Compiled model cache |
| `calibration.json` | Calibration |
| `sessions/` | **Save data** |
| `calibration_sounds/` | `make_calibration_sound.py` |
| `test_output/` | Tests |

---

## 2. Requirements

* **Ubuntu 22.04 or 24.04** desktop, on x86_64 (Intel/AMD) or arm64. Debian-based systems with `apt` should work but were not tested.
* Python 3.9–3.12. Ubuntu 22.04 ships 3.10 and 24.04 ships 3.12, both fine. Ubuntu 20.04 (Python 3.8) needs a newer Python; see section 14.
* `sudo` rights once, to install system libraries:
  * PortAudio (`libportaudio2`);
  * the X11/XCB libraries Qt needs;
  * `python3-venv`;
  * on arm64 also `python3-pyqt5`.
* An internet connection for the first installation (about 200 MB download).
* A graphical desktop session (X11, or Wayland with XWayland, which is the Ubuntu default) and a microphone.
* For calibration (recommended): a sound level meter, or a smartphone sound-level app.
* WSL (Linux on Windows) is **not** recommended for the microphone; use the Windows version instead.

---

## 3. Installation (one time)

1. Copy `ans_cvs_realtime_ubuntu.zip` to the Ubuntu machine and extract it:

   ```bash
   unzip ans_cvs_realtime_ubuntu.zip -d ~/
   ```

   ```bash
   cd ~/ans_cvs_realtime_ubuntu
   ```

2. Run the setup:

   ```bash
   bash setup_ubuntu.sh
   ```

   The script:
   * finds Python 3.12 / 3.11 / 3.10 / 3.9;
   * checks the system libraries and lists any that are missing, then asks whether to install them with `sudo apt-get install …`. Answer `y` and enter your password;
   * creates `.venv/` in this folder and installs `requirements.txt` there (Python packages are not installed system-wide);
   * compiles the model once and checks that it settles at rest.

   It ends with `Setup complete`, after a line like `model: rest HR 80.00 bpm (target 80.00), MAP 96.40 mmHg (target 96.40)`.

Options:

```bash
bash setup_ubuntu.sh --install-system-deps
```

```bash
bash setup_ubuntu.sh --recreate
```

```bash
bash setup_ubuntu.sh --python=/usr/bin/python3.11
```

`--install-system-deps` installs missing system packages without asking. `--recreate` deletes `.venv` and reinstalls. `--python=` chooses a specific interpreter.

The system packages the script may install:

```
libportaudio2 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0
libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xfixes0 libxcb-xkb1 libx11-xcb1
libgl1 libegl1 libfontconfig1 libdbus-1-3 libglib2.0-0 python3-venv (python3.X-venv)
```

**arm64 machines** (for example ARM laptops or Raspberry Pi 5 with Ubuntu): PyPI has no PyQt5 package for arm64 Linux. The script detects this. It installs Ubuntu's `python3-pyqt5`, uses the system Python, and gives `.venv` access to it. Everything else is the same.

---

## 4. "Compilation"

There is no separate build step. The numerical core (`ans_cvs_model.py`) is compiled to machine code by **numba** the first time it runs (during setup) and cached in `__pycache__/`. Later starts take about a second.

---

## 5. Check the installation without a microphone

```bash
bash run_model_tests.sh
```

It runs four test scripts headless and ends with `ALL TESTS PASSED`. None of them opens the microphone.

| Script | What it checks |
|---|---|
| `test_simulated_stimulus.py` | Model with simulated "above 85 dB" events: rest, 1 s rise, 5 s recovery, sustained sound, re-trigger, weights, reciprocity, stability. Report in `test_output/` |
| `test_realtime_engine.py` | Real-time clock sync, stimulus latency, pause/resume |
| `test_audio_detector.py` | Trigger logic with synthetic sounds |
| `test_gui_smoke.py` | Start / Pause / Resume / End / Save data in TEST MODE (offscreen) |

To see the program without sound:

```bash
bash run_test_mode.sh
```

Click **Start**, wait for `State: rest`, then use the **Inject simulated stimulus** buttons.

---

## 6. Microphone and sound settings on Ubuntu

**Permission.** Ubuntu does not ask for microphone permission for programs started from the terminal; the microphone is available as long as it is not muted. The program uses PortAudio through ALSA, which forwards to PipeWire (24.04) or PulseAudio (22.04). The device called **`default`** follows the input chosen in the system settings.

1. **Settings › Sound › Input**: choose the microphone, set the **input volume** to a fixed value (for example 75%), and make sure it is not muted. Do not change the volume after calibration.
2. Optional, for a detailed view:

   ```bash
   sudo apt install pavucontrol
   ```

   In **pavucontrol**, the *Input Devices* tab shows the level meter and mute state. While the program runs, the *Recording* tab shows which source it records from.
3. In the program's **Input** list, entries show the audio interface in brackets, for example `default [ALSA]`, `pipewire [ALSA]`, `pulse [ALSA]`, or `HDA Intel PCH: ALC… (hw:0,0) [ALSA]`.
   * Use **default** (or `pipewire` / `pulse`) so the system settings apply.
   * Direct `hw:` devices bypass the mixer, may support only fixed sample rates, and block other programs.
   * Use the **same entry** for calibration and testing.

If the microphone cannot be used, a red line reads "*The microphone delivers only zeros…*" or an error dialog appears. The model is then **not** started; the program never switches to simulated sound on its own.

---

## 7. Run the program

**Real-microphone mode (default):**

```bash
bash run_realtime.sh
```

The same command without the launcher:

```bash
.venv/bin/python ans_cvs_realtime.py
```

To choose an input device, add for example `--device 5`. On a Wayland session the launcher runs Qt through XWayland (`QT_QPA_PLATFORM=xcb`). To add the program to the application menu:

```bash
bash install_desktop_launcher.sh
```

### The window

* **Top row**
  * **Start**, **Pause/Resume**, **End**, **Save data**.
  * A green **REAL MICROPHONE MODE** label (an orange **TEST MODE** label in test mode).
  * The **Input** list, the **calibration offset** box and **Calibrate…**.
* **Second row**
  * The current level in dB(A) and the highest level of the last 2 s ("max 2 s").
  * The threshold (85 dB(A)) and whether the detector is armed.
  * Calibration status: red **Estimated sound level / Uncalibrated** until you calibrate.
  * The model state: `initialising`, `rest`, `activation (rise)`, `sustained activation`, `recovery`, `paused`, `ended`.
* **Three panels, shared rolling 40 s time axis**
  1. Heart rate (bpm), with a reference line at rest.
  2. Pulsatile arterial pressure (blue), MAP (black dashed), and SBP ▲ / DBP ▼ per beat.
  3. ANS activity, 0–1:
     * `SNS_HR`: red, solid
     * `PSNS_HR`: blue, solid
     * `SNS_BP`: red, dashed
     * `PSNS_BP`: blue, dashed
  * Red vertical line = stimulus detected; green = recovery onset.
* **Event log**: every detected sound with its trigger level, maximum level and latency.

### Buttons

* **Start**: establishes stable rest (spin-up and settle, computed in under a second) and checks it. Then real-time computation starts and the detector is armed. No stimulus is injected.
* **Pause / Resume**: freezes model time and plots; stimuli are ignored and audio from the pause is discarded.
* **End**: stops computation, releases the microphone and keeps the plots. Closing the window also releases everything.
* **Save data**: writes the last 60 s to `sessions/` (section 10).

---

## 8. Sound-level calibration

The microphone delivers a **digital level in dBFS**, which depends on the microphone, the input volume and the driver. A physical level in **dB SPL** needs a measured offset:

    L_SPL [dB(A)] = L_dBFS + offset          (0 dBFS = RMS of a full-scale sine)

Until you calibrate, the program uses a **provisional offset of 100 dB**. That value was found on the development MacBook; **your computer may differ by 10–20 dB**. The window says **"Estimated sound level / Uncalibrated"**.

### Procedure (about 5 minutes)

1. Generate the calibration sounds (this only writes files):

   ```bash
   .venv/bin/python make_calibration_sound.py
   ```

2. Place the **sound level meter** next to the computer microphone, set to **A-weighting** and **Leq** or **Fast**.
3. Start the program (`bash run_realtime.sh`).
4. Play steady pink noise from a speaker about 0.5–1 m away, loud enough for the meter to read **about 75–90 dB(A)**:

   ```bash
   .venv/bin/python play_sound.py calibration_sounds/pink_noise_30s.wav
   ```

   `paplay calibration_sounds/pink_noise_30s.wav` also works.
5. While the noise plays, click **Calibrate…**, type the **meter reading** and press **OK**. The program measures for 3 s.
6. The program saves the offset in `calibration.json`, and the label turns green: **Calibrated (offset … dB, date)**.
7. Check: play the noise again and compare with the meter. They should agree within about ±1–2 dB.

### Notes

* Recalibrate if you change the microphone, the input entry or the input volume.
* A smartphone sound-level app is only an approximate reference.
* Metric used everywhere in the program: A-weighted RMS over a **20 ms** window, updated every **10 ms**.

---

## 9. Clap testing: step-by-step protocol

**Preparation**
1. Use a quiet room.
2. Calibrate (section 8), or write "uncalibrated, offset 100 dB" in the results.
3. Mark a position **1.0 m** in front of the microphone.
4. Start `bash run_realtime.sh`, click **Start**, and wait for `State: rest` and `detector armed`.

**Step 1: background (2 min)**
* 1 min of silence, then 1 min of normal conversation at about 1 m.
* Expected: **no** "STIMULUS" lines; HR stays at 80 bpm and MAP at 96.4 mmHg.
* Record the highest "max 2 s".

**Step 2: single claps at 1.0 m (10 claps)**
1. Clap once.
2. Wait at least **8 s** (until `back at rest`).
3. Repeat 10 times.

Expected for each clap:
* `STIMULUS (xx.x dB(A))`, then `sound ended (max yy.y dB(A))`.
* HR rises to about 100 bpm and MAP to about 113 mmHg within 1 s.
* `recovery onset` 1.0 s after the clap, then `back at rest`.

**Step 3: distance (optional)**: 5 claps at **0.5 m** and 5 at **2 m**.

**Step 4: sustained sound**
1. Play the 3 s burst loud enough to exceed 85 dB(A):

   ```bash
   .venv/bin/python play_sound.py calibration_sounds/pink_noise_3s.wav
   ```

2. Expected: `sustained activation`, then `recovery onset` about 0.2 s after the sound stops.

**Step 5: re-trigger**
1. Clap.
2. Clap again 1–2 s after `recovery onset`.
3. Expected: the curves rise again from their current value.

**Step 6: pause**: click **Pause** and clap (expected: no reaction), then **Resume**.

**Step 7: save and analyse**: click **Save data** after each step, then run `analyze_session.py` (section 10).

**If claps do not trigger**: read "max 2 s"; check the calibration; clap closer. Change the threshold in the configuration rather than the offset.

**If speech triggers**: check the calibration; raise `TRIGGER_THRESHOLD_DB` or `HYSTERESIS_DB`.

Results table:

| # | Distance | Detected? | Trigger dB(A) | Max dB(A) | HR peak | MAP peak | Rise ≤ 1 s? | Recovery ≤ 5 s? | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.0 m | | | | | | | | |

---

## 10. Saving and analysing data

**Save data** writes three files to `sessions/`:

| File | Contents |
|---|---|
| `session_YYYYmmdd_HHMMSS.csv` | Model samples every 5 ms |
| `…_beats.csv` | SBP/DBP per beat |
| `…_events.txt` | Settings, calibration, event log and markers |

Evaluate the newest session:

```bash
.venv/bin/python analyze_session.py
```

For each stimulus it prints the time to 95% of the target increase, HR/MAP at 1 s, recovery time, the error 5 s after recovery onset, SBP/DBP, and **PASS** or **CHECK** against the 1 s / 5 s criteria.

---

## 11. Configuration

All adjustable values are in **`ans_cvs_config.py`**. Restart the program after editing.

| Setting | Current value | Meaning |
|---|---|---|
| `TRIGGER_THRESHOLD_DB` | 85.0 | Trigger level, dB(A) |
| `HYSTERESIS_DB`, `RELEASE_HOLD_S` | 6.0, 0.15 | The sound has ended when the level stays below 79 dB(A) for 150 ms |
| `CAL_OFFSET_DB_DEFAULT` | 100.0 | Provisional offset before calibration |
| `HR_REST_BPM`, `MAP_REST_MMHG` | 80, 96.40 | Resting targets |
| `HR_ELEVATED_BPM`, `MAP_ELEVATED_MMHG` | 100.0, 113.0 | Elevated targets |
| `RISE_MIN_S` | 1.0 | Minimum activation per trigger |
| `PLOT_WINDOW_S` | 40 | Rolling plot window, s |

* The μ values are recalculated from the targets at every **Start**.
* After changes, run `bash run_model_tests.sh` again.
* The four ANS weights must stay at 1.0; the program refuses to start otherwise.

---

## 12. How it works

### Timing convention

1. When the level reaches 85 dB(A), the μ targets switch to their activation values.
2. Activation is latched for **at least 1 s**.
3. If the sound is still above threshold at 1 s, activation is sustained until the sound ends (below 79 dB(A) for 150 ms).
4. **Recovery starts at the later of "rise completed" and "sound ended".**
5. The state is `rest` again when HR and MAP stay within 5% of the stimulus-induced increase for 0.5 s.
6. A new sound during recovery re-activates from the current state. One sound counts once.

### ANS weights and mode

* All four weights are fixed at **1.0** (Theory Eq. 10 → code): α_HR → `Aneu_HR`, β_HR → `Bneu_HR`, α_BP → `Aneu_BP`, β_BP → `Bneu_BP`. The activities `SNS_HR`, `PSNS_HR`, `SNS_BP` and `PSNS_BP` are computed by the model.
* **Coupled reciprocal mode in every phase**: each pathway has slopes of equal size and opposite sign.

| Slope | Value |
|---|---|
| `nu_s_HR` | −2 |
| `nu_p_HR` | +2 |
| `nu_s_BP` | +14 |
| `nu_p_BP` | −14 |

  Only μ moves: activation means μ_HR down and μ_BP up.

### Derived values for the current targets (80 → 100 bpm, 96.4 → 113 mmHg)

| Parameter | Rest | Activation |
|---|---|---|
| μ_HR | 1.63299 bps (97.98 bpm) | 1.42565 bps (85.54 bpm) |
| μ_BP | 94.848 mmHg | 110.266 mmHg |

* Cneu_HR = 1.53449 bps.
* Time constants: Tneu_HR 0.12 s, Tneu_BP 0.05 s, tau_Faa_BP 0.03 s, τ_μ 0.05 s.
* Coupling: K_BP_to_HR 0.5 bpm/mmHg; knee at rest MAP + 1.626 mmHg.
* Cardiovascular parameters are unchanged from the Fortran.

Reasons for each change relative to the Fortran are given in the macOS README (`ans_cvs_realtime/README.md`, section 12).

---

## 13. What has and has not been tested

**Tested before delivery: the complete workflow of this folder in clean Ubuntu containers**
* Ubuntu 24.04 x86_64, Ubuntu 22.04 x86_64 (emulated on Apple silicon), and Ubuntu 24.04 arm64. Each started with only `python3` installed, plus Xvfb for the X11 test.
* `setup_ubuntu.sh --install-system-deps` detected and installed the missing system packages, built `.venv`, and passed the self-check (rest 80.00 bpm / 96.40 mmHg). The arm64 run used Ubuntu's `python3-pyqt5` as intended.
* `run_model_tests.sh` ended with **ALL TESTS PASSED** in all three (model 22/22, engine 11/11, audio 14/14, GUI 13/13).
* The GUI also passed all 13 checks under a **real X11 display server** (Xvfb, `QT_QPA_PLATFORM=xcb`), and its screenshot looked correct.
* With no microphone present, the program reports *"No usable input device … Linux: check that the input is not muted …"* instead of failing silently.
* Key numbers with your current targets: HR reached 95% of its rise in 0.63 s and cycle-mean MAP in 0.67 s; back within 5% after 1.11 s (HR) and 0.31 s (MAP). Plateau 100.02 bpm / 112.99 mmHg.

**Not tested**
* A physical Ubuntu desktop (Wayland/XWayland session, the application-menu launcher).
* Real audio devices through PipeWire/PulseAudio.
* The microphone, calibration, or a real clap.

---

## 14. Troubleshooting

| Problem | What to do |
|---|---|
| `missing system packages` | Answer `y`, or run `bash setup_ubuntu.sh --install-system-deps` |
| `no Python 3.9 - 3.12 found` (Ubuntu 20.04) | Run `sudo add-apt-repository ppa:deadsnakes/ppa` and `sudo apt install python3.11 python3.11-venv`, then `bash setup_ubuntu.sh --python=python3.11` |
| `ensurepip is not available` / venv error | `sudo apt install python3-venv` (on 24.04 also `python3.12-venv`), then `bash setup_ubuntu.sh --recreate` |
| `qt.qpa.plugin: Could not load the Qt platform plugin "xcb"` | Run `bash setup_ubuntu.sh --install-system-deps`. To see which library is missing, start once with `QT_DEBUG_PLUGINS=1 bash run_realtime.sh` |
| Window does not appear on Wayland | Start with `QT_QPA_PLATFORM=xcb bash run_realtime.sh` (needs XWayland, default on Ubuntu) |
| `PortAudio library not found` | `sudo apt install libportaudio2` |
| "Microphone could not be opened" | Choose **default** in the **Input** list; close programs that hold a `hw:` device |
| "Microphone delivers only zeros" / level never moves | Input muted or wrong source: Settings › Sound › Input, or pavucontrol |
| Claps never reach 85 dB(A) | Calibrate; raise the input volume *before* calibrating; clap closer; read "max 2 s" |
| Speech triggers | Calibrate; raise the threshold in `ans_cvs_config.py` |
| "(model lagging real time …)" | Close heavy programs; check the CPU power mode |
| Anything else | Run `bash run_model_tests.sh` and read the first failing line |

---

## 15. Differences from the macOS version

**Scripts and documentation**
* `.sh` scripts replace `.command`.
* `setup_ubuntu.sh` also installs system libraries via `apt`, and uses `python3-pyqt5` on arm64.
* `play_sound.py` replaces `afplay`.
* The optional menu launcher `install_desktop_launcher.sh` is new.
* This README covers PipeWire/PulseAudio and device selection.

**Python changes, which also run unchanged on macOS**
* Microphone help messages depend on the operating system.
* The **Input** list shows the audio interface for each device.
* A clear error appears if the PortAudio library is missing.
* Text files are written as UTF-8.
* High-DPI scaling is enabled.
* New `selfcheck.py` and `play_sound.py`.
* `test_realtime_engine.py` samples HR 0.9 s after a stimulus instead of 0.6 s. With your current targets (80 → 100 bpm), HR reaches 95% at about 0.63 s, so the old 0.6 s sample point would report a false failure.

**Unchanged**: the model, the parameter values, your configuration (targets 80 / 100 bpm, threshold 85 dB(A)) and the GUI layout.
