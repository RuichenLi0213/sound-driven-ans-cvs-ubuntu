#!/bin/bash
# =====================================================================
# setup_ubuntu.sh  -  one-time installation for the real-time ANS-CVS program (Ubuntu / Debian)
#
#   bash setup_ubuntu.sh                         check system libraries, create ./.venv, install requirements.txt
#   bash setup_ubuntu.sh --install-system-deps   also install missing system packages with apt (asks for sudo)
#   bash setup_ubuntu.sh --recreate              delete ./.venv first and reinstall
#   bash setup_ubuntu.sh --python=/usr/bin/python3.11   use a specific Python (3.9 - 3.12)
#
# Python packages go into ./.venv only.  System packages (PortAudio, Qt/X11 libraries,
# python3-venv) are installed only with --install-system-deps or after you confirm.
# =====================================================================
set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
RECREATE=0
SYSDEPS=0
PY_ARG=""
for a in "$@"; do
  case "$a" in
    --recreate) RECREATE=1 ;;
    --install-system-deps) SYSDEPS=1 ;;
    --python=*) PY_ARG="${a#--python=}" ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

ok_version() {   # Python 3.9 - 3.12 (numba 0.60 supports these)
  "$1" -c 'import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 12) else 1)' >/dev/null 2>&1
}

find_python() {
  local c
  for c in "$PY_ARG" python3.12 python3.11 python3.10 python3.9 python3 /usr/bin/python3 \
           "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3"; do
    [ -z "$c" ] && continue
    if command -v "$c" >/dev/null 2>&1 && ok_version "$c"; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

echo "== ANS-CVS real-time program: setup in $HERE"
ARCH="$(uname -m)"
# PyPI has PyQt5 wheels only for x86_64 Linux.  On ARM (aarch64) the GUI toolkit comes from
# Ubuntu (python3-pyqt5) and the virtual environment uses the system Python with access to it.
SYSQT=0
if [ "$ARCH" != "x86_64" ]; then
  SYSQT=1
  echo "-- $ARCH: using Ubuntu's python3-pyqt5 with the system Python (/usr/bin/python3)"
  [ -z "$PY_ARG" ] && PY_ARG=/usr/bin/python3
fi

# ---------------------------------------------------------------- Python
if [ "$RECREATE" = "1" ] && [ -d .venv ]; then
  echo "-- removing existing .venv"
  rm -rf .venv
fi
PY=""
if [ ! -x .venv/bin/python ]; then
  PY="$(find_python)" || {
    echo
    echo "ERROR: no Python 3.9 - 3.12 found."
    echo "Ubuntu 22.04 ships Python 3.10 and 24.04 ships 3.12 (both fine): sudo apt install python3"
    echo "Ubuntu 20.04 (Python 3.8):  sudo add-apt-repository ppa:deadsnakes/ppa"
    echo "                            sudo apt install python3.11 python3.11-venv"
    echo "then run:  bash setup_ubuntu.sh --python=python3.11"
    exit 1
  }
  echo "-- using $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
fi

# ---------------------------------------------------------------- system libraries
# shared library -> Ubuntu package
LIBS="libportaudio.so.2:libportaudio2
libxkbcommon-x11.so.0:libxkbcommon-x11-0
libxcb-icccm.so.4:libxcb-icccm4
libxcb-image.so.0:libxcb-image0
libxcb-keysyms.so.1:libxcb-keysyms1
libxcb-randr.so.0:libxcb-randr0
libxcb-render-util.so.0:libxcb-render-util0
libxcb-shape.so.0:libxcb-shape0
libxcb-xinerama.so.0:libxcb-xinerama0
libxcb-xfixes.so.0:libxcb-xfixes0
libxcb-xkb.so.1:libxcb-xkb1
libX11-xcb.so.1:libx11-xcb1
libGL.so.1:libgl1
libEGL.so.1:libegl1
libfontconfig.so.1:libfontconfig1
libdbus-1.so.3:libdbus-1-3
libglib-2.0.so.0:libglib2.0-0"
MISSING=""
LDCACHE="$(/sbin/ldconfig -p 2>/dev/null || ldconfig -p 2>/dev/null || true)"
while IFS=: read -r lib pkg; do
  echo "$LDCACHE" | grep -qE "(^|[[:space:]])${lib}[[:space:]]" || MISSING="$MISSING $pkg"
done <<< "$LIBS"
if [ "$SYSQT" = "1" ] && ! /usr/bin/python3 -c 'import PyQt5.QtWidgets' >/dev/null 2>&1; then
  MISSING="$MISSING python3-pyqt5"
fi
if [ -n "$PY" ] && ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  VENVPKG="$("$PY" -c 'import sys; print("python%d.%d-venv" % sys.version_info[:2])')"
  MISSING="$MISSING python3-venv $VENVPKG"
fi
if [ -n "$MISSING" ]; then
  echo "-- missing system packages:$MISSING"
  SUDO=""
  [ "$(id -u)" != "0" ] && SUDO="sudo"
  if [ "$SYSDEPS" != "1" ] && [ -t 0 ]; then
    read -r -p "   Install them now with: $SUDO apt-get install$MISSING ? [y/N] " ans
    case "$ans" in y|Y|yes|YES) SYSDEPS=1 ;; esac
  fi
  if [ "$SYSDEPS" = "1" ]; then
    $SUDO apt-get update
    $SUDO apt-get install -y $MISSING
  else
    echo
    echo "Install them and run this script again:"
    echo "   sudo apt-get install$MISSING"
    echo "(or: bash setup_ubuntu.sh --install-system-deps)"
    exit 1
  fi
else
  echo "-- system libraries present (PortAudio, Qt/X11)"
fi

# ---------------------------------------------------------------- venv + packages
if [ ! -x .venv/bin/python ]; then
  if [ "$SYSQT" = "1" ]; then
    "$PY" -m venv --system-site-packages .venv
  else
    "$PY" -m venv .venv
  fi
fi
echo "-- installing packages into .venv (requirements.txt)"
.venv/bin/python -m pip install --upgrade pip >/dev/null
if [ "$SYSQT" = "1" ]; then
  grep -v -i '^PyQt5' requirements.txt > .requirements_noqt.txt
  .venv/bin/python -m pip install --ignore-installed -r .requirements_noqt.txt
  rm -f .requirements_noqt.txt
else
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "-- checking the installation (imports, PortAudio, model compilation, stable rest)"
set +e
.venv/bin/python selfcheck.py
RC=$?
set -e
[ "$RC" = "1" ] && { echo "ERROR: self-check failed"; exit 1; }
[ "$RC" = "2" ] && echo "WARNING: the program will run, but the microphone library could not be loaded (libportaudio2)."

chmod +x run_realtime.sh run_test_mode.sh run_model_tests.sh run_microphone_check.sh install_desktop_launcher.sh 2>/dev/null || true
echo
echo "Setup complete. Next:"
echo "   bash run_model_tests.sh        (optional, no microphone)"
echo "   bash run_realtime.sh           (real microphone, default mode)"
echo "   bash run_test_mode.sh          (simulated stimuli, microphone not used)"
