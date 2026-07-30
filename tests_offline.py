"""Offline checks. No display, no mido, no /dev/uinput needed.

Run with:  ./venv/bin/python tests_offline.py
"""

import time
from dataclasses import dataclass

from rpiano.backends import Backend, BackendError
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

# --------------------------------------------------- TRACK AND CHANNEL FILTERS

# None means no filter; a set means exactly those. An empty set therefore means
# nothing at all - which is what "All off" produces, and it used to be read as
# "no filter" and play the lot.
mixed = FakeSong([
    E(0.0, True, 60, track=0, channel=0), E(0.2, False, 60, track=0, channel=0),
    E(0.4, True, 64, track=1, channel=1), E(0.6, False, 64, track=1, channel=1),
])


def struck_with(**settings):
    backend, _errors, _player = run(mixed, PlayerSettings(**BASE, **settings))
    return [what for kind, what in backend.kinds() if kind == "down"]


check("no filter plays every track",
      len(struck_with(enabled_tracks=None)) == 2,
      str(struck_with(enabled_tracks=None)))
check("an empty track set plays nothing",
      struck_with(enabled_tracks=set()) == [],
      str(struck_with(enabled_tracks=set())))
check("a track set plays only those tracks",
      len(struck_with(enabled_tracks={0})) == 1,
      str(struck_with(enabled_tracks={0})))
check("an empty channel set plays nothing",
      struck_with(enabled_channels=set()) == [],
      str(struck_with(enabled_channels=set())))
check("a channel set plays only those channels",
      len(struck_with(enabled_channels={1})) == 1,
      str(struck_with(enabled_channels={1})))

# ------------------------------------------------------- AUDIO PREVIEW

# The preview backend reads keystrokes back through the layout, asking the same
# question the game asks. No synth is needed for that part, so it is checked
# with a stub that records instead of sounding.
from rpiano.backends import SoundfontBackend


class StubSynth:
    def __init__(self):
        self.events = []

    def noteon(self, _channel, note, _velocity):
        self.events.append(("on", note))

    def noteoff(self, _channel, note):
        self.events.append(("off", note))

    def setting(self, *_args):
        pass


preview = SoundfontBackend()
preview.configure(build_88(), " ")
preview._synth = StubSynth()
check("preview reads back every note in the layout",
      len(preview._reverse) == 88, f"{len(preview._reverse)} entries")


def struck(char, mods=()):
    preview._synth.events.clear()
    if mods:
        preview.mods_down(mods)
    preview.key_down(char)
    if mods:
        preview.mods_up(mods)
    return preview._synth.events


seen = struck("t")
check("a plain key sounds its natural", seen == [("on", 60)], str(seen))
preview.release_all()
seen = struck("t", ("shift",))
check("shift makes the same key sound the sharp", ("on", 61) in seen, str(seen))
preview.release_all()
seen = struck("1", ("ctrl",))
check("ctrl reaches the outer octaves", ("on", 21) in seen, str(seen))
preview.release_all()
seen = struck("\\")
check("a key the layout does not use is ignored", seen == [], str(seen))

# The pedal: lifting a key while it is down must not damp the note.
preview.release_all()
preview._synth.events.clear()
preview.key_down(" ")
preview.key_down("t")
preview.key_up("t")
held = list(preview._synth.events)
preview.key_up(" ")
after = list(preview._synth.events)
check("the sustain pedal keeps a released note ringing",
      held == [("on", 60)] and after == [("on", 60), ("off", 60)],
      f"held {held}, after pedal up {after}")

preview.release_all()
preview._synth.events.clear()
preview.key_down("u")
preview.key_up("u")
check("without the pedal a released key damps at once",
      preview._synth.events == [("on", 64), ("off", 64)],
      str(preview._synth.events))

# Choosing a different soundfont has to reach a backend that is already
# running, and open() short-circuits on an existing synth - so the synth must
# be discarded or the previous file would go on playing.
swap = SoundfontBackend(path="/one.sf2", bank=0, program=0)
swap.set_soundfont("/two.sf2", 0, 0)
check("choosing another soundfont drops the loaded one",
      swap.path == "/two.sf2" and swap._synth is None,
      f"path {swap.path}, synth {swap._synth}")

# ...but if a song is already playing there is nobody left to reopen it. The
# player only opens a backend at the start of a song, so without reopening here
# the rest of that song went silent while the keys carried on being pressed.
reopened = []


class ReopeningBackend(SoundfontBackend):
    def open(self):
        reopened.append(self.path)
        self._synth = StubSynth()
        self._sfid = 1


live = ReopeningBackend(path="/one.sf2", bank=0, program=0)
live.open()
reopened.clear()
live.set_soundfont("/two.sf2", 0, 0)
check("swapping mid-song reopens instead of going silent",
      reopened == ["/two.sf2"] and live._synth is not None,
      f"reopened {reopened}, synth {live._synth is not None}")

idle = ReopeningBackend(path="/one.sf2", bank=0, program=0)
reopened.clear()
idle.set_soundfont("/two.sf2", 0, 0)
check("swapping while stopped does not open anything early",
      reopened == [] and idle._synth is None, f"reopened {reopened}")

