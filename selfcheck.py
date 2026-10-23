"""
selfcheck.py
============
Installation check used by the setup scripts (no microphone is opened):
Python and package versions, PortAudio availability, model compilation
(numba JIT, cached in __pycache__) and a short stable-rest simulation.
Exit code 0 = OK, 1 = model/package problem, 2 = OK except PortAudio.
"""
import platform
import sys
import time


def main():
    t0 = time.time()
    print('   Python %s on %s (%s)' % (sys.version.split()[0], platform.platform(), platform.machine()))
    try:
        import numpy
        import scipy
        import numba
        import PyQt5.QtCore
        import pyqtgraph
        print('   numpy %s | scipy %s | numba %s | Qt %s | pyqtgraph %s'
              % (numpy.__version__, scipy.__version__, numba.__version__, PyQt5.QtCore.QT_VERSION_STR,
                 pyqtgraph.__version__))
    except Exception as e:
        print('ERROR: a required package could not be imported: %s' % e)
        return 1
    audio_ok = True
    try:
        from audio_monitor import import_sounddevice
        sd = import_sounddevice()
        print('   sounddevice %s | %s' % (sd.__version__, sd.get_portaudio_version()[1]))
    except Exception as e:
        audio_ok = False
        print('WARNING: microphone input will not work: %s' % e)
    import ans_cvs_config as C
    import ans_cvs_design as D
    import ans_cvs_model as M
    params, d = D.derive()
    mod, out = D.rest_model(params, d, spinup_s=10.0, settle_s=10.0)
    hr = out[-200:, M.OC['HR_bpm']].mean()
    mp = out[-200:, M.OC['MAP_mean']].mean()
    print('   model: rest HR %.2f bpm (target %.2f), MAP %.2f mmHg (target %.2f), %.1f s incl. compilation'
          % (hr, C.HR_REST_BPM, mp, C.MAP_REST_MMHG, time.time() - t0))
    if not (abs(hr - C.HR_REST_BPM) < 0.1 and abs(mp - C.MAP_REST_MMHG) < 0.1):
        print('ERROR: the model did not settle at the resting targets')
        return 1
    return 0 if audio_ok else 2


if __name__ == '__main__':
    sys.exit(main())
