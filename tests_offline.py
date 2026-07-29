"""Offline checks. No display, no mido, no /dev/uinput needed.

Run with:  ./venv/bin/python tests_offline.py
"""

import time
from dataclasses import dataclass

from rpiano.backends import Backend
from rpiano.layouts import build_61, build_88, note_name
from rpiano.player import (
    Player,
    PlayerSettings,
    coverage,
    out_of_range,
    suggest_transpose,
    test_pattern,
    range_test,
)


@dataclass(frozen=True)
class E:
    time: float
    on: bool
    note: int
    velocity: int = 90
    track: int = 0
    channel: int = 0


class FakeSong:
    def __init__(self, events, sustain=()):
        self.events = sorted(events, key=lambda e: (e.time, e.on))
        self.sustain = list(sustain)
        self.duration = max((e.time for e in events), default=0)
        self.tracks = []
        self.channels = []

    def note_ons(self, enabled=None):
        return [e for e in self.events if e.on]


class TimedBackend(Backend):
    """Records every event with a timestamp, so timing can be asserted."""

    name = "timed"

    def __init__(self):
        self.log = []

    def _stamp(self, kind, what):
        self.log.append((time.perf_counter(), kind, what))

    def key_down(self, char):
        self._stamp("down", char)

    def key_up(self, char):
        self._stamp("up", char)

    def mods_down(self, mods):
        if mods:
            self._stamp("mod_down", tuple(mods))

    def mods_up(self, mods):
        if mods:
            self._stamp("mod_up", tuple(mods))

    def release_all(self):
        self._stamp("allup", None)

    def events(self):
        """Log with the initial all-keys-up from load() trimmed off."""
        log = list(self.log)
        while log and log[0][1] == "allup":
            log.pop(0)
        return log

    def kinds(self):
        return [(kind, what) for _, kind, what in self.events()]


def run(song, settings, layout=None, timeout=15):
    backend = TimedBackend()
    player = Player(backend, layout or build_61(), settings)
    errors = []
    player.on_error = errors.append
    player.load(song)
    player.play()
    deadline = time.time() + timeout
    while player.state != "idle" and time.time() < deadline:
        time.sleep(0.005)
    player.stop()
    return backend, errors, player


results = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    results.append(bool(condition))
    return condition


BASE = dict(start_delay=0, modifier_dwell_ms=20, min_note_ms=35, retrigger_gap_ms=20)

# ---------------------------------------------------------------- mapping

a, b = build_61(), build_88()
check("61-key layout has 61 notes, C2 to C7",
      len(a.notes) == 61 and a.low == 36 and a.high == 96)
check("88-key layout has 88 notes, A0 to C8",
      len(b.notes) == 88 and b.low == 21 and b.high == 108)
check("61-key spot checks match the standard layout",
      all(a.notes[n].label() == k for n, k in
          {36: "1", 37: "shift+1", 60: "t", 61: "shift+t", 71: "a", 96: "m"}.items()))
check("no two notes share a keystroke (61)",
      len({k.label() for k in a.notes.values()}) == 61)
check("no two notes share a keystroke (88)",
      len({k.label() for k in b.notes.values()}) == 88)

# The outer octaves were checked note for note against a working MIDI++ config,
# so they are pinned here: they are not derivable from the 61-key layout, and
# an earlier attempt to derive them produced 27 wrong keys that looked orderly.
check("88-key outer octaves walk the row on ctrl, one key per semitone",
      all(b.notes[n].label() == k for n, k in
          {21: "ctrl+1", 28: "ctrl+8", 35: "ctrl+t",
           97: "ctrl+y", 108: "ctrl+j"}.items()),
      str({n: b.notes[n].label() for n in (21, 28, 35, 97, 108)}))
check("88-key layout never needs two modifiers at once",
      not any(len(k.mods) > 1 for k in b.notes.values()),
      str(sorted({k.label() for k in b.notes.values() if len(k.mods) > 1})))

# ------------------------------------------------------------ basic notes

