"""Ways of getting a keypress into Roblox.

uinput is the one that matters. It opens /dev/uinput and registers a virtual
keyboard directly with the kernel, so the events arrive indistinguishable from
a real USB keyboard. That's the point: Sober runs the Android build of Roblox,
and the Android client is known to ignore synthetic input that isn't backed by
a registered keyboard device.

It's written against the raw ioctl interface with fcntl and struct rather than
the python-evdev package, so there is nothing to compile and no system package
to install - the venv stays disposable.

xdotool is the fallback. It's X11-only, it shells out once per event so the
timing is noticeably worse, and Roblox may ignore it entirely. Useful mainly
for checking that the rest of the program works.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import struct
import subprocess
import time

from .keycodes import (
    EV_KEY,
    EV_SYN,
    MOD_TO_KEYSYM,
    MOD_TO_SCANCODE,
    SYN_REPORT,
    all_scancodes,
    keysym_for,
    scancode_for,
)

UINPUT_PATH = "/dev/uinput"

# ioctl request number encoding, from asm-generic/ioctl.h
_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1


def _IO(type_: int, nr: int) -> int:
    return (type_ << _IOC_TYPESHIFT) | (nr << _IOC_NRSHIFT)


def _IOW(type_: int, nr: int, size: int) -> int:
    return (
        (_IOC_WRITE << _IOC_DIRSHIFT)
        | (type_ << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


UINPUT_IOCTL_BASE = ord("U")
UI_DEV_CREATE = _IO(UINPUT_IOCTL_BASE, 1)
UI_DEV_DESTROY = _IO(UINPUT_IOCTL_BASE, 2)
UI_SET_EVBIT = _IOW(UINPUT_IOCTL_BASE, 100, 4)
UI_SET_KEYBIT = _IOW(UINPUT_IOCTL_BASE, 101, 4)

BUS_USB = 0x03
UINPUT_MAX_NAME_SIZE = 80
ABS_CNT = 64

# struct input_event on 64-bit: two longs of timeval, then type, code, value
INPUT_EVENT = struct.Struct("<qqHHi")


class BackendError(RuntimeError):
    pass


class Backend:
    """Interface the player talks to.

    Key and modifier presses are separate calls on purpose. Roblox reads the
    shift state by polling when it handles a key-down, not from the event
    itself, so the player has to hold a modifier across a frame boundary
    rather than pulsing it. That timing is the player's job, which means the
    backend must expose the pieces rather than one atomic "press shift+t".
    """

    name = "none"
    description = ""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def key_down(self, char: str) -> None: ...

    def key_up(self, char: str) -> None: ...

    def mods_down(self, mods) -> None: ...

    def mods_up(self, mods) -> None: ...

    def release_all(self) -> None: ...

    # Convenience for the test path; the player never uses these.
    def press(self, char: str, mods=()) -> None:
        if mods:
            self.mods_down(mods)
        self.key_down(char)
        if mods:
            self.mods_up(mods)

    def release(self, char: str, mods=()) -> None:
        self.key_up(char)


class UinputBackend(Backend):
    """A virtual keyboard registered with the kernel."""

    name = "uinput"
    description = "Kernel-level virtual keyboard. Works under Sober, X11 and Wayland."

    def __init__(self, device_name: str = "Roblox Piano Virtual Keyboard"):
        self.device_name = device_name
        self._fd = None
        self._down = set()
        self._mods_down = set()

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def availability() -> tuple:
        """(ok, message) describing whether this backend can be used."""
        if not os.path.exists(UINPUT_PATH):
            return False, (
                "/dev/uinput does not exist. Load the module with "
                "'sudo modprobe uinput'."
            )
        if not os.access(UINPUT_PATH, os.W_OK):
            return False, (
                "No write permission on /dev/uinput. Run setup-uinput.sh once, "
                "then log out and back in."
            )
        return True, "Ready."

    def open(self) -> None:
        if self._fd is not None:
            return
        ok, message = self.availability()
        if not ok:
            raise BackendError(message)
        try:
            fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise BackendError(f"Could not open /dev/uinput: {exc}") from exc

        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            for code in all_scancodes():
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)

            name = self.device_name.encode()[: UINPUT_MAX_NAME_SIZE - 1]
            payload = struct.pack(
                "<%dsHHHHI%di" % (UINPUT_MAX_NAME_SIZE, ABS_CNT * 4),
                name,
                BUS_USB,
                0x1234,   # vendor
                0x5678,   # product
                1,        # version
                0,        # ff_effects_max
                *([0] * (ABS_CNT * 4)),
            )
            os.write(fd, payload)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError as exc:
            os.close(fd)
            raise BackendError(f"Could not create the virtual keyboard: {exc}") from exc

        self._fd = fd
        # Give udev a moment to notice the new device before the first keypress.
        time.sleep(0.25)

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            self.release_all()
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    # -- emitting ----------------------------------------------------------

    def _emit(self, type_: int, code: int, value: int) -> None:
        if self._fd is None:
            raise BackendError("Backend is not open.")
        os.write(self._fd, INPUT_EVENT.pack(0, 0, type_, code, value))

    def _sync(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    def key_down(self, char: str) -> None:
        code = scancode_for(char)
        self._emit(EV_KEY, code, 1)
        self._sync()
        self._down.add(code)

    def key_up(self, char: str) -> None:
        code = scancode_for(char)
        self._emit(EV_KEY, code, 0)
        self._sync()
        self._down.discard(code)

    def mods_down(self, mods) -> None:
        codes = [MOD_TO_SCANCODE[m] for m in mods if m in MOD_TO_SCANCODE]
        for code in codes:
            self._emit(EV_KEY, code, 1)
            self._mods_down.add(code)
        if codes:
            self._sync()

    def mods_up(self, mods) -> None:
        codes = [MOD_TO_SCANCODE[m] for m in mods if m in MOD_TO_SCANCODE]
        for code in reversed(codes):
            self._emit(EV_KEY, code, 0)
            self._mods_down.discard(code)
        if codes:
            self._sync()

    def release_all(self) -> None:
        if self._fd is None:
            return
        for code in list(self._down) + list(MOD_TO_SCANCODE.values()):
            try:
                self._emit(EV_KEY, code, 0)
            except (OSError, BackendError):
                pass
        try:
            self._sync()
        except (OSError, BackendError):
            pass
        self._down.clear()
        self._mods_down.clear()


class XdotoolBackend(Backend):
    """Fallback that shells out to xdotool. X11 only, and slower."""

    name = "xdotool"
    description = "X11 only. Spawns a process per event, so timing is loose."

    def __init__(self):
        self._down = set()
        self._mods_down = set()

    @staticmethod
    def availability() -> tuple:
        if shutil.which("xdotool") is None:
            return False, "xdotool is not installed. sudo apt install xdotool"
        if not os.environ.get("DISPLAY"):
            return False, "No DISPLAY set, so this is not an X11 session."
        return True, "Ready."

    def open(self) -> None:
        ok, message = self.availability()
        if not ok:
            raise BackendError(message)

    def close(self) -> None:
        self.release_all()

    def _run(self, action: str, spec: str) -> None:
        subprocess.run(
            ["xdotool", action, spec],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def key_down(self, char: str) -> None:
        spec = keysym_for(char)
        self._run("keydown", spec)
        self._down.add(spec)

    def key_up(self, char: str) -> None:
        spec = keysym_for(char)
        self._run("keyup", spec)
        self._down.discard(spec)

    def mods_down(self, mods) -> None:
        for mod in mods:
            spec = MOD_TO_KEYSYM.get(mod)
            if spec:
                self._run("keydown", spec)
                self._mods_down.add(spec)

    def mods_up(self, mods) -> None:
        for mod in reversed(list(mods)):
            spec = MOD_TO_KEYSYM.get(mod)
            if spec:
                self._run("keyup", spec)
                self._mods_down.discard(spec)

    def release_all(self) -> None:
        for spec in list(self._down) + list(self._mods_down):
            self._run("keyup", spec)
        self._down.clear()
        self._mods_down.clear()


class NullBackend(Backend):
    """Logs instead of typing. Used by the tests and the dry-run toggle."""

    name = "dry run"
    description = "Sends nothing. Use it to check timing and mapping safely."

    def __init__(self):
        self.log = []

    def key_down(self, char: str) -> None:
        self.log.append(("down", char, ()))

    def key_up(self, char: str) -> None:
        self.log.append(("up", char, ()))

    def mods_down(self, mods) -> None:
        if mods:
            self.log.append(("mod_down", None, tuple(mods)))

    def mods_up(self, mods) -> None:
        if mods:
            self.log.append(("mod_up", None, tuple(mods)))

    def release_all(self) -> None:
        self.log.append(("allup", None, ()))


BACKENDS = {
    "uinput": UinputBackend,
    "xdotool": XdotoolBackend,
    "dry run": NullBackend,
}


def make_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise BackendError(f"Unknown backend {name!r}") from None
