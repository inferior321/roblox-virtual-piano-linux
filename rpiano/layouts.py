"""Mappings from MIDI note numbers to keystrokes.

A layout is just a dict of {midi_note: KeyStroke}. Layouts are generated from
the patterns below rather than typed out by hand, then serialised to JSON so
you can edit any individual note in the mapping editor without touching code.

MIDI note numbers follow the usual convention: 60 is middle C (C4), 21 is the
bottom A of an 88-key piano, 108 is the top C.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Pitch classes that have a black key immediately above them.
HAS_SHARP_ABOVE = (0, 2, 5, 7, 9)

# The standard virtualpiano.net white-key row, bottom to top. Almost every
# Roblox piano uses this, with shift on the same key for the black note above.
WHITE_KEY_ROW = "1234567890qwertyuiopasdfghjklzxcvbnm"

BASE_LOW = 36   # C2, the bottom of the 61-key range
BASE_HIGH = 96  # C7, the top


def note_name(note: int) -> str:
    """'C4' for 60."""
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


@dataclass(frozen=True)
class KeyStroke:
    """One physical key plus whatever modifiers are held while it's struck."""

    char: str
    mods: tuple = ()

    def label(self) -> str:
        if not self.mods:
            return self.char
        return "+".join(list(self.mods) + [self.char])

    def to_json(self) -> dict:
        return {"char": self.char, "mods": list(self.mods)}

    @staticmethod
    def from_json(d: dict) -> "KeyStroke":
        return KeyStroke(d["char"], tuple(d.get("mods", ())))