backend, errors, _ = run(FakeSong([E(0.0, True, 60), E(0.4, False, 60)]),
                         PlayerSettings(**BASE))
check("natural key needs no modifier",
      backend.kinds()[:1] == [("down", "t")], str(backend.kinds()[:3]))
check("note is released", ("up", "t") in backend.kinds())
check("no errors", not errors, str(errors))

# ------------------------------------------------- MODIFIER DWELL (the fix)

backend, _, _ = run(FakeSong([E(0.0, True, 61), E(0.4, False, 61)]),
                    PlayerSettings(**BASE))
kinds = backend.kinds()
check("black key sends shift down, then the key, then shift up",
      kinds[:3] == [("mod_down", ("shift",)), ("down", "t"), ("mod_up", ("shift",))],
      str(kinds[:4]))

log = backend.events()
if len(log) >= 3:
    t_mod_down = log[0][0]
    t_key_down = log[1][0]
    t_mod_up = log[2][0]
    before = (t_key_down - t_mod_down) * 1000
    after = (t_mod_up - t_key_down) * 1000
    check("shift is held for a real interval BEFORE the key strikes",
          before >= 19, f"{before:.1f}ms (want >=20)")
    check("shift is still held for a real interval AFTER the key strikes",
          after >= 19, f"{after:.1f}ms (want >=20)")
else:
    check("dwell timing recorded", False, str(backend.events()))

# ------------------------------------------------ MINIMUM NOTE DURATION

# A 5ms note: far shorter than a frame, would previously vanish.
backend, _, _ = run(FakeSong([E(0.0, True, 60), E(0.005, False, 60)]),
                    PlayerSettings(**BASE))
downs = [t for t, k, _ in backend.events() if k == "down"]
ups = [t for t, k, w in backend.events() if k == "up"]
if downs and ups:
    held = (ups[0] - downs[0]) * 1000
    check("a 5ms note is stretched to the minimum hold",
          held >= 33, f"held {held:.1f}ms (want >=35)")
else:
    check("short note was struck at all", False, str(backend.kinds()))

# --------------------------------------------------- RETRIGGER GAP

# Same note twice, back to back with no gap in the MIDI.
backend, _, _ = run(
    FakeSong([E(0.0, True, 60), E(0.10, False, 60), E(0.10, True, 60), E(0.3, False, 60)]),
    PlayerSettings(**BASE),
)
seq = [(t, k) for t, k, w in backend.events() if k in ("down", "up")]
gap_ok = True
worst = None
for i in range(len(seq) - 1):
    if seq[i][1] == "up" and seq[i + 1][1] == "down":
        gap = (seq[i + 1][0] - seq[i][0]) * 1000
        worst = gap if worst is None else min(worst, gap)
        if gap < 19:
            gap_ok = False
check("a repeated note gets a real gap before restriking",
      gap_ok and worst is not None, f"gap {worst:.1f}ms" if worst else "no retrigger seen")

# ------------------------------------------------------ CHORD BATCHING

# C major triad: all naturals, so one group, no dwell at all.
chord = [60, 64, 67]
backend, _, _ = run(
    FakeSong([E(0.0, True, n) for n in chord] + [E(0.4, False, n) for n in chord]),
    PlayerSettings(**BASE),
)
check("an all-natural chord uses no modifier at all",
      not any(k.startswith("mod") for k, _ in backend.kinds()),
      str(backend.kinds()))

# F# major triad: F#, A#, C# -- all sharps, so ONE shift group for three notes.
chord = [66, 70, 73]
backend, _, _ = run(
    FakeSong([E(0.0, True, n) for n in chord] + [E(0.4, False, n) for n in chord]),
    PlayerSettings(**BASE),
)
kinds = [k for k, _ in backend.kinds()]
check("an all-sharp chord costs ONE shift dwell, not three",
      kinds.count("mod_down") == 1 and kinds.count("down") == 3,
      f"{kinds.count('mod_down')} dwells for {kinds.count('down')} notes")

