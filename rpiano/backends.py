"""Ways of getting a keypress into Roblox.

uinput is the one that matters. It opens /dev/uinput and registers a virtual
keyboard directly with the kernel, so the events arrive indistinguishable from
a real USB keyboard. That's the point: Sober runs the Android build of Roblox,
and the Android client is known to ignore synthetic input that isn't backed by
a registered keyboard device.

It's written against the raw ioctl interface with fcntl and struct rather than
the python-evdev package, so there is nothing to compile and no system package
to install - the venv stays disposable.

The other three send nothing to the system: the dry run logs what it would have
typed, and the audio preview reads the keystrokes back through the layout and
plays them, so a mapping can be heard without the game.
"""

from __future__ import annotations

import fcntl
import functools
import os
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

from .keycodes import (
    EV_KEY,
    EV_SYN,
    MOD_TO_SCANCODE,
    SYN_REPORT,
    all_scancodes,
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


SOUNDFONT_MAGIC = b"RIFF"


def _locked(method):
    """Serialise a call that touches the synth.

    FluidSynth is a C library. Deleting a synth on the GUI thread while the
    player thread is calling noteon on it is a use-after-free, and it takes the
    process down with a segfault rather than raising anything catchable - so
    reconfiguring from the interface has to be held apart from the notes.
    """

    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return guarded


def soundfont_available() -> tuple:
    """(ok, message) for whether audio preview can work at all."""
    try:
        import fluidsynth  # noqa: F401
    except Exception as exc:
        return False, f"pyfluidsynth is not usable ({exc}). Re-run install.sh."
    return True, "Ready."


def read_soundfont_presets(path) -> list:
    """Load a soundfont and return its presets as [(bank, program, name)].

    This is the full check: the file is handed to the synth, so anything that
    is missing, truncated or not a soundfont fails here rather than the first
    time a note is due. Slow enough on a large file that the caller caches the
    result - see AppConfig.soundfont_presets.
    """
    import fluidsynth

    synth = fluidsynth.Synth(samplerate=44100.0)
    try:
        sfid = synth.sfload(str(path))
        if sfid == -1:
            raise BackendError("FluidSynth could not read that file.")
        presets = []
        for bank in range(129):
            for program in range(128):
                try:
                    name = synth.sfpreset_name(sfid, bank, program)
                except Exception:
                    name = None
                if name:
                    presets.append((bank, program, name.strip()))
        if not presets:
            raise BackendError("That soundfont contains no instruments.")
        return presets
    finally:
        synth.delete()


class SoundfontBackend(Backend):
    """Turns the keystrokes into sound instead of sending them anywhere.

    The point is that it learns nothing from the MIDI file. It is handed the
    same key presses the game would receive and asks the layout the same
    question the game asks - which note is this key, with these modifiers held?
    - so a mapping that is wrong here is wrong in the game too. The dwell,
    retrigger gap and minimum-note floor are all audible, because they are
    still what decides when these calls arrive.

    FluidSynth renders on its own thread, so a note-on from the player thread
    only queues a voice. Nothing here touches an audio buffer.
    """

    name = "audio preview"
    description = "Plays the keystrokes through a soundfont instead of sending them."

    # FluidSynth defaults to 64-frame periods: a render deadline every
    # 1.45ms at 44.1kHz, which it misses on a desktop that denies it
    # realtime priority while the player thread busy-waits. A missed
    # deadline is an underrun, and an underrun is the buzzing.
    PERIOD_SIZE = 512
    PERIODS = 4

    def __init__(self, path: str = "", bank: int = 0, program: int = 0,
                 gain: float = 0.4):
        self.path = path
        self.bank = bank
        self.program = program
        self.gain = gain
        self._synth = None
        self._sfid = None
        self._lock = threading.RLock()   # see _locked
        self._reverse = {}          # (char, frozenset(mods)) -> note
        self._sustain_char = ""
        self._mods = set()
        self._by_key = {}           # char -> note currently sounding for it
        self._pedal = False
        self._sustained = set()     # notes held only by the pedal

    # -- configuration -----------------------------------------------------

    def configure(self, layout, sustain_key: str = "") -> None:
        """Teach it the mapping to read keys back through.

        Rebuilt whenever the layout or the pedal key changes, because that is
        exactly what changes the meaning of an incoming keystroke.
        """
        self._reverse = {
            (stroke.char, frozenset(stroke.mods)): note
            for note, stroke in layout.notes.items()
        }
        self._sustain_char = sustain_key or ""

    @_locked
    def set_gain(self, gain: float) -> None:
        self.gain = gain
        if self._synth is not None:
            self._synth.setting("synth.gain", gain)

    @_locked
    def set_soundfont(self, path, bank: int, program: int) -> None:
        """Point at a different soundfont, or a different instrument in it.

        A new file has to be reloaded, so any synth already holding the old one
        is torn down and the next open() reads the new one - open() otherwise
        short-circuits on an existing synth and would go on playing the file
        that was chosen before.

        Changing instrument within the same file is just a program change, and
        deliberately not a reload: rebuilding the synth to move from one piano
        to another would mean reading a large soundfont again for nothing.
        """
        path = str(path or "")
        if path != self.path:
            was_open = self._synth is not None
            self.path = path
            self.bank, self.program = bank, program
            self._teardown()
            if was_open:
                # A song is already playing. The player only opens a backend at
                # the start of one, so tearing the synth down and waiting for
                # the next open() would leave the rest of this song silent while
                # the keys carried on being pressed.
                self.open()
            return
        self.bank, self.program = bank, program
        if self._synth is not None and self._sfid is not None:
            self._synth.program_select(0, self._sfid, bank, program)

    def _teardown(self) -> None:
        if self._synth is not None:
            try:
                self._synth.delete()
            except Exception:
                pass
        self._synth = None
        self._sfid = None
        self._by_key.clear()
        self._sustained.clear()
        self._pedal = False

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def availability() -> tuple:
        return soundfont_available()

    @_locked
    def open(self) -> None:
        # Kept alive across playbacks: the player opens and closes a backend
        # around every song, and reloading a soundfont each time would be a
        # long pause before the first note.
        if self._synth is not None:
            return
        ok, message = soundfont_available()
        if not ok:
            raise BackendError(message)
        if not self.path or not Path(self.path).is_file():
            raise BackendError(
                "No soundfont loaded. Choose one in the Input tab first."
            )
        import fluidsynth

        synth = fluidsynth.Synth(samplerate=44100.0)
        synth.setting("synth.gain", self.gain)
        # FluidSynth defaults to 64-frame periods, which at 44.1kHz is a render
        # deadline every 1.45ms. It asks for realtime priority to meet that and
        # is refused on an ordinary desktop, while the player thread busy-waits
        # on its own schedule and holds the interpreter lock - so the deadline
        # gets missed, and a missed deadline is an underrun, which is audible as
        # buzzing. Bigger periods give it room. The added latency does not
        # matter for something you listen to rather than perform on.
        synth.setting("audio.period-size", self.PERIOD_SIZE)
        synth.setting("audio.periods", self.PERIODS)

        # Load before starting the driver. Started first, the driver is already
        # pulling audio while a soundfont is read - tens of milliseconds of
        # nothing to render, which is the burst of noise on a cold start and on
        # every soundfont change.
        sfid = synth.sfload(str(self.path))
        if sfid == -1:
            synth.delete()
            raise BackendError(f"Could not load {Path(self.path).name}.")
        synth.program_select(0, sfid, self.bank, self.program)
        try:
            synth.start()
        except Exception as exc:
            synth.delete()
            raise BackendError(f"No audio output available: {exc}") from exc
        self._synth, self._sfid = synth, sfid

    def sounding(self) -> list:
        """The notes whose keys are down right now.

        Keys rather than voices: a note still ringing under the pedal has had
        its key let go, and the keyboard strip is showing hands, not sound.
        """
        with self._lock:
            return sorted(self._by_key.values())

    def close(self) -> None:
        # Silence, but keep the synth: close runs at the end of every song.
        self.release_all()

    def __del__(self):
        if getattr(self, "_synth", None) is not None:
            try:
                self._synth.delete()
            except Exception:
                pass

    # -- the keys ----------------------------------------------------------

    @_locked
    def key_down(self, char: str) -> None:
        if self._synth is None:
            return
        if char and char == self._sustain_char:
            self._pedal = True
            return
        note = self._reverse.get((char, frozenset(self._mods)))
        if note is None:
            return
        # A key already sounding is being restruck: stop the old voice first,
        # the way a real key does when it returns.
        previous = self._by_key.get(char)
        if previous is not None:
            self._synth.noteoff(0, previous)
        self._synth.noteon(0, note, 100)
        self._by_key[char] = note
        self._sustained.discard(note)

    @_locked
    def key_up(self, char: str) -> None:
        if self._synth is None:
            return
        if char and char == self._sustain_char:
            self._pedal = False
            for note in self._sustained:
                self._synth.noteoff(0, note)
            self._sustained.clear()
            return
        note = self._by_key.pop(char, None)
        if note is None:
            return
        if self._pedal:
            # The pedal is down, so lifting the key does not damp the string.
            self._sustained.add(note)
        else:
            self._synth.noteoff(0, note)

    def mods_down(self, mods) -> None:
        self._mods.update(mods)

    def mods_up(self, mods) -> None:
        self._mods.difference_update(mods)

    @_locked
    def release_all(self) -> None:
        if self._synth is None:
            return
        for note in list(self._by_key.values()) + list(self._sustained):
            try:
                self._synth.noteoff(0, note)
            except Exception:
                pass
        self._by_key.clear()
        self._sustained.clear()
        self._mods.clear()
        self._pedal = False


class LivePlayBackend(SoundfontBackend):
    """The same synth, played by your fingers instead of by a file.

    Everything about the sound is the soundfont backend's - the same file, the
    same instrument, the same volume, and the same question asked of the layout
    for every key. The only difference is where the keystrokes come from, which
    is why this is a subclass and not a second implementation: a mapping heard
    here is the mapping, not an approximation of it.

    It is a backend so that it can sit in Send keys via, which is where you
    choose what the keys do. Nothing is ever sent to it by the player, though -
    the transport is switched off while it is chosen, because a song and a pair
    of hands playing the same piano at once is not what anybody meant.
    """

    name = "live play"
    description = "Play the piano yourself. Your keys sound here; nothing is sent."


BACKENDS = {
    "uinput": UinputBackend,
    "dry run": NullBackend,
    "audio preview": SoundfontBackend,
    "live play": LivePlayBackend,
}


def make_backend(name: str) -> Backend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise BackendError(f"Unknown backend {name!r}") from None
