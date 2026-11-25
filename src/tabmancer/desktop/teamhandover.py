"""h twice on a team in the window: the handover its lead wrote, and the line-up it suggests for the team's
next generation, asked as the terminal dashboard's team handover dialog asks it (teamview.TeamHandoverDialog),
in the same words (handoverwords: what handing a team over does, the line-up's preview and what it still
needs, the cap's words). The dialog only asks: Ctrl+S answers (the line-up, the cap) and the window's engine
starts the new generation (Engine.team_start); Esc answers None and the team stays closed (h again shows
this again)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from .. import handoverwords, picks, teams
from ..newforms import CAP_SHORT
from . import look
from .formparts import Card, Choice, keys_html
from .handover import label, parts_html

HINT = (("Ctrl+S", "start the new team"), ("Tab", "next box"), ("←→", "the cap"),
        ("Esc", "not now: the team stays closed, h shows this again"))
CAPS = (*picks.CAPS, picks.NO_CAP)


def text_box(text: str, read_only: bool) -> QPlainTextEdit:
    """Plain text, never markup (the lead's words); Tab goes to the next box, as in the terminal."""
    box = QPlainTextEdit(text)
    box.setReadOnly(read_only)
    box.setTabChangesFocus(True)
    box.setFont(look.font(9.5))
    box.setStyleSheet(f"QPlainTextEdit {{ background:{look.BLACK}; color:{look.TEXT}; border:none; }}")
    return box


class TeamHandoverDialog(QDialog):
    """The handover (to read), the line-up (one member a line, as `tabmancer team new` takes them: yours
    to change) and the context cap the members start under. Ctrl+S calls then with (the line-up, the cap),
    unless the line-up still needs something (said in the dialog, which stays); Esc, or closing it, calls
    then with None. Once, however it closes. Window-modal and never waited on (open(), not exec())."""

    def __init__(self, parent: QWidget, team: teams.Team, written: str, lineup: str, suggested: bool,
                 then: Callable[[tuple[str, str] | None], None], accent: str) -> None:
        super().__init__(parent)
        self.team, self.then, self.answered = team, then, False
        self.setWindowTitle(f"Hand the team over · {team.name}")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(980, 600)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(look.span("Hand the team over  ", f"bold {look.TEXT}")
                            + look.span(team.name, f"bold {look.CYAN}") + look.span("  its new generation", look.MUTED)))
        box.addWidget(label(look.span(handoverwords.TEAM_ABOUT, look.MUTED), wrap=True))
        body = QHBoxLayout()
        body.setSpacing(10)
        box.addLayout(body, 1)
        handed = Card("Handover")
        self.text = handed.add(text_box(written, read_only=True), 1)
        body.addWidget(handed, 11)
        side = QVBoxLayout()
        side.setSpacing(10)
        body.addLayout(side, 9)
        lined = Card("Line-up", "the lead's suggestion" if suggested else "as it was: the lead suggested none")
        self.lineup = lined.add(text_box(lineup, read_only=False), 1)
        side.addWidget(lined, 1)
        capped = Card("Context")
        self.cap = capped.add(Choice(CAPS, team.cap or picks.default_cap(), accent, CAP_SHORT))
        self.cap_words = capped.add(label("", wrap=True))
        side.addWidget(capped)
        self.preview = label("")
        side.addWidget(self.preview)
        self.warning = label("", wrap=True)
        self.warning.hide()
        box.addWidget(self.warning)
        box.addWidget(label(keys_html(HINT)))
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}")
        QWidget.setTabOrder(self.lineup, self.cap)
        QWidget.setTabOrder(self.cap, self.text)
        self.lineup.textChanged.connect(self.show_preview)
        self.cap.changed.connect(self.show_preview)
        QShortcut(QKeySequence("Ctrl+S"), self, self.start)
        self.finished.connect(self.closed)
        self.finished.connect(self.deleteLater)
        self.show_preview()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.lineup.setFocus()  # the line-up is the part to change

    def members(self) -> list[teams.Member]:
        """The line-up's members, the lead left out (it goes on as it was)."""
        lead = self.team.lead.name if self.team.lead is not None else teams.LEAD
        return [m for m in teams.parse_members(self.lineup.toPlainText()) if m.name != lead]

    def problems(self) -> list[str]:
        return handoverwords.lineup_problems(self.team, self.members())

    def show_preview(self) -> None:
        chosen = self.cap.value
        self.cap_words.setText(look.span(handoverwords.cap_words(chosen), look.MUTED))
        parts = handoverwords.lineup_parts(self.members(), picks.cap_tokens(chosen), self.problems())
        self.preview.setText(parts_html(parts).replace("\n", "<br>"))
        if not self.problems():
            self.warning.hide()  # what a Ctrl+S said it needed, given since

    def start(self) -> None:
        """Ctrl+S: the new generation, unless its line-up still needs something (said here; it stays)."""
        problems = self.problems()
        if problems:
            self.warning.setText(look.span("The new team needs " + "; ".join(problems) + ".", look.AMBER))
            self.warning.show()
            return
        self.answer(("\n".join(m.line() for m in self.members()), self.cap.value))

    def answer(self, given: tuple[str, str] | None) -> None:
        if self.answered:
            return
        self.answered = True
        if given is not None:
            self.accept()
        self.then(given)

    def closed(self) -> None:
        """Esc, or closed by its window's button: not now (then with None), unless it was answered."""
        self.answer(None)