# Mixed chord: naturals struck first with no dwell, sharps in one group after.
chord = [60, 61, 64]
backend, _, _ = run(
    FakeSong([E(0.0, True, n) for n in chord] + [E(0.5, False, n) for n in chord]),
    PlayerSettings(**BASE),
)
kinds = backend.kinds()
first_mod = next((i for i, (k, _) in enumerate(kinds) if k == "mod_down"), None)
downs_before = sum(1 for k, _ in kinds[:first_mod] if k == "down") if first_mod else 0
check("mixed chord strikes naturals before raising shift",
      downs_before == 2, f"{downs_before} naturals before the first dwell")

# ------------------------------------------------ MODIFIER COALESCING


def fast_run(notes, interval, **over):
    """A run of `notes` struck `interval` apart, each note 80% of the gap."""
    events = []
    for index, note in enumerate(notes):
        start = index * interval
        events.append(E(start, True, note))
        events.append(E(start + interval * 0.8, False, note))
    return run(FakeSong(events), PlayerSettings(**{**BASE, **over}))[0]


def shift_state_at_each_strike(backend):
    """[(key, was shift held when it was struck)] in the order struck."""
    held = False
    struck = []
    for kind, what in backend.kinds():
        if kind == "mod_down":
            held = True
        elif kind == "mod_up" or kind == "allup":
            held = False
        elif kind == "down":
            struck.append((what, held))
    return struck


# A fast run of black keys wants shift down throughout. Dropping and retaking
# it between every note costs two dwells a note, which is what made fast
# passages drag.
sharps = [61, 63, 66, 68, 70] * 4
backend = fast_run(sharps, 0.04)
presses = [k for k, _ in backend.kinds()].count("mod_down")
check("a fast run of black keys holds shift instead of retaking it each note",
      presses <= 5, f"shift pressed {presses} times for {len(sharps)} notes")

# ...and the rule that must never be broken while doing it: a natural struck
# with shift still held sounds a semitone sharp. This is the whole safety
# property of holding the modifier on.
chromatic = list(range(60, 72)) * 2
backend = fast_run(chromatic, 0.04)
struck = shift_state_at_each_strike(backend)
expected = [bool(a.notes[n].mods) for n in chromatic]
mismatches = [
    (i, chromatic[i], want, got)
    for i, ((_, got), want) in enumerate(zip(struck, expected))
    if want != got
]
check("every key in a fast mixed run is struck with the right shift state",
      not mismatches and len(struck) == len(chromatic),
      f"{len(mismatches)} wrong of {len(struck)} struck"
      + (f", first at note {mismatches[0][1]}" if mismatches else ""))

# The chord window groups notes struck together. It must not reach across the
# gap after a release and drag the *next* note forward, which on a fast
# passage is a note struck early rather than a chord held together.
backend = fast_run([60, 67], 0.04)
downs = [t for t, kind, _ in backend.events() if kind == "down"]
apart = (downs[1] - downs[0]) * 1000 if len(downs) >= 2 else 0
check("the chord window does not pull the following note forward",
      36 <= apart <= 44, f"notes struck {apart:.1f}ms apart, want 40")

# --------------------------------------------------- OVERLAPPING PARTS

# Two parts holding the same pitch at once - hands crossing, a melody doubled
# at unison. A key is identified by pitch alone, so releasing on the first
# note-off used to cut the second part off mid-note.
backend, _, _ = run(
    FakeSong([E(0.00, True, 60, track=0), E(0.50, False, 60, track=0),
              E(0.20, True, 60, track=1), E(0.90, False, 60, track=1)]),
    PlayerSettings(**BASE),
)
ups = [t for t, kind, _ in backend.events() if kind == "up"]
last_up = (ups[-1] - backend.events()[0][0]) if ups else 0
check("a pitch held by two parts stays down until the later part lets go",
      0.86 <= last_up <= 0.95, f"released at {last_up*1000:.0f}ms, want ~900")

