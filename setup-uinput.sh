#!/usr/bin/env bash
# Grants your user permission to create virtual input devices.
#
# This is the one thing that touches the system outside this folder. It writes
# a udev rule and adds you to the "input" group. Undo instructions are at the
# bottom of the README.

set -euo pipefail

RULE=/etc/udev/rules.d/99-uinput-roblox-piano.rules

echo "==> Loading the uinput kernel module"
sudo modprobe uinput

echo "==> Making it load at boot"
echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null

echo "==> Writing $RULE"
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee "$RULE" >/dev/null

echo "==> Adding $USER to the input group"
sudo usermod -aG input "$USER"

echo "==> Reloading udev"
sudo udevadm control --reload-rules
sudo udevadm trigger

echo
if [ -w /dev/uinput ]; then
    echo "/dev/uinput is writable. You're ready to go."
else
    echo "Set up, but the new group membership isn't active in this session yet."
    echo "Log out and back in, then run ./run.sh"
fi
