""", in the window: which CLIs' sessions the window lists, and which of them is the main one, as the terminal
dashboard's , dialog picks them (CliSettingsDialog: a heading, a row for each CLI, the main CLI and the
keys). What it says and what its keys do are clis.py's (Picked, Saved and the dialog's words): one copy with
the terminal's. Nothing is saved till Enter.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QVBoxLayout, QWidget

from .. import clis
from . import look
from .formparts import keys_html
from .handover import label

TITLE = "CLI settings"  # the dialog's window title


def parts_html(parts: list[clis.Part]) -> str:
    """clis' (words, style) parts as HTML for a QLabel, a line break kept."""
    return "".join(look.styled(words, style) for words, style in parts)


class Row(QLabel):
    """A CLI's row: a click turns it on or off (clicked: its place in the list)."""

    clicked = Signal(int)

    def __init__(self, at: int) -> None:
        super().__init__()
        self.at = at
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFont(look.font(9.5))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.at)
            event.accept()
        else:
            super().mousePressEvent(event)


class CliSettingsDialog(QDialog):
    """↑↓ pick a CLI, Space (or a click) turns it on or off, m picks the main CLI, Enter saves (saved, and it
    closes), Esc leaves things as they were. Window-modal, never waited on."""

    saved = Signal(object)  # what Enter came to (clis.Saved)

    def __init__(self, parent: QWidget, accent: str) -> None:
        super().__init__(parent)
        self.picked = clis.Picked.saved()
        self.names = list(clis.CLIS)
        self.at = 0
        self.accent = accent
        self.answered = False
        self.setWindowTitle(TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(680)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the keys are the dialog's: nothing in it takes the focus
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(look.span(clis.SETTINGS_TITLE, f"bold {look.TEXT}")))
        box.addWidget(label(look.span(clis.SETTINGS_ABOUT, look.MUTED), wrap=True))
        listed = QFrame()
        listed.setObjectName("listed")
        rows = QVBoxLayout(listed)
        rows.setContentsMargins(0, 4, 0, 4)
        rows.setSpacing(2)
        self.rows: list[Row] = []
        for at in range(len(self.names)):
            row = Row(at)
            row.clicked.connect(self.click)
            rows.addWidget(row)
            self.rows.append(row)
        box.addWidget(listed)
        self.main = label("", wrap=True)
        box.addWidget(self.main)
        self.keys = label(keys_html(clis.SETTINGS_KEYS))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QFrame#listed {{ background:{look.BLACK}; border:none; }}")
        self.finished.connect(self.deleteLater)
        self.draw()

    def draw(self) -> None:
        """Each CLI's row as the terminal's dialog words it, the picked one lit; the main CLI below."""
        for at, (name, row) in enumerate(zip(self.names, self.rows, strict=True)):
            row.setText(parts_html(clis.option_parts(name, name in self.picked.on)))
            edge, ground = (self.accent, look.PANEL) if at == self.at else ("transparent", "transparent")
            row.setStyleSheet(f"background:{ground}; border-left:2px solid {edge}; padding:4px 10px;")
        self.main.setText(parts_html(clis.main_parts(self.picked.main)))

    def click(self, at: int) -> None:
        self.at = at
        self.toggle()

    def toggle(self) -> None:
        self.picked.toggle(self.names[self.at])
        self.draw()

    def step(self, by: int) -> None:
        self.at = (self.at + by) % len(self.names)
        self.draw()

    def turn_main(self) -> None:
        self.picked.turn_main()
        self.draw()

    def save(self) -> None:
        if self.answered:
            return
        self.answered = True
        saved = self.picked.save()
        self.accept()
        self.saved.emit(saved)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.step(-1)
        elif key == Qt.Key.Key_Down:
            self.step(1)
        elif key == Qt.Key.Key_Space:
            self.toggle()
        elif key == Qt.Key.Key_M and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.turn_main()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.save()
        else:
            super().keyPressEvent(event)  # Esc: rejected, nothing saved
