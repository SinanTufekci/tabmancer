"""h in the window: how a session's handover is written, asked as the terminal dashboard's h dialog asks it
(handoverview), in the same words (handoverwords: the ways and what handing over does; sessionwords: how
full its context is). The dialog only asks: the answer goes to the window's engine (Engine.start_handover),
which hands it over now, or once it finishes when it is working."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .. import handoverwords, sessionwords
from ..sessions import Session, _shorten
from . import look

HINT = (("Enter", "hand over"), ("↑↓", "the other way"), ("Esc", "cancel"))
TITLE_CHARS = 50  # the session's title in the heading, clipped as the terminal's dialog clips it


def parts_html(parts: list[tuple[str, str]]) -> str:
    """(words, style) parts, as sessionwords gives them, as HTML for a QLabel."""
    return "".join(look.span(words, style) for words, style in parts)


def way_html(way: handoverwords.Way) -> str:
    return (look.span(way.title, f"bold {look.TEXT}") + look.span(f"  {way.why}", look.MUTED) + "<br>"
            + look.span(f"  {way.how}", look.MUTED))


def label(html: str, wrap: bool = False) -> QLabel:
    made = QLabel(html)
    made.setTextFormat(Qt.TextFormat.RichText)
    made.setFont(look.font(9.5))
    made.setWordWrap(wrap)
    made.setStyleSheet("background:transparent;")
    return made


class HandoverDialog(QDialog):
    """Enter (or a double click) on a way closes it and calls then with the way's id ("self": the session
    writes it; "transcript": Claude writes it from the transcript); Esc closes it, then never called.
    Window-modal and never waited on (open(), not exec()): the window goes on reading meanwhile."""

    def __init__(self, parent: QWidget, session: Session, then: Callable[[str], None], accent: str) -> None:
        super().__init__(parent)
        self.then = then
        self.answered = False
        self.setWindowTitle(f"Hand over · {session.project}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(640)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(look.span("Hand over  ", f"bold {look.TEXT}") + look.span(session.project, f"bold {look.CYAN}")
                            + look.span(f"  “{_shorten(session.title, TITLE_CHARS)}”", look.TEXT)))
        box.addWidget(label(look.span("Context ", look.MUTED) + parts_html(sessionwords.fill_parts(session))))
        self.about = label(look.span(handoverwords.about(session), look.MUTED), wrap=True)
        box.addWidget(self.about)
        self.ways = QListWidget()
        self.ways.setFont(look.font(9.5))
        for way in handoverwords.ways(session):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, way.id)
            shown = label(way_html(way))
            shown.setContentsMargins(6, 5, 6, 5)
            item.setSizeHint(shown.sizeHint())
            self.ways.addItem(item)
            self.ways.setItemWidget(item, shown)
        self.ways.setCurrentRow(0)
        self.ways.setFixedHeight(sum(self.ways.sizeHintForRow(row) for row in range(self.ways.count())) + 6)
        self.ways.itemActivated.connect(self.choose)  # Enter, or a double click
        box.addWidget(self.ways)
        box.addWidget(label(look.span("   ").join(look.span(key, f"bold {look.TEXT}") + look.span(f" {words}", look.MUTED)
                                                  for key, words in HINT)))  # the gaps kept: HTML folds spaces
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QListWidget {{ background:{look.BLACK}; border:none; outline:none; }}"
                           f"QListWidget::item:selected {{ background:{look.PANEL}; border-left:2px solid {accent}; }}")
        self.finished.connect(self.deleteLater)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.ways.setFocus()  # ↑↓ and Enter go to the ways at once

    def ids(self) -> list[str]:
        return [self.ways.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.ways.count())]

    def choose(self, item: QListWidgetItem) -> None:
        """A way picked: the dialog closes, then the answer goes on (once, however it was picked)."""
        if self.answered:
            return
        self.answered = True
        how = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self.then(how)
