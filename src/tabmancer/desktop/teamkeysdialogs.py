"""a and g on a team in the window: what the terminal's team view asks in a dialog, asked here in its words
(teamforms), and the answer handed to the team keys (teamkeys.TeamKeys, the terminal's one copy). a asks for
new members on one line (AddMembersDialog: name: area, several with ;); g shows the team's goal in a box of
several lines, its lines kept (GoalDialog: Ctrl+S saves, Enter is a new line, Esc leaves it as it is).

d (twice) and H ask nothing: the window calls TeamKeys.remove / changed and TeamKeys.hand_over itself.

The team keys come from a callable (the window's front builds a TeamKeys on each call, its presses kept in
the engine's state), and the members as they are when the answer comes: a dialog stays open while the
window reads the sessions again."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent, QTextCursor
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QVBoxLayout, QWidget

from .. import teamforms, teamkeys, teams
from ..sessions import Session
from . import look
from .formparts import keys_html
from .handover import label
from .naming import NameDialog

Members = Callable[[], dict[str, Session | None]]
Keys = Callable[[], teamkeys.TeamKeys]


class AddMembersDialog(NameDialog):
    """a: new members on one line, as the terminal's asks for them. Enter with some says them (named); Enter on
    nothing, or Esc, adds none."""

    def __init__(self, parent: QWidget, accent: str) -> None:
        super().__init__(parent, teamforms.ADD_QUESTION, "", teamforms.ADD_EXAMPLE, teamforms.ADD_KEYS, accent)


class GoalDialog(QDialog):
    """g: the team's goal in a box of several lines, the cursor at its end. Ctrl+S says it (saved, once); Esc
    closes it with nothing said. Window-modal and never waited on (open(), not exec())."""

    saved = Signal(str)

    def __init__(self, parent: QWidget, goal: str, accent: str) -> None:
        super().__init__(parent)
        self.answered = False
        self.setWindowTitle(teamforms.GOAL_TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(640, 320)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(look.span(teamforms.GOAL_TITLE, f"bold {look.TEXT}")))
        self.goal = QPlainTextEdit(goal)
        self.goal.setFont(look.font(10))
        box.addWidget(self.goal, 1)
        box.addWidget(label(keys_html(teamforms.GOAL_KEYS)))
        self.save = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save.activated.connect(self.answer)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QPlainTextEdit {{ color:{look.TEXT}; background:{look.BLACK}; border:1px solid {look.BORDER};"
                           " border-radius:4px; padding:5px 8px; }"
                           f"QPlainTextEdit:focus {{ border-color:{accent}; }}")
        self.finished.connect(self.deleteLater)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.goal.setFocus()
        self.goal.moveCursor(QTextCursor.MoveOperation.End)

    def answer(self) -> None:
        if self.answered:
            return
        self.answered = True
        goal = teamforms.answer(self.goal.toPlainText())
        self.accept()
        if goal is not None:
            self.saved.emit(goal)


def ask_add(parent: QWidget, accent: str, keys: Keys, team: teams.Team, members: Members) -> AddMembersDialog:
    """a on a team: its new members asked for, then added (TeamKeys.add). At work when it was asked, each
    opens and introduces itself to the lead; closed, they join it and start with it (s), as in the terminal."""
    at_work = teamkeys.at_work(members())
    dialog = AddMembersDialog(parent, accent)
    dialog.named.connect(lambda text: keys().add(team, text, at_work))
    dialog.open()
    return dialog


def ask_goal(parent: QWidget, accent: str, keys: Keys, team: teams.Team, members: Members) -> GoalDialog:
    """g on a team: its goal shown to change, then saved and the lead told (TeamKeys.change_goal); the same
    goal, or none, changes nothing."""
    dialog = GoalDialog(parent, team.goal, accent)
    dialog.saved.connect(lambda goal: keys().change_goal(team, goal, members()))
    dialog.open()
    return dialog
