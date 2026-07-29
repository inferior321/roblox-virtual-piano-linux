"""Custom widgets: the keyboard strip and the mapping editor."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .layouts import KeyStroke, Layout, note_name

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
