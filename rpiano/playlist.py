"""The order songs are to be played in.

A list of paths and the four things you can do to it. None of this touches a
file: a playlist is an order, not a place, and the same song may legitimately
appear in it twice.

Kept apart from the window so the awkward parts - moving a scattered selection
without it folding in on itself, and following a song that has been renamed or
moved on disk - can be reasoned about and tested with no display anywhere near.
"""

from __future__ import annotations

from pathlib import Path


def move_up(items, positions) -> tuple:
    """Shift the chosen rows one place towards the top.

    Returns (the new list, where the chosen rows ended up).

    A selection that reaches the top stops there rather than folding over
    itself: rows 0, 1 and 4 moved up give 0, 1 and 3, not a scramble. That is
    what the running count is for - it is how many are already stacked against
    the ceiling and cannot go further.
    """
    items = list(items)
    landed = []
    stacked = 0
    for position in sorted(set(positions)):
        if not 0 <= position < len(items):
            continue
        if position == stacked:
            stacked += 1
            landed.append(position)
            continue
        items[position - 1], items[position] = items[position], items[position - 1]
        landed.append(position - 1)
    return items, landed


def move_down(items, positions) -> tuple:
    """Shift the chosen rows one place towards the bottom."""
    items = list(items)
    landed = []
    stacked = len(items) - 1
    for position in sorted(set(positions), reverse=True):
        if not 0 <= position < len(items):
            continue
        if position == stacked:
            stacked -= 1
            landed.append(position)
            continue
        items[position + 1], items[position] = items[position], items[position + 1]
        landed.append(position + 1)
    return items, landed


def remove_at(items, positions) -> list:
    """Everything except the chosen rows."""
    drop = set(positions)
    return [item for index, item in enumerate(items) if index not in drop]


def relocate(items, was, now) -> list:
    """Follow a song, or a whole folder, that has moved or gone.

    `was` is a file or a folder; `now` is where it is instead, or None if it
    has been deleted. Entries below a folder come along with it, so renaming a
    folder does not quietly empty the playlist of everything inside it.
    """
    was = Path(was)
    out = []
    for item in items:
        path = Path(item)
        if path == was:
            if now is not None:
                out.append(Path(now))
            continue
        if was in path.parents:
            if now is None:
                continue
            out.append(Path(now) / path.relative_to(was))
            continue
        out.append(path)
    return out


def next_index(position: int, total: int, looping: bool):
    """Where the queue goes after the song at `position`, or None to stop."""
    if total <= 0:
        return None
    if position + 1 < total:
        return position + 1
    return 0 if looping else None
