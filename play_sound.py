"""
play_sound.py
=============
Plays a WAV file through the default (or a chosen) output device, for the
calibration procedure and the sustained-sound test.  Works the same on
Windows, Ubuntu and macOS.

    python play_sound.py calibration_sounds/pink_noise_30s.wav
    python play_sound.py calibration_sounds/pink_noise_3s.wav --gain -6     (6 dB quieter)
    python play_sound.py --list                                             (output devices)
    python play_sound.py file.wav --device 3
"""
import argparse
import sys

import numpy as np
from scipy.io import wavfile

from audio_monitor import import_sounddevice


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('wav', nargs='?')
    ap.add_argument('--device', default=None, help='output device index or name')
    ap.add_argument('--gain', type=float, default=0.0, help='gain in dB (negative = quieter)')
    ap.add_argument('--list', action='store_true', help='list output devices')
    a = ap.parse_args()
    sd = import_sounddevice()
    if a.list or not a.wav:
        apis = [h['name'] for h in sd.query_hostapis()]
        for i, d in enumerate(sd.query_devices()):
            if d['max_output_channels'] > 0:
                print('%3d  %s [%s]' % (i, d['name'], apis[d['hostapi']]))
        print('default output:', sd.default.device[1])
        return 0
    fs, x = wavfile.read(a.wav)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    elif x.dtype == np.int32:
        x = x.astype(np.float32) / 2147483648.0
    x = (x * 10 ** (a.gain / 20.0)).astype(np.float32)
    dev = int(a.device) if a.device is not None and str(a.device).isdigit() else a.device
    print('playing %s (%.1f s, %d Hz) ...' % (a.wav, len(x) / fs, fs))
    sd.play(x, fs, device=dev)
    sd.wait()
    return 0


if __name__ == '__main__':
    sys.exit(main())
