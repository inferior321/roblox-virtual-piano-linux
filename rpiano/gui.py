"""The main window."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QDir, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont

try:  # Qt 6 moved this out of QtWidgets
    from PyQt6.QtGui import QFileSystemModel
except ImportError:  # pragma: no cover
    from PyQt6.QtWidgets import QFileSystemModel

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, theme
from .backends import BACKENDS, BackendError, UinputBackend, XdotoolBackend, make_backend
from .config import LAYOUT_DIR, AppConfig
from .layouts import (
    builtin_layouts,
    import_midiplusplus_config,
    load_custom_layouts,
    save_custom_layout,
)
from .midi_loader import format_time, load_song
from .player import (
    COUNTING_IN,
    IDLE,
    PAUSED,
    PLAYING,
    Player,
    PlayerSettings,
    coverage,
    out_of_range,
    range_test,
    suggest_transpose,
    test_pattern,
)
from .widgets import (
    ClickableLabel,
    KeyboardStrip,
    MappingEditor,
    SearchResultDelegate,
)

SUSTAIN_CHOICES = [("None", ""), ("Space", " ")]


class PlayerBridge(QObject):
    """Marshals the player thread's callbacks onto the GUI thread."""

    state = pyqtSignal(str)
    progress = pyqtSignal(float, list)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    countdown = pyqtSignal(float)
    log = pyqtSignal(str, str)
    # Hotkeys arrive on pynput's listener thread. Emitting a signal is the only
    # thread-safe way back into Qt, so everything routes through here.
    hotkey = pyqtSignal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = AppConfig.load()
        self.layouts = builtin_layouts()
        self.layouts.update(load_custom_layouts(LAYOUT_DIR))
        self.song = None
        self._seeking = False
        self._hotkeys = None
        self._solo = set()
        # Set properly by _set_folder; defined here so the search cannot be
        # asked about a library that has not been scanned yet.
        self._library_root = Path.home()
        self._library_files = []
        self._library_dirs = []

        settings = PlayerSettings(
            transpose=self.config.transpose,
            speed=self.config.speed,
            hold_notes=self.config.hold_notes,
            tap_ms=self.config.tap_ms,
            fold_out_of_range=self.config.fold_out_of_range,
            max_held_keys=self.config.max_held_keys,
            start_delay=self.config.start_delay,
            sustain_key=(self.config.sustain_key
                         if self.config.sustain_enabled else ""),
            sustain_cutoff=self.config.sustain_cutoff,
            modifier_dwell_ms=self.config.modifier_dwell_ms,
            min_note_ms=self.config.min_note_ms,
            retrigger_gap_ms=self.config.retrigger_gap_ms,
            batch_window_ms=self.config.batch_window_ms,
        )
        if self.config.backend not in BACKENDS:
            self.config.backend = "uinput"
        self.player = Player(
            make_backend(self.config.backend), self._current_layout(), settings
        )

        self.bridge = PlayerBridge()
        self.player.on_state = self.bridge.state.emit
        self.player.on_progress = self.bridge.progress.emit
        self.player.on_finished = self.bridge.finished.emit
        self.player.on_error = self.bridge.error.emit
        self.player.on_countdown = self.bridge.countdown.emit
        self.player.on_log = self.bridge.log.emit
        self.bridge.state.connect(self._on_state)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.error.connect(self._on_error)
        self.bridge.countdown.connect(self._on_countdown)
        self.bridge.log.connect(self.log)
        self.bridge.hotkey.connect(self._on_hotkey)

        self.setWindowTitle(f"Roblox Piano {__version__}")
        self._build_ui()
        self._install_hotkeys()
        self._refresh_backend_status()
        self._sync_layout_view()
        self._apply_window_options()

        if self.config.window and len(self.config.window) == 4:
            self.setGeometry(*self.config.window)
        else:
            self.resize(1180, 820)

        # Version goes in the log too: it is the first thing worth knowing when
        # someone reports that something behaves differently than described.
        self.log("info", f"Roblox Piano {__version__} ready.")
        if self.config.last_file and Path(self.config.last_file).is_file():
            self._load_path(Path(self.config.last_file))

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_library())
        splitter.addWidget(self._build_main())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 880])
        self.setCentralWidget(splitter)

        self.status = self.statusBar()
        self.backend_label = QLabel("")
        self.status.addPermanentWidget(self.backend_label)
        self._update_hotkey_hint()

    def _build_library(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)

        header = QLabel("LIBRARY")
        header.setObjectName("SectionLabel")
        box.addWidget(header)

        self.folder_label = QLabel(self.config.midi_folder)
        self.folder_label.setObjectName("Subtitle")
        self.folder_label.setWordWrap(True)
        box.addWidget(self.folder_label)

        row = QHBoxLayout()
        choose = QPushButton("Choose folder")
        choose.clicked.connect(self._choose_folder)
        row.addWidget(choose)
        open_file = QPushButton("Open file")
        open_file.clicked.connect(self._open_file)
        row.addWidget(open_file)
        box.addLayout(row)

        self.fs_model = QFileSystemModel()
        self.fs_model.setNameFilters(["*.mid", "*.midi", "*.MID", "*.MIDI"])
        self.fs_model.setNameFilterDisables(False)
        self.fs_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search this folder…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setToolTip(
            "Type at least two letters to search every folder below this one.\n"
            "Clear the box to go back to browsing."
        )
        self.search_box.textChanged.connect(self._search_changed)
        box.addWidget(self.search_box)

        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        for column in range(1, 4):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._tree_activated)

        self.results = QTreeWidget()
        self.results.setHeaderHidden(True)
        self.results.setItemDelegate(SearchResultDelegate(self.results))
        # Rows differ in height - a hit is two lines, a folder one - so uniform
        # row heights must stay off or every row gets the tallest one's size.
        self.results.setUniformRowHeights(False)
        self.results.itemExpanded.connect(self._result_expanded)
        self.results.itemDoubleClicked.connect(self._result_activated)

        self.no_results = QLabel("")
        self.no_results.setObjectName("Subtitle")
        self.no_results.setWordWrap(True)
        self.no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Three ways to fill one slot: browse, results, or nothing found.
        self.library_stack = QStackedWidget()
        self.library_stack.addWidget(self.tree)
        self.library_stack.addWidget(self.results)
        self.library_stack.addWidget(self.no_results)
        box.addWidget(self.library_stack, 1)

        self.library_hint = QLabel("Double-click to load")
        self.library_hint.setObjectName("Subtitle")
        box.addWidget(self.library_hint)

        self._set_folder(self.config.midi_folder, save=False)
        return panel

    def _build_main(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setSpacing(10)

        self.title_label = QLabel("No file loaded")
        self.title_label.setObjectName("Title")
        box.addWidget(self.title_label)

        self.subtitle_label = QLabel("Pick a MIDI file from the library.")
        self.subtitle_label.setObjectName("Subtitle")
        box.addWidget(self.subtitle_label)

        transport = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setObjectName("Transport")
        self.play_button.clicked.connect(self._toggle)
        self.play_button.setEnabled(False)
        transport.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Transport")
        self.stop_button.clicked.connect(lambda: self.player.stop())
        self.stop_button.setEnabled(False)
        transport.addWidget(self.stop_button)

        self.restart_button = QPushButton("Restart")
        self.restart_button.clicked.connect(lambda: self.player.restart())
        self.restart_button.setEnabled(False)
        transport.addWidget(self.restart_button)

        self.back_button = QPushButton(f"-{self.config.skip_seconds}s")
        self.back_button.clicked.connect(lambda: self._nudge(-1))
        self.back_button.setEnabled(False)
        transport.addWidget(self.back_button)

        self.forward_button = QPushButton(f"+{self.config.skip_seconds}s")
        self.forward_button.clicked.connect(lambda: self._nudge(1))
        self.forward_button.setEnabled(False)
        transport.addWidget(self.forward_button)
        transport.addStretch(1)
        box.addLayout(transport)

        seek_row = QHBoxLayout()
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("Clock")
        seek_row.addWidget(self.elapsed_label)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        seek_row.addWidget(self.seek, 1)
        self.total_label = ClickableLabel("0:00")
        self.total_label.setObjectName("Clock")
        self.total_label.setToolTip("Click to switch between total length and time left.")
        self.total_label.clicked.connect(self._toggle_time_display)
        seek_row.addWidget(self.total_label)
        box.addLayout(seek_row)

        self.keyboard = KeyboardStrip()
        box.addWidget(self.keyboard)

        tabs = QTabWidget()
        tabs.addTab(self._build_playback_tab(), "Playback")
        tabs.addTab(self._build_timing_tab(), "Timing")
        tabs.addTab(self._build_tracks_tab(), "Tracks")
        tabs.addTab(self._build_input_tab(), "Input")
        tabs.addTab(self._build_details_tab(), "Details")
        tabs.addTab(self._build_log_tab(), "Log")
        box.addWidget(tabs, 1)
        return panel

    def _build_playback_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(9)

        self.layout_combo = QComboBox()
        self._reload_layout_combo()
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        form.addRow("Piano layout", self.layout_combo)

        self.layout_note = QLabel("")
        self.layout_note.setObjectName("Subtitle")
        self.layout_note.setWordWrap(True)
        form.addRow("", self.layout_note)

        transpose_row = QHBoxLayout()
        # Buttons either side of the box, so the two transpose hotkeys sit on
        # controls of their own like every other hotkey does.
        self.transpose_down = QPushButton("-1")
        self.transpose_down.setToolTip("Down a semitone.")
        self.transpose_down.clicked.connect(lambda: self._nudge_transpose(-1))
        transpose_row.addWidget(self.transpose_down)

        self.transpose_spin = QSpinBox()
        self.transpose_spin.setRange(-36, 36)
        self.transpose_spin.setValue(self.config.transpose)
        self.transpose_spin.setSuffix(" semitones")
        self.transpose_spin.valueChanged.connect(self._transpose_changed)
        transpose_row.addWidget(self.transpose_spin)

        self.transpose_up = QPushButton("+1")
        self.transpose_up.setToolTip("Up a semitone.")
        self.transpose_up.clicked.connect(lambda: self._nudge_transpose(1))
        transpose_row.addWidget(self.transpose_up)

        auto = QPushButton("Fit to keyboard")
        auto.setToolTip("Pick the shift that leaves the fewest notes off the piano.")
        auto.clicked.connect(self._auto_transpose)
        transpose_row.addWidget(auto)
        transpose_row.addStretch(1)
        self.transpose_label = QLabel("Transpose")
        form.addRow(self.transpose_label, transpose_row)

        self.auto_transpose_check = QCheckBox(
            "Fit automatically when a file is opened or the layout changes"
        )
        self.auto_transpose_check.setToolTip(
            "Untick to keep whatever transpose you set by hand.\n"
            "Switching layout changes which notes are reachable, so a fit made\n"
            "for the layout before is stale."
        )
        self.auto_transpose_check.setChecked(self.config.auto_transpose)
        form.addRow("", self.auto_transpose_check)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.1, 4.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setValue(self.config.speed)
        self.speed_spin.setSuffix(" x")
        self.speed_spin.valueChanged.connect(
            lambda value: setattr(self.player.settings, "speed", value)
        )
        form.addRow("Speed", self.speed_spin)

        self.hold_combo = QComboBox()
        self.hold_combo.addItems(["Hold for the note's length", "Tap briefly"])
        self.hold_combo.setCurrentIndex(0 if self.config.hold_notes else 1)
        self.hold_combo.currentIndexChanged.connect(self._hold_changed)
        form.addRow("Note length", self.hold_combo)

        self.tap_spin = QSpinBox()
        self.tap_spin.setRange(5, 500)
        self.tap_spin.setValue(self.config.tap_ms)
        self.tap_spin.setSuffix(" ms")
        self.tap_spin.setEnabled(not self.config.hold_notes)
        self.tap_spin.valueChanged.connect(
            lambda value: setattr(self.player.settings, "tap_ms", value)
        )
        form.addRow("Tap length", self.tap_spin)

        self.fold_check = QCheckBox("Move notes outside the range into the nearest octave")
        self.fold_check.setChecked(self.config.fold_out_of_range)
        self.fold_check.toggled.connect(self._fold_changed)
        form.addRow("Out of range", self.fold_check)

        self.fold_note = QLabel("")
        self.fold_note.setObjectName("Subtitle")
        self.fold_note.setWordWrap(True)
        form.addRow("", self.fold_note)
        # _update_subtitle drives this from here on, but it bails out with no
        # song loaded, so the label needs its opening line set here.
        self._update_fold_recommendation()

        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 30.0)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setValue(self.config.start_delay)
        self.delay_spin.setSuffix(" s")
        self.delay_spin.setToolTip("Time to switch to Roblox after pressing Play.")
        self.delay_spin.valueChanged.connect(
            lambda value: setattr(self.player.settings, "start_delay", value)
        )
        form.addRow("Count-in", self.delay_spin)

        self.skip_spin = QSpinBox()
        self.skip_spin.setRange(1, 120)
        self.skip_spin.setValue(self.config.skip_seconds)
        self.skip_spin.setSuffix(" s")
        self.skip_spin.valueChanged.connect(self._skip_changed)
        form.addRow("Skip step", self.skip_spin)

        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFrameShape(QFrame.Shape.HLine)
        form.addRow(rule)

        self.on_top_check = QCheckBox("Keep this window above Roblox")
        self.on_top_check.setChecked(self.config.always_on_top)
        self.on_top_check.toggled.connect(self._window_options_changed)
        form.addRow("Window", self.on_top_check)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(self.config.opacity)
        self.opacity_slider.valueChanged.connect(self._window_options_changed)
        form.addRow("Opacity", self.opacity_slider)
        return page

    def _build_timing_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        blurb = QLabel(
            "Roblox checks its input once per frame. These three values exist "
            "because of that, and the right numbers depend on the frame rate "
            "you actually get in game. If notes sound wrong, start here."
        )
        blurb.setObjectName("Subtitle")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Presets for"))
        for label, fps in (("30 fps", 30), ("60 fps", 60), ("120 fps", 120)):
            button = QPushButton(label)
            button.clicked.connect(lambda _, f=fps: self._apply_fps_preset(f))
            preset_row.addWidget(button)
        preset_row.addStretch(1)
        outer.addLayout(preset_row)

        form = QFormLayout()
        form.setSpacing(9)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(0, 200)
        self.dwell_spin.setValue(self.config.modifier_dwell_ms)
        self.dwell_spin.setSuffix(" ms")
        self.dwell_spin.setToolTip(
            "How long shift or ctrl is held either side of the key it modifies.\n"
            "Too short and black keys come out as the white key below them."
        )
        self.dwell_spin.valueChanged.connect(
            lambda v: setattr(self.player.settings, "modifier_dwell_ms", v)
        )
        form.addRow("Modifier dwell", self.dwell_spin)

        self.min_note_spin = QSpinBox()
        self.min_note_spin.setRange(0, 500)
        self.min_note_spin.setValue(self.config.min_note_ms)
        self.min_note_spin.setSuffix(" ms")
        self.min_note_spin.setToolTip(
            "Every note is held at least this long, however short it is in the\n"
            "MIDI. Too short and fast passages lose notes entirely."
        )
        self.min_note_spin.valueChanged.connect(
            lambda v: setattr(self.player.settings, "min_note_ms", v)
        )
        form.addRow("Minimum note", self.min_note_spin)

        self.retrigger_spin = QSpinBox()
        self.retrigger_spin.setRange(0, 200)
        self.retrigger_spin.setValue(self.config.retrigger_gap_ms)
        self.retrigger_spin.setSuffix(" ms")
        self.retrigger_spin.setToolTip(
            "Gap between releasing a key and striking it again.\n"
            "Too short and repeated notes only sound once."
        )
        self.retrigger_spin.valueChanged.connect(
            lambda v: setattr(self.player.settings, "retrigger_gap_ms", v)
        )
        form.addRow("Retrigger gap", self.retrigger_spin)

        self.batch_spin = QSpinBox()
        self.batch_spin.setRange(0, 60)
        self.batch_spin.setValue(self.config.batch_window_ms)
        self.batch_spin.setSuffix(" ms")
        self.batch_spin.setToolTip(
            "Notes this close together count as one chord, so they share a\n"
            "single modifier press instead of one each."
        )
        self.batch_spin.valueChanged.connect(
            lambda v: setattr(self.player.settings, "batch_window_ms", v)
        )
        form.addRow("Chord window", self.batch_spin)

        self.max_held_spin = QSpinBox()
        self.max_held_spin.setRange(0, 30)
        self.max_held_spin.setValue(self.config.max_held_keys)
        self.max_held_spin.setSpecialValueText("no limit")
        self.max_held_spin.setToolTip(
            "If sustained chords start dropping notes, cap this at 6 to 10."
        )
        self.max_held_spin.valueChanged.connect(
            lambda v: setattr(self.player.settings, "max_held_keys", v)
        )
        form.addRow("Keys held at once", self.max_held_spin)
        outer.addLayout(form)
        outer.addStretch(1)
        return page

    def _build_tracks_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        hint = QLabel(
            "Untick a part to drop it. Solo overrides everything else. Busy "
            "arrangements usually play better with the inner voices removed."
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        box.addWidget(hint)

        self.track_table = QTableWidget(0, 3)
        self.track_table.setHorizontalHeaderLabels(["PLAY", "SOLO", "PART"])
        self.track_table.verticalHeader().setVisible(False)
        header = self.track_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.track_table, 1)

        row = QHBoxLayout()
        for label, value in (("All on", True), ("All off", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _, v=value: self._set_all_tracks(v))
            row.addWidget(button)
        clear_solo = QPushButton("Clear solo")
        clear_solo.clicked.connect(self._clear_solo)
        row.addWidget(clear_solo)
        row.addStretch(1)
        self.drums_check = QCheckBox("Include the drum channel")
        self.drums_check.setChecked(self.config.include_drums)
        self.drums_check.toggled.connect(self._drums_changed)
        row.addWidget(self.drums_check)
        box.addLayout(row)

        channel_label = QLabel("CHANNELS")
        channel_label.setObjectName("SectionLabel")
        box.addWidget(channel_label)
        self.channel_holder = QWidget()
        self.channel_layout = QGridLayout(self.channel_holder)
        self.channel_layout.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.channel_holder)
        self.channel_boxes = []
        return page

    def _build_input_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(9)

        self.backend_combo = QComboBox()
        for name in BACKENDS:
            self.backend_combo.addItem(name)
        index = self.backend_combo.findText(self.config.backend)
        if index >= 0:
            self.backend_combo.setCurrentIndex(index)
        self.backend_combo.currentTextChanged.connect(self._backend_changed)
        form.addRow("Send keys via", self.backend_combo)

        self.backend_note = QLabel("")
        self.backend_note.setObjectName("Subtitle")
        self.backend_note.setWordWrap(True)
        form.addRow("", self.backend_note)

        test_row = QHBoxLayout()
        test = QPushButton("Play a test scale")
        test.setToolTip(
            "Eight naturals, a pause, then five sharps.\n"
            "If the sharps sound like white keys, raise the modifier dwell."
        )
        test.clicked.connect(self._test_input)
        test_row.addWidget(test)
        range_button = QPushButton("Range test")
        range_button.setToolTip(
            "Plays only the notes outside the standard C2-C7 range,\n"
            "for checking whether the 88-key extension works in your game."
        )
        range_button.clicked.connect(self._range_test)
        test_row.addWidget(range_button)
        test_row.addStretch(1)
        form.addRow("", test_row)

        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFrameShape(QFrame.Shape.HLine)
        form.addRow(rule)

        edit = QPushButton("Edit mapping")
        edit.clicked.connect(self._edit_mapping)
        form.addRow("Key mapping", edit)

        import_button = QPushButton("Import a MIDI++ config.json")
        import_button.clicked.connect(self._import_config)
        form.addRow("", import_button)

        self.sustain_check = QCheckBox("Use a sustain pedal")
        self.sustain_check.setToolTip(
            "The 88-key pianos pedal with space. Most 61-key pianos have no\n"
            "pedal at all, and there space makes your character jump instead.\n"
            "Switching layout sets this to whichever suits it; change it after\n"
            "if your game differs."
        )
        self.sustain_check.setChecked(self.config.sustain_enabled)
        self.sustain_check.toggled.connect(self._sustain_changed)
        form.addRow("Sustain pedal", self.sustain_check)

        sustain_row = QHBoxLayout()
        self.sustain_combo = QComboBox()
        for label, _ in SUSTAIN_CHOICES:
            self.sustain_combo.addItem(label)
        self.sustain_combo.addItem("Other key")
        self.sustain_custom = QLineEdit()
        self.sustain_custom.setMaxLength(1)
        self.sustain_custom.setFixedWidth(46)
        self._set_sustain_widgets(self.config.sustain_key)
        self.sustain_combo.currentIndexChanged.connect(self._sustain_changed)
        self.sustain_custom.textChanged.connect(self._sustain_changed)
        sustain_row.addWidget(self.sustain_combo)
        sustain_row.addWidget(self.sustain_custom)
        sustain_row.addStretch(1)
        form.addRow("", sustain_row)
        self._sustain_changed()

        self.cutoff_slider = QSlider(Qt.Orientation.Horizontal)
        self.cutoff_slider.setRange(1, 127)
        self.cutoff_slider.setValue(self.config.sustain_cutoff)
        self.cutoff_label = QLabel(str(self.config.sustain_cutoff))
        self.cutoff_label.setObjectName("Clock")
        self.cutoff_slider.valueChanged.connect(self._cutoff_changed)
        cutoff_row = QHBoxLayout()
        cutoff_row.addWidget(self.cutoff_slider, 1)
        cutoff_row.addWidget(self.cutoff_label)
        form.addRow("Sustain cutoff", cutoff_row)

        # Text is filled in by _refresh_hotkey_labels, from the actual bindings.
        self.hotkeys_check = QCheckBox("Global hotkeys")
        self.hotkeys_check.setChecked(self.config.hotkeys_enabled)
        self.hotkeys_check.toggled.connect(self._hotkeys_toggled)
        form.addRow("", self.hotkeys_check)
        return page

    def _build_details_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        self.details_view = QPlainTextEdit()
        self.details_view.setReadOnly(True)
        self.details_view.setFont(QFont("monospace"))
        self.details_view.setPlainText("No file loaded.")
        box.addWidget(self.details_view)
        return page

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setFont(QFont("monospace"))
        box.addWidget(self.log_view)
        row = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.log_view.clear)
        row.addWidget(clear)
        row.addStretch(1)
        box.addLayout(row)
        return page

    # -- logging -----------------------------------------------------------

    def log(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {level:>5}  {message}")

    # -- library -----------------------------------------------------------

    def _set_folder(self, path: str, save: bool = True) -> None:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            folder = Path.home()
        self.fs_model.setRootPath(str(folder))
        self.tree.setRootIndex(self.fs_model.index(str(folder)))
        self.folder_label.setText(str(folder))
        if save:
            self.config.midi_folder = str(folder)

        self._library_root = folder
        self._library_files, self._library_dirs, partial = self._scan_library(folder)
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.search_box.setEnabled(bool(self._library_files))
        if not self._library_files:
            self.search_box.setPlaceholderText("No songs in this folder")
        elif partial:
            self.search_box.setPlaceholderText(
                f"Search the first {len(self._library_files)} songs…")
            self.log("warning", f"{folder} is very large; the search covers the "
                                f"first {len(self._library_files)} files found.")
        else:
            self.search_box.setPlaceholderText(
                f"Search {len(self._library_files)} songs…")
        self._show_browse()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choose your MIDI folder", self.config.midi_folder
        )
        if path:
            self._set_folder(path)
            self.log("info", f"Library folder set to {path}")

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a MIDI file", self.config.midi_folder, "MIDI files (*.mid *.midi)"
        )
        if path:
            self._load_path(Path(path))

    # -- library search ----------------------------------------------------

    MIN_QUERY = 2
    SCAN_LIMIT = 20000      # files
    SCAN_SECONDS = 1.5

    def _scan_library(self, folder: Path) -> tuple:
        """Every MIDI file below `folder`, and whether the walk was cut short.

        Bounded, because the folder is not always one you chose: a missing
        configured folder falls back to your home directory, and walking that
        found a quarter of a million files without finishing - which would hang
        the window on startup. Stopping early leaves the search working over
        what was found rather than not working at all.

        Dot-directories are skipped: caches and version control hold no music
        and are where the file counts run away.
        """
        found = []
        subdirs = []
        started = time.perf_counter()
        partial = False
        for root, dirs, names in os.walk(folder, onerror=lambda _e: None):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            base = Path(root)
            subdirs.extend(base / d for d in dirs)
            for name in names:
                if name.lower().endswith((".mid", ".midi")):
                    found.append(base / name)
            if (len(found) >= self.SCAN_LIMIT
                    or time.perf_counter() - started > self.SCAN_SECONDS):
                partial = True
                break
        return sorted(found), sorted(subdirs), partial

    def _show_browse(self) -> None:
        self.library_stack.setCurrentWidget(self.tree)
        self.library_hint.setText("Double-click to load")

    def _search_changed(self, text: str) -> None:
        """Filter on every keystroke, but only once there is enough to filter on.

        A single letter matches most of a library, so below MIN_QUERY it stays
        on the browse tree rather than showing a wall of results.
        """
        query = text.strip().lower()
        if len(query) < self.MIN_QUERY:
            self._show_browse()
            return

        folders = self._outermost(
            [d for d in self._library_dirs if query in d.name.lower()]
        )
        files = [p for p in self._library_files if query in p.name.lower()]
        if not folders and not files:
            self.no_results.setText(
                f"Nothing matches “{text.strip()}”.\n\n"
                "Clear the box to go back to browsing."
            )
            self.library_stack.setCurrentWidget(self.no_results)
            self.library_hint.setText("no matches")
            return

        self._fill_results(folders, files)
        self.library_stack.setCurrentWidget(self.results)
        counts = []
        if folders:
            counts.append(f"{len(folders)} folder" + ("s" if len(folders) > 1 else ""))
        if files:
            counts.append(f"{len(files)} of {len(self._library_files)} songs")
        self.library_hint.setText("   ·   ".join(counts))

    @staticmethod
    def _outermost(folders):
        """Drop any match that sits inside another match.

        A broad query hits an ancestor and its descendants together - `a` finds
        111 of this library's folders, 89 of them nested in another hit - and
        listing all of them says the same thing many times over. The outermost
        one is the useful row: the rest are reached by opening it.
        """
        chosen = set(folders)
        return sorted(
            f for f in folders
            if not any(parent in chosen for parent in f.parents)
        )

    def _fill_results(self, folders, files) -> None:
        """Matching folders to open, then matching songs, under labelled headers."""
        self.results.clear()
        if folders:
            self.results.addTopLevelItem(self._header_item("FOLDERS"))
            for path in folders:
                self.results.addTopLevelItem(self._folder_item(path))
        if files:
            self.results.addTopLevelItem(self._header_item("SONGS"))
            for path in files:
                self.results.addTopLevelItem(self._hit_item(path))

    @staticmethod
    def _header_item(text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, SearchResultDelegate.KIND_ROLE, SearchResultDelegate.HEADER)
        # A label, not a row: nothing to select, nothing to activate.
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        return item

    @staticmethod
    def _folder_item(path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([path.name])
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        item.setData(0, SearchResultDelegate.KIND_ROLE, SearchResultDelegate.FOLDER)
        # Claim an arrow without reading the folder: contents are filled in when
        # it is actually opened, so a hundred hits cost a hundred names, not a
        # hundred directory listings.
        item.setChildIndicatorPolicy(
            QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
        )
        return item

    def _hit_item(self, path: Path) -> QTreeWidgetItem:
        folder = path.parent.relative_to(self._library_root).as_posix()
        item = QTreeWidgetItem([path.name])
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        item.setData(0, SearchResultDelegate.KIND_ROLE, SearchResultDelegate.HIT)
        item.setData(
            0, SearchResultDelegate.PATH_ROLE,
            "top level" if folder == "." else folder.replace("/", "  /  "),
        )
        return item

    def _result_expanded(self, item) -> None:
        """Fill a folder the first time it is opened."""
        if item.childCount():
            return
        stored = item.data(0, Qt.ItemDataRole.UserRole)
        if not stored:
            return
        try:
            entries = sorted(
                Path(stored).iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if not entry.name.startswith("."):
                    item.addChild(self._folder_item(entry))
            elif entry.suffix.lower() in (".mid", ".midi"):
                child = QTreeWidgetItem([entry.name])
                child.setData(0, Qt.ItemDataRole.UserRole, str(entry))
                child.setData(0, SearchResultDelegate.KIND_ROLE,
                              SearchResultDelegate.PLAIN)
                item.addChild(child)

    def _result_activated(self, item, _column: int = 0) -> None:
        kind = item.data(0, SearchResultDelegate.KIND_ROLE)
        if kind == SearchResultDelegate.FOLDER:
            item.setExpanded(not item.isExpanded())
            return
        stored = item.data(0, Qt.ItemDataRole.UserRole)
        if stored:
            self._load_path(Path(stored))

    def _tree_activated(self, index) -> None:
        path = Path(self.fs_model.filePath(index))
        if path.is_file() and path.suffix.lower() in (".mid", ".midi"):
            self._load_path(path)

    def _load_path(self, path: Path) -> None:
        self.player.stop()
        try:
            song = load_song(path, include_drums=self.config.include_drums)
        except Exception as exc:
            QMessageBox.warning(self, "Could not read that file", f"{path.name}\n\n{exc}")
            self.log("error", f"{path.name}: {exc}")
            return
        if not song.events:
            QMessageBox.information(self, "Nothing to play", f"{path.name} contains no notes.")
            return

        self.song = song
        self.config.last_file = str(path)
        self.player.load(song)
        self._solo.clear()
        self.player.settings.enabled_tracks = {t.index for t in song.tracks if t.note_count}
        self.player.settings.enabled_channels = set()
        self._populate_tracks()
        self._populate_channels()

        if self.auto_transpose_check.isChecked():
            shift, _ = suggest_transpose(
                song, self._current_layout(), self.player.settings.enabled_tracks
            )
            self.transpose_spin.setValue(shift)

        self.title_label.setText(song.title or path.name)
        self._refresh_total_label()
        self.details_view.setPlainText(song.details)
        self.seek.setValue(0)
        self.elapsed_label.setText("0:00")
        for button in (
            self.play_button, self.stop_button, self.restart_button,
            self.back_button, self.forward_button,
        ):
            button.setEnabled(True)
        self._update_subtitle()
        self.log("info", f"Loaded {path.name}")

    # -- tracks and channels -----------------------------------------------

    def _populate_tracks(self) -> None:
        rows = [t for t in self.song.tracks if t.note_count]
        self.track_table.setRowCount(len(rows))
        for row, track in enumerate(rows):
            play = QCheckBox()
            play.setChecked(True)
            play.toggled.connect(self._tracks_changed)
            self.track_table.setCellWidget(row, 0, self._centered(play))

            solo = QCheckBox()
            solo.toggled.connect(
                lambda checked, i=track.index: self._solo_changed(i, checked)
            )
            self.track_table.setCellWidget(row, 1, self._centered(solo))

            item = QTableWidgetItem(track.label())
            item.setData(Qt.ItemDataRole.UserRole, track.index)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.track_table.setItem(row, 2, item)

    @staticmethod
    def _centered(widget) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(widget)
        layout.addStretch(1)
        return holder

    def _track_index(self, row: int) -> int:
        item = self.track_table.item(row, 2)
        return item.data(Qt.ItemDataRole.UserRole) if item else -1

    def _play_box(self, row: int):
        holder = self.track_table.cellWidget(row, 0)
        return holder.findChild(QCheckBox) if holder else None

    def _solo_box(self, row: int):
        holder = self.track_table.cellWidget(row, 1)
        return holder.findChild(QCheckBox) if holder else None

    def _solo_changed(self, index: int, checked: bool) -> None:
        if checked:
            self._solo.add(index)
        else:
            self._solo.discard(index)
        self._tracks_changed()

    def _clear_solo(self) -> None:
        for row in range(self.track_table.rowCount()):
            box = self._solo_box(row)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(False)
                box.blockSignals(False)
        self._solo.clear()
        self._tracks_changed()

    def _tracks_changed(self, *_args) -> None:
        if self._solo:
            enabled = set(self._solo)
        else:
            enabled = set()
            for row in range(self.track_table.rowCount()):
                box = self._play_box(row)
                if box is not None and box.isChecked():
                    enabled.add(self._track_index(row))
        self.player.settings.enabled_tracks = enabled
        self._update_subtitle()

    def _set_all_tracks(self, on: bool) -> None:
        for row in range(self.track_table.rowCount()):
            box = self._play_box(row)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(on)
                box.blockSignals(False)
        self._tracks_changed()

    def _populate_channels(self) -> None:
        for box in self.channel_boxes:
            box.setParent(None)
        self.channel_boxes = []
        for position, channel in enumerate(self.song.channels):
            box = QCheckBox(str(channel + 1))
            box.setChecked(True)
            box.toggled.connect(self._channels_changed)
            box.setProperty("channel", channel)
            self.channel_layout.addWidget(box, position // 8, position % 8)
            self.channel_boxes.append(box)

    def _channels_changed(self, *_args) -> None:
        enabled = {
            box.property("channel") for box in self.channel_boxes if box.isChecked()
        }
        # Empty means "everything", so only narrow when something is off.
        if len(enabled) == len(self.channel_boxes):
            enabled = set()
        self.player.settings.enabled_channels = enabled
        self._update_subtitle()

    def _drums_changed(self, checked: bool) -> None:
        self.config.include_drums = checked
        if self.song is not None:
            self._load_path(self.song.path)

    # -- settings ----------------------------------------------------------

    def _current_layout(self):
        return self.layouts.get(self.config.layout) or next(iter(self.layouts.values()))

    def _reload_layout_combo(self) -> None:
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        for ident, layout in self.layouts.items():
            self.layout_combo.addItem(layout.name, ident)
        index = self.layout_combo.findData(self.config.layout)
        if index >= 0:
            self.layout_combo.setCurrentIndex(index)
        self.layout_combo.blockSignals(False)

    def _layout_changed(self) -> None:
        ident = self.layout_combo.currentData()
        if not ident:
            return
        self.config.layout = ident
        layout = self.layouts[ident]
        self.player.set_layout(layout)
        self._sync_layout_view()
        self._update_subtitle()
        self.log("info", f"Layout: {layout.name}")

        # Changing layout changes which notes are reachable, so a transpose
        # fitted to the layout before is now stale - going from 88 to 61 leaves
        # everything below C2 quietly folding an octave. That is the same
        # silent wrong the pedal used to be, so the fit is redone here on the
        # same terms as opening a file: only when the box is ticked, which is
        # where anyone who sets a transpose by hand has already opted out.
        if self.song is not None and self.auto_transpose_check.isChecked():
            self._auto_transpose()

        # The 88-key pianos pedal with space; the 61-key ones mostly have no
        # pedal, and there space is jump, so a pedal left on from the layout
        # before makes the character hop through the whole song. Each switch
        # repopulates the tick box with whatever suits the layout, and it can
        # be set back afterwards for a game that differs.
        wants_pedal = layout.high > 96
        if wants_pedal and not self._chosen_sustain_key():
            self._set_sustain_widgets(" ")
        self.sustain_check.blockSignals(True)
        self.sustain_check.setChecked(wants_pedal)
        self.sustain_check.blockSignals(False)
        self._sustain_changed()
        if wants_pedal:
            self.log("info", "Sustain pedal on for the 88-key layout.")
        else:
            self.log("info", "Sustain pedal off: most 61-key pianos have none.")

    def _sync_layout_view(self) -> None:
        layout = self._current_layout()
        self.keyboard.set_layout_range(layout, self.player.settings.transpose)
        self.layout_note.setText(layout.note_text)

    def _nudge_transpose(self, step: int) -> None:
        """Shift by a semitone. Shared by the buttons and the hotkeys, so the
        two cannot drift apart. The spin box clamps to its own range."""
        self.transpose_spin.setValue(self.transpose_spin.value() + step)

    def _transpose_changed(self, value: int) -> None:
        self.player.settings.transpose = value
        self.keyboard.set_layout_range(self._current_layout(), value)
        self._update_subtitle()

    def _auto_transpose(self) -> None:
        if self.song is None:
            return
        shift, fraction = suggest_transpose(
            self.song, self._current_layout(), self.player.settings.enabled_tracks
        )
        self.transpose_spin.setValue(shift)
        self.log("info", f"Fitted at {shift:+d} semitones, {fraction:.0%} reachable.")

    def _hold_changed(self, index: int) -> None:
        hold = index == 0
        self.player.settings.hold_notes = hold
        self.tap_spin.setEnabled(not hold)

    def _fold_changed(self, checked: bool) -> None:
        self.player.settings.fold_out_of_range = checked
        self._update_subtitle()

    def _skip_changed(self, value: int) -> None:
        self.config.skip_seconds = value
        self._refresh_hotkey_labels()

    def _apply_fps_preset(self, fps: int) -> None:
        frame = 1000.0 / fps
        self.dwell_spin.setValue(max(4, round(frame * 1.5)))
        self.min_note_spin.setValue(max(8, round(frame * 2.0)))
        self.retrigger_spin.setValue(max(4, round(frame * 1.5)))
        self.log("info", f"Timing set for {fps} fps (frame is {frame:.1f}ms).")

    def _set_sustain_widgets(self, key: str) -> None:
        self.sustain_combo.blockSignals(True)
        self.sustain_custom.blockSignals(True)
        match = next((i for i, (_, k) in enumerate(SUSTAIN_CHOICES) if k == key), None)
        if match is not None:
            self.sustain_combo.setCurrentIndex(match)
            self.sustain_custom.setText("")
            self.sustain_custom.setEnabled(False)
        else:
            self.sustain_combo.setCurrentIndex(len(SUSTAIN_CHOICES))
            self.sustain_custom.setText(key)
            self.sustain_custom.setEnabled(True)
        self.sustain_combo.blockSignals(False)
        self.sustain_custom.blockSignals(False)

    def _chosen_sustain_key(self) -> str:
        index = self.sustain_combo.currentIndex()
        if index < len(SUSTAIN_CHOICES):
            return SUSTAIN_CHOICES[index][1]
        # Deliberately no strip(): a space is a legitimate key here.
        return self.sustain_custom.text().lower()[:1]

    def _sustain_changed(self, *_args) -> None:
        """The pedal key, or whether a pedal is used at all, has changed.

        The chosen key is remembered either way, so turning the pedal off and
        on again does not lose it.
        """
        enabled = self.sustain_check.isChecked()
        custom = self.sustain_combo.currentIndex() >= len(SUSTAIN_CHOICES)
        key = self._chosen_sustain_key()
        self.config.sustain_enabled = enabled
        self.config.sustain_key = key
        self.sustain_combo.setEnabled(enabled)
        self.sustain_custom.setEnabled(enabled and custom)
        self.player.settings.sustain_key = key if enabled else ""

    def _cutoff_changed(self, value: int) -> None:
        self.player.settings.sustain_cutoff = value
        self.cutoff_label.setText(str(value))

    def _window_options_changed(self, *_args) -> None:
        self.config.always_on_top = self.on_top_check.isChecked()
        self.config.opacity = self.opacity_slider.value()
        self._apply_window_options()

    def _apply_window_options(self) -> None:
        self.setWindowOpacity(self.config.opacity / 100.0)
        on_top = self.config.always_on_top
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            self.show()

    def _backend_changed(self, name: str) -> None:
        try:
            self.player.set_backend(make_backend(name))
        except BackendError as exc:
            QMessageBox.warning(self, "Backend", str(exc))
            return
        self.config.backend = name
        self._refresh_backend_status()
        self.log("info", f"Backend: {name}")

    def _refresh_backend_status(self) -> None:
        name = self.backend_combo.currentText()
        cls = BACKENDS.get(name)
        if cls is None:
            self.backend_label.setText("no backend")
            self.backend_label.setStyleSheet(f"color: {theme.FELT};")
            return
        if cls in (UinputBackend, XdotoolBackend):
            ok, message = cls.availability()
        else:
            ok, message = True, "Nothing is sent to the system."
        self.backend_note.setText(f"{cls.description}\n{message}")
        self.backend_label.setText(f"{name}: {'ready' if ok else 'unavailable'}")
        self.backend_label.setStyleSheet(f"color: {theme.GREEN if ok else theme.FELT};")
        if not ok:
            self.log("warn", message)

    def _update_subtitle(self) -> None:
        if self.song is None:
            return
        playable, total = coverage(
            self.song,
            self._current_layout(),
            self.player.settings.transpose,
            self.player.settings.enabled_tracks,
        )
        parts = [format_time(self.song.duration), f"{total} notes"]
        if total:
            if playable == total:
                parts.append("all within range")
            else:
                action = "folded in" if self.player.settings.fold_out_of_range else "skipped"
                parts.append(f"{total - playable} out of range, {action}")
        self.subtitle_label.setText("   ·   ".join(parts))
        self._update_fold_recommendation()

    def _update_fold_recommendation(self) -> None:
        """Advise on the fold box from what this song actually overflows by.

        Folding is not free. A note pushed back into range can land on a key
        another note is already holding, and one of them loses it - so folding
        buys notes in the wrong octave at the cost of damaging notes that were
        fine. That price falls hardest on bass, which folds *up* into the
        busiest part of the keyboard, where the tune already is. Treble folds
        down into a sparser register and usually collides with nothing.

        Hence: overflowing at the bottom, drop; at the top, fold.
        """
        if self.song is None:
            self.fold_note.setText("RECOMMENDATION: N/A — no file loaded.")
            return
        below, above, total = out_of_range(
            self.song,
            self._current_layout(),
            self.player.settings.transpose,
            self.player.settings.enabled_tracks,
            self.player.settings.enabled_channels,
        )
        out = below + above
        if not total or not out:
            self.fold_note.setText(
                "RECOMMENDATION: N/A — every note is in range."
            )
            return
        share = out / total
        if share < 0.01:
            self.fold_note.setText(
                f"RECOMMENDATION: EITHER — {out} of {total} notes "
                f"({share:.1%}) out of range, too few to hear."
            )
        elif below >= above:
            self.fold_note.setText(
                f"RECOMMENDATION: OFF — {below} notes ({below / total:.0%}) "
                f"below the range. Folding jumps them into the middle of the "
                f"tune, stealing keys from notes already there."
            )
        else:
            self.fold_note.setText(
                f"RECOMMENDATION: ON — {above} notes ({above / total:.0%}) "
                f"above the range. Folding drops them an octave into a "
                f"sparser register, rarely colliding."
            )

    # -- mapping and tests -------------------------------------------------

    def _edit_mapping(self) -> None:
        dialog = MappingEditor(self._current_layout(), self)
        dialog.saved.connect(self._store_layout)
        dialog.exec()

    def _store_layout(self, layout) -> None:
        save_custom_layout(LAYOUT_DIR, layout)
        self.layouts[layout.ident] = layout
        self.config.layout = layout.ident
        self._reload_layout_combo()
        self._layout_changed()
        self.log("info", f"Saved layout {layout.name}")

    def _import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Find your MIDI++ config.json", str(Path.home()), "JSON (*.json)"
        )
        if not path:
            return
        try:
            layout = import_midiplusplus_config(Path(path))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Could not read that config",
                f"{exc}\n\nThe mapping table wasn't where this expected it. "
                "You can still build the layout by hand in Edit mapping.",
            )
            self.log("error", f"Import failed: {exc}")
            return
        save_custom_layout(LAYOUT_DIR, layout)
        self.layouts[layout.ident] = layout
        self.config.layout = layout.ident
        self._reload_layout_combo()
        self._layout_changed()
        self.log("info", f"Imported {len(layout.notes)} notes from {Path(path).name}")

    def _play_builtin(self, song, subtitle: str) -> None:
        self.player.stop()
        self.song = None
        self.player.load(song)
        self.title_label.setText(song.title)
        self.subtitle_label.setText(subtitle)
        self.details_view.setPlainText(song.details)
        self._refresh_total_label()
        for button in (self.play_button, self.stop_button, self.restart_button):
            button.setEnabled(True)
        self.player.play()

    def _test_input(self) -> None:
        self._play_builtin(
            test_pattern(self._current_layout()),
            "Naturals, then sharps. Switch to Roblox now.",
        )

    def _range_test(self) -> None:
        layout = self._current_layout()
        song = range_test(layout)
        if not song.events:
            QMessageBox.information(
                self,
                "Range test",
                "This layout has no notes outside the standard C2-C7 range, "
                "so there is nothing to test. Switch to the 88-key layout first.",
            )
            return
        self._play_builtin(
            song, "Extended notes only. The Details tab lists what each should be."
        )

    # -- transport ---------------------------------------------------------

    def _toggle(self) -> None:
        if self.player.song is None:
            return
        self.player.toggle()

    def _nudge(self, direction: int) -> None:
        self.player.nudge(direction * self.config.skip_seconds)

    def _seek_released(self) -> None:
        self._seeking = False
        if self.player.song is None:
            return
        fraction = self.seek.value() / 1000.0
        self.player.seek(fraction * self.player.song.duration)

    def _on_state(self, state: str) -> None:
        self.play_button.setText(
            self._play_labels.get(state, self._play_labels[IDLE])
        )
        live = state in (PLAYING, COUNTING_IN)
        self.play_button.setProperty("live", "true" if live else "false")
        self.play_button.style().unpolish(self.play_button)
        self.play_button.style().polish(self.play_button)
        if state == IDLE:
            # Stopping rewinds the engine to the start, so the clock and the
            # slider have to come back with it. Doing this on the state change
            # rather than on the finished signal covers pressing Stop as well,
            # which does not finish a song and so never raised that signal -
            # leaving the display frozen at the time you stopped while the
            # engine sat at zero, ready to play from the top.
            self._reset_transport_display()

    def _reset_transport_display(self) -> None:
        self.seek.setValue(0)
        self.elapsed_label.setText(format_time(0))
        self._refresh_total_label()
        self.keyboard.clear()

    def _toggle_time_display(self) -> None:
        self.config.show_remaining = not self.config.show_remaining
        self._refresh_total_label()

    def _refresh_total_label(self) -> None:
        """The right-hand clock: total length, or what is left of it.

        Counting down has to be recomputed as the playhead moves, where the
        total never changes, so this is called from the progress tick as well
        as the places that set a song up.
        """
        song = self.player.song
        if song is None:
            self.total_label.setText(format_time(0))
        elif self.config.show_remaining:
            left = max(0.0, song.duration - self.player.position)
            self.total_label.setText(f"-{format_time(left)}")
        else:
            self.total_label.setText(format_time(song.duration))

    def _on_progress(self, position: float, held: list) -> None:
        self.keyboard.set_held(held)
        self.elapsed_label.setText(format_time(position))
        if self.config.show_remaining:
            self._refresh_total_label()
        song = self.player.song
        if song and song.duration > 0 and not self._seeking:
            self.seek.setValue(int(1000 * min(1.0, position / song.duration)))

    def _on_finished(self) -> None:
        self._reset_transport_display()

    def _on_error(self, message: str) -> None:
        self.status.showMessage(message, 8000)

    def _on_countdown(self, remaining: float) -> None:
        if remaining > 0:
            self.play_button.setText(f"{remaining:.0f}")

    # -- hotkeys -----------------------------------------------------------

    def _install_hotkeys(self) -> None:
        self._remove_hotkeys()
        if not self.config.hotkeys_enabled:
            self._update_hotkey_hint()
            return
        try:
            from pynput import keyboard
        except Exception:
            self._update_hotkey_hint("pynput is not installed, so hotkeys are off.")
            return
        try:
            emit = self.bridge.hotkey.emit
            self._hotkeys = keyboard.GlobalHotKeys(
                {
                    self.config.hotkey_playpause: lambda: emit("toggle"),
                    self.config.hotkey_stop: lambda: emit("stop"),
                    self.config.hotkey_down: lambda: emit("down"),
                    self.config.hotkey_up: lambda: emit("up"),
                    self.config.hotkey_restart: lambda: emit("restart"),
                    self.config.hotkey_back: lambda: emit("back"),
                    self.config.hotkey_forward: lambda: emit("forward"),
                }
            )
            self._hotkeys.daemon = True
            self._hotkeys.start()
            self._update_hotkey_hint()
        except Exception as exc:
            self._hotkeys = None
            self._update_hotkey_hint(f"Hotkeys unavailable: {exc}")

    def _remove_hotkeys(self) -> None:
        if self._hotkeys is not None:
            try:
                self._hotkeys.stop()
            except Exception:
                pass
            self._hotkeys = None

    def _hotkeys_toggled(self, checked: bool) -> None:
        self.config.hotkeys_enabled = checked
        self._install_hotkeys()

    # Modifier and key names pynput spells in lower case, spelled the way a
    # keyboard label does.
    _KEY_NAMES = {
        "ctrl": "Ctrl", "ctrl_l": "Ctrl", "ctrl_r": "Ctrl",
        "alt": "Alt", "alt_l": "Alt", "alt_r": "Alt", "alt_gr": "AltGr",
        "shift": "Shift", "shift_l": "Shift", "shift_r": "Shift",
        "cmd": "Cmd", "space": "Space", "esc": "Esc", "tab": "Tab",
        "enter": "Enter", "backspace": "Backspace", "delete": "Delete",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    }

    def _hotkey_text(self, spec: str) -> str:
        """A pynput binding as it would read on a keyboard: '<f1>' -> 'F1'."""
        parts = []
        for piece in str(spec or "").split("+"):
            piece = piece.strip().strip("<>").lower()
            if not piece:
                continue
            name = self._KEY_NAMES.get(piece)
            if name is None:
                name = piece.upper() if len(piece) <= 2 else piece.replace(
                    "_", " ").title()
            parts.append(name)
        return "+".join(parts)

    def _hotkeys_live(self) -> bool:
        return bool(self.config.hotkeys_enabled and self._hotkeys is not None)

    def _tagged(self, base: str, spec: str) -> str:
        """`base` with its hotkey appended, when that hotkey actually works."""
        if not self._hotkeys_live():
            return base
        key = self._hotkey_text(spec)
        return f"{base}  ({key})" if key else base

    def _refresh_hotkey_labels(self) -> None:
        """Put every hotkey on the control it drives.

        Read from the config rather than written into each label, because the
        bindings are editable in settings.json and a hardcoded 'F1' would start
        lying the moment one was changed. They come off again when hotkeys are
        switched off or fail to install, so a control never advertises a key
        that does nothing.
        """
        config = self.config
        self._play_labels = {
            IDLE: self._tagged("Play", config.hotkey_playpause),
            PAUSED: self._tagged("Play", config.hotkey_playpause),
            PLAYING: self._tagged("Pause", config.hotkey_playpause),
            COUNTING_IN: self._tagged("Starting", config.hotkey_playpause),
        }
        self.play_button.setText(
            self._play_labels.get(self.player.state, self._play_labels[IDLE])
        )
        self.stop_button.setText(self._tagged("Stop", config.hotkey_stop))
        self.restart_button.setText(self._tagged("Restart", config.hotkey_restart))
        self.back_button.setText(
            self._tagged(f"-{config.skip_seconds}s", config.hotkey_back)
        )
        self.forward_button.setText(
            self._tagged(f"+{config.skip_seconds}s", config.hotkey_forward)
        )

        self.transpose_down.setText(self._tagged("-1", config.hotkey_down))
        self.transpose_up.setText(self._tagged("+1", config.hotkey_up))

        self.hotkeys_check.setText(f"Global hotkeys: {self._hotkey_summary()}")

    def _hotkey_summary(self) -> str:
        config = self.config
        pairs = [
            (self._hotkey_text(config.hotkey_playpause), "play"),
            (self._hotkey_text(config.hotkey_stop), "stop"),
            (f"{self._hotkey_text(config.hotkey_down)}/"
             f"{self._hotkey_text(config.hotkey_up)}", "transpose"),
            (self._hotkey_text(config.hotkey_restart), "restart"),
            (f"{self._hotkey_text(config.hotkey_back)}/"
             f"{self._hotkey_text(config.hotkey_forward)}", "skip"),
        ]
        return ", ".join(f"{key} {what}" for key, what in pairs if key.strip("/"))

    def _update_hotkey_hint(self, override: str = "") -> None:
        self._refresh_hotkey_labels()
        if override:
            self.status.showMessage(override)
        elif self._hotkeys_live():
            self.status.showMessage(self._hotkey_summary().replace(", ", "   "))
        else:
            self.status.showMessage("Global hotkeys are off.")

    def _on_hotkey(self, action: str) -> None:
        """Runs on the GUI thread, via the bridge signal."""
        if action == "toggle":
            self._toggle()
        elif action == "stop":
            self.player.stop()
        elif action == "restart":
            self.player.restart()
        elif action == "down":
            self._nudge_transpose(-1)
        elif action == "up":
            self._nudge_transpose(1)
        elif action == "back":
            self._nudge(-1)
        elif action == "forward":
            self._nudge(1)

    # -- shutdown ----------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.player.stop()
        self._remove_hotkeys()
        geometry = self.geometry()
        self.config.window = [
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        ]
        self.config.transpose = self.transpose_spin.value()
        self.config.auto_transpose = self.auto_transpose_check.isChecked()
        self.config.speed = self.speed_spin.value()
        self.config.hold_notes = self.hold_combo.currentIndex() == 0
        self.config.tap_ms = self.tap_spin.value()
        self.config.fold_out_of_range = self.fold_check.isChecked()
        self.config.max_held_keys = self.max_held_spin.value()
        self.config.start_delay = self.delay_spin.value()
        self.config.sustain_cutoff = self.cutoff_slider.value()
        self.config.modifier_dwell_ms = self.dwell_spin.value()
        self.config.min_note_ms = self.min_note_spin.value()
        self.config.retrigger_gap_ms = self.retrigger_spin.value()
        self.config.batch_window_ms = self.batch_spin.value()
        self.config.hotkeys_enabled = self.hotkeys_check.isChecked()
        self.config.save()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Roblox Piano")
    app.setStyleSheet(theme.STYLESHEET)
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    window = MainWindow()
    window.show()
    return app.exec()
