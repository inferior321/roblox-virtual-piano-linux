"""Managing the files in the library, not only listing them.

The parts that are about paths and names rather than about widgets, kept here
so they can be reasoned about - and tested - without a display. What is left in
the window is the wiring: which row you clicked, and the one Qt call that puts
a file in the Trash.

Nothing in here deletes anything. Copying and renaming are here because they
have a right answer that is worth pinning down; deleting has to go through Qt
to reach the Trash, so it stays where Qt already is.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

MIDI_SUFFIXES = (".mid", ".midi")

# Characters a file name cannot carry. The separator is the one that matters -
# a name with a slash in it is a path, and would quietly write somewhere else.
FORBIDDEN = set('/\\\0')


def is_midi(path) -> bool:
    return Path(path).suffix.lower() in MIDI_SUFFIXES


def split_midi(paths) -> tuple:
    """(the MIDI files, everything else), keeping the order given."""
    wanted, rest = [], []
    for item in paths:
        (wanted if is_midi(item) else rest).append(Path(item))
    return wanted, rest


def unique_name(folder: Path, name: str) -> Path:
    """A path in `folder` for `name` that treads on nothing already there.

    A file manager's answer to a name collision, and the reason it is the right
    one: overwriting loses a file you did not ask to lose, and asking turns
    dragging twenty songs into twenty questions.
    """
    target = Path(folder) / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    attempt = 2
    candidate = Path(folder) / f"{stem} (copy){suffix}"
    while candidate.exists():
        candidate = Path(folder) / f"{stem} (copy {attempt}){suffix}"
        attempt += 1
    return candidate


def copy_into(sources, folder) -> tuple:
    """Copy files into a folder. Returns (what landed, what failed and why).

    Each name is settled against the folder as it is at that moment, so copying
    two files of the same name in one go gives two files rather than one.
    """
    folder = Path(folder)
    copied, failed = [], []
    for source in sources:
        source = Path(source)
        try:
            # Dropping a file into the folder it already lives in lands on its
            # own name, so unique_name turns it into a copy beside it - which
            # is the only thing that could have been meant.
            target = unique_name(folder, source.name)
            shutil.copy2(source, target)
            copied.append(target)
        except OSError as exc:
            failed.append((source, exc.strerror or str(exc)))
    return copied, failed


def rename_target(path, typed: str) -> tuple:
    """Where renaming `path` to `typed` would put it, or (None, why not).

    The extension is kept whatever is typed: these are the files the library
    lists, and a song renamed to something the pane filters out would simply
    vanish, which reads as having deleted it.
    """
    path = Path(path)
    wanted = (typed or "").strip()
    if not wanted:
        return None, "A name cannot be empty."
    if FORBIDDEN & set(wanted):
        return None, "A name cannot contain \\ or /."
    if wanted in (".", ".."):
        return None, "That is not a name."
    if Path(wanted).suffix.lower() not in MIDI_SUFFIXES:
        wanted += path.suffix
    target = path.with_name(wanted)
    if target == path:
        return None, ""          # nothing typed that was not already there
    if target.exists():
        return None, f"There is already a {target.name} in that folder."
    return target, ""


def trashed(result) -> bool:
    """Did a QFile.moveToTrash call actually work?

    Qt returns the answer and writes where the file went into an out-parameter,
    and PyQt hands both back as a (worked, where it went) pair rather than a
    plain bool. A two-item tuple is always truthy, so reading the call as a
    bool makes a failure read as a success - and on a drive with no Trash to
    move anything to, which is exactly when it fails, the file is reported gone
    and is still sitting there. Hence a function, and hence a test.
    """
    if isinstance(result, tuple):
        return bool(result[0]) if result else False
    return bool(result)


def parse_clipboard(payload: str) -> tuple:
    """(action, files) out of what a file manager leaves on the clipboard.

    Nemo, and every other GNOME-descended file manager, says whether you copied
    or cut in a clipboard entry of its own - `x-special/gnome-copied-files`,
    holding "copy" or "cut" and then the URIs. The plain URI list beside it
    says nothing about which. Reading only that would turn a cut into a copy
    and leave the original sitting where it was, which is not what was asked
    for and is worth the few lines it takes to get right.
    """
    lines = [line for line in (payload or "").splitlines() if line.strip()]
    if not lines:
        return "", []
    action = lines[0].strip().lower()
    if action not in ("copy", "cut"):
        return "", uris_to_paths(lines)
    return action, uris_to_paths(lines[1:])


def uris_to_paths(uris) -> list:
    """Local files out of a URI list, ignoring anything not on this machine."""
    out = []
    for uri in uris:
        text = uri.strip()
        if not text.startswith("file://"):
            continue
        parsed = urlparse(text)
        if parsed.netloc not in ("", "localhost"):
            continue
        out.append(Path(unquote(parsed.path)))
    return out