# Changing instrument inside the same file must not throw the synth away: on a
# large soundfont that would be a needless reload.
class ProgramSynth(StubSynth):
    def __init__(self):
        super().__init__()
        self.selected = None

    def program_select(self, _chan, _sfid, bank, program):
        self.selected = (bank, program)


swap = SoundfontBackend(path="/one.sf2", bank=0, program=0)
swap._synth = ProgramSynth()
swap._sfid = 1
kept = swap._synth
swap.set_soundfont("/one.sf2", 0, 4)
check("changing instrument keeps the loaded soundfont",
      swap._synth is kept and swap._synth.selected == (0, 4)
      and swap.program == 4,
      f"synth kept {swap._synth is kept}, selected {swap._synth.selected}")

# FluidSynth's own default is 64-frame periods, a render deadline every 1.45ms
# at 44.1kHz. It is refused realtime priority on an ordinary desktop and the
# player thread busy-waits on its own schedule, so that deadline gets missed -
# and a missed deadline is an underrun, heard as buzzing. Pinned because the
# symptom is subtle enough that the numbers could drift back unnoticed.
deadline_ms = SoundfontBackend.PERIOD_SIZE / 44.1
check("the audio render deadline has real slack in it",
      deadline_ms >= 8.0, f"{deadline_ms:.2f}ms per period")
check("the audio buffer is not so deep it lags behind",
      SoundfontBackend.PERIOD_SIZE * SoundfontBackend.PERIODS / 44.1 <= 120.0,
      f"{SoundfontBackend.PERIOD_SIZE * SoundfontBackend.PERIODS / 44.1:.0f}ms of buffer")

# FluidSynth is a C library, so deleting a synth on the GUI thread while the
# player thread is calling noteon on it segfaults rather than raising - a
# soundfont change mid-song took the whole process down. Every path that reaches
# the synth has to be serialised. Checked structurally, since the failure it
# guards against is a core dump and cannot be asserted on from inside.
unguarded = [
    name for name in
    ("open", "key_down", "key_up", "release_all", "set_gain", "set_soundfont")
    if not hasattr(getattr(SoundfontBackend, name), "__wrapped__")
]
check("every path that touches the synth is lock-guarded",
      not unguarded, f"unguarded: {unguarded}")

# The instrument dropdown stores its bank and program as a string, because Qt's
# findData compares through QVariant and will not match a Python tuple against a
# stored one - it returned -1 and the box silently fell back to the first
# instrument, so a saved choice never came back. Only the key format is checked
# here; the lookup itself needs a widget, which this suite deliberately avoids.
#
# Guarded because importing the window pulls in PyQt6, and the rest of this
# suite runs on a bare interpreter with no Qt at all.
try:
    from rpiano.gui import MainWindow

    key = MainWindow._preset_key(0, 20)
    check("a preset key is a string, not a tuple",
          isinstance(key, str) and key == "0:20", repr(key))
    check("a preset key round-trips to its bank and program",
          tuple(int(part) for part in key.split(":")) == (0, 20))
except ImportError:
    print("SKIP  preset-key checks (PyQt6 not installed)")

# ------------------------------------------------------ MALFORMED FILES

# A single data byte over 127 makes a whole file unreadable, because the parser
# cannot tell a stray data byte from the start of the next command. Real files
# in the wild have exactly this damage, so the loader clamps rather than
# refusing. Skipped rather than failed without mido, since the rest of this
# suite deliberately needs neither mido nor a MIDI file on disk.
try:
    import os
    import struct
    import tempfile
    from pathlib import Path

    import mido

    from rpiano.midi_loader import load_song

    # One note whose velocity byte is 135, then a note-off and end-of-track.
    track = bytes([
        0x00, 0x90, 0x3C, 0x87,              # note_on C4, velocity 135 - bad
        0x83, 0x60, 0x80, 0x3C, 0x00,        # 480 ticks later, note_off C4
        0x00, 0xFF, 0x2F, 0x00,              # end of track
    ])
    raw = (b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
           + b"MTrk" + struct.pack(">I", len(track)) + track)
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as handle:
        handle.write(raw)
        broken = handle.name

    # The byte really is rejected by default, or this proves nothing.
    try:
        mido.MidiFile(broken)
        rejected = False
    except Exception:
        rejected = True
    check("a data byte over 127 is genuinely unreadable by default", rejected)

    song = load_song(Path(broken))
    ons = [e for e in song.events if e.on]
    check("the loader reads a file with an out-of-range data byte anyway",
          len(ons) == 1 and ons[0].note == 60,
          f"{len(ons)} notes" + (f", note {ons[0].note}" if ons else ""))
    check("the out-of-range velocity is clamped, not dropped",
          bool(ons) and ons[0].velocity == 127,
          str(ons[0].velocity) if ons else "no note")
    os.unlink(broken)
except ImportError:
    print("SKIP  malformed-file checks (mido not installed)")

# ------------------------------------------------ SWAPPING BACKEND MID-SONG

