"""Which window the keys are actually landing in.

uinput is a keyboard, not a message to a window. What it types goes wherever
the focus happens to be, exactly as a physical keyboard does, and there is no
way to address a window with it - the one X11 call that can reach an unfocused
window marks its events as synthetic, which games and browsers ignore on
purpose. So there is no honest version of "send the keys to that window"; what
there is, is watching what has the focus and stopping when it stops being the
window the song was meant for.

Reading the focused window costs nothing here: python-xlib is already installed
because pynput needs it for the global hotkeys, and the answer is one property
on the root window. It is cached for a moment because the player asks often.
"""

from __future__ import annotations

import os
import threading
import time

# Long enough that asking on every batch costs nothing, short enough that a
# song caught typing into the wrong window stops within a note or two.
CACHE_SECONDS = 0.05


def availability() -> tuple:
    """(usable, why not) for reading the focused window on this machine."""
    try:
        import Xlib.display  # noqa: F401
    except Exception:
        return False, "python-xlib is missing, so the focused window cannot be read."
    if not os.environ.get("DISPLAY"):
        return False, "This needs X11. Under Wayland no program may ask what has the focus."
    return True, ""


class Focus:
    """The focused window, asked for as often as you like.

    Its own connection to the display, guarded by a lock: the player thread
    asks before it plays anything and the window asks on a timer, and Xlib
    connections are not for sharing between threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._display = None
        self._atom = None
        self._pid_atom = None
        self._asked = 0.0
        self._answer = None
        self._pointer_for = None
        self._pointer_asked = 0.0
        self._pointer_answer = None

    def _connect(self):
        if self._display is None:
            import Xlib.display

            self._display = Xlib.display.Display()
            self._atom = self._display.intern_atom("_NET_ACTIVE_WINDOW")
            self._pid_atom = self._display.intern_atom("_NET_WM_PID")
        return self._display

    def active(self) -> tuple:
        """(window id, its name, the pid behind it), or None if unknowable."""
        now = time.perf_counter()
        with self._lock:
            if now - self._asked < CACHE_SECONDS:
                return self._answer
            self._asked = now
            self._answer = self._read()
            return self._answer

    def _read(self) -> tuple:
        try:
            display = self._connect()
            root = display.screen().root
            prop = root.get_full_property(self._atom, 0)
            if not prop or not prop.value:
                return None
            window_id = int(prop.value[0])
            if not window_id:
                return None
            window = display.create_resource_object("window", window_id)
            name = window.get_wm_name() or ""
            if not name:
                found = window.get_wm_class()
                name = found[1] if found and len(found) > 1 else ""
            owner = window.get_full_property(self._pid_atom, 0)
            pid = int(owner.value[0]) if owner and owner.value else 0
            return window_id, str(name), pid
        except Exception:
            # A display that has gone away, or a window that vanished between
            # being named and being asked about. Drop the connection so the
            # next call builds a fresh one rather than failing for ever.
            self._display = None
            return None

    def pointer_inside(self, window_id: int):
        """Is the mouse within that window? True, False, or None if unknowable.

        A game can treat the pointer leaving its edges as losing the focus and
        stop reading the keyboard, while the window manager still says it is
        the active window - so the focus alone does not answer whether a song
        is landing anywhere.

        One rectangle test in root coordinates, which is all a second monitor
        is: X gives every screen one coordinate space, so a window on the
        monitor to the right is simply a window at x=3840 and there is nothing
        per-monitor to work out.
        """
        now = time.perf_counter()
        with self._lock:
            if (window_id == self._pointer_for
                    and now - self._pointer_asked < CACHE_SECONDS):
                return self._pointer_answer
            self._pointer_for = window_id
            self._pointer_asked = now
            self._pointer_answer = self._read_pointer(window_id)
            return self._pointer_answer

    def _read_pointer(self, window_id: int):
        try:
            display = self._connect()
            root = display.screen().root
            pointer = root.query_pointer()
            if not pointer.same_screen:
                # A different X *screen*, which is not a second monitor - those
                # share this one. Nothing of ours is under that pointer.
                return False
            window = display.create_resource_object("window", window_id)
            size = window.get_geometry()
            # The destination is whichever window the call is made on, so the
            # root translates the window's own (0, 0) into root coordinates.
            # Asking the window instead returns the negative of it, which looks
            # plausible and puts the rectangle in the wrong place.
            origin = root.translate_coords(window, 0, 0)
            return (
                origin.x <= pointer.root_x < origin.x + size.width
                and origin.y <= pointer.root_y < origin.y + size.height
            )
        except Exception:
            self._display = None
            return None

    def is_ours(self, found) -> bool:
        """Is that window this program's own?

        By process rather than by window id, so a dialog or a menu of ours
        counts as ours too - the point is whether the song would be typing
        into itself, not which of its windows it would be typing into.
        """
        return bool(found) and found[2] == os.getpid()
