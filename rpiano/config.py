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
    # The 61-key layout is the verified one, so it is the default.
    layout: str = "roblox_61"
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
    skip_seconds: int = 10

    # Which key pedals, and whether a pedal is used at all. Kept apart so
    # turning the pedal off remembers the key for when it comes back on.
    # Defaults to on, so an existing settings file keeps the pedal it had.
    sustain_key: str = ""
    sustain_enabled: bool = True
    sustain_cutoff: int = 64

    # Timing, milliseconds. Defaults assume roughly 60fps in game.
    modifier_dwell_ms: int = 20
    min_note_ms: int = 35
    retrigger_gap_ms: int = 20
    batch_window_ms: int = 8

    # Clock on the right of the seek bar: total length, or time remaining.
    show_remaining: bool = False

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
