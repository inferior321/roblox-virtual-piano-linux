"""Playing like a person rather than a machine.

A pass over the song's events, run once before playback. It returns a new list
of events, so everything downstream - transpose, folding, chord batching, the
minimum-note floor, voice counting - carries on working unchanged: it is just
given a different, still perfectly valid, performance to play.

Two kinds of thing happen here, and they are not the same kind of thing.

Looseness is continuous. It touches every note, as an amount: nobody lands
exactly on the beat, holds a note for exactly its written length, or puts every
finger of a chord down at the same instant. It has no probability, only a size.

Mistakes are discrete. They are rare events, and they need a probability. A
wrong key, a note that never sounds, a neighbouring key brushed on the way in,
a key struck twice. These get one dial - how often - and a choice of which
kinds are in play.

Two details are worth knowing about, because getting them wrong turns a mistake
into a bug:

A wrong key is a wrong key, not a wrong note. A finger slips to the key beside
the one it wanted, so the candidates come from the layout's own key row and not
from pitch arithmetic. That matters on the 88-key layout, where ctrl+t is B1 and
the key beside it is C#7 - adjacent to the hand, five and a half octaves away to
the ear. Both tests have to pass: next door on the keyboard, and close by in
pitch.

Nothing is decided while the music plays. The dice are rolled once, before the
first note, from a seed you can keep - so a run is repeatable, the mistakes can
be counted and shown before you commit to hearing them, and seeking back
through a passage finds the same slip in the same place, the way a player has
the same weak spot every time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .layouts import WHITE_KEY_ROW

# Everything here is capped at what a person could plausibly do. These are the
# outer edge of sloppy-but-human, not the outer edge of the arithmetic.
MAX_TIMING_MS = 40
MAX_LENGTH_MS = 60
MAX_ROLL_MS = 50
# Expressed as one note in N, so the sloppiest allowed is the smallest number.
BUSIEST_RATE = 25
QUIETEST_RATE = 1000

DRIFTS = ("steady", "rush", "drag")

# No two mistakes closer together than this. Independent rolls clump - three in
# one bar and none for the next half minute - and a clump reads as a broken
# program rather than a fallible player.
REFRACTORY = 0.30

# How far ahead of the note it wanted a brushed key lands. Held clear of the
# chord window, or the batcher gathers the two into a chord and the brush is
# not a brush any more. That is the only thing the window has to say about it -
# how long the key is then held is a question about a finger, and taking it
# from the window made a wide chord window lengthen the brush for no reason.
BRUSH_LEAD = 0.045

# And how long it is held: a share of the note it is brushing past, so it stays
# a blip whether the music is slow or quick, floored so it is not lost under
# the minimum-note setting and capped so it never grows into a note of its own.
BRUSH_SHARE = 0.15
BRUSH_MIN = 0.012
BRUSH_MAX = 0.040

# A key bouncing under the finger: brief contact, a gap, then the note proper.
# Cut a note down the middle instead and it stops being a bounce and becomes a
# note somebody played twice on purpose, which is a different mistake and a far
# less convincing one. The rest is what has to be left over to be worth hearing.
BOUNCE_MIN = 0.018
BOUNCE_MAX = 0.035
BOUNCE_REST = 0.045

# A slip has to be next door on the keyboard AND no further than this in pitch.
SLIP_SPAN = 4

# Which mistakes come up more often, given several are in play. A slip is the
# common one; striking a key twice is a rarity.
WEIGHTS = {"slip": 5, "miss": 3, "brush": 2, "double": 1}


@dataclass
class Options:
    """Everything the Humanizer tab sets."""

    enabled: bool = False

    # Looseness, in milliseconds. Sizes, not probabilities.
    timing_ms: int = 18
    length_ms: int = 12
    roll_ms: int = 20
    drift: str = "steady"

    # Mistakes: one in this many notes, and which kinds are allowed.
    rate: int = 150
    slip: bool = True
    miss: bool = True
    brush: bool = True
    double: bool = True

    repeatable: bool = True
    seed: int = 1

    def kinds(self) -> tuple:
        return tuple(
            kind
            for kind, on in (
                ("slip", self.slip),
                ("miss", self.miss),
                ("brush", self.brush),
                ("double", self.double),
            )
            if on
        )


@dataclass
class Report:
    """What the pass actually did, for the Log and for the tab's own subtitle."""

    slipped: int = 0
    missed: int = 0
    brushed: int = 0
    doubled: int = 0
    loosened: int = 0

    @property
    def mistakes(self) -> int:
        return self.slipped + self.missed + self.brushed + self.doubled

    def summary(self) -> str:
        if not self.mistakes:
            return "Humanizer: playing loose, no mistakes."
        parts = []
        for count, name in (
            (self.slipped, "wrong key"),
            (self.missed, "missed"),
            (self.brushed, "brushed"),
            (self.doubled, "struck twice"),
        ):
            if count:
                parts.append(f"{count} {name}")
        return f"Humanizer: {', '.join(parts)}."


