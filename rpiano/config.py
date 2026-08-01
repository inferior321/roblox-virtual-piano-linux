"""Settings that survive a restart.

Everything lands in ~/.config/roblox-piano-linux/. Deleting the venv leaves
this behind on purpose - it's a few kilobytes of preferences plus your custom
key mappings, which you probably don't want to lose on a reinstall. The README
says how to remove it if you do.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "roblox-piano-linux"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
LAYOUT_DIR = CONFIG_DIR / "layouts"


@dataclass
class AppConfig:
    midi_folder: str = str(Path.home() / "Music" / "midi")
    last_file: str = ""
    # Both layouts are checked note for note against a working MIDI++ config,
    # so the wider one is the default: it reaches every note a file can hold,
    # and its middle five octaves are the 61-key layout unchanged. Games that
    # cannot take the ctrl-modified octaves - a piano in a browser, where ctrl
    # is the browser's - want the 61-key layout instead.
    layout: str = "roblox_88"
    backend: str = "uinput"

    transpose: int = 0
    auto_transpose: bool = True
    speed: float = 1.0
    hold_notes: bool = True
    tap_ms: int = 40
    fold_out_of_range: bool = True
    max_held_keys: int = 0
    start_delay: float = 3.0
    include_drums: bool = False

    # Play the song again when it reaches the end, after this long. Off by
    # default. The wait replaces the count-in rather than adding to it: the
    # count-in is there to give you time to reach the game, and by the time a
    # song has played through you are already there.
    loop_song: bool = False
    loop_delay: float = 3.0
    skip_seconds: int = 10

    # Which key pedals, and whether a pedal is used at all. Kept apart so
    # turning the pedal off remembers the key for when it comes back on.
    # Defaults to on, so an existing settings file keeps the pedal it had, and
    # to space, which is what the 88-key pianos pedal with - the same pedal
    # switching to that layout by hand sets, so a fresh install is not left
    # ticked with no key behind it.
    sustain_key: str = " "
    sustain_enabled: bool = True
    sustain_cutoff: int = 64

    # Timing, milliseconds. Tight values, arrived at by trying them rather than
    # by reasoning from a frame rate - in practice the frame rate turned out not
    # to predict how well the piano plays. Raise them if notes go missing.
    modifier_dwell_ms: int = 5
    min_note_ms: int = 8
    retrigger_gap_ms: int = 4
    batch_window_ms: int = 8

    # The Humanizer. Off by default, and off plays the file exactly as written.
    # The numbers are what it uses once switched on, so ticking the box does
    # something audible straight away rather than nothing at all.
    humanize: bool = False
    humanize_timing_ms: int = 18
    humanize_length_ms: int = 12
    humanize_roll_ms: int = 20
    humanize_drift: str = "steady"
    humanize_rate: int = 150
    humanize_slip: bool = True
    humanize_miss: bool = True
    humanize_brush: bool = True
    humanize_double: bool = True
    humanize_repeatable: bool = True
    humanize_seed: int = 1

    # Clock on the right of the seek bar: total length, or time remaining.
    show_remaining: bool = False

    # The audio-preview backend. The soundfont is whichever file you point at;
    # nothing is bundled. Its presets and a size/mtime stamp are cached so the
    # dropdown fills at startup without loading the file again - swap a
    # different soundfont in at the same path and the stamp no longer matches,
    # which is what forces a re-read.
    soundfont_path: str = ""
    soundfont_stamp: str = ""
    soundfont_presets: list = field(default_factory=list)
    soundfont_bank: int = 0
    soundfont_program: int = 0
    preview_volume: int = 40

    # Watch the focused window while a song plays, and pause when it stops
    # being the one the count-in ended on. uinput only: it is a keyboard, and
    # what a keyboard types goes wherever the focus is.
    #
    # On by default. The accident it prevents - a song typed into a chat box,
    # or into this program's own search field - costs more than the setting
    # does, and it is inert wherever it cannot help: the other backends send
    # nothing to the system, and a machine that cannot say what has the focus
    # says so in the Log and plays anyway.
    lock_window: bool = True

    always_on_top: bool = False
    opacity: int = 100

    hotkey_playpause: str = "<f1>"
    hotkey_stop: str = "<f2>"
    hotkey_down: str = "<f3>"
    hotkey_up: str = "<f4>"
    hotkey_restart: str = "<f5>"
    hotkey_back: str = "<f6>"
    hotkey_forward: str = "<f7>"
    hotkeys_enabled: bool = True

    window: list = field(default_factory=list)

    @staticmethod
    def load() -> "AppConfig":
        if not SETTINGS_PATH.exists():
            return AppConfig()
        try:
            data = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return AppConfig()
        config = AppConfig()
        for key, value in data.items():
            if hasattr(config, key):
                setattr(config, key, value)
        return config

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2))
        except OSError:
            pass
