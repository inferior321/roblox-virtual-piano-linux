"""The main window."""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QDir, QFile, QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QKeySequence, QShortcut

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
    QInputDialog,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
from .backends import (
    BACKENDS,
    SOUNDFONT_MAGIC,
    BackendError,
    SoundfontBackend,
    UinputBackend,
    XdotoolBackend,
    make_backend,
    read_soundfont_presets,
    soundfont_available,
)
from .config import LAYOUT_DIR, AppConfig
from .humanize import (
    BUSIEST_RATE,
    DRIFTS,
    MAX_LENGTH_MS,
    MAX_ROLL_MS,
    MAX_TIMING_MS,
    QUIETEST_RATE,
)
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
    plan,
    range_test,
    suggest_transpose,
    test_pattern,
)
from .library import (
    copy_into,
    is_midi,
    trashed,
    parse_clipboard,
    rename_target,
    split_midi,
    uris_to_paths,
)
from .widgets import (
    ClickableLabel,
    LibraryResults,
    LibraryTree,
    WrappedLabel,
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
        self._reload_presets()
        self._refresh_soundfont_state()
        self._configure_preview()
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

        self.tree = LibraryTree()
        self.tree.setModel(self.fs_model)
        for column in range(1, 4):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self._tree_activated)

        self.results = LibraryResults()
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

        self._wire_file_management()
        self._set_folder(self.config.midi_folder, save=False)
        return panel

    # -- managing the files, not just listing them -------------------------

    def _wire_file_management(self) -> None:
        """Delete, rename, drop and paste, on both ways of showing a song.

        Every shortcut is bound to the view rather than to the window. Delete
        on the window would fire while you were editing the search box, and
        take the song behind it.
        """
        for view in (self.tree, self.results):
            view.filesDropped.connect(self._files_dropped)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, v=view: self._library_menu(v, pos)
            )
            remove = QShortcut(QKeySequence.StandardKey.Delete, view)
            remove.setContext(Qt.ShortcutContext.WidgetShortcut)
            remove.activated.connect(self._delete_selected)
            paste = QShortcut(QKeySequence.StandardKey.Paste, view)
            paste.setContext(Qt.ShortcutContext.WidgetShortcut)
            paste.activated.connect(self._paste_into_selected)

    def _selected_song(self) -> Path:
        """The song the next action applies to, or None if a row is not one."""
        if self.library_stack.currentWidget() is self.tree:
            index = self.tree.currentIndex()
            if not index.isValid():
                return None
            path = Path(self.fs_model.filePath(index))
            return path if path.is_file() and is_midi(path) else None
        item = self.results.currentItem()
        if item is None:
            return None
        stored = item.data(0, Qt.ItemDataRole.UserRole)
        if not stored:
            return None
        path = Path(stored)
        return path if path.is_file() and is_midi(path) else None

    def _selected_folder(self) -> Path:
        """Where a paste would go: the folder chosen, or the one holding it."""
        if self.library_stack.currentWidget() is self.tree:
            index = self.tree.currentIndex()
            path = (Path(self.fs_model.filePath(index)) if index.isValid()
                    else self._library_root)
        else:
            item = self.results.currentItem()
            stored = item.data(0, Qt.ItemDataRole.UserRole) if item else None
            if not stored:
                return None
            path = Path(stored)
        return path if path.is_dir() else path.parent

    def _refresh_library(self) -> None:
        """Read the library again after something on disk has changed.

        The whole thing rather than a patch to the list: it takes about thirty
        milliseconds on a library this size, and there is no version of this
        that is wrong. The browse tree looks after itself - QFileSystemModel
        watches the folders it is showing - but the search list is a scan taken
        once, and would otherwise go on offering songs that are not there.
        """
        self._library_files, self._library_dirs, _partial = self._scan_library(
            self._library_root
        )
        self.search_box.setEnabled(bool(self._library_files))
        text = self.search_box.text()
        if len(text.strip()) >= self.MIN_QUERY:
            self._search_changed(text)

    def _library_menu(self, view, pos) -> None:
        # Right-clicking a row acts on that row, the way it does everywhere
        # else, so the click takes the selection with it before the menu asks
        # what is selected.
        if view is self.tree:
            index = view.indexAt(pos)
            if index.isValid():
                view.setCurrentIndex(index)
        else:
            item = view.itemAt(pos)
            if item is not None:
                view.setCurrentItem(item)
        song = self._selected_song()
        if song is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename…", self._rename_selected)
        menu.addAction("Delete", self._delete_selected)
        menu.addSeparator()
        menu.addAction(
            "Show in folder",
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(song.parent))
            ),
        )
        menu.exec(view.viewport().mapToGlobal(pos))

    def _rename_selected(self) -> None:
        song = self._selected_song()
        if song is None:
            return
        typed, ok = QInputDialog.getText(
            self, "Rename", "New name:", QLineEdit.EchoMode.Normal, song.stem
        )
        if not ok:
            return
        target, why = rename_target(song, typed)
        if target is None:
            if why:
                QMessageBox.warning(self, "Rename", why)
            return
        try:
            song.rename(target)
        except OSError as exc:
            QMessageBox.warning(self, "Rename", exc.strerror or str(exc))
            return
        self._file_moved(song, target)
        self.log("info", f"Renamed to {target.name}")
        self._refresh_library()

    def _delete_selected(self) -> None:
        song = self._selected_song()
        if song is None:
            return
        # Asked for even though the Trash can give it back, because Delete sits
        # one row under Rename in the menu and one key from the arrows that
        # move the selection. The cost of the question is a keystroke; the cost
        # of not asking is going to look for a song that is not there any more.
        if QMessageBox.question(
            self,
            "Delete",
            f"Move {song.name} to the Trash?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            # Answering by reflex says no. Enter and space are how a dialog you
            # did not mean to open gets dismissed.
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if not trashed(QFile.moveToTrash(str(song))):
            # A Trash is a property of the volume, and a drive formatted for
            # Windows, or mounted without room for one, simply has not got one.
            # Deleting for good is a different question, so it gets asked.
            answer = QMessageBox.question(
                self,
                "Delete",
                f"{song.name} cannot be moved to the Trash - the drive it is "
                f"on has nowhere to put it.\n\nDelete it permanently instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                song.unlink()
            except OSError as exc:
                QMessageBox.warning(self, "Delete", exc.strerror or str(exc))
                return
            self.log("warning", f"Deleted {song.name} permanently.")
        else:
            self.log("info", f"{song.name} moved to the Trash.")
        self._file_moved(song, None)
        self._refresh_library()

    def _file_moved(self, old: Path, new: Path) -> None:
        """Keep what points at a song pointing at it, or at nothing.

        A song that is playing goes on playing - its notes are already in
        memory and have nothing to do with the file any more - but everything
        that would go back to the file for it needs to know.
        """
        if self.config.last_file == str(old):
            self.config.last_file = str(new) if new else ""
        if self.song is not None and getattr(self.song, "path", None) == old:
            if new is None:
                self.log("warning",
                         "That is the song you have open. It will play to the "
                         "end, but there is nothing to load it from again.")
            else:
                self.song.path = new
                self.title_label.setText(new.stem)

    def _files_dropped(self, paths, folder: str) -> None:
        songs, rest = split_midi([Path(p) for p in paths if p])
        self._copy_in(songs, rest, Path(folder), "Dropped")

    def _paste_into_selected(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            self.log("info", "Choose a folder to paste into first.")
            return
        clip = QApplication.clipboard().mimeData()
        action, files = "", []
        if clip.hasFormat("x-special/gnome-copied-files"):
            action, files = parse_clipboard(
                bytes(clip.data("x-special/gnome-copied-files")).decode(
                    "utf-8", "replace"
                )
            )
        elif clip.hasUrls():
            files = [Path(u.toLocalFile()) for u in clip.urls() if u.isLocalFile()]
        if not files:
            self.log("info", "There are no files on the clipboard.")
            return
        songs, rest = split_midi(files)
        copied = self._copy_in(songs, rest, folder, "Pasted")
        if action == "cut" and copied:
            for source in songs:
                try:
                    source.unlink()
                except OSError as exc:
                    self.log("warning",
                             f"Copied {source.name}, but could not remove the "
                             f"original: {exc.strerror or exc}")
            self._refresh_library()

    def _copy_in(self, songs, rest, folder: Path, verb: str) -> list:
        if rest:
            names = ", ".join(p.name for p in rest[:3])
            more = f" and {len(rest) - 3} more" if len(rest) > 3 else ""
            self.log("warning",
                     f"Ignored {names}{more}: the library holds MIDI files.")
        if not songs:
            return []
        copied, failed = copy_into(songs, folder)
        for source, why in failed:
            self.log("error", f"Could not copy {source.name}: {why}")
        if copied:
            names = ", ".join(p.name for p in copied[:3])
            more = f" and {len(copied) - 3} more" if len(copied) > 3 else ""
            self.log("info", f"{verb} {names}{more} into {folder.name}.")
            self._refresh_library()
        return copied

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
        for page, name in (
            (self._build_playback_tab(), "Playback"),
            (self._build_timing_tab(), "Timing"),
            (self._build_tracks_tab(), "Tracks"),
            (self._build_input_tab(), "Input"),
            (self._build_humanizer_tab(), "Humanizer"),
            (self._build_details_tab(), "Details"),
            (self._build_log_tab(), "Log"),
        ):
            tabs.addTab(self._scrolled(page), name)
        box.addWidget(tabs, 1)
        return panel

    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        """A tab page that can be taller than the window without shoving it.

        A tab widget takes its minimum height from whichever page is showing,
        and a page taller than that minimum makes the window grow to fit it -
        which the window manager then has to find room for, so opening a tall
        tab could pick the whole window up and move it across the screen.
        Inside a scroll area a page asks the window for nothing, and whatever
        does not fit scrolls instead.
        """
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        # The page paints its own background; a viewport painting one over the
        # top of it would sit a lighter panel inside the tab.
        area.viewport().setAutoFillBackground(False)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area

    @staticmethod
    def _explain(form, text: str) -> None:
        """A line of plain English under the control it belongs to."""
        label = WrappedLabel(text)
        label.setObjectName("Subtitle")
        form.addRow("", label)

    @staticmethod
    def _rule() -> QFrame:
        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFrameShape(QFrame.Shape.HLine)
        return rule

    def _defaults_row(self, handler, what: str) -> QHBoxLayout:
        """The Restore defaults button that closes a settings tab."""
        row = QHBoxLayout()
        row.addStretch(1)
        button = QPushButton("Restore defaults")
        button.setToolTip(
            f"Put {what} back to the values a fresh install has.\n"
            "The Log says what they were, in case you want one of them back."
        )
        button.clicked.connect(handler)
        row.addWidget(button)
        return row

    def _build_playback_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(9)

        self.layout_combo = QComboBox()
        self._reload_layout_combo()
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        form.addRow("Piano layout", self.layout_combo)

        self.layout_note = WrappedLabel("")
        self.layout_note.setObjectName("Subtitle")
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

        self.fold_note = WrappedLabel("")
        self.fold_note.setObjectName("Subtitle")
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

        form.addRow(self._rule())
        form.addRow("", self._defaults_row(
            self._reset_playback, "everything on this tab"))
        return page

    def _build_timing_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        blurb = QLabel(
            "The game can miss a key that goes down and up again too quickly, "
            "so each of these holds something a little longer than the music "
            "asks. They start tight. If notes go missing or sound wrong, raise "
            "the one whose symptom matches."
        )
        blurb.setObjectName("Subtitle")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

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
        self._explain(
            form,
            "Black keys are played by holding shift along with a white key. "
            "This is how long shift is held down either side of it, so the "
            "game is certain to notice shift was there."
        )

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
        self._explain(
            form,
            "The shortest any key is ever held down. Notes written shorter "
            "than this get stretched to it, because a tap much quicker than "
            "this can pass by without the game seeing it at all."
        )

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
        self._explain(
            form,
            "When the same note is played twice in a row, the key has to come "
            "back up before it goes down again. This is the pause in between. "
            "Without it the game sees one long press instead of two notes."
        )

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
        self._explain(
            form,
            "Notes starting within this much of each other are treated as one "
            "chord and pressed as a group, which lets them share a single "
            "shift press instead of taking one each."
        )

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
        self._explain(
            form,
            "A ceiling on how many keys are held down together. Some pianos "
            "ignore keys past a certain number, so if big chords come out "
            "thin, capping this at 6 or 10 can help. Otherwise leave it."
        )
        outer.addLayout(form)
        outer.addStretch(1)
        outer.addWidget(self._rule())
        outer.addLayout(self._defaults_row(
            self._reset_timing, "all five timing values"))
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

        self.backend_note = WrappedLabel("")
        self.backend_note.setObjectName("Subtitle")
        form.addRow("", self.backend_note)

        sf_row = QHBoxLayout()
        self.soundfont_button = QPushButton("Choose soundfont")
        self.soundfont_button.setToolTip(
            "A .sf2 file of your own. Nothing is bundled, and the file stays\n"
            "where it is - only its path is remembered."
        )
        self.soundfont_button.clicked.connect(self._choose_soundfont)
        sf_row.addWidget(self.soundfont_button)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(190)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        sf_row.addWidget(self.preset_combo, 1)
        form.addRow("Soundfont", sf_row)

        self.soundfont_note = WrappedLabel("")
        self.soundfont_note.setObjectName("Subtitle")
        form.addRow("", self.soundfont_note)

        volume_row = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.config.preview_volume)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        volume_row.addWidget(self.volume_slider, 1)
        self.volume_label = QLabel(str(self.config.preview_volume))
        self.volume_label.setObjectName("Clock")
        volume_row.addWidget(self.volume_label)
        form.addRow("Preview volume", volume_row)

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

        form.addRow(self._rule())
        form.addRow("", self._defaults_row(
            self._reset_input, "the backend, pedal and hotkeys"))
        return page

    def _build_humanizer_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        blurb = QLabel(
            "A real person never plays a piece exactly as it is written. They "
            "land a fraction early or late, hold notes a little longer or "
            "shorter, and every so often their finger lands on the wrong key "
            "or misses one. This does the same. Leave it off and the song is "
            "played exactly as the file says, which is how it has always been."
        )
        blurb.setObjectName("Subtitle")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self.humanize_check = QCheckBox("Play with the looseness and mistakes below")
        self.humanize_check.setToolTip(
            "Off, the song is played exactly as the file writes it.\n"
            "This box can be ticked while a song is playing and takes effect\n"
            "from that moment; the rest wait until it is paused or stopped."
        )
        self.humanize_check.setChecked(self.config.humanize)
        self.humanize_check.toggled.connect(self._humanize_toggled)
        outer.addWidget(self.humanize_check)

        form = QFormLayout()
        form.setSpacing(9)

        # Two dials, because these are two different kinds of thing. Looseness
        # is a size and touches every note; mistakes are rare events and need a
        # how-often. One dial governing both cannot be set to a value that is
        # right for either.
        heading = QLabel("Looseness")
        heading.setObjectName("SectionLabel")
        form.addRow(heading)
        self._explain(
            form,
            "How precisely it plays. These are sizes, not chances - they "
            "touch every note, a little."
        )

        self.hz_timing_spin = QSpinBox()
        self.hz_timing_spin.setRange(0, MAX_TIMING_MS)
        self.hz_timing_spin.setValue(self.config.humanize_timing_ms)
        self.hz_timing_spin.setSuffix(" ms")
        self.hz_timing_spin.valueChanged.connect(
            lambda v: self._humanize_changed("timing_ms", "humanize_timing_ms", v)
        )
        form.addRow("Off the beat by", self.hz_timing_spin)
        self._explain(
            form,
            "How far ahead of or behind the written moment a note can land. "
            "Nobody is exactly on time; at zero, every note is."
        )

        self.hz_length_spin = QSpinBox()
        self.hz_length_spin.setRange(0, MAX_LENGTH_MS)
        self.hz_length_spin.setValue(self.config.humanize_length_ms)
        self.hz_length_spin.setSuffix(" ms")
        self.hz_length_spin.valueChanged.connect(
            lambda v: self._humanize_changed("length_ms", "humanize_length_ms", v)
        )
        form.addRow("Held for longer or shorter by", self.hz_length_spin)
        self._explain(
            form,
            "The same idea for how long a key stays down, so some notes ring "
            "on a little and others are clipped off early."
        )

        self.hz_roll_spin = QSpinBox()
        self.hz_roll_spin.setRange(0, MAX_ROLL_MS)
        self.hz_roll_spin.setValue(self.config.humanize_roll_ms)
        self.hz_roll_spin.setSuffix(" ms")
        self.hz_roll_spin.valueChanged.connect(
            lambda v: self._humanize_changed("roll_ms", "humanize_roll_ms", v)
        )
        form.addRow("Chords spread over", self.hz_roll_spin)
        self._explain(
            form,
            "When several notes are meant to sound together, five fingers "
            "never quite land at the same instant. This spreads them, which is "
            "the single most human-sounding thing on this tab."
        )

        self.hz_drift_combo = QComboBox()
        for label in ("Neither, it stays even", "Rushing ahead", "Dragging behind"):
            self.hz_drift_combo.addItem(label)
        self.hz_drift_combo.setCurrentIndex(
            DRIFTS.index(self.config.humanize_drift)
            if self.config.humanize_drift in DRIFTS else 0
        )
        self.hz_drift_combo.currentIndexChanged.connect(self._humanize_drift_changed)
        form.addRow("Leans towards", self.hz_drift_combo)
        self._explain(
            form,
            "People are not evenly wrong. Most have a habit of pushing ahead "
            "of the beat or sitting behind it, and this gives it one."
        )

        rule = self._rule()
        form.addRow(rule)

        heading = QLabel("Mistakes")
        heading.setObjectName("SectionLabel")
        form.addRow(heading)
        self._explain(
            form,
            "How often it actually gets one wrong. Rare things, so this one "
            "is a chance rather than a size."
        )

        self.hz_rate_spin = QSpinBox()
        self.hz_rate_spin.setRange(BUSIEST_RATE, QUIETEST_RATE)
        self.hz_rate_spin.setSingleStep(5)
        self.hz_rate_spin.setPrefix("1 in ")
        self.hz_rate_spin.setSuffix(" notes")
        self.hz_rate_spin.setValue(self.config.humanize_rate)
        self.hz_rate_spin.valueChanged.connect(
            lambda v: self._humanize_changed("rate", "humanize_rate", v)
        )
        form.addRow("How often", self.hz_rate_spin)

        self.humanize_note = WrappedLabel("")
        self.humanize_note.setObjectName("Subtitle")
        form.addRow("", self.humanize_note)

        self.hz_slip_check = QCheckBox("Hit the key next to the right one")
        self.hz_slip_check.setToolTip(
            "A slipped finger, so the note that sounds is a neighbour of the\n"
            "one meant - close enough to sound like a mistake, not a glitch."
        )
        self.hz_miss_check = QCheckBox("Miss a note completely")
        self.hz_miss_check.setToolTip("The key is never pressed, so nothing sounds.")
        self.hz_brush_check = QCheckBox("Brush a nearby key on the way in")
        self.hz_brush_check.setToolTip(
            "A quick unwanted note just before the right one, the way a finger\n"
            "catches its neighbour coming down."
        )
        self.hz_double_check = QCheckBox("Press the same key twice")
        self.hz_double_check.setToolTip(
            "The note is struck again partway through, as though the key\n"
            "bounced under the finger."
        )
        for check, field_name, config_name in (
            (self.hz_slip_check, "slip", "humanize_slip"),
            (self.hz_miss_check, "miss", "humanize_miss"),
            (self.hz_brush_check, "brush", "humanize_brush"),
            (self.hz_double_check, "double", "humanize_double"),
        ):
            check.setChecked(getattr(self.config, config_name))
            check.toggled.connect(
                lambda v, f=field_name, c=config_name: self._humanize_changed(f, c, v)
            )
        form.addRow("Kinds allowed", self.hz_slip_check)
        form.addRow("", self.hz_miss_check)
        form.addRow("", self.hz_brush_check)
        form.addRow("", self.hz_double_check)

        form.addRow(self._rule())

        repeat_row = QHBoxLayout()
        self.hz_repeat_check = QCheckBox("The same mistakes every time")
        self.hz_repeat_check.setToolTip(
            "On, a song goes wrong in the same places on every play, the way a\n"
            "player has the same weak spots. Off, it is different each time."
        )
        self.hz_repeat_check.setChecked(self.config.humanize_repeatable)
        self.hz_repeat_check.toggled.connect(
            lambda v: self._humanize_changed("repeatable", "humanize_repeatable", v)
        )
        repeat_row.addWidget(self.hz_repeat_check)
        self.hz_reroll = QPushButton("Reroll")
        self.hz_reroll.setToolTip("Keep the settings, but get a different set of mistakes.")
        self.hz_reroll.clicked.connect(self._humanize_reroll)
        repeat_row.addWidget(self.hz_reroll)
        repeat_row.addStretch(1)
        form.addRow("Repeatable", repeat_row)

        outer.addLayout(form)
        outer.addStretch(1)
        outer.addWidget(self._rule())
        defaults = self._defaults_row(self._reset_humanize, "everything on this tab")
        self.humanize_defaults = defaults.itemAt(defaults.count() - 1).widget()
        outer.addLayout(defaults)

        self._humanize_cache = None
        self._humanize_widgets = [
            self.hz_timing_spin, self.hz_length_spin, self.hz_roll_spin,
            self.hz_drift_combo, self.hz_rate_spin, self.hz_slip_check,
            self.hz_miss_check, self.hz_brush_check, self.hz_double_check,
            self.hz_repeat_check, self.hz_reroll,
        ]
        self._push_humanize()
        self._humanize_enable()
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
        self.player.settings.enabled_channels = None
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
        # Exactly the ticked channels. Collapsing "all ticked" to an empty set
        # used to stand in for "no filter", which meant unticking every channel
        # produced the same empty set and played everything.
        self.player.settings.enabled_channels = {
            box.property("channel") for box in self.channel_boxes if box.isChecked()
        }
        self._update_subtitle()

    def _drums_changed(self, checked: bool) -> None:
        self.config.include_drums = checked
        if self.song is not None:
            self._load_path(self.song.path)

    # -- the Humanizer -----------------------------------------------------

    def _push_humanize(self) -> None:
        """Copy the whole tab into the player's settings in one go."""
        options = self.player.settings.humanize
        options.enabled = self.config.humanize
        options.timing_ms = self.config.humanize_timing_ms
        options.length_ms = self.config.humanize_length_ms
        options.roll_ms = self.config.humanize_roll_ms
        options.drift = self.config.humanize_drift
        options.rate = self.config.humanize_rate
        options.slip = self.config.humanize_slip
        options.miss = self.config.humanize_miss
        options.brush = self.config.humanize_brush
        options.double = self.config.humanize_double
        options.repeatable = self.config.humanize_repeatable
        options.seed = self.config.humanize_seed

    def _humanize_enable(self) -> None:
        """The tick box is always live; the rest waits for the music to stop.

        Switching it on or off mid-song replans on the spot, which is one
        deliberate act and worth the moment of silence it costs. Dragging a
        spin box is not - it would replan on every step of the drag, and each
        one of those drops every key that is down. So they are locked while
        the song is running, and freed the moment it is paused or stopped.
        """
        settled = self.player.state in (IDLE, PAUSED)
        on = self.humanize_check.isChecked()
        for widget in self._humanize_widgets:
            widget.setEnabled(on and settled)
        self.humanize_defaults.setEnabled(settled)
        self._update_humanize_note()

    def _humanize_changed(self, field_name: str, config_name: str, value) -> None:
        setattr(self.config, config_name, value)
        setattr(self.player.settings.humanize, field_name, value)
        # Paused counts as mid-song: the plan the rest of it will be played
        # from is already made, so a change has to go into it now or it would
        # not be heard until the song after this one. It hands back what it
        # worked out, which is exactly what the line below the dial wants -
        # planning it twice for the same answer is a visible pause on a big
        # arrangement.
        report = self.player.replan()
        if report is not None and self.song is not None:
            self._humanize_cache = (self._humanize_key(), report)
        self._update_humanize_note()

    def _humanize_drift_changed(self, index: int) -> None:
        self._humanize_changed(
            "drift", "humanize_drift", DRIFTS[max(0, min(index, len(DRIFTS) - 1))]
        )

    def _humanize_toggled(self, checked: bool) -> None:
        live = self.player.state in (PLAYING, COUNTING_IN, PAUSED)
        rest = " Takes effect from here on." if live else ""
        self.log("info", ("Humanizer on." if checked
                          else "Humanizer off: playing exactly as written.") + rest)
        self._humanize_changed("enabled", "humanize", checked)
        self._humanize_enable()

    def _humanize_reroll(self) -> None:
        self._humanize_changed(
            "seed", "humanize_seed", random.randrange(1, 1_000_000)
        )
        self.log("info", f"Humanizer: rerolled to {self.config.humanize_seed}.")

    def _update_humanize_note(self) -> None:
        """Say what these settings mean for the song that is actually open.

        Worked out by running the real pass rather than by estimating from the
        rate, so the number is the number - mistakes it could not place, like a
        slip with no neighbouring key to slip onto, are already not counted.
        """
        if not self.config.humanize:
            self.humanize_note.setText(
                "Off. Every note lands exactly where the file puts it."
            )
            return
        if self.song is None:
            self.humanize_note.setText("Open a song and this says how many to expect.")
            return
        report = self._humanize_report()
        if not self.player.settings.humanize.kinds():
            text = ("No kinds of mistake are ticked, so it will play loose but "
                    "never wrong.")
        else:
            text = (
                f"About {report.mistakes} in this song, spread over "
                f"{format_time(self.song.duration)}, and never two closer "
                "together than a third of a second."
            )
        if self.player.state not in (IDLE, PAUSED):
            text += " Pause or stop to change these."
        self.humanize_note.setText(text)

    def _humanize_key(self) -> tuple:
        """Everything the count depends on, so a repeat can be recognised."""
        options = self.player.settings.humanize
        settings = self.player.settings
        return (
            id(self.song), self._current_layout().ident, settings.transpose,
            None if settings.enabled_tracks is None
            else tuple(sorted(settings.enabled_tracks)),
            None if settings.enabled_channels is None
            else tuple(sorted(settings.enabled_channels)),
            options.enabled, options.timing_ms, options.length_ms,
            options.roll_ms, options.drift, options.rate, options.slip,
            options.miss, options.brush, options.double, options.repeatable,
            options.seed,
        )

    def _humanize_report(self):
        """The count for the song as it stands, worked out at most once.

        The pass is a few hundred milliseconds on a large arrangement, and this
        line is refreshed by anything that touches the song - so without a
        cache, starting or pausing a song would run it again for an answer that
        cannot have changed.
        """
        key = self._humanize_key()
        if self._humanize_cache is not None and self._humanize_cache[0] == key:
            return self._humanize_cache[1]
        _, report = plan(self.song, self._current_layout(), self.player.settings)
        self._humanize_cache = (key, report)
        return report

    def _reset_humanize(self) -> None:
        fresh = AppConfig()
        self.humanize_check.setChecked(fresh.humanize)
        self.hz_timing_spin.setValue(fresh.humanize_timing_ms)
        self.hz_length_spin.setValue(fresh.humanize_length_ms)
        self.hz_roll_spin.setValue(fresh.humanize_roll_ms)
        self.hz_drift_combo.setCurrentIndex(DRIFTS.index(fresh.humanize_drift))
        self.hz_rate_spin.setValue(fresh.humanize_rate)
        self.hz_slip_check.setChecked(fresh.humanize_slip)
        self.hz_miss_check.setChecked(fresh.humanize_miss)
        self.hz_brush_check.setChecked(fresh.humanize_brush)
        self.hz_double_check.setChecked(fresh.humanize_double)
        self.hz_repeat_check.setChecked(fresh.humanize_repeatable)
        self._humanize_changed("seed", "humanize_seed", fresh.humanize_seed)
        self._humanize_enable()
        self.log(
            "info",
            f"Humanizer back to defaults: off, {fresh.humanize_timing_ms}ms off "
            f"the beat, chords over {fresh.humanize_roll_ms}ms, "
            f"1 mistake in {fresh.humanize_rate} notes.",
        )

    # -- restoring defaults ------------------------------------------------
    #
    # AppConfig() is the only place a default is written down, so a fresh one
    # is the whole answer. Setting a widget is enough to apply it: the same
    # signal a hand edit sends carries it to the player, and closeEvent saves
    # the settings file out of the widgets. Nothing here touches the soundfont
    # or a custom key mapping - those are choices that cost real work to make
    # again, so a stray click must not be able to lose them.

    def _reset_timing(self) -> None:
        fresh = AppConfig()
        for spin, value in (
            (self.dwell_spin, fresh.modifier_dwell_ms),
            (self.min_note_spin, fresh.min_note_ms),
            (self.retrigger_spin, fresh.retrigger_gap_ms),
            (self.batch_spin, fresh.batch_window_ms),
            (self.max_held_spin, fresh.max_held_keys),
        ):
            spin.setValue(value)
        self.log(
            "info",
            f"Timing back to defaults: dwell {fresh.modifier_dwell_ms}ms, "
            f"floor {fresh.min_note_ms}ms, "
            f"retrigger {fresh.retrigger_gap_ms}ms, "
            f"chord window {fresh.batch_window_ms}ms, no key limit.",
        )

    def _reset_playback(self) -> None:
        fresh = AppConfig()
        index = self.layout_combo.findData(fresh.layout)
        if index >= 0:
            # Fires _layout_changed, which sets the pedal to suit the layout
            # and, with a song open, refits the transpose.
            self.layout_combo.setCurrentIndex(index)
        self.auto_transpose_check.setChecked(fresh.auto_transpose)
        self.transpose_spin.setValue(fresh.transpose)
        self.speed_spin.setValue(fresh.speed)
        self.hold_combo.setCurrentIndex(0 if fresh.hold_notes else 1)
        self.tap_spin.setValue(fresh.tap_ms)
        self.fold_check.setChecked(fresh.fold_out_of_range)
        self.delay_spin.setValue(fresh.start_delay)
        self.skip_spin.setValue(fresh.skip_seconds)
        self.on_top_check.setChecked(fresh.always_on_top)
        self.opacity_slider.setValue(fresh.opacity)
        # A default transpose is not zero when a song is open and the box is
        # ticked - it is the fit, which is what a fresh install would show.
        # The layout may not have changed above, so this cannot be left to it.
        if self.song is not None and self.auto_transpose_check.isChecked():
            self._auto_transpose()
        self.log(
            "info",
            f"Playback back to defaults: {self._current_layout().name}, "
            f"speed {fresh.speed:g}x, count-in {fresh.start_delay:g}s, "
            f"skip {fresh.skip_seconds}s, transpose "
            f"{self.transpose_spin.value():+d}.",
        )

    def _reset_input(self) -> None:
        fresh = AppConfig()
        self.backend_combo.setCurrentText(fresh.backend)
        self.volume_slider.setValue(fresh.preview_volume)
        # The pedal has no one default: each layout has its own, and this is
        # the rule switching layout applies, so it is the one that belongs here.
        wants_pedal = self._current_layout().high > 96
        self._set_sustain_widgets(" " if wants_pedal else "")
        self.sustain_check.setChecked(wants_pedal)
        self._sustain_changed()
        self.cutoff_slider.setValue(fresh.sustain_cutoff)
        self.hotkeys_check.setChecked(fresh.hotkeys_enabled)
        self.log(
            "info",
            f"Input back to defaults: {self.backend_combo.currentText()}, "
            f"pedal {'on' if wants_pedal else 'off'}, "
            f"cutoff {fresh.sustain_cutoff}, volume {fresh.preview_volume}, "
            f"hotkeys {'on' if fresh.hotkeys_enabled else 'off'}. "
            "Your soundfont and key mapping are untouched.",
        )

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
        self._configure_preview()
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
        self._configure_preview()

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

    # -- audio preview -----------------------------------------------------

    @staticmethod
    def _soundfont_stamp(path: Path) -> str:
        """Size and modification time, so a swapped file is noticed.

        Without it, dropping a different soundfont at the same path would keep
        serving the presets cached from the old one.
        """
        info = path.stat()
        return f"{info.st_size}:{int(info.st_mtime)}"

    def _soundfont_ready(self) -> tuple:
        """(ok, message) for whether audio preview can be used right now.

        Cheap on purpose. The full read happens once, when a soundfont is
        chosen; after that this only confirms the file is still there,
        unchanged, and really is a soundfont.
        """
        ok, message = soundfont_available()
        if not ok:
            return False, message
        if not self.config.soundfont_path:
            return False, "No soundfont loaded. Choose a .sf2 file to use audio preview."
        path = Path(self.config.soundfont_path)
        if not path.is_file():
            return False, f"Soundfont missing: {path}"
        try:
            with path.open("rb") as handle:
                if handle.read(4) != SOUNDFONT_MAGIC:
                    return False, f"{path.name} is not a soundfont."
            if self._soundfont_stamp(path) != self.config.soundfont_stamp:
                return False, f"{path.name} has changed. Choose it again to re-read it."
        except OSError as exc:
            return False, f"Cannot read {path.name}: {exc}"
        if not self.config.soundfont_presets:
            return False, "No instruments cached. Choose the soundfont again."
        return True, f"{path.name}   ·   {len(self.config.soundfont_presets)} instruments"

    def _choose_soundfont(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a soundfont", self.config.soundfont_path or str(Path.home()),
            "Soundfonts (*.sf2 *.SF2)",
        )
        if not path:
            return
        chosen = Path(path)
        # The one full check: hand it to the synth and see. Slow on a large
        # file, hence the wait cursor and hence caching the result.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            presets = read_soundfont_presets(chosen)
            stamp = self._soundfont_stamp(chosen)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Soundfont", f"Could not use that file.\n\n{exc}")
            self.log("error", f"Soundfont rejected: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.config.soundfont_path = str(chosen)
        self.config.soundfont_stamp = stamp
        self.config.soundfont_presets = [list(p) for p in presets]
        self.config.soundfont_bank, self.config.soundfont_program = presets[0][0], presets[0][1]
        self.log("info", f"Soundfont loaded: {chosen.name}, "
                         f"{len(presets)} instruments.")
        self._reload_presets()
        self._refresh_soundfont_state()
        self._configure_preview()

    @staticmethod
    def _preset_key(bank, program) -> str:
        """Item data as a string.

        Not the (bank, program) tuple it wants to be: findData compares through
        QVariant, which does not match a Python tuple against a stored one, so
        looking up the saved instrument silently returned -1 and the dropdown
        fell back to the first entry. A string compares.
        """
        return f"{int(bank)}:{int(program)}"

    def _reload_presets(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for bank, program, name in self.config.soundfont_presets:
            self.preset_combo.addItem(name, self._preset_key(bank, program))
        index = self.preset_combo.findData(
            self._preset_key(self.config.soundfont_bank,
                             self.config.soundfont_program)
        )
        self.preset_combo.setCurrentIndex(max(0, index))
        self.preset_combo.blockSignals(False)
        # If the saved instrument is not in this soundfont, the box has fallen
        # back to the first entry - so bring the config with it rather than
        # leaving the setting pointing at something the display disagrees with.
        current = self.preset_combo.currentData()
        if current:
            bank, program = (int(part) for part in current.split(":"))
            self.config.soundfont_bank = bank
            self.config.soundfont_program = program

    def _preset_changed(self, _index: int) -> None:
        current = self.preset_combo.currentData()
        if not current:
            return
        chosen = tuple(int(part) for part in current.split(":"))
        self.config.soundfont_bank, self.config.soundfont_program = chosen
        backend = self.player.backend
        if isinstance(backend, SoundfontBackend):
            backend.set_soundfont(
                self.config.soundfont_path, *chosen
            )
            self.log("info", f"Preview instrument: {self.preset_combo.currentText()}")

    def _volume_changed(self, value: int) -> None:
        self.config.preview_volume = value
        self.volume_label.setText(str(value))
        backend = self.player.backend
        if isinstance(backend, SoundfontBackend):
            backend.set_gain(self._preview_gain())

    def _preview_gain(self) -> float:
        # FluidSynth gain runs 0..10 and is loud well before the top; a
        # hundredth of the slider keeps the useful range across the whole
        # travel rather than bunched at the bottom.
        return self.config.preview_volume / 100.0

    def _refresh_soundfont_state(self) -> None:
        """Grey out audio preview unless a soundfont is genuinely usable."""
        ok, message = self._soundfont_ready()
        self.soundfont_note.setText(message)
        self.preset_combo.setEnabled(ok)
        self.volume_slider.setEnabled(ok)
        self.volume_label.setEnabled(ok)

        # The entry stays listed, so it is discoverable, but cannot be picked.
        index = self.backend_combo.findText(SoundfontBackend.name)
        if index >= 0:
            model = self.backend_combo.model()
            model.item(index).setEnabled(ok)
        if not ok and self.config.backend == SoundfontBackend.name:
            # Configured but no longer usable: fall back rather than fail later.
            self.backend_combo.setCurrentText("uinput")

    def _configure_preview(self, backend=None) -> None:
        """Give the preview backend the mapping it reads keystrokes through.

        Takes a backend so one can be set up before it is handed to the
        player, which has to open it if a song is already playing - and an
        unconfigured preview backend has no soundfont to open.
        """
        if backend is None:
            backend = self.player.backend
        if isinstance(backend, SoundfontBackend):
            backend.configure(
                self._current_layout(), self.player.settings.sustain_key
            )
            try:
                # Reopens on the spot if a song is already playing, so this can
                # fail the way opening a backend can.
                backend.set_soundfont(
                    self.config.soundfont_path,
                    self.config.soundfont_bank,
                    self.config.soundfont_program,
                )
            except BackendError as exc:
                self.log("error", f"Audio preview: {exc}")
                return
            backend.set_gain(self._preview_gain())

    def _backend_changed(self, name: str) -> None:
        try:
            backend = make_backend(name)
            # Configured before the handover, not after: mid-song the player
            # opens what it is given, and a preview backend with no soundfont
            # set on it yet cannot open.
            self._configure_preview(backend)
            self.player.set_backend(backend)
        except BackendError as exc:
            QMessageBox.warning(self, "Backend", str(exc))
            # The old backend is still the one playing, so put the box back to
            # agree with it rather than naming one that was never adopted.
            self.backend_combo.blockSignals(True)
            self.backend_combo.setCurrentText(self.config.backend)
            self.backend_combo.blockSignals(False)
            self._refresh_backend_status()
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
        self._update_humanize_note()

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
        # The test patterns are a single track of their own, so they must not
        # inherit whatever selection the last real song left behind - with
        # that song's tracks unticked, the test scale would play nothing.
        self.player.settings.enabled_tracks = None
        self.player.settings.enabled_channels = None
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
        self._humanize_enable()
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
        self.config.humanize_timing_ms = self.hz_timing_spin.value()
        self.config.humanize_length_ms = self.hz_length_spin.value()
        self.config.humanize_roll_ms = self.hz_roll_spin.value()
        self.config.humanize_rate = self.hz_rate_spin.value()
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