# ...but waiting for a note-off that never arrives would be far worse than
# releasing early: the key would drone for the rest of the song. A file with
# an unpaired note-on must still release on the last note-off it does get.
backend, _, _ = run(
    FakeSong([E(0.00, True, 60, track=0),
              E(0.20, True, 60, track=1),
              E(0.50, False, 60, track=0)]),
    PlayerSettings(**BASE),
)
net = 0
for kind, _ in backend.kinds():
    if kind == "down":
        net += 1
    elif kind == "up":
        net -= 1
    elif kind == "allup":
        net = 0
check("an unpaired note-on does not leave the key stuck down",
      net <= 0, f"net={net} keys still down at the end")

# A single part must behave exactly as it always did.
backend, _, _ = run(
    FakeSong([E(0.0, True, 60), E(0.4, False, 60), E(0.6, True, 60), E(1.0, False, 60)]),
    PlayerSettings(**BASE),
)
check("a single part is unaffected by voice counting",
      [k for k, _ in backend.kinds() if k in ("down", "up")]
      == ["down", "up", "down", "up"],
      str(backend.kinds()))

# ---------------------------------------------------- KEY CONFLICT

backend, _, _ = run(
    FakeSong([E(0.0, True, 60), E(0.15, True, 61), E(0.5, False, 60), E(0.6, False, 61)]),
    PlayerSettings(**BASE),
)
net = 0
for kind, _ in backend.kinds():
    if kind == "down":
        net += 1
    elif kind == "up":
        net -= 1
    elif kind == "allup":
        net = 0
check("C4 and C#4 clashing leaves nothing stuck", net <= 0, f"net={net}")

# --------------------------------------------------------- range / transpose

backend, _, _ = run(FakeSong([E(0.0, True, 20), E(0.3, False, 20)]),
                    PlayerSettings(**BASE, fold_out_of_range=False))
check("out-of-range note is skipped when folding is off",
      not any(k == "down" for k, _ in backend.kinds()))

backend, _, player = run(FakeSong([E(0.0, True, 20), E(0.3, False, 20)]),
                         PlayerSettings(**BASE, fold_out_of_range=True))
check("out-of-range note is folded into range when folding is on",
      any(k == "down" for k, _ in backend.kinds()))

backend, _, _ = run(FakeSong([E(0.0, True, 60), E(0.3, False, 60)]),
                    PlayerSettings(**BASE, transpose=12))
check("transpose +12 moves C4 (t) to C5 (s)",
      ("down", "s") in backend.kinds(), str(backend.kinds()[:2]))

song = FakeSong([E(i * 0.05, True, n) for i, n in enumerate(range(100, 108))])
shift, frac = suggest_transpose(song, build_61())
check("auto-transpose pulls a too-high song into range",
      frac == 1.0 and shift < 0, f"shift={shift} playable={frac:.0%}")
song = FakeSong([E(i * 0.05, True, n) for i, n in enumerate(range(60, 72))])
shift, _ = suggest_transpose(song, build_61())
check("auto-transpose leaves an in-range song alone", shift == 0, f"shift={shift}")

# The fold-or-drop recommendation rests on which end a song overflows at, so
# the two ends have to be counted separately.
low_song = FakeSong([E(i * 0.05, True, n) for i, n in enumerate([28, 30, 33, 60, 64, 67])])
below, above, total = out_of_range(low_song, build_61(), 0)
check("out-of-range counts a bass overflow at the bottom",
      (below, above, total) == (3, 0, 6), f"{below} below, {above} above, {total} total")

high_song = FakeSong([E(i * 0.05, True, n) for i, n in enumerate([60, 64, 100, 104])])
below, above, total = out_of_range(high_song, build_61(), 0)
check("out-of-range counts a treble overflow at the top",
      (below, above, total) == (0, 2, 4), f"{below} below, {above} above, {total} total")

below, above, total = out_of_range(low_song, build_61(), 8)
check("out-of-range respects the transpose",
      (below, above, total) == (0, 0, 6), f"{below} below, {above} above at +8")

mixed = FakeSong([E(0.0, True, 28, track=0), E(0.1, True, 100, track=1),
                  E(0.2, True, 60, track=0)])
