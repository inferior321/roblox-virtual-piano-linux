"""Keyboard code tables.

Two backends need two different names for the same physical key:

  * the uinput backend needs the kernel scancode from input-event-codes.h
  * the xdotool backend needs an X11 keysym name

Everything in this project refers to a physical key by its *unshifted US QWERTY
character* ("t", "5") plus a list of modifiers (["shift"]). These tables convert
that into whatever the backend wants.
"""

# --- Kernel scancodes (linux/input-event-codes.h) --------------------------

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00

KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56

CHAR_TO_SCANCODE = {
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
    "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "=": 13,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
    "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "[": 26, "]": 27,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34,
    "h": 35, "j": 36, "k": 37, "l": 38,
    ";": 39, "'": 40, "`": 41, "\\": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48,
    "n": 49, "m": 50,
    ",": 51, ".": 52, "/": 53,
    " ": 57,
}

MOD_TO_SCANCODE = {
    "shift": KEY_LEFTSHIFT,
    "ctrl": KEY_LEFTCTRL,
    "alt": KEY_LEFTALT,
}

# --- X11 keysym names (for xdotool) ---------------------------------------

CHAR_TO_KEYSYM = {
    "-": "minus", "=": "equal",
    "[": "bracketleft", "]": "bracketright",
    ";": "semicolon", "'": "apostrophe", "`": "grave",
    "\\": "backslash", ",": "comma", ".": "period", "/": "slash",
    " ": "space",
}

MOD_TO_KEYSYM = {
    "shift": "shift",
    "ctrl": "ctrl",
    "alt": "alt",
}


def keysym_for(char: str) -> str:
    """X11 keysym name for an unshifted QWERTY character."""
    return CHAR_TO_KEYSYM.get(char, char)


def scancode_for(char: str) -> int:
    """Kernel scancode for an unshifted QWERTY character."""
    try:
        return CHAR_TO_SCANCODE[char]
    except KeyError:
        raise KeyError(f"No scancode known for key {char!r}") from None


def all_scancodes() -> list:
    """Every scancode this program might ever emit.

    The uinput device has to declare its full key repertoire up front, so this
    is what gets registered at device creation time.
    """
    return sorted(set(CHAR_TO_SCANCODE.values()) | set(MOD_TO_SCANCODE.values()))
