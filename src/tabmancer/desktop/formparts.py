"""The window's dialogs' parts: one of a few words picked as pills (Choice, as the terminal's dialogs.Choice
picks), a card with a title over what it holds, and the line of keys at a dialog's foot."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import picks
from . import look

GAP = "   "  # between a key line's keys


def keys_html(keys: Iterable[tuple[str, str]]) -> str:
    """'Enter hand over   Esc cancel': each key bold, what it does muted; the gaps kept (HTML folds spaces)."""
    return look.span(GAP).join(look.span(key, f"bold {look.TEXT}") + look.span(f" {words}", look.MUTED)
                               for key, words in keys)


class Choice(QWidget):
    """One of a few words side by side, the one picked marked (● 300k  ○ 400k), as the terminal's Choice
    shows them. ←/→ (or Space) moves the mark while it has the focus, round from the last to the first;
    a click picks a word. labels: how a word is shown, where it is not shown as it is. A value not among
    the words (a 500k cap from config.json) is offered too, first, and stays picked (picks.choices): one
    swapped for the first word would be saved so. No value: the first word."""

    changed = Signal(str)

    def __init__(self, words: Iterable[str], value: str, accent: str, labels: dict[str, str] | None = None) -> None:
        super().__init__()
        self.words = picks.choices(words, value)
        self.labels = labels or {}
        self.accent = accent
        self.value = value if value in self.words else self.words[0]
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.group = QButtonGroup(self)
        for index in range(len(self.words)):
            pill = QPushButton()
            pill.setFont(look.font(9))
            pill.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the Choice has the focus, its words the mark
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(pill, index)
            row.addWidget(pill)
        row.addStretch(1)
        self.group.idClicked.connect(self.picked)
        self.draw()

    def pick(self, value: str) -> None:
        if value in self.words and value != self.value:
            self.value = value
            self.draw()
            self.changed.emit(value)

    def picked(self, index: int) -> None:
        self.setFocus()
        self.pick(self.words[index])

    def step(self, by: int) -> None:
        self.pick(self.words[(self.words.index(self.value) + by) % len(self.words)])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.step(-1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.step(1)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        self.draw()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.draw()

    def shown(self, word: str) -> str:
        return f"{'●' if word == self.value else '○'} {self.labels.get(word, word)}"

    def draw(self) -> None:
        """The picked word lit, edged in the accent while the Choice has the focus."""
        for index, word in enumerate(self.words):
            pill = self.group.button(index)
            pill.setText(self.shown(word))
            if word == self.value:
                edge = self.accent if self.hasFocus() else look.DIM
                pill.setStyleSheet(f"color:{look.TEXT}; background:{look.PANEL}; border:1px solid {edge};"
                                   "border-radius:9px; padding:2px 10px;")
            else:
                pill.setStyleSheet(f"color:{look.MUTED}; background:transparent; border:1px solid {look.FAINT};"
                                   "border-radius:9px; padding:2px 10px;")


class Card(QFrame):
    """A titled box: its title (and a muted word on what it shows) over what it holds (add)."""

    def __init__(self, title: str, about: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(f"QFrame#card {{ background:{look.BLACK}; border:1px solid {look.BORDER}; border-radius:6px; }}")
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(10, 8, 10, 10)
        self.box.setSpacing(6)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setFont(look.font(9.5))
        self.title.setStyleSheet("background:transparent; border:none;")
        self.box.addWidget(self.title)
        self.set_title(title, about)

    def set_title(self, title: str, about: str = "") -> None:
        self.title.setText(look.span(title, f"bold {look.TEXT}") + (look.span(f"  {about}", look.MUTED) if about else ""))

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.box.addWidget(widget, stretch)
        return widget
