"""
make_calibration_sound.py
=========================
Writes test sounds (WAV, 48 kHz, mono) into the folder calibration_sounds/.
It does NOT play anything.  Play them yourself, e.g.  afplay calibration_sounds/pink_noise_30s.wav

  pink_noise_30s.wav     steady pink noise, for the "Calibrate..." procedure
  tone_1kHz_30s.wav      steady 1 kHz tone, alternative calibration signal
  pink_noise_3s.wav      3 s burst, for the sustained-sound test (play loud enough to exceed 85 dB(A))
"""
import os

import numpy as np
from scipy.io import wavfile

FS = 48000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calibration_sounds')


def pink(n, seed=0):
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.normal(size=n))
    f = np.fft.rfftfreq(n, 1 / FS)
    f[0] = f[1]
    X /= np.sqrt(f)                       # 1/f power
    X[f < 20] = 0
    x = np.fft.irfft(X, n)
    return x / np.max(np.abs(x))


def fade(x, s=0.05):
    k = int(s * FS)
    w = np.ones_like(x)
    w[:k] = np.linspace(0, 1, k)
    w[-k:] = np.linspace(1, 0, k)
    return x * w


def write(name, x, peak=0.5):
    x = fade(x) * peak / np.max(np.abs(x))
    wavfile.write(os.path.join(OUT, name), FS, (x * 32767).astype(np.int16))
    print('wrote', os.path.join(OUT, name))


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    write('pink_noise_30s.wav', pink(30 * FS))
    t = np.arange(30 * FS) / FS
    write('tone_1kHz_30s.wav', np.sin(2 * np.pi * 1000 * t))
    write('pink_noise_3s.wav', pink(3 * FS, seed=1))
