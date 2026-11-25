"""Closing the window with handovers queued in it (h on sessions at work, handed over once they finish):
they are this window's, and nothing hands them over once it is closed, so it asks first, in handoverwords'
words. A handover already being written is not asked about: the window waits for it (MainWindow.closeEvent)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import handoverwords
from . import look
from .formparts import keys_html
from .handover import label

HINT = (("Enter", "keep it open"), ("Tab", "the other button"), ("Esc", "keep it open"))


class CloseDialog(QDialog):
    """The handovers queued here, by name. Keep it open (picked: Enter, Esc, or a click) rejects; Close anyway
    accepts, for the window to close after all. Window-modal and never waited on (open(), not exec())."""

    def __init__(self, parent: QWidget, names: list[str], accent: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Close tabmancer?")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.question = label(look.span(handoverwords.queued_on_closing(len(names)), f"bold {look.TEXT}"), wrap=True)
        box.addWidget(self.question)
        self.names = label("<br>".join(look.span(f"  {name}", look.CYAN) for name in names))
        box.addWidget(self.names)
        box.addWidget(label(look.span(handoverwords.QUEUED_WHY, look.MUTED), wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        self.keep = QPushButton("Keep it open")
        self.leave = QPushButton("Close anyway")
        for button in (self.keep, self.leave):
            button.setFont(look.font(9.5))
            row.addWidget(button)
        self.keep.setDefault(True)
        self.keep.clicked.connect(self.reject)
        self.leave.clicked.connect(self.accept)
        box.addLayout(row)
        box.addWidget(label(keys_html(HINT)))
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QPushButton {{ color:{look.TEXT}; background:{look.PANEL}; border:1px solid {look.BORDER};"
                           " border-radius:4px; padding:4px 14px; }"
                           f"QPushButton:focus {{ border-color:{accent}; }}")
        self.finished.connect(self.deleteLater)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.keep.setFocus()  # the safe one: Enter keeps the window, and its handovers, as they are
