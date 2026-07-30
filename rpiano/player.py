"""The playback engine.

Timing model
------------
Roblox is a frame-polled game, and that shapes everything here. Three
consequences drive the timing settings, all of which are adjustable because
the right values depend on the frame rate you're actually running at.

1. Modifier dwell. When the game handles a key-down it asks "is shift held
   right now?" rather than reading a shift state carried on the event. If
   shift goes down and back up between two frames, the game never sees it and
   a sharp comes out as the natural below it. So shift is pressed, held across
   at least one frame boundary, and only then released.

2. Minimum note duration. A key pressed and released inside one frame can be
   missed entirely. Every note is held for a floor duration regardless of how
   short it is in the MIDI, which is what rescues fast passages.

3. Retrigger gap. A repeated note has to be released and struck again with a
   real gap between, or the game sees no transition and the second strike
   never lands.

Notes that start together are batched and grouped by modifier, so a chord
costs one dwell per modifier group rather than one per note. Naturals are
struck first with no dwell at all, then each modifier group in turn. A chord
mixing naturals and sharps therefore spreads over a few tens of milliseconds,
which is unavoidable and sounds like a slight roll.

The lead a batch starts early by is shared across the whole batch rather than
taken from its first event, so where that roll sits relative to the beat does
not depend on the order the notes happen to appear in the file.

A modifier is held across consecutive keys that want it rather than pressed
and released once per note. That costs nothing at ordinary spacing, where it
releases as it always did, and is what makes a fast run of black keys playable
at all - such a run wants shift down almost continuously, and letting go of it
between every note was the single largest source of delay in the engine.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .layouts import Layout, note_name

IDLE = "idle"
COUNTING_IN = "counting in"
PLAYING = "playing"
PAUSED = "paused"


@dataclass
class PlayerSettings:
    transpose: int = 0
    speed: float = 1.0
    hold_notes: bool = True
    tap_ms: int = 40
    fold_out_of_range: bool = True
    max_held_keys: int = 0
    start_delay: float = 3.0
    sustain_key: str = ""
    sustain_cutoff: int = 64

    # Timing, all in milliseconds. Defaults assume roughly 60fps.
    modifier_dwell_ms: int = 20
    min_note_ms: int = 35
    retrigger_gap_ms: int = 20
    batch_window_ms: int = 8

    # None means no filter at all; a set means exactly those, and an empty set
    # therefore means nothing. Conflating the two is what made "All off" play
    # everything: it produced an empty set, which read as "no filter".
    enabled_tracks: set | None = None
    enabled_channels: set | None = None


# A batch struck further behind its nominal time than this is worth counting.
# Below it you are inside the frame the note belonged to anyway.
LATE_THRESHOLD = 0.025


@dataclass
class Stats:
    struck: int = 0
    dropped_range: int = 0
    stolen: int = 0
    capped: int = 0
    late: int = 0

    def reset(self) -> None:
        self.struck = self.dropped_range = self.stolen = self.capped = 0
        self.late = 0

    def summary(self) -> str:
        parts = [f"{self.struck} notes struck"]
        if self.dropped_range:
            parts.append(f"{self.dropped_range} skipped as out of range")
        if self.stolen:
            parts.append(f"{self.stolen} cut short by a key clash")
        if self.capped:
            parts.append(f"{self.capped} dropped by the held-key cap")
        if self.late:
            parts.append(f"{self.late} struck over {LATE_THRESHOLD * 1000:.0f}ms late")
        return ", ".join(parts)


class Player:
    """Owns the playback thread. Public methods are safe to call from the GUI."""

    def __init__(self, backend, layout: Layout, settings: PlayerSettings):
        self.backend = backend
        self.layout = layout
        self.settings = settings
        self.song = None
        self.stats = Stats()

        self.on_state = lambda state: None
        self.on_progress = lambda position, held_notes: None
        self.on_finished = lambda: None
        self.on_error = lambda message: None
        self.on_countdown = lambda remaining: None
        self.on_log = lambda level, message: None

        self._state = IDLE
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread = None
        self._should_stop = False
        self._seek_request = None

        self._position = 0.0
        self._index = 0
        self._key_owner = {}        # char -> note holding it
        self._note_stroke = {}      # note -> KeyStroke
        self._press_time = {}       # char -> when it went down
        self._last_release = {}     # char -> when it last came up
        self._press_order = []
        self._pending_release = []  # (deadline, char, note, strike id)
        self._strike_seq = 0        # bumped per strike, so a timed release
        self._key_seq = {}          # can tell its own strike from a later one
        self._note_voices = {}      # note -> how many parts are holding it
        self._offs_remaining = {}   # note -> note-offs still ahead of us
        self._mods_held = frozenset()   # modifiers down at the backend now
        self._mods_hold_until = 0.0     # ...and the dwell they still owe
        self._sustain_index = 0
        self._sustain_down = False
        self._last_progress = 0.0

    # -- state -------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def position(self) -> float:
        return self._position

    def _set_state(self, state: str) -> None:
        self._state = state
        self.on_state(state)

    def held_keys(self) -> list:
        with self._lock:
            return sorted(self._key_owner.values())

    # -- loading -----------------------------------------------------------

    def load(self, song) -> None:
        self.stop()
        self.song = song
        self._position = 0.0
        self._index = 0
        self._sustain_index = 0
        self.stats.reset()

    def set_layout(self, layout: Layout) -> None:
        with self._lock:
            self._panic()
            self.layout = layout

    def set_backend(self, backend) -> None:
        with self._lock:
            self._panic()
            old = self.backend
            self.backend = backend
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass

    # -- transport ---------------------------------------------------------

    def play(self) -> None:
        if self.song is None:
            return
        with self._lock:
            if self._state == PAUSED:
                self._set_state(PLAYING)
                self._wake.set()
                return
            if self._state in (PLAYING, COUNTING_IN):
                return
            self._should_stop = False
            self._thread = threading.Thread(
                target=self._run, name="rpiano-player", daemon=True
            )
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state != PLAYING:
                return
            self._set_state(PAUSED)
            self._panic()
        self._wake.set()

    def toggle(self) -> None:
        if self._state == PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        thread = self._thread
        self._should_stop = True
        self._wake.set()
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            self._panic()
            self._position = 0.0
            self._index = 0
            self._sustain_index = 0
        if self._state != IDLE:
            self._set_state(IDLE)

    def restart(self) -> None:
        was_live = self._state in (PLAYING, COUNTING_IN, PAUSED)
        self.stop()
        if was_live:
            saved = self.settings.start_delay
            self.settings.start_delay = 0.0
            self.play()
            self.settings.start_delay = saved

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._seek_request = max(0.0, seconds)
            if self._state not in (PLAYING, COUNTING_IN):
                self._apply_seek()
        self._wake.set()

    def nudge(self, seconds: float) -> None:
        """Skip forward or back relative to where we are now."""
        if self.song is None:
            return
        target = min(max(0.0, self._position + seconds), self.song.duration)
        self.seek(target)

    # -- the thread --------------------------------------------------------

    def _run(self) -> None:
        try:
            self.backend.open()
        except Exception as exc:
            self.on_error(str(exc))
            self.on_log("error", str(exc))
            self._set_state(IDLE)
            return

        self.on_log(
            "info",
            f"{self.backend.name} open. dwell {self.settings.modifier_dwell_ms}ms, "
            f"floor {self.settings.min_note_ms}ms, "
            f"retrigger {self.settings.retrigger_gap_ms}ms",
        )

        if self.settings.start_delay > 0:
            self._set_state(COUNTING_IN)
            deadline = time.perf_counter() + self.settings.start_delay
            while not self._should_stop:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                self.on_countdown(remaining)
                self._wake.wait(min(0.1, remaining))
                self._wake.clear()
            self.on_countdown(0.0)

        if self._should_stop:
            self._finish()
            return

        self._set_state(PLAYING)
        self.stats.reset()
        self._count_remaining_offs()
        events = self.song.events
        origin_wall = time.perf_counter()
        origin_song = self._position
        last_speed = max(0.05, self.settings.speed)

        while not self._should_stop:
            if self._seek_request is not None:
                with self._lock:
                    self._apply_seek()
                origin_wall = time.perf_counter()
                origin_song = self._position

            if self._state == PAUSED:
                self._wake.wait(0.05)
                self._wake.clear()
                origin_wall = time.perf_counter()
                origin_song = self._position
                continue

            speed = max(0.05, self.settings.speed)
            now = time.perf_counter()
            if speed != last_speed:
                # The playhead is the whole elapsed span times the speed, so a
                # speed change has to rebase the origin. Without this, nudging
                # the dial mid-song rescales everything played so far and the
                # playhead jumps forwards or backwards by seconds.
                origin_song += (now - origin_wall) * last_speed
                origin_wall = now
                last_speed = speed
            playhead = origin_song + (now - origin_wall) * speed
            self._position = playhead

            with self._lock:
                self._flush_pending_releases(now)
                batch = self._collect_batch(events, playhead)
                if batch:
                    if playhead - batch[0].time > LATE_THRESHOLD:
                        self.stats.late += 1
                    self._dispatch_batch(batch)
                self._apply_sustain(playhead)

            if now - self._last_progress >= 0.033:
                self._last_progress = now
                self.on_progress(playhead, list(self._key_owner.values()))

            if (
                self._index >= len(events)
                and not self._key_owner
                and not self._pending_release
            ):
                break

            deadlines = []
            if self._index < len(events):
                head = events[self._index]
                due = head.time - self._batch_lead(events)
                deadlines.append(origin_wall + (due - origin_song) / speed)
            if self._pending_release:
                deadlines.append(min(item[0] for item in self._pending_release))
            target = min(deadlines) if deadlines else time.perf_counter() + 0.02

            gap = target - time.perf_counter()
            if gap > 0.002:
                self._wake.wait(min(gap - 0.001, 0.05))
                self._wake.clear()
            else:
                while time.perf_counter() < target and not self._should_stop:
                    pass

        self._finish()

    def _finish(self) -> None:
        with self._lock:
            self._panic()
        try:
            self.backend.close()
        except Exception:
            pass
        finished_naturally = not self._should_stop
        if self.stats.struck:
            self.on_log("info", self.stats.summary())
        self._set_state(IDLE)
        if finished_naturally:
            self._position = 0.0
            self._index = 0
            self._sustain_index = 0
            self.on_finished()

    # -- batching ----------------------------------------------------------

    def _batch_lead(self, events) -> float:
        """How far ahead of its nominal time the next batch has to start.

        A modifier that has to be *pressed* costs a dwell of blocked thread
        before the key can be struck, so the batch starts a dwell early and the
        key still lands on the beat. Two things decide whether that is owed.

        The first is that the lead is shared by the whole batch rather than
        taken from its head. Taking it from the head made a chord's timing
        depend on the order its notes happen to sit in the file: led by a sharp
        it fired a dwell early, led by a natural it fired on the beat and put
        its sharps a dwell behind. Same chord, two answers, swapping back and
        forth through a piece - which is what reads as loose playing rather
        than an honest roll.

        The second is that a modifier already down is not pressed again, so no
        dwell is paid and no lead is owed. Getting this wrong is worse than not
        coalescing at all: the batch starts early, the dwell it was compensating
        for never happens, and the note lands a full dwell ahead of the beat.

        Deliberately uncached. Resolving a note is an addition and a dict
        lookup; a cache keyed on transposition, dwell and layout measured
        slower than just doing the work (299ns against 399ns per call). The
        cheap gate in _collect_batch is what keeps this off the hot path.
        """
        if self._index >= len(events):
            return 0.0
        dwell = self.settings.modifier_dwell_ms / 1000.0
        if dwell <= 0:
            return 0.0

        # Find the next note-on, stepping over the releases queued in front of
        # it. Those releases matter: events are handled in order, so a release
        # sitting between here and the note is something the lead has to reach
        # over. On a fast passage the previous note is still finishing when the
        # next one needs its modifier pressed, and without this the engine only
        # arrives at the note after the release has been dealt with - too late
        # to give the modifier its dwell, leaving every note in the run a
        # constant few milliseconds behind.
        head_time = events[self._index].time
        first = None
        for index in range(self._index, len(events)):
            event = events[index]
            if event.time - head_time > dwell:
                break
            if event.on and self._event_enabled(event):
                first = event
                break
        if first is None:
            return 0.0
        offset = first.time - head_time

        window = self.settings.batch_window_ms / 1000.0
        limit = first.time + window
        groups = set()
        for index in range(self._index, len(events)):
            event = events[index]
            if event.time > limit:
                break
            if not event.on or not self._event_enabled(event):
                continue
            stroke = self._resolve(event.note)
            if stroke is not None:
                groups.add(frozenset(stroke.mods))
        if not any(groups):
            return 0.0
        # Every key in the batch content with what is already held: nothing
        # gets pressed, nothing is blocked on, nothing is owed.
        if self._mods_held and groups == {self._mods_held}:
            return 0.0
        # Otherwise a modifier has to go down, and the batch starts early
        # enough to pay for it - less whatever head start the note already has
        # by sitting further along than the release in front of it.
        return max(0.0, dwell - offset)

    def _collect_batch(self, events, playhead: float) -> list:
        """Every event that's due, plus note-ons within the batch window of it.

        Notes written as a chord rarely land on exactly the same tick, so a
        small window keeps them together and saves a modifier dwell.

        The window still does NOT sweep in note-offs from beyond it. A note
        shorter than the window would otherwise have its release processed
        before its press and leave the key held down for good.
        """
        if self._index >= len(events):
            return []
        head = events[self._index]
        # Conservative gate first. No event can need more lead than the dwell,
        # so this rules out the common "nothing due yet" case without touching
        # the layout at all.
        max_lead = self.settings.modifier_dwell_ms / 1000.0
        if head.time - max_lead > playhead:
            return []
        lead = self._batch_lead(events)
        if head.time - lead > playhead:
            return []

        # The window exists to gather a chord, so it is measured from the first
        # note-on that is genuinely due, not from the head event - which may be
        # a note-off. Anchored to a release instead, the window reached across
        # the gap after it and dragged the *next* note forward: harmless when
        # notes are far apart, but on a fast passage the gap between one note
        # ending and the next starting is itself only a few milliseconds, so
        # the next note got struck early.
        limit = None
        for index in range(self._index, len(events)):
            event = events[index]
            if event.time - lead > playhead:
                break
            if event.on:
                limit = event.time + self.settings.batch_window_ms / 1000.0
                break

        batch = []
        while self._index < len(events):
            event = events[self._index]
            if event.time - lead > playhead and not (
                # Not due yet. Only a note-on may be pulled forward, and only
                # if it falls inside the chord window.
                event.on and limit is not None and event.time <= limit
            ):
                break
            self._index += 1
            if not event.on:
                left = self._offs_remaining.get(event.note)
                if left:
                    self._offs_remaining[event.note] = left - 1
            if self._event_enabled(event):
                batch.append(event)
        return batch

    def _count_remaining_offs(self) -> None:
        """Tally the note-offs still ahead of the playhead, per pitch.

        Cheap to rebuild and only done on load and on seek, which is why the
        count can be maintained by simple decrement on the hot path.
        """
        counts = {}
        if self.song is not None:
            events = self.song.events
            for index in range(self._index, len(events)):
                event = events[index]
                if not event.on:
                    counts[event.note] = counts.get(event.note, 0) + 1
        self._offs_remaining = counts

    def _event_enabled(self, event) -> bool:
        tracks = self.settings.enabled_tracks
        if tracks is not None and event.track not in tracks:
            return False
        channels = self.settings.enabled_channels
        if channels is not None and event.channel not in channels:
            return False
        return True

    def _dispatch_batch(self, batch: list) -> None:
        """Releases, then presses, then any release that belongs after a press.

        Releases go first so a repeated note frees its key before restriking.
        But a note-off for a note struck in this same batch has to come after
        the press, or it would be discarded as a release of something not yet
        held - and the key would stay down.
        """
        struck_here = set()
        early_offs = []
        ons = []
        late_offs = []
        for event in batch:
            if event.on:
                ons.append(event)
                struck_here.add(event.note)
            elif event.note in struck_here:
                late_offs.append(event)
            else:
                early_offs.append(event)

        for event in early_offs:
            self._note_off(event.note)
        if ons:
            self._press_group(ons)
        for event in late_offs:
            # Will hit the minimum-hold floor and be released on a timer.
            self._note_off(event.note)

    def _press_group(self, events: list) -> None:
        resolved = []
        for event in events:
            stroke = self._resolve(event.note)
            if stroke is None:
                self.stats.dropped_range += 1
                continue
            resolved.append((event.note, stroke))
        if not resolved:
            # Nothing playable here, but modifiers may still be held from the
            # batch before, and this one is no longer a reason to keep them.
            self._settle_mods(events[0].time)
            return

        # Lift every key this batch is about to strike again, before anything
        # blocks. The retrigger gap is a wait since the key came *up*, so
        # starting it here lets it run down during the modifier dwell instead
        # of after it - the difference between paying both in series and paying
        # whichever is longer. Left to _strike, the key came up immediately
        # before the check, so the full gap was owed every single time; on a
        # dense arrangement that was the largest block in the engine.
        now = time.perf_counter()
        for note, stroke in resolved:
            self._release_for_restrike(stroke.char, note, now)

        groups = {}
        for note, stroke in resolved:
            groups.setdefault(stroke.mods, []).append((note, stroke))

        # Plain keys first and with no dwell; then one dwell per modifier group.
        for mods in sorted(groups, key=lambda m: (len(m), m)):
            self._press_modifier_group(mods, groups[mods])
        self._settle_mods(events[0].time)

    def _release_for_restrike(self, char: str, note: int, when: float) -> None:
        """Lift a key the batch is about to strike again, ahead of any blocking.

        Whether it is the same pitch returning or a different one taking the
        key over decides what survives: the voice count belongs to the pitch,
        so a pitch reclaiming its own key keeps it, while a pitch losing the
        key to another gives it up along with everything else.
        """
        owner = self._key_owner.get(char)
        if owner is None:
            return
        self.backend.key_up(char)
        self._last_release[char] = when
        if owner != note:
            self._forget_key(char, owner)
            self.stats.stolen += 1
            return
        self._key_owner.pop(char, None)
        self._press_time.pop(char, None)
        if char in self._press_order:
            self._press_order.remove(char)

    def _press_modifier_group(self, mods, items) -> None:
        self._ensure_mods(mods)
        # Whatever is left of the retrigger gap once the dwell has run.
        gap = self.settings.retrigger_gap_ms / 1000.0
        if gap > 0:
            now = time.perf_counter()
            wait = 0.0
            for _note, stroke in items:
                last = self._last_release.get(stroke.char)
                if last is not None:
                    wait = max(wait, gap - (now - last))
            if wait > 0:
                self._block(wait)
        for note, stroke in items:
            self._strike(note, stroke)
        if mods:
            # The game reads the modifier when it handles the key-down, so it
            # has to stay down for a dwell past the strike. That is recorded as
            # a debt rather than paid here: if the next thing struck wants the
            # same modifier, it is discharged by simply not letting go, and
            # costs nothing at all.
            dwell = self.settings.modifier_dwell_ms / 1000.0
            if dwell > 0:
                self._mods_hold_until = time.perf_counter() + dwell

    # -- modifier state ----------------------------------------------------
    #
    # A run of black keys used to press and release shift once per note, at two
    # dwells of blocked thread each time, even though shift wanted to be down
    # for the whole run. Holding it across the run is both faster and closer to
    # what a player's hand does. The one rule that cannot be broken is that a
    # natural must never be struck while shift is down, or it sounds a semitone
    # sharp - so a modifier is only kept if the very next key wants it.

    def _wait_for_mod_hold(self) -> None:
        """Pay off the dwell a struck key is still owed before mods can move."""
        if not self._mods_hold_until:
            return
        wait = self._mods_hold_until - time.perf_counter()
        if wait > 0:
            self._block(wait)
        self._mods_hold_until = 0.0

    def _ensure_mods(self, mods) -> None:
        """Put exactly `mods` down, moving only what actually has to move.

        Releasing needs no dwell: the game reads the modifier state when it
        handles the key-down, and an up sent before that key-down is already
        visible by then. Pressing does, which is the whole reason dwell exists.
        """
        target = frozenset(mods)
        if self._mods_held == target:
            return
        self._wait_for_mod_hold()
        stale = self._mods_held - target
        fresh = target - self._mods_held
        if stale:
            self.backend.mods_up(tuple(stale))
        if fresh:
            self.backend.mods_down(tuple(fresh))
            dwell = self.settings.modifier_dwell_ms / 1000.0
            if dwell > 0:
                self._block(dwell)
        self._mods_held = target

    def _settle_mods(self, struck_at: float) -> None:
        """Let go of the modifiers unless the next key is too close to bother.

        At any ordinary spacing the next note is far enough away that releasing
        and re-pressing is free - the lead built into the schedule absorbs the
        dwell - so this releases, exactly as the player always did. It only
        holds on when the gap is too small for that cycle to fit, which is the
        case that was costing two dwells a note in a fast run.
        """
        if not self._mods_held:
            return
        if self._next_strike_mods(struck_at) == self._mods_held:
            return
        self._wait_for_mod_hold()
        self.backend.mods_up(tuple(self._mods_held))
        self._mods_held = frozenset()

    def _next_strike_mods(self, struck_at: float):
        """What the next note-on wants, or None if it is too far off to matter.

        The horizon is measured in song time against the notes just struck,
        not against the wall clock, so it asks a question about the music -
        are these notes close together? - and cannot be skewed by however long
        this dispatch happened to block for.
        """
        if self.song is None:
            return None
        dwell = self.settings.modifier_dwell_ms / 1000.0
        if dwell <= 0:
            return None
        # A song-time gap g takes g/speed of real time, so the gap worth
        # bridging grows with the speed the piece is being played at.
        horizon = struck_at + 2 * dwell * max(0.05, self.settings.speed)
        events = self.song.events
        for index in range(self._index, len(events)):
            event = events[index]
            if event.time > horizon:
                return None
            if not event.on or not self._event_enabled(event):
                continue
            stroke = self._resolve(event.note)
            return frozenset(stroke.mods) if stroke is not None else None
        return None

    # -- individual keys ---------------------------------------------------

    def _resolve(self, note: int):
        target = note + self.settings.transpose
        if target not in self.layout.notes:
            if not self.settings.fold_out_of_range:
                return None
            folded = self.layout.fold_into_range(target)
            if folded is None:
                return None
            target = folded
        return self.layout.notes[target]

    def _strike(self, note: int, stroke) -> None:
        char = stroke.char

        cap = self.settings.max_held_keys
        if cap and len(self._key_owner) >= cap and char not in self._key_owner:
            self._release_oldest()
            self.stats.capped += 1

        owner = self._key_owner.get(char)
        if owner is not None:
            self.backend.key_up(char)
            self._last_release[char] = time.perf_counter()
            if owner != note:
                # A different pitch wanting the same physical key - C4 and C#4
                # share one, shift apart - so one of them has to give it up.
                self._forget_key(char, owner)
                self.stats.stolen += 1
            # Otherwise it is the same pitch struck again while still held: a
            # second part doubling it, or an overlapping repeat. The attack is
            # real so the key is restruck, but the parts already holding it are
            # deliberately not forgotten - see _note_off.

        gap = self.settings.retrigger_gap_ms / 1000.0
        last = self._last_release.get(char)
        if gap > 0 and last is not None:
            wait = gap - (time.perf_counter() - last)
            if wait > 0:
                self._block(wait)

        self.backend.key_down(char)
        now = time.perf_counter()
        self._strike_seq += 1
        seq = self._strike_seq
        self._key_owner[char] = note
        self._key_seq[char] = seq
        self._note_stroke[note] = stroke
        self._note_voices[note] = self._note_voices.get(note, 0) + 1
        self._press_time[char] = now
        if char in self._press_order:
            self._press_order.remove(char)
        self._press_order.append(char)
        self.stats.struck += 1

        if not self.settings.hold_notes:
            floor = max(self.settings.tap_ms, self.settings.min_note_ms) / 1000.0
            self._pending_release.append((now + floor, char, note, seq))

    def _note_off(self, note: int) -> None:
        if not self.settings.hold_notes:
            return
        stroke = self._note_stroke.get(note)
        if stroke is None:
            return
        char = stroke.char
        if self._key_owner.get(char) != note:
            self._note_stroke.pop(note, None)
            return

        # Several parts can hold one pitch at once - hands crossing, a melody
        # doubled at unison, any two-part reduction. They are indistinguishable
        # here because a key is identified by pitch alone, so releasing on the
        # first note-off cut every other part off mid-note.
        #
        # The guard on remaining note-offs is what makes waiting safe. A file
        # with an unpaired note-on would otherwise never bring the count back
        # to zero and would leave the key down for the rest of the song, which
        # is a far worse failure than the one being fixed. Releasing on the
        # last note-off a pitch will ever get bounds it either way.
        voices = self._note_voices.get(note, 0)
        if voices > 1 and self._offs_remaining.get(note, 0) > 0:
            self._note_voices[note] = voices - 1
            return

        floor = self.settings.min_note_ms / 1000.0
        pressed_at = self._press_time.get(char, 0.0)
        if floor > 0 and time.perf_counter() - pressed_at < floor:
            # Too short to survive a frame poll. Hold it to the floor instead.
            self._pending_release.append(
                (pressed_at + floor, char, note, self._key_seq.get(char))
            )
            return
        self._release_key(char, note)

    def _release_key(self, char: str, note) -> None:
        self.backend.key_up(char)
        self._last_release[char] = time.perf_counter()
        self._forget_key(char, note)

    def _forget_key(self, char: str, note) -> None:
        self._key_owner.pop(char, None)
        if note is not None:
            self._note_stroke.pop(note, None)
            self._note_voices.pop(note, None)
        self._press_time.pop(char, None)
        if char in self._press_order:
            self._press_order.remove(char)

    def _flush_pending_releases(self, now: float) -> None:
        if not self._pending_release:
            return
        still = []
        for deadline, char, note, seq in self._pending_release:
            if deadline > now:
                still.append((deadline, char, note, seq))
                continue
            # Only the strike that asked for this release may act on it. The
            # key may since have been let go and struck again, and releasing
            # *that* strike early would cut a note that has only just started.
            if self._key_seq.get(char) != seq or self._key_owner.get(char) != note:
                continue
            # A pitch several parts are holding is not this one strike's to
            # end; the last part to let go releases it.
            if self._note_voices.get(note, 0) > 1:
                continue
            self._release_key(char, note)
        self._pending_release = still

    def _release_oldest(self) -> None:
        if not self._press_order:
            return
        char = self._press_order[0]
        note = self._key_owner.get(char)
        self._release_key(char, note)

    def _block(self, seconds: float) -> None:
        """Hold the thread for a short, deliberate interval.

        The playhead is recomputed from the wall clock every loop, so blocking
        here delays the next event slightly but never accumulates drift.
        """
        end = time.perf_counter() + seconds
        remaining = seconds - 0.001
        if remaining > 0:
            time.sleep(remaining)
        while time.perf_counter() < end:
            pass

    # -- sustain -----------------------------------------------------------

    def _apply_sustain(self, playhead: float) -> None:
        key = self.settings.sustain_key
        if not key or not self.song.sustain:
            return
        events = self.song.sustain
        cutoff = self.settings.sustain_cutoff
        while (
            self._sustain_index < len(events)
            and events[self._sustain_index].time <= playhead
        ):
            value = events[self._sustain_index].value
            self._sustain_index += 1
            want_down = value >= cutoff
            if want_down == self._sustain_down:
                continue
            self._sustain_down = want_down
            try:
                if want_down:
                    self.backend.key_down(key)
                else:
                    self.backend.key_up(key)
            except Exception as exc:
                self.on_log("error", f"Sustain key {key!r} failed: {exc}")

    # -- teardown ----------------------------------------------------------

    def _panic(self) -> None:
        for char in list(self._key_owner):
            try:
                self.backend.key_up(char)
            except Exception:
                pass
        if self._mods_held:
            try:
                self.backend.mods_up(tuple(self._mods_held))
            except Exception:
                pass
        if self._sustain_down and self.settings.sustain_key:
            try:
                self.backend.key_up(self.settings.sustain_key)
            except Exception:
                pass
        try:
            self.backend.release_all()
        except Exception:
            pass
        self._key_owner.clear()
        self._note_stroke.clear()
        self._press_time.clear()
        self._press_order.clear()
        self._pending_release.clear()
        self._note_voices.clear()
        self._key_seq.clear()
        self._mods_held = frozenset()
        self._mods_hold_until = 0.0
        self._sustain_down = False

    def _apply_seek(self) -> None:
        target = self._seek_request
        self._seek_request = None
        if target is None or self.song is None:
            return
        self._panic()
        self._position = target
        events = self.song.events
        index = 0
        while index < len(events) and events[index].time < target:
            index += 1
        self._index = index
        sustain = self.song.sustain
        sindex = 0
        while sindex < len(sustain) and sustain[sindex].time < target:
            sindex += 1
        self._sustain_index = sindex
        self._count_remaining_offs()


# -- helpers ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SimpleEvent:
    time: float
    on: bool
    note: int
    velocity: int = 90
    track: int = 0
    channel: int = 0


class _SimpleSong:
    """A song built in memory. Same shape as a loaded one."""

    def __init__(self, events, title="Test pattern", details=""):
        self.events = sorted(events, key=lambda e: (e.time, e.on))
        self.sustain = []
        self.tracks = []
        self.channels = []
        self.title = title
        self.path = None
        self.duration = max((e.time for e in events), default=0.0) + 0.5
        self.details = details or title

    def note_ons(self, enabled_tracks=None):
        return [e for e in self.events if e.on]


def test_pattern(layout: Layout) -> "_SimpleSong":
    """A slow C major scale, then the black keys of the same octave.

    The second half is the part that matters: if the sharps come out sounding
    like the naturals below them, the modifier dwell is too short for the
    frame rate you're running at.
    """
    root = 60 if 60 in layout.notes else layout.low
    events = []
    index = 0
    for step in (0, 2, 4, 5, 7, 9, 11, 12):
        note = root + step
        if note in layout.notes:
            events.append(_SimpleEvent(index * 0.32, True, note))
            events.append(_SimpleEvent(index * 0.32 + 0.26, False, note))
            index += 1
    index += 1
    for step in (1, 3, 6, 8, 10):
        note = root + step
        if note in layout.notes:
            events.append(_SimpleEvent(index * 0.32, True, note))
            events.append(_SimpleEvent(index * 0.32 + 0.26, False, note))
            index += 1
    return _SimpleSong(
        events,
        "Scale, then sharps",
        "Eight naturals, a pause, then five sharps.\n"
        "If the sharps sound like the white keys below them,\n"
        "raise the modifier dwell.",
    )


def range_test(layout: Layout) -> "_SimpleSong":
    """Walks only the notes outside the plain 61-key range, slowly."""
    notes = [n for n in sorted(layout.notes) if n < 36 or n > 96]
    events = []
    for index, note in enumerate(notes):
        events.append(_SimpleEvent(index * 0.55, True, note))
        events.append(_SimpleEvent(index * 0.55 + 0.45, False, note))
    if not notes:
        return _SimpleSong(
            [], "No extended range", "This layout has no notes outside C2-C7."
        )
    labels = "\n".join(
        f"{note_name(n)}  ->  {layout.notes[n].label()}" for n in notes
    )
    return _SimpleSong(events, "Extended range only", labels)


def _pitch_counts(song, enabled_tracks=None) -> tuple:
    """{pitch: how many times it occurs} plus the total.

    A song has thousands of notes but at most 128 distinct pitches, so counting
    them once turns the 49-shift search below from tens of thousands of lookups
    into a few thousand.
    """
    counts = {}
    total = 0
    for event in song.note_ons(enabled_tracks):
        note = event.note
        counts[note] = counts.get(note, 0) + 1
        total += 1
    return counts, total


def suggest_transpose(song, layout: Layout, enabled_tracks=None) -> tuple:
    """Pick the semitone shift that leaves the fewest notes off the keyboard."""
    counts, total = _pitch_counts(song, enabled_tracks)
    if not total:
        return 0, 1.0
    playable = layout.notes
    items = tuple(counts.items())
    best_shift, best_hits = 0, -1
    for shift in range(-24, 25):
        hits = 0
        for note, count in items:
            if note + shift in playable:
                hits += count
        if hits > best_hits or (hits == best_hits and abs(shift) < abs(best_shift)):
            best_shift, best_hits = shift, hits
    return best_shift, best_hits / total


def timing_analysis(song, layout: Layout, transpose: int,
                    enabled_tracks=None, enabled_channels=None) -> dict:
    """What this song can tell us about the timing values it wants.

    Two notes a semitone apart share one physical key - C4 and C#4 are the same
    key with shift - so if both are sounding at once, one loses it. Some of
    those collisions are written into the music and unavoidable. The rest are
    manufactured by the minimum-note floor: a note held longer than the file
    asks runs into the next use of its key.

    The highest floor that manufactures none is simply the smallest gap between
    two different notes on one key. Computed in a single pass rather than by
    counting clashes at every candidate value, which on a dense file meant
    ninety passes and nearly three seconds.

    Returns the ceiling in ms, how many collisions are unavoidable, the tenth
    percentile note length, and how many notes were examined.
    """
    spans = {}          # (track, channel, note) -> [start times awaiting an end]
    by_key = {}         # physical key -> [(start, end, note)]
    lengths = []
    for event in song.events:
        if enabled_tracks is not None and event.track not in enabled_tracks:
            continue
        if enabled_channels is not None and event.channel not in enabled_channels:
            continue
        voice = (event.track, event.channel, event.note)
        if event.on:
            spans.setdefault(voice, []).append(event.time)
            continue
        starts = spans.get(voice)
        if not starts:
            continue
        start = starts.pop(0)
        lengths.append(event.time - start)
        target = event.note + transpose
        stroke = layout.notes.get(target)
        if stroke is None:
            folded = layout.fold_into_range(target)
            stroke = layout.notes.get(folded) if folded is not None else None
        if stroke is not None:
            by_key.setdefault(stroke.char, []).append(
                (start, event.time, target)
            )

    ceiling = float("inf")
    genuine = 0
    for items in by_key.values():
        items.sort()
        for index, (start, end, note) in enumerate(items):
            for other_start, _other_end, other_note in items[index + 1:]:
                gap = other_start - start
                # Two reasons to keep looking, and they stop at different
                # points: a genuine overlap needs other_start inside this note,
                # while the ceiling needs any gap smaller than the smallest so
                # far. Breaking on the ceiling alone silently stopped counting
                # overlaps that lay beyond it.
                if other_start >= end and gap >= ceiling:
                    break
                if other_note == note:
                    continue
                if end > other_start:
                    genuine += 1        # already overlapping as written
                elif gap < ceiling:
                    ceiling = gap       # the floor has to stay under this
    lengths.sort()
    return {
        "ceiling_ms": None if ceiling == float("inf") else ceiling * 1000.0,
        "genuine": genuine,
        "tenth_ms": lengths[len(lengths) // 10] * 1000.0 if lengths else None,
        "notes": len(lengths),
    }


def out_of_range(song, layout: Layout, transpose: int,
                 enabled_tracks=None, enabled_channels=None) -> tuple:
    """Note-ons the layout cannot reach, split into those below and above it.

    Returns (below, above, total), counted by occurrence rather than by
    distinct pitch: what decides whether an overflow matters is how often you
    hear it, not how many different notes are involved.

    Notes falling in a gap *inside* the range are in neither count. Folding
    cannot rescue those - there is no octave for them to move to - so they are
    dropped either way and have no bearing on the choice between the two.
    """
    low, high = layout.low, layout.high
    playable = layout.notes
    below = above = total = 0
    for event in song.events:
        if not event.on:
            continue
        if enabled_tracks is not None and event.track not in enabled_tracks:
            continue
        if enabled_channels is not None and event.channel not in enabled_channels:
            continue
        total += 1
        note = event.note + transpose
        if note in playable:
            continue
        if note < low:
            below += 1
        elif note > high:
            above += 1
    return below, above, total


def coverage(song, layout: Layout, transpose: int, enabled_tracks=None) -> tuple:
    counts, total = _pitch_counts(song, enabled_tracks)
    if not total:
        return 0, 0
    playable = layout.notes
    hits = 0
    for note, count in counts.items():
        if note + transpose in playable:
            hits += count
    return hits, total
