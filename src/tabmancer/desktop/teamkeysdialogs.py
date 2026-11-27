"""a and g on a team in the window: what the terminal's team view asks in a dialog, asked here in its words
(teamforms), and the answer handed to the team keys (teamkeys.TeamKeys, the terminal's one copy). a asks for
new members on one line (AddMembersDialog: name: area, several with ;); g shows the team's goal in a box of
several lines, its lines kept (GoalDialog: Ctrl+S saves, Enter is a new line, Esc leaves it as it is).

d (twice) and H ask nothing: the window calls TeamKeys.remove / changed and TeamKeys.hand_over itself.

Delete team… (a team card's menu, the drawer's button, the palette) lists the saved teams, the picked one
first in view, as the terminal's T list does: d (or Delete) twice deletes the one picked (TeamKeys.delete),
its sessions kept (DeleteTeamDialog).

The team keys come from a callable (the window's front builds a TeamKeys on each call, its presses kept in
the engine's state), and the members as they are when the answer comes: a dialog stays open while the
window reads the sessions again."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent, QTextCursor
from PySide6.QtWidgets import QDialog, QListWidget, QPlainTextEdit, QVBoxLayout, QWidget

from .. import teamforms, teamkeys, teams
from ..config import UI_TRIES
from ..sessions import Session
from . import look
from .formparts import keys_html
from .handover import label
from .naming import NameDialog

Members = Callable[[], dict[str, Session | None]]
Keys = Callable[[], teamkeys.TeamKeys]
DELETE_KEYS = (("↑↓", "pick a team"), ("d d", "delete it, its sessions kept"), ("Esc", "close"))


class AddMembersDialog(NameDialog):
    """a: new members on one line, as the terminal's asks for them, the team named in the title. Enter with
    some says them (named); Enter on nothing, or Esc, adds none."""

    def __init__(self, parent: QWidget, accent: str, team: str = "") -> None:
        super().__init__(parent, teamforms.ADD_QUESTION, "", teamforms.ADD_EXAMPLE, teamforms.ADD_KEYS, accent)
        self.setWindowTitle(teamforms.team_title(teamforms.ADD_TITLE, team))


class GoalDialog(QDialog):
    """g: the team's goal in a box of several lines, the cursor at its end, the team named in the title and the
    heading. Ctrl+S says it (saved, once); Esc closes it with nothing said. Window-modal and never waited on
    (open(), not exec())."""

    saved = Signal(str)

    def __init__(self, parent: QWidget, goal: str, accent: str, team: str = "") -> None:
        super().__init__(parent)
        self.answered = False
        self.setWindowTitle(teamforms.team_title(teamforms.GOAL_TITLE, team))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(640, 320)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        heading = look.span(teamforms.GOAL_TITLE, f"bold {look.TEXT}")
        box.addWidget(label(heading + (look.span(f" · {team}", look.MUTED) if team else "")))
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
    dialog = AddMembersDialog(parent, accent, team.name)
    dialog.named.connect(lambda text: keys().add(team, text, at_work))
    dialog.open()
    return dialog


def ask_goal(parent: QWidget, accent: str, keys: Keys, team: teams.Team, members: Members) -> GoalDialog:
    """g on a team: its goal shown to change, then saved and the lead told (TeamKeys.change_goal); the same
    goal, or none, changes nothing."""
    dialog = GoalDialog(parent, team.goal, accent, team.name)
    dialog.saved.connect(lambda goal: keys().change_goal(team, goal, members()))
    dialog.open()
    return dialog


class DeleteTeamDialog(QDialog):
    """Delete team…: the saved teams, the picked one first in view; d (or Delete) twice deletes the one picked
    (TeamKeys.delete: the first press says what the second does, here and on the window's line), and it leaves
    the list. Esc closes it. Window-modal and never waited on (open(), not exec())."""

    def __init__(self, parent: QWidget, accent: str, keys: Keys, names: list[str], picked: str | None = None) -> None:
        super().__init__(parent)
        self.keys = keys
        self.setWindowTitle(teamkeys.DELETE_TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(460, 320)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(look.span(teamkeys.DELETE_TITLE, f"bold {look.TEXT}")))
        self.teams = QListWidget()
        self.teams.setFont(look.font(10))
        self.teams.addItems(names)
        box.addWidget(self.teams, 1)
        self.said = label("", wrap=True)
        box.addWidget(self.said)
        box.addWidget(label(keys_html(DELETE_KEYS)))
        at = names.index(picked) if picked in names else 0
        if names:
            self.teams.setCurrentRow(at)
        else:
            self.said.setText(look.span(teamkeys.NO_TEAM_TO_DELETE, look.MUTED))
        for key in ("D", "Delete"):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(self.delete)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QListWidget {{ color:{look.TEXT}; background:{look.BLACK}; border:1px solid {look.BORDER};"
                           " border-radius:4px; padding:4px; }"
                           f"QListWidget::item:selected {{ background:{look.PANEL}; color:{accent}; }}")
        self.finished.connect(self.deleteLater)

    def picked(self) -> str | None:
        item = self.teams.currentItem()
        return item.text() if item is not None else None

    def delete(self) -> None:
        name = self.picked()
        if name is None:
            return
        if self.keys().delete(name):
            self.teams.takeItem(self.teams.currentRow())
            self.said.setText(look.span(teamkeys.deleted(name), look.MUTED))
        elif self.keys().presses.get("delete", ("",))[0] == name:  # a first press: what the second does
            self.said.setText(look.span(teamkeys.delete_again(name), look.AMBER))
        else:  # not deleted: the window's line says why
            self.said.setText("")


def ask_delete(parent: QWidget, accent: str, keys: Keys, picked: str | None = None) -> DeleteTeamDialog:
    """Delete team…: the saved teams listed (teams.load, UI_TRIES: a teams.json held past them, or unreadable,
    lists none), the picked one first in view; d twice deletes one (DeleteTeamDialog)."""
    dialog = DeleteTeamDialog(parent, accent, keys, sorted(teams.load(UI_TRIES)), picked)
    dialog.open()
    return dialog