def _remake(event, time_: float, note: int):
    """A copy of an event at a new time, or playing a new note.

    dataclasses.replace() does this too, and does it by introspecting the
    fields on every call - which on a large arrangement was most of the cost of
    the whole pass. Building one directly is the same thing without the
    lookups, and an event that has not actually moved is handed back as it is.
    """
    if time_ == event.time and note == event.note:
        return event
    return type(event)(
        time=time_,
        on=event.on,
        note=note,
        velocity=event.velocity,
        track=event.track,
        channel=event.channel,
    )


def _flip(event):
    """The same event the other way round: a press becomes a release."""
    return type(event)(
        time=event.time,
        on=not event.on,
        note=event.note,
        velocity=event.velocity,
        track=event.track,
        channel=event.channel,
    )


def _reverse(layout) -> dict:
    """Keystroke back to the note it plays, for finding a key's neighbours."""
    return {
        (stroke.char, tuple(stroke.mods)): note
        for note, stroke in layout.notes.items()
    }


def neighbours(layout, note: int, span: int = SLIP_SPAN) -> list:
    """Notes a finger could plausibly hit instead of this one.

    Next door in the key row, under the same modifiers, and close enough in
    pitch to sound like a slip rather than a jump. A custom mapping using keys
    outside the standard row simply has no neighbours, and no slips.
    """
    stroke = layout.notes.get(note)
    if stroke is None:
        return []
    index = WHITE_KEY_ROW.find(stroke.char)
    if index < 0:
        return []
    mods = tuple(stroke.mods)
    reverse = _reverse(layout)
    found = []
    for step in (-1, 1):
        position = index + step
        if not 0 <= position < len(WHITE_KEY_ROW):
            continue
        other = reverse.get((WHITE_KEY_ROW[position], mods))
        if other is not None and other != note and abs(other - note) <= span:
            found.append(other)
    return found


def _pairs(events) -> dict:
    """Each note-on's index mapped to the index of the note-off that ends it.

    Paired oldest-first per pitch and part, which is how the player's own voice
    counting reads them. An unpaired note-on keeps whatever end it had.
    """
    open_notes = {}
    pairs = {}
    for index, event in enumerate(events):
        key = (event.note, event.track, event.channel)
        if event.on:
            open_notes.setdefault(key, []).append(index)
        else:
            waiting = open_notes.get(key)
            if waiting:
                pairs[waiting.pop(0)] = index
    return pairs


def _batches(events, window: float) -> list:
    """Note-on indices grouped the way the player will group them into chords."""
    groups = []
    current = []
    anchor = None
    for index, event in enumerate(events):
        if not event.on:
            continue
        if anchor is None or event.time - anchor > window:
            if current:
                groups.append(current)
            current = [index]
            anchor = event.time
        else:
            current.append(index)
    if current:
        groups.append(current)
    return groups


def _spread(rng, amount: float, bias: float) -> float:
    """A wobble of up to `amount` either side of nothing, leaning by `bias`."""
    if amount <= 0:
        return 0.0
    value = rng.gauss(bias * amount, amount / 2)
    return max(-amount, min(amount, value))


