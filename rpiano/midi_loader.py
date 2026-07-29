"""Turn a .mid file into a flat, absolutely-timed list of note events.

Tempo changes are resolved up front into a tempo map so every event carries an
absolute time in seconds. That's what makes seeking and speed changes cheap:
the playhead is just a number of seconds and nothing has to be re-derived from
ticks while a song is playing.

Tracks and channels are both kept, because they aren't the same thing - one
track can carry several channels, and it's often the channel you actually want
to filter on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mido

DEFAULT_TEMPO = 500000  # microseconds per beat, i.e. 120 bpm
DRUM_CHANNEL = 9        # zero-indexed, so this is "channel 10"

GM_FAMILIES = [
    "Piano", "Chromatic percussion", "Organ", "Guitar", "Bass", "Strings",
    "Ensemble", "Brass", "Reed", "Pipe", "Synth lead", "Synth pad",
    "Synth effects", "Ethnic", "Percussive", "Sound effects",
]


def gm_name(program: int) -> str:
    if program is None:
        return ""
    return GM_FAMILIES[min(15, max(0, program // 8))]


@dataclass(frozen=True, slots=True)
class NoteEvent:
    time: float      # seconds from the start of the song
    on: bool
    note: int        # MIDI note number, before transposition
    velocity: int
    track: int
    channel: int


@dataclass(frozen=True, slots=True)
class SustainEvent:
    time: float
    value: int       # raw CC64 value, so the cutoff can be adjusted later


@dataclass
class TrackInfo:
    index: int
    name: str
    note_count: int
    low: int
    high: int
    program: int = None
    channels: set = field(default_factory=set)

    def range_text(self) -> str:
        from .layouts import note_name
        if not self.note_count:
            return "empty"
        return f"{note_name(self.low)}-{note_name(self.high)}"

    def label(self) -> str:
        bits = [self.name]
        family = gm_name(self.program)
        if family:
            bits.append(family)
        bits.append(f"{self.note_count} notes")
        bits.append(self.range_text())
        return "   ·   ".join(bits)


@dataclass
class Song:
    path: Path
    title: str
    events: list
    sustain: list
    tracks: list
    channels: list
    duration: float
    details: str

    def note_ons(self, enabled_tracks=None):
        for event in self.events:
            if not event.on:
                continue
            if enabled_tracks is not None and event.track not in enabled_tracks:
                continue
            yield event


def _build_tempo_map(mid) -> list:
    """[(absolute_tick, microseconds_per_beat)] sorted by tick."""
    tempo_map = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((tick, msg.tempo))
    tempo_map.sort(key=lambda item: item[0])
    if not tempo_map or tempo_map[0][0] != 0:
        tempo_map.insert(0, (0, DEFAULT_TEMPO))
    return tempo_map


class _TickClock:
    """Converts absolute ticks to seconds under a tempo map."""

    def __init__(self, tempo_map: list, ticks_per_beat: int):
        self.ticks_per_beat = ticks_per_beat
        self.points = []  # (tick, seconds_at_tick, tempo_from_here)
        seconds = 0.0
        for index, (tick, tempo) in enumerate(tempo_map):
            if index > 0:
                prev_tick, _, prev_tempo = self.points[-1]
                seconds += mido.tick2second(tick - prev_tick, ticks_per_beat, prev_tempo)
            self.points.append((tick, seconds, tempo))

    def seconds(self, tick: int) -> float:
        points = self.points
        if len(points) == 1:
            # The common case: a single tempo for the whole file.
            _, _, tempo = points[0]
            return mido.tick2second(tick, self.ticks_per_beat, tempo)
        lo, hi = 0, len(points) - 1
        while lo < hi:
            mid_index = (lo + hi + 1) // 2
            if points[mid_index][0] <= tick:
                lo = mid_index
            else:
                hi = mid_index - 1
        base_tick, base_seconds, tempo = points[lo]
        return base_seconds + mido.tick2second(
            tick - base_tick, self.ticks_per_beat, tempo
        )


def load_song(path, include_drums: bool = False) -> Song:
    path = Path(path)
    # clip=True clamps out-of-range data bytes to 127 instead of refusing the
    # file. A single velocity byte over 127 is enough to make a whole file
    # unreadable, because the parser cannot tell a stray data byte from the
    # start of the next command and everything after it decodes as rubbish.
    # Nothing is lost by clamping: the only bytes affected are velocities, and
    # velocity is unrepresentable on a keyboard piano, so the player ignores it.
    mid = mido.MidiFile(str(path), clip=True)
    tempo_map = _build_tempo_map(mid)
    clock = _TickClock(tempo_map, mid.ticks_per_beat or 480)

    events = []
    sustain = []
    tracks = []
    channels = set()
    time_signature = "4/4"

    for index, track in enumerate(mid.tracks):
        tick = 0
        name = ""
        program = None
        notes_seen = []
        track_channels = set()

        for msg in track:
            tick += msg.time
            if msg.type == "track_name" and not name:
                name = msg.name.strip()
                continue
            if msg.type == "time_signature":
                time_signature = f"{msg.numerator}/{msg.denominator}"
                continue
            if msg.is_meta:
                continue

            channel = getattr(msg, "channel", 0)
            if channel == DRUM_CHANNEL and not include_drums:
                continue
            if msg.type == "program_change":
                if program is None:
                    program = msg.program
                continue

            when = clock.seconds(tick)
            if msg.type == "note_on" and msg.velocity > 0:
                events.append(NoteEvent(when, True, msg.note, msg.velocity, index, channel))
                notes_seen.append(msg.note)
                track_channels.add(channel)
                channels.add(channel)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                events.append(NoteEvent(when, False, msg.note, 0, index, channel))
            elif msg.type == "control_change" and msg.control == 64:
                sustain.append(SustainEvent(when, msg.value))

        tracks.append(
            TrackInfo(
                index=index,
                name=name or f"Track {index}",
                note_count=len(notes_seen),
                low=min(notes_seen) if notes_seen else 0,
                high=max(notes_seen) if notes_seen else 0,
                program=program,
                channels=track_channels,
            )
        )

    # Note-offs sort ahead of note-ons at the same instant, so a repeated note
    # is released and struck again rather than being swallowed.
    events.sort(key=lambda e: (e.time, e.on))
    sustain.sort(key=lambda e: e.time)

    duration = max((e.time for e in events), default=0.0)
    note_total = sum(t.note_count for t in tracks)
    starting_bpm = round(mido.tempo2bpm(tempo_map[0][1]), 1)
    played = [t for t in tracks if t.note_count]

    details = "\n".join(
        [
            f"File: {path.name}",
            f"Format: {mid.type}",
            f"Tracks: {len(played)}/{len(tracks)} ({note_total} notes)",
            f"Tempo: {starting_bpm} BPM ({len(tempo_map) - 1} changes)",
            f"Time signature: {time_signature}",
            f"Channels: {', '.join(str(c + 1) for c in sorted(channels)) or 'none'}",
            f"Sustain events: {len(sustain)}",
            f"Length: {format_time(duration)}",
        ]
    )

    return Song(
        path=path,
        title=path.stem.replace("_", " ").strip(),
        events=events,
        sustain=sustain,
        tracks=tracks,
        channels=sorted(channels),
        duration=duration,
        details=details,
    )


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
