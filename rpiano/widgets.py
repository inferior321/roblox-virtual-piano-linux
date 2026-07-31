"""Custom widgets: the keyboard strip and the mapping editor."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from . import theme
from .layouts import KeyStroke, Layout, note_name
from .library import is_midi

WHITE_PITCH_CLASSES = (0, 2, 4, 5, 7, 9, 11)
LOWEST = 21    # A0
HIGHEST = 108  # C8


def is_white(note: int) -> bool:
    return note % 12 in WHITE_PITCH_CLASSES


# Fixed geometry, so it is worked out once at import rather than on every
# repaint. WHITE_INDEX turns the old linear search for each black key's
# neighbour into a dict lookup.
WHITE_NOTES = tuple(n for n in range(LOWEST, HIGHEST + 1) if is_white(n))
BLACK_NOTES = tuple(n for n in range(LOWEST, HIGHEST + 1) if not is_white(n))
WHITE_INDEX = {note: index for index, note in enumerate(WHITE_NOTES)}
MIDDLE_C_INDEX = WHITE_INDEX.get(60)


class _MidiDrops:
    """Takes MIDI files dragged in from a file manager, for the library views.

    Only from outside. Dragging inside the pane is not a way to move a song
    around, so nothing here starts a drag and one that started here is refused
    - a song cannot be relocated, or lost, by an accidental tug on the list.

    A drag has to be carrying at least one song to be accepted - the same rule
    the Paste menu entry follows. A cursor that says no is the answer to
    dragging a photo onto a music library, and it says it while there is still
    time to drop it somewhere else. Anything else riding along with a song is
    let through and reported in the Log rather than refusing the whole drag.
    """

    filesDropped = pyqtSignal(list, str)

    def enable_drops(self) -> None:
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)

    def folder_at(self, pos) -> str:
        """The folder a drop at this point belongs in, or "" for nowhere."""
        raise NotImplementedError

    def _wanted(self, event) -> bool:
        if event.source() is not None or not event.mimeData().hasUrls():
            return False
        return any(
            url.isLocalFile() and is_midi(url.toLocalFile())
            for url in event.mimeData().urls()
        )

    def dragEnterEvent(self, event) -> None:
        if self._wanted(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._wanted(event) and self.folder_at(event.position().toPoint()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        folder = self.folder_at(event.position().toPoint())
        if not self._wanted(event) or not folder:
            event.ignore()
            return
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.filesDropped.emit(urls, folder)
        event.acceptProposedAction()


class LibraryTree(_MidiDrops, QTreeView):
    """The browse tree, with somewhere for a dragged-in file to land."""

    filesDropped = pyqtSignal(list, str)

    def __init__(self) -> None:
        super().__init__()
        self.enable_drops()

    def folder_at(self, pos) -> str:
        index = self.indexAt(pos)
        if not index.isValid():
            # The empty space below the rows is still the folder being browsed.
            index = self.rootIndex()
            if not index.isValid():
                return ""
        path = Path(self.model().filePath(index))
        return str(path if path.is_dir() else path.parent)


class LibraryResults(_MidiDrops, QTreeWidget):
    """The search results, which are real folders and real files too."""

    filesDropped = pyqtSignal(list, str)

    def __init__(self) -> None:
        super().__init__()
        self.enable_drops()

    def folder_at(self, pos) -> str:
        item = self.itemAt(pos)
        if item is None:
            # A list of results from all over the library has no one folder,
            # so the space around them is not anywhere to put a file.
            return ""
        kind = item.data(0, SearchResultDelegate.KIND_ROLE)
        if kind == SearchResultDelegate.HEADER:
            return ""
        stored = item.data(0, Qt.ItemDataRole.UserRole)
        if not stored:
            return ""
        path = Path(stored)
        return str(path if path.is_dir() else path.parent)


class WrappedLabel(QLabel):
    """A word-wrapped label that gets the height its text actually needs.

    A layout asks a widget how tall it wants to be before the widget knows how
    wide it is going to end up, and a QLabel answers for a single line. Inside
    a form, where the width is settled by the column rather than by the text,
    that answer stops being true the moment the text wraps - and the last line
    is cut off, worse the narrower the window. Answering again once the width
    is known is the whole fix.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setWordWrap(True)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setMinimumHeight(self.heightForWidth(self.width()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setMinimumHeight(self.heightForWidth(self.width()))


class ClickableLabel(QLabel):
    """A label that reports clicks, for a readout that doubles as a switch.

    The pointing cursor is the only thing telling anyone it can be clicked, so
    it is set here rather than left to the caller to remember.
    """

    clicked = pyqtSignal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        # On release, and only if the pointer is still over the label, so a
        # click begun here and dragged away is abandoned the way a button is.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SearchResultDelegate(QStyledItemDelegate):
    """Draws the four kinds of row the search results hold.

    HIT is a file the query matched: its name, and beneath it in smaller grey
    text the folder holding it. That second line is what tells apart the
    several files in a library that share a name.

    FOLDER is a folder the query matched, and PLAIN is anything found by
    opening one. Both are a single line - inside an opened folder the path is
    already obvious from what you clicked to get there.

    HEADER labels a section and is drawn, not selected.

    A hit's path is elided from the *left*, so the folder immediately holding
    the file survives a narrow panel. That end is the one that distinguishes
    two matches; the top of the tree is the part they have in common.
    """

    PATH_ROLE = Qt.ItemDataRole.UserRole + 1
    KIND_ROLE = Qt.ItemDataRole.UserRole + 2

    HEADER = "header"
    FOLDER = "folder"
    HIT = "hit"
    PLAIN = "plain"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = QColor(theme.IVORY)
        self._path = QColor(theme.MUTED)
        self._selected = QColor(theme.AMETHYST_DIM)

    def _small(self, font):
        small = QFont(font)
        size = font.pointSizeF()
        small.setPointSizeF(max(6.0, size - 1.5) if size > 0 else 7.0)
        return small

    def sizeHint(self, option, index):
        kind = index.data(self.KIND_ROLE)
        tall = QFontMetrics(option.font).height()
        if kind == self.HIT:
            short = QFontMetrics(self._small(option.font)).height()
            return QSize(option.rect.width(), tall + short + 8)
        if kind == self.HEADER:
            return QSize(option.rect.width(), tall + 12)
        return QSize(option.rect.width(), tall + 8)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect
        kind = index.data(self.KIND_ROLE)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        left = rect.left() + 7
        width = max(10, rect.width() - 14)
        metrics = QFontMetrics(option.font)
        small_font = self._small(option.font)
        small_metrics = QFontMetrics(small_font)

        if kind == self.HEADER:
            # A label, so no selection highlight and nothing to elide against.
            painter.setFont(small_font)
            painter.setPen(QPen(self._path))
            painter.drawText(
                QRectF(left, rect.top() + 6, width, small_metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            painter.restore()
            return

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, self._selected)

        painter.setFont(option.font)
        painter.setPen(QPen(self._name))
        painter.drawText(
            QRectF(left, rect.top() + 4, width, metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, width),
        )

        if kind == self.HIT:
            painter.setFont(small_font)
            painter.setPen(QPen(self._path))
            painter.drawText(
                QRectF(left, rect.top() + 4 + metrics.height(),
                       width, small_metrics.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                small_metrics.elidedText(
                    index.data(self.PATH_ROLE) or "",
                    Qt.TextElideMode.ElideLeft, width),
            )
        painter.restore()


class KeyboardStrip(QWidget):
    """A full 88-key keyboard that lights the notes currently sounding.

    It also dims whatever falls outside the active layout's range, so you can
    see at a glance how much of a song the piano can actually reach - and,
    more usefully, spot a key that has got stuck down.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(78)
        self.setMaximumHeight(96)
        self._held = set()
        self._layout_notes = set()
        self._transpose = 0
        # Colours are fixed, so build them once rather than per repaint.
        self._ivory = QColor(theme.IVORY)
        self._ivory_dim = QColor(theme.IVORY).darker(190)
        self._ebony = QColor(theme.EBONY)
        self._ebony_dim = QColor(theme.EBONY).lighter(160)
        self._accent = QColor(theme.AMETHYST)
        self._accent_dark = QColor(theme.AMETHYST).darker(115)
        self._accent_dim = QColor(theme.AMETHYST_DIM)
        self._panel = QColor(theme.PANEL)
        self._line_pen = QPen(QColor(theme.LINE), 1)
        self._label_pen = QPen(QColor(theme.MUTED))
        self._label_font = QFont()
        self._label_font.setPointSize(7)

    def set_held(self, notes) -> None:
        held = {n + self._transpose for n in notes}
        if held != self._held:
            self._held = held
            self.update()

    def set_layout_range(self, layout: Layout, transpose: int = 0) -> None:
        self._layout_notes = set(layout.notes)
        self._transpose = transpose
        self.update()

    def clear(self) -> None:
        if self._held:
            self._held = set()
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        width = self.width()
        height = self.height()
        white_w = width / len(WHITE_NOTES)
        black_w = white_w * 0.62
        black_h = height * 0.62

        held = self._held
        in_layout = self._layout_notes
        painter.fillRect(self.rect(), self._panel)

        # White keys first, black keys drawn over them.
        painter.setPen(self._line_pen)
        for index, note in enumerate(WHITE_NOTES):
            x = index * white_w
            if note in held:
                colour = self._accent
            elif note in in_layout:
                colour = self._ivory
            else:
                colour = self._ivory_dim
            painter.fillRect(QRectF(x, 0, white_w - 1, height), colour)
            painter.drawLine(int(x), 0, int(x), height)

        for note in BLACK_NOTES:
            x = (WHITE_INDEX[note - 1] + 1) * white_w - black_w / 2
            if note in held:
                colour = self._accent_dark
            elif note in in_layout:
                colour = self._ebony
            else:
                colour = self._ebony_dim
            painter.fillRect(QRectF(x, 0, black_w, black_h), colour)

        # A single tick under middle C, the only orientation mark needed.
        if MIDDLE_C_INDEX is not None:
            x = MIDDLE_C_INDEX * white_w
            painter.fillRect(
                QRectF(x + white_w * 0.25, height - 4, white_w * 0.5, 3),
                self._accent_dim,
            )
            painter.setPen(self._label_pen)
            painter.setFont(self._label_font)
            painter.drawText(
                QRectF(x - white_w, height - 20, white_w * 3, 12),
                Qt.AlignmentFlag.AlignCenter,
                "C4",
            )
        painter.end()


class KeyCaptureDialog(QDialog):
    """Grabs one real keypress and reports it as a KeyStroke."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Press a key")
        self.stroke = None
        layout = QVBoxLayout(self)
        label = QLabel(
            "Press the key combination this note should use.\n"
            "Shift and ctrl are picked up automatically. Escape cancels."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self.setMinimumWidth(320)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (
            Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
        ):
            return
        text = event.text()
        mods = []
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            mods.append("ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            mods.append("shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            mods.append("alt")

        from .layouts import SHIFTED_PUNCTUATION

        char = text.lower() if text else ""
        if text in SHIFTED_PUNCTUATION:
            char = SHIFTED_PUNCTUATION[text]
            if "shift" not in mods:
                mods.append("shift")
        if not char or len(char) != 1:
            return
        self.stroke = KeyStroke(char, tuple(mods))
        self.accept()


class MappingEditor(QDialog):
    """Edit a layout note by note and save it as a custom layout."""

    saved = pyqtSignal(object)

    def __init__(self, layout: Layout, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit mapping")
        self.setMinimumSize(560, 620)
        self.source = layout
        self.working = dict(layout.notes)

        outer = QVBoxLayout(self)

        blurb = QLabel(
            "Each row is one note and the key that plays it. Change the key "
            "directly, or select a row and press Capture to use whatever you "
            "type next. Saving creates a new custom layout and leaves the "
            "built-in ones untouched."
        )
        blurb.setWordWrap(True)
        blurb.setObjectName("Subtitle")
        outer.addWidget(blurb)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Save as"))
        self.name_edit = QLineEdit(f"{layout.name} (edited)")
        name_row.addWidget(self.name_edit, 1)
        outer.addLayout(name_row)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["NOTE", "MIDI", "KEY", "MODIFIERS"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self.table, 1)

        self._populate()

        buttons_row = QHBoxLayout()
        capture = QPushButton("Capture key")
        capture.clicked.connect(self._capture)
        buttons_row.addWidget(capture)
        buttons_row.addStretch(1)
        outer.addLayout(buttons_row)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def _populate(self) -> None:
        notes = sorted(self.working)
        self.table.setRowCount(len(notes))
        for row, note in enumerate(notes):
            stroke = self.working[note]
            name_item = QTableWidgetItem(note_name(note))
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            midi_item = QTableWidgetItem(str(note))
            midi_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, midi_item)

            key_edit = QLineEdit(stroke.char)
            key_edit.setMaxLength(1)
            key_edit.textChanged.connect(
                lambda text, n=note: self._set_char(n, text)
            )
            self.table.setCellWidget(row, 2, key_edit)

            mods = QWidget()
            mods_layout = QHBoxLayout(mods)
            mods_layout.setContentsMargins(4, 0, 4, 0)
            for name in ("shift", "ctrl"):
                box = QCheckBox(name)
                box.setChecked(name in stroke.mods)
                box.toggled.connect(
                    lambda checked, n=note, m=name: self._set_mod(n, m, checked)
                )
                mods_layout.addWidget(box)
            mods_layout.addStretch(1)
            self.table.setCellWidget(row, 3, mods)

    def _set_char(self, note: int, text: str) -> None:
        text = text.strip().lower()
        if not text:
            return
        current = self.working[note]
        self.working[note] = KeyStroke(text, current.mods)

    def _set_mod(self, note: int, mod: str, checked: bool) -> None:
        current = self.working[note]
        mods = [m for m in current.mods if m != mod]
        if checked:
            mods.append(mod)
        order = {"ctrl": 0, "shift": 1, "alt": 2}
        mods.sort(key=lambda m: order.get(m, 9))
        self.working[note] = KeyStroke(current.char, tuple(mods))

    def _capture(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Capture key", "Select a row first.")
            return
        note = int(self.table.item(row, 1).text())
        dialog = KeyCaptureDialog(self)
        if dialog.exec() and dialog.stroke is not None:
            self.working[note] = dialog.stroke
            self._populate()
            self.table.setCurrentCell(row, 2)

    def _save(self) -> None:
        name = self.name_edit.text().strip() or "Custom layout"
        ident = "custom_" + "".join(
            c if c.isalnum() else "_" for c in name.lower()
        ).strip("_")
        duplicates = self._duplicate_strokes()
        if duplicates:
            answer = QMessageBox.warning(
                self,
                "Duplicate keys",
                f"{len(duplicates)} keys are used by more than one note "
                f"(for example {duplicates[0]}). Those notes will cut each "
                "other off when they overlap.\n\nSave anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return
        layout = Layout(
            ident=ident,
            name=name,
            notes=dict(self.working),
            verified=False,
            note_text=f"Edited from {self.source.name}.",
        )
        self.saved.emit(layout)
        self.accept()

    def _duplicate_strokes(self) -> list:
        seen = {}
        clashes = []
        for note, stroke in sorted(self.working.items()):
            label = stroke.label()
            if label in seen:
                clashes.append(f"{note_name(seen[label])} and {note_name(note)} both use {label}")
            else:
                seen[label] = note
        return clashes