def humanize(
    events,
    layout,
    options: Options,
    *,
    batch_window_ms: int = 8,
    retrigger_gap_ms: int = 4,
    transpose: int = 0,
    enabled_tracks=None,
    enabled_channels=None,
) -> tuple:
    """Return (events to play, report). Off, the events come back untouched."""
    kinds = options.kinds()
    if not options.enabled or not events:
        return list(events), Report()

    rng = random.Random(options.seed if options.repeatable else None)
    report = Report()

    window = batch_window_ms / 1000.0
    gap = retrigger_gap_ms / 1000.0
    brush_lead = max(BRUSH_LEAD, window * 2)
    bias = {"rush": -0.35, "drag": 0.35}.get(options.drift, 0.0)
    rate = max(BUSIEST_RATE, min(QUIETEST_RATE, options.rate))

    pairs = _pairs(events)
    # Where each note ends up: index -> (time, note) for the on, and the same
    # for its off. Collected first, emitted after, so a mistake can move both
    # ends of a note and still leave the list sorted.
    starts = {}
    ends = {}
    dropped = set()
    extra = []

    def playable(event) -> bool:
        if enabled_tracks is not None and event.track not in enabled_tracks:
            return False
        if enabled_channels is not None and event.channel not in enabled_channels:
            return False
        return True

    last_mistake = -REFRACTORY
    for group in _batches(events, window):
        # One shift for the whole chord, so a chord leans early or late as a
        # unit instead of smearing. Smearing is the roll's job, below.
        shift = _spread(rng, options.timing_ms / 1000.0, bias)
        for index in group:
            event = events[index]
            roll = 0.0
            if options.roll_ms > 0 and len(group) > 1:
                roll = abs(_spread(rng, options.roll_ms / 1000.0, 0.0))
            start = max(0.0, event.time + shift + roll)
            note = event.note
            if shift or roll:
                report.loosened += 1

            off = pairs.get(index)
            if off is not None:
                length = events[off].time - event.time
                length += _spread(rng, options.length_ms / 1000.0, 0.0)
                length = max(0.005, length)
            else:
                length = None

            mistake = None
            if (
                kinds
                and playable(event)
                and event.time - last_mistake >= REFRACTORY
                and rng.random() < 1.0 / rate
            ):
                mistake = rng.choices(
                    kinds, weights=[WEIGHTS[kind] for kind in kinds]
                )[0]

            if mistake == "slip":
                options_here = neighbours(layout, note + transpose)
                if options_here:
                    note = rng.choice(options_here) - transpose
                    report.slipped += 1
                    last_mistake = event.time
                else:
                    mistake = None
            elif mistake == "miss":
                dropped.add(index)
                if off is not None:
                    dropped.add(off)
                report.missed += 1
                last_mistake = event.time
                continue
            elif mistake == "brush":
                nearby = neighbours(layout, note + transpose)
                against = length if length is not None else 0.2
                held = min(max(against * BRUSH_SHARE, BRUSH_MIN), BRUSH_MAX)
                at = start - brush_lead
                # Nothing before the start of the song, and it has to be up and
                # out of the way before the note it is brushing past arrives.
                if nearby and at >= 0.0 and at + held < start:
                    brushed = rng.choice(nearby) - transpose
                    extra.append(_remake(event, at, brushed))
                    extra.append(_remake(_flip(event), at + held, brushed))
                    report.brushed += 1
                    last_mistake = event.time
                else:
                    mistake = None
            elif mistake == "double":
                bounce = rng.uniform(BOUNCE_MIN, BOUNCE_MAX)
                # Room for the contact, the gap the game needs to see a fresh
                # press, and enough of the note left afterwards to be the note.
                if (length is not None
                        and length > bounce + gap * 2 + BOUNCE_REST):
                    extra.append(_remake(_flip(event), start + bounce, note))
                    extra.append(
                        _remake(event, start + bounce + gap * 2, note)
                    )
                    report.doubled += 1
                    last_mistake = event.time
                else:
                    mistake = None

            starts[index] = (start, note)
            if off is not None:
                ends[off] = (start + length, note)

    out = []
    for index, event in enumerate(events):
        if index in dropped:
            continue
        if index in starts:
            time_, note = starts[index]
            out.append(_remake(event, time_, note))
        elif index in ends:
            time_, note = ends[index]
            out.append(_remake(event, time_, note))
        else:
            out.append(event)
    out.extend(extra)
    # A note-on and a note-off at the same instant have to be ordered, and the
    # player wants the release first, exactly as a loaded song is sorted.
    out.sort(key=lambda e: (e.time, e.on))
    return out, report
