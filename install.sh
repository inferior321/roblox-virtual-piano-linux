#!/usr/bin/env bash
# Sets up a self-contained virtualenv next to this script.
# To uninstall, delete this whole folder.

set -euo pipefail
cd "$(dirname "$0")"

echo "==> Checking Python version"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo
    echo "Python 3.10 or newer is required. Found: $(python3 --version)"
    exit 1
fi

echo "==> Checking for python3-venv"
if ! python3 -c "import venv" 2>/dev/null; then
    echo
    echo "python3-venv is missing. Install it first:"
    echo "    sudo apt install python3-venv"
    exit 1
fi

echo "==> Creating the virtualenv in ./venv"
python3 -m venv venv

echo "==> Installing PyQt6, mido and pynput"
./venv/bin/pip install --upgrade pip >/dev/null
./venv/bin/pip install -r requirements.txt

cat > run.sh <<'LAUNCHER'
#!/usr/bin/env bash
cd "$(dirname "$0")"
exec ./venv/bin/python -m rpiano "$@"
LAUNCHER
chmod +x run.sh

echo "==> Creating a menu entry"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/roblox-piano.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Roblox Piano
Comment=Play MIDI files on a Roblox piano
Exec=$(pwd)/run.sh
Terminal=false
Categories=AudioVideo;Audio;Game;
DESKTOP

echo
echo "Done."
echo
echo "Next, give yourself access to /dev/uinput (one time):"
echo "    ./setup-uinput.sh"
echo
echo "Then start it with:"
echo "    ./run.sh"
