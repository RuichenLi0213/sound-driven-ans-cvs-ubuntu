#!/bin/bash
# Optional: adds "ANS-CVS real-time model" to the application menu (current user only).
# Remove again with:  rm ~/.local/share/applications/ans-cvs-realtime.desktop
cd "$(dirname "$0")"
HERE="$(pwd)"
mkdir -p "$HOME/.local/share/applications"
F="$HOME/.local/share/applications/ans-cvs-realtime.desktop"
cat > "$F" <<DESK
[Desktop Entry]
Type=Application
Name=ANS-CVS real-time model
Comment=Microphone-driven ANS-CVS simulation (real-microphone mode)
Exec=bash "$HERE/run_realtime.sh"
Path=$HERE
Terminal=true
Categories=Science;Education;
DESK
chmod +x "$F"
echo "created $F"