class OpenCounting(TimedBackend):
    """A backend that insists on being opened before it will take a key."""

    name = "counts opens"

    def __init__(self, opens=True):
        super().__init__()
        self.opens, self.opened = opens, 0

    def open(self):
        if not self.opens:
            raise BackendError("no audio device")
        self.opened += 1

    def key_down(self, char):
        if not self.opened:
            raise BackendError("Backend is not open.")
        super().key_down(char)


def playing_player():
    """A player a second into a long song, so a swap lands mid-playback."""
    song = FakeSong([E(i * 0.02, i % 2 == 0, 60 + (i % 12)) for i in range(600)])
    first = TimedBackend()
    player = Player(first, build_61(), PlayerSettings(**BASE))
    player.load(song)
    player.play()
    deadline = time.time() + 5
    while player.position < 0.3 and time.time() < deadline:
        time.sleep(0.005)
    return player, first


player, first = playing_player()
second = OpenCounting()
player.set_backend(second)
time.sleep(0.3)
struck = [k for k in second.kinds() if k[0] == "down"]
check("a backend swapped in mid-song is opened, so it actually plays",
      second.opened == 1 and bool(struck),
      f"opened {second.opened}x, {len(struck)} keys struck")
check("the swap does not stop the song",
      player.state == "playing", player.state)
player.stop()

player, first = playing_player()
refused = OpenCounting(opens=False)
try:
    player.set_backend(refused)
    raised = False
except BackendError:
    raised = True
before = player.position
time.sleep(0.3)
check("a backend that will not open is refused rather than swapped in",
      raised and player.backend is first, f"backend is {player.backend.name}")
check("the old backend carries on playing after a refused swap",
      player.state == "playing" and player.position > before,
      f"{player.state}, {before:.2f} -> {player.position:.2f}")
player.stop()


class BreaksOnKey(TimedBackend):
    name = "breaks"

    def key_down(self, char):
        raise BackendError("Backend is not open.")


# A backend failing mid-song used to kill the player thread outright, leaving
# the transport reading as playing for ever: the clock stopped and the seek bar
# did nothing, because it is that thread which applies a seek.
player, first = playing_player()
errors = []
player.on_error = errors.append
player.set_backend(BreaksOnKey())
deadline = time.time() + 5
while player.state != "idle" and time.time() < deadline:
    time.sleep(0.01)
check("a backend failing mid-song ends the song instead of wedging the transport",
      player.state == "idle", player.state)
check("and says what went wrong", bool(errors), str(errors[:1]))
player.stop()

# ------------------------------------------------------ RESTORE DEFAULTS
#
# The buttons read every value out of a fresh AppConfig, so a field named
# wrongly there is an AttributeError under the cursor of whoever clicks it and
# nowhere else. Read as source rather than imported: the window needs PyQt6 and
# a display, and building one starts a global hotkey listener, which a test
# suite has no business doing to the machine it runs on.
import ast
from dataclasses import fields as dataclass_fields
from pathlib import Path as _Path

from rpiano.config import AppConfig

_gui = ast.parse((_Path(__file__).parent / "rpiano" / "gui.py").read_text())
_functions = {
    node.name: node
    for node in ast.walk(_gui)
    if isinstance(node, ast.FunctionDef)
}
RESETS = ("_reset_timing", "_reset_playback", "_reset_input")
TABS = {
    "_build_timing_tab": "_reset_timing",
    "_build_playback_tab": "_reset_playback",
    "_build_input_tab": "_reset_input",
}

check("every tab that should have a Restore defaults button has one",
      all(
          any(
              isinstance(node, ast.Attribute) and node.attr == "_defaults_row"
              for node in ast.walk(_functions[tab])
          )
          for tab in TABS
      ),
      ", ".join(sorted(TABS)))

known = {field.name for field in dataclass_fields(AppConfig)}
restored = set()
for name in RESETS:
    restored |= {
        node.attr
        for node in ast.walk(_functions[name])
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "fresh"
    }
check("every default a Restore button reads is a real setting",
      restored <= known, str(sorted(restored - known)) + " unknown")
check("the timing values are among them",
      {"modifier_dwell_ms", "min_note_ms", "retrigger_gap_ms",
       "batch_window_ms", "max_held_keys"} <= restored)

# The one decision worth pinning down: a stray click must not be able to lose a
# soundfont, which costs a file dialogue and a full read to choose again.
KEEP = {"soundfont_path", "soundfont_stamp", "soundfont_presets",
        "soundfont_bank", "soundfont_program"}
check("no Restore button clears the chosen soundfont",
      not (restored & KEEP), str(sorted(restored & KEEP)))

# The default layout is the wide one, and the pedal it comes with is the pedal
# that layout wants - a fresh install ticked with no key behind it was the way
# these two could disagree.
_default = AppConfig()
check("the 88-key layout is the default",
      _default.layout == "roblox_88", _default.layout)
check("and the default pedal is the one that layout uses",
      _default.sustain_key == " " and _default.sustain_enabled,
      repr(_default.sustain_key))

print()
print(f"{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