below, above, total = out_of_range(mixed, build_61(), 0, enabled_tracks={0})
check("out-of-range respects the track filter",
      (below, above, total) == (1, 0, 2), f"{below} below, {above} above, {total} total")

song = FakeSong([E(i * 0.05, True, n) for i, n in enumerate(range(21, 109))])
playable, total = coverage(song, build_88(), 0)
check("88-key layout reaches a full-range song", playable == total == 88, f"{playable}/{total}")

# --------------------------------------------------------------- sustain

class Sus:
    def __init__(self, time, value):
        self.time = time
        self.value = value

backend, _, _ = run(
    FakeSong([E(0.0, True, 60), E(0.4, False, 60)],
             sustain=[Sus(0.05, 127), Sus(0.35, 0)]),
    PlayerSettings(**BASE, sustain_key=" "),
)
check("sustain pedal presses and releases the space key",
      ("down", " ") in backend.kinds() and ("up", " ") in backend.kinds(),
      str([k for k in backend.kinds() if k[1] == " "]))

backend, _, _ = run(
    FakeSong([E(0.0, True, 60), E(0.4, False, 60)],
             sustain=[Sus(0.05, 70)]),
    PlayerSettings(**BASE, sustain_key=" ", sustain_cutoff=100),
)
check("sustain cutoff is respected (value 70 vs cutoff 100)",
      ("down", " ") not in backend.kinds())

# ------------------------------------------------------------- held cap

chord = [60, 62, 64, 65, 67, 69]
backend, _, player = run(
    FakeSong([E(0.0, True, n) for n in chord] + [E(0.5, False, n) for n in chord]),
    PlayerSettings(**BASE, max_held_keys=3),
)
peak = 0
held = set()
for kind, what in backend.kinds():
    if kind == "down":
        held.add(what)
    elif kind == "up":
        held.discard(what)
    elif kind == "allup":
        held.clear()
    peak = max(peak, len(held))
check("held-key cap is enforced", peak <= 3, f"peak {peak} keys held")

# ---------------------------------------------------------------- timing

events = ([E(i * 0.25, True, 60 + 2 * i) for i in range(8)]
          + [E(i * 0.25 + 0.15, False, 60 + 2 * i) for i in range(8)])
backend, _, _ = run(FakeSong(events), PlayerSettings(**BASE))
downs = [t for t, k, _ in backend.events() if k == "down"]
if len(downs) >= 8:
    drift = max(abs((downs[i] - downs[0]) - i * 0.25) for i in range(8)) * 1000
    check("timing drift stays under 10ms across 2 seconds", drift < 10, f"worst {drift:.1f}ms")
else:
    check("timing test collected all notes", False, f"{len(downs)} of 8")

# The playhead is elapsed wall time times the speed, so changing speed has to
# rebase the origin. Without that, the whole span played so far is rescaled at
# once and the playhead jumps by seconds.
song = FakeSong([E(i * 0.25, True, 60) for i in range(40)]
                + [E(i * 0.25 + 0.1, False, 60) for i in range(40)])
settings = PlayerSettings(**BASE)
player = Player(TimedBackend(), build_61(), settings)
player.load(song)
player.play()
time.sleep(1.0)
before = player.position
settings.speed = 1.05
time.sleep(0.05)
moved = (player.position - before) * 1000
player.stop()
check("changing speed mid-song does not jump the playhead",
      0 <= moved < 100, f"playhead moved {moved:.1f}ms over a 50ms wall gap")

# ------------------------------------------------------------ built-ins

pattern = test_pattern(build_61())
check("test pattern contains naturals and sharps",
      len(pattern.events) >= 20, f"{len(pattern.events)} events")
rt = range_test(build_88())
check("range test covers exactly the 27 extended notes",
      len([e for e in rt.events if e.on]) == 27,
      f"{len([e for e in rt.events if e.on])} notes")
check("range test on a 61-key layout is empty",
      len(range_test(build_61()).events) == 0)

print()
print(f"{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