@dataclass
class Layout:
    ident: str
    name: str
    notes: dict = field(default_factory=dict)
    verified: bool = False
    note_text: str = ""

    # Cached range. A layout's notes are set once at construction everywhere in
    # this program - the mapping editor builds a new Layout rather than mutating
    # one - so this only needs recomputing if something edits notes in place.
    _low: int = field(default=None, repr=False, compare=False)
    _high: int = field(default=None, repr=False, compare=False)

    def refresh(self) -> None:
        """Recompute the cached range. Call after editing notes in place."""
        self._low = min(self.notes) if self.notes else 0
        self._high = max(self.notes) if self.notes else 0

    @property
    def low(self) -> int:
        if self._low is None:
            self.refresh()
        return self._low

    @property
    def high(self) -> int:
        if self._high is None:
            self.refresh()
        return self._high

    def get(self, note: int):
        return self.notes.get(note)

    def fold_into_range(self, note: int):
        """Shift an out-of-range note by whole octaves until it fits.

        Returns None if even that fails, which can only happen for a layout
        spanning less than an octave.
        """
        notes = self.notes
        if note in notes:
            return note
        low, high = self.low, self.high
        if note < low:
            note += 12 * ((low - note + 11) // 12)
        elif note > high:
            note -= 12 * ((note - high + 11) // 12)
        return note if note in notes else None

    def to_json(self) -> dict:
        return {
            "ident": self.ident,
            "name": self.name,
            "verified": self.verified,
            "note_text": self.note_text,
            "notes": {str(n): k.to_json() for n, k in sorted(self.notes.items())},
        }

    @staticmethod
    def from_json(d: dict) -> "Layout":
        return Layout(
            ident=d["ident"],
            name=d["name"],
            verified=d.get("verified", False),
            note_text=d.get("note_text", ""),
            notes={int(n): KeyStroke.from_json(k) for n, k in d["notes"].items()},
        )

    def copy_as(self, ident: str, name: str) -> "Layout":
        return Layout(ident=ident, name=name, notes=dict(self.notes),
                      verified=False, note_text=self.note_text)


def build_61() -> Layout:
    """The standard 61-key Roblox / virtualpiano layout, C2 to C7.

    White keys walk the row 1234567890qwerty... upward; each black key is
    shift plus the white key directly below it.
    """
    notes: dict = {}
    n = BASE_LOW
    for char in WHITE_KEY_ROW:
        if n > BASE_HIGH:
            break
        notes[n] = KeyStroke(char)
        pc = n % 12
        if pc in HAS_SHARP_ABOVE and n + 1 <= BASE_HIGH:
            notes[n + 1] = KeyStroke(char, ("shift",))
        n += 2 if pc in HAS_SHARP_ABOVE else 1
    return Layout(
        ident="roblox_61",
        name="Roblox 61-key (standard virtual piano)",
        notes=notes,
        verified=True,
        note_text="The layout nearly every Roblox piano uses. C2-C7, shift for black keys.",
    )


def build_88() -> Layout:
    """88-key layout: the 61-key range untouched, extended with ctrl.

    The 27 notes outside C2-C7 walk the same row again with ctrl held, one key
    per semitone: A0 is ctrl+1, A#0 ctrl+2, on through ctrl+t at B1, then
    resuming at ctrl+y for C#7 and finishing at ctrl+j for C8. Black keys out
    there take their own key rather than shift plus a neighbour, so ctrl+shift
    never arises.

    This is not a scheme derived from first principles - it is the mapping a
    working MIDI++ config used, checked note for note. An earlier version put
    the outer octaves on ctrl plus the key two octaves inward, which is tidy
    reasoning and produced entirely different keys for all 27 notes. The middle
    five octaves agreed exactly, so the disagreement was confined to precisely
    the range nobody had ever confirmed against a real piano.
    """
    notes = dict(build_61().notes)
    outer = list(range(21, BASE_LOW)) + list(range(BASE_HIGH + 1, 109))
    for index, note in enumerate(outer):
        notes[note] = KeyStroke(WHITE_KEY_ROW[index], ("ctrl",))
    return Layout(
        ident="roblox_88",
        name="Roblox 88-key (Piano Rooms and similar)",
        notes=notes,
        verified=True,
        note_text="The layout Piano Rooms uses. A0-C8, shift for black keys, ctrl for the outer octaves.",
    )


def builtin_layouts() -> dict:
    layouts = {}
    for layout in (build_61(), build_88()):
        layouts[layout.ident] = layout
    return layouts


def load_custom_layouts(directory: Path) -> dict:
    layouts = {}
    if not directory.is_dir():
        return layouts
    for path in sorted(directory.glob("*.json")):
        try:
            layout = Layout.from_json(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        layouts[layout.ident] = layout
    return layouts


def save_custom_layout(directory: Path, layout: Layout) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{layout.ident}.json"
    path.write_text(json.dumps(layout.to_json(), indent=2))
    return path


def import_midiplusplus_config(path: Path) -> Layout:
    """Best-effort import of a MIDI++ config.json key map.

    MIDI++ stores its mapping as a note -> key-string table. The exact shape has
    changed between versions, so this looks for the first dict whose keys parse
    as MIDI note numbers and whose values are strings, then interprets each
    string the way MIDI++ does: an uppercase letter or a shifted punctuation
    character means shift is held.
    """
    data = json.loads(Path(path).read_text())
    table = _find_note_table(data)
    if table is None:
        raise ValueError("No note-to-key table found in that file.")

    notes = {}
    for raw_note, raw_key in table.items():
        note = int(raw_note)
        stroke = _parse_key_string(str(raw_key))
        if stroke is not None:
            notes[note] = stroke
    if not notes:
        raise ValueError("The note table was empty or unreadable.")
    return Layout(
        ident="imported_midipp",
        name="Imported from MIDI++ config",
        notes=notes,
        verified=False,
        note_text=f"Imported from {Path(path).name}.",
    )


SHIFTED_PUNCTUATION = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", ":": ";",
    '"': "'", "~": "`", "|": "\\", "<": ",", ">": ".", "?": "/",
}


def _parse_key_string(raw: str):
    """Turn "T", "!", or "ctrl+t" into a KeyStroke."""
    raw = raw.strip()
    if not raw:
        return None
    mods = []
    while "+" in raw and len(raw) > 1:
        head, rest = raw.split("+", 1)
        head = head.strip().lower()
        if head in ("shift", "ctrl", "control", "alt"):
            mods.append("ctrl" if head == "control" else head)
            raw = rest.strip()
        else:
            break
    if len(raw) != 1:
        return None
    if raw in SHIFTED_PUNCTUATION:
        return KeyStroke(SHIFTED_PUNCTUATION[raw], tuple(dict.fromkeys(mods + ["shift"])))
    if raw.isalpha() and raw.isupper():
        return KeyStroke(raw.lower(), tuple(dict.fromkeys(mods + ["shift"])))
    return KeyStroke(raw.lower(), tuple(mods))


def _find_note_table(data):
    if isinstance(data, dict):
        numeric = [k for k in data if str(k).lstrip("-").isdigit()]
        if len(numeric) >= 24 and all(isinstance(data[k], str) for k in numeric):
            return {k: data[k] for k in numeric}
        for value in data.values():
            found = _find_note_table(value)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_note_table(value)
            if found is not None:
                return found
    return None
