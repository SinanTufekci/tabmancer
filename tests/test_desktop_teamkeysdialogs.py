"""a and g on a team in the window (desktop/teamkeysdialogs.py): the dialogs ask in the terminal's words
(teamforms) and hand the answer to the team keys (teamkeys.TeamKeys, one copy); nothing, or Esc, hands nothing.
Offscreen. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import html
import importlib
import os
import re
from pathlib import Path

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from tabmancer import teamforms, teams
from tabmancer.desktop import look
from tabmancer.desktop.teamkeysdialogs import AddMembersDialog, GoalDialog, ask_add, ask_goal
from tabmancer.sessions import Exit, Session


@pytest.fixture(scope="module")
def qapp():
    look.fonts_for_offscreen()
    return QApplication.instance() or QApplication(["tabmancer-tests"])


class Keys:
    """Team keys that keep what they were asked (as teamkeys.TeamKeys.add / change_goal take it)."""

    def __init__(self) -> None:
        self.calls: list = []

    def add(self, team, text, at_work) -> None:
        self.calls.append(("add", team.name, text, at_work))

    def change_goal(self, team, goal, members) -> None:
        self.calls.append(("change_goal", team.name, goal, sorted(members)))


def a_team() -> teams.Team:
    return teams.new_team("shop", "C:/shop", "Add a checkout\n- the page\n- tests", teams.parse_members("api: x"))


def opened(sid: str) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), exit=Exit.RUNNING, title=f"shop-{sid}")


def shown_words(dialog) -> str:
    """What its labels say, as read: no markup."""
    return html.unescape(re.sub("<[^>]+>", "", " ".join(label.text() for label in dialog.findChildren(QLabel))))


def opened_from_the_loop(qapp, make):
    """A dialog opened from the event loop, as a key opens it: the active window, so its shortcuts work."""
    made = []
    QTimer.singleShot(0, lambda: made.append(make()))
    while not made:
        qapp.processEvents()
    dialog = made[0]
    dialog.activateWindow()
    QTest.qWaitForWindowActive(dialog, 2000)
    return dialog


def test_a_asks_in_the_terminals_words_and_adds_what_is_typed(qapp):
    parent, keys, team = QWidget(), Keys(), a_team()
    members = {"lead": opened("L"), "api": None}
    dialog = ask_add(parent, look.ORANGE, lambda: keys, team, lambda: members)
    assert isinstance(dialog, AddMembersDialog)
    assert teamforms.ADD_QUESTION in shown_words(dialog) and "add them" in shown_words(dialog)
    assert dialog.windowTitle() == "Add members · shop"  # from the Team panel, the team it adds to
    assert dialog.line.placeholderText() == teamforms.ADD_EXAMPLE
    members.clear()  # at work when it was asked, as the terminal's a decides it
    QTest.keyClicks(dialog.line, "docs: the README @agy")
    QTest.keyClick(dialog.line, Qt.Key.Key_Return)
    assert keys.calls == [("add", "shop", "docs: the README @agy", True)]
    parent.deleteLater()


def test_a_with_nothing_typed_or_esc_adds_none_and_a_closed_team_is_not_at_work(qapp):
    parent, keys, team = QWidget(), Keys(), a_team()
    dialog = ask_add(parent, look.ORANGE, lambda: keys, team, lambda: {"lead": None})
    QTest.keyClicks(dialog.line, "   ")
    QTest.keyClick(dialog.line, Qt.Key.Key_Return)
    dialog = ask_add(parent, look.ORANGE, lambda: keys, team, lambda: {"lead": None})
    QTest.keyClicks(dialog.line, "qa: tests")
    dialog.reject()
    assert keys.calls == []
    dialog = ask_add(parent, look.ORANGE, lambda: keys, team, lambda: {"lead": None})
    QTest.keyClicks(dialog.line, "qa: tests")
    QTest.keyClick(dialog.line, Qt.Key.Key_Return)
    assert keys.calls == [("add", "shop", "qa: tests", False)]
    parent.deleteLater()


def test_g_shows_the_goal_with_its_lines_and_ctrl_s_saves_it_with_the_members_of_then(qapp):
    parent, keys, team = QWidget(), Keys(), a_team()
    parent.show()
    members = {"lead": None}
    dialog = opened_from_the_loop(qapp, lambda: ask_goal(parent, look.ORANGE, lambda: keys, team, lambda: members))
    assert isinstance(dialog, GoalDialog)
    words = shown_words(dialog)
    assert teamforms.GOAL_TITLE + " · shop" in words and "Ctrl+S" in words and "a new line" in words
    assert dialog.windowTitle() == "The team's goal · shop"
    assert dialog.goal.toPlainText() == team.goal
    assert dialog.goal.textCursor().atEnd()
    QTest.keyClick(dialog.goal, Qt.Key.Key_Return)  # Enter is a new line, not a save
    QTest.keyClicks(dialog.goal, "- docs")
    assert keys.calls == []
    members["api"] = opened("A")  # the members as they are when it is saved
    QTest.keyClick(dialog.goal, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert keys.calls == [("change_goal", "shop", team.goal + "\n- docs", ["api", "lead"])]
    parent.close()
    parent.deleteLater()


def test_g_with_esc_or_an_empty_goal_saves_nothing(qapp):
    parent, keys, team = QWidget(), Keys(), a_team()
    parent.show()
    dialog = opened_from_the_loop(qapp, lambda: ask_goal(parent, look.ORANGE, lambda: keys, team, dict))
    QTest.keyClick(dialog.goal, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    dialog = opened_from_the_loop(qapp, lambda: ask_goal(parent, look.ORANGE, lambda: keys, team, dict))
    dialog.goal.setPlainText("  \n ")
    QTest.keyClick(dialog.goal, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert keys.calls == [] and not dialog.isVisible()
    parent.close()
    parent.deleteLater()
