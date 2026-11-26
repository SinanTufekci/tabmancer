"""T in the window (desktop/newteam.py): a new team made as the terminal's new-team form makes one, in
teamforms.py's words: what is still needed, the line-ups, tabs or the background, a line-up suggested and the
goal rewritten off the window's thread, Ctrl+L and Ctrl+G, a team at work added to; an agy member drawn with the
model agy will run (Neon, then Hydrogen), never a Claude one. Offscreen, with a made-up Claude. Skipped without
the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid
from test_desktop_handover import until

from tabmancer import agy, improve, summarize, teamforms, teams
from tabmancer.desktop import look, naming, newsession, newteam
from tabmancer.teamforms import JoinTeam

MAIN = threading.main_thread()
NEON, HYDROGEN = agy.DEFAULT_CHAIN
SUGGESTED = "Team size: 2, because two parts\napi: endpoints @sonnet @high\nui: screens @agy"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A project folder, the config in a folder of its own (no agy quota read yet: the chain's first), and a
    made-up Claude that keeps what it was asked and on which thread; gate: an Event it waits on."""
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    project = tmp_path / "shop"
    project.mkdir()
    kept = SimpleNamespace(project=project, asked=[], improves=[], gate=None, answer=SUGGESTED,
                           better="Add Stripe checkout to the shop, with tests for each payment path")

    def wait():
        if kept.gate is not None:
            assert kept.gate.wait(10)

    def ask(prompt, model, timeout=180, effort=""):
        kept.asked.append((prompt, threading.current_thread()))
        wait()
        return kept.answer

    def improve_goal(goal, folder):
        kept.improves.append((goal, folder, threading.current_thread()))
        wait()
        return kept.better

    monkeypatch.setattr(summarize, "ask", ask)
    monkeypatch.setattr(improve, "improve_goal", improve_goal)
    return kept


@pytest.fixture
def opened(qapp, world):
    parent = QWidget()
    parent.resize(1200, 800)
    parent.show()
    asker = newsession.Asker(parent)
    made = []

    def make(working=(), taken=frozenset()):
        dialog = newteam.NewTeamDialog(parent, str(world.project), set(taken), [str(world.project)], list(working),
                                       asker, "#00b4d8")
        dialog.made.connect(made.append)
        dialog.open()
        qapp.processEvents()
        return dialog

    yield SimpleNamespace(make=make, made=made, asker=asker)
    if world.gate is not None:
        world.gate.set()
    asker.settle()
    qapp.processEvents()
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def text_of(widget) -> str:
    doc = QTextDocument()
    doc.setHtml(widget.text())
    return doc.toPlainText().replace("\xa0", " ")


def closed(dialog) -> bool:
    """Closed: hidden, or already deleted (it deletes itself once it has finished)."""
    return not isValid(dialog) or not dialog.isVisible()


def key(widget, which, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, which, modifier)
    QApplication.processEvents()


def ctrl(widget, which) -> None:
    key(widget, which, Qt.KeyboardModifier.ControlModifier)


def fill(dialog, name="checkout", goal="Add Stripe checkout with tests") -> None:
    dialog.name.setText(name)
    dialog.goal.setPlainText(goal)


def test_what_is_missing_is_said_and_louder_once_ctrl_s_was_tried(qapp, world, opened):
    dialog = opened.make()
    assert dialog.folder.text().endswith("shop")
    assert "Still needed: a name, the goal" in text_of(dialog.preview)
    ctrl(dialog.name, Qt.Key.Key_S)
    assert opened.made == [] and dialog.isVisible()
    assert dialog.form.tried and look.AMBER.lower() in dialog.preview.text().lower()  # said louder


def test_a_team_made_with_ctrl_s(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    assert "ready" in text_of(dialog.ready)
    assert text_of(dialog.graph_card.title).startswith("Opens")
    ctrl(dialog.name, Qt.Key.Key_S)
    [team] = opened.made
    assert isinstance(team, teams.Team)
    assert (team.name, Path(team.cwd), team.goal) == ("checkout", world.project.resolve(),
                                                            "Add Stripe checkout with tests")
    assert [m.name for m in team.members if m.lead] == [teams.LEAD]
    assert closed(dialog)


def test_enter_goes_on_to_the_next_box(qapp, world, opened):
    dialog = opened.make()
    dialog.name.setFocus()
    key(dialog.name, Qt.Key.Key_Return)
    assert dialog.folder.hasFocus()
    assert opened.made == []


def test_ctrl_t_goes_through_the_line_ups_to_the_lead_deciding(qapp, world, opened):
    dialog = opened.make()
    first = dialog.members.toPlainText()
    ctrl(dialog.name, Qt.Key.Key_T)
    assert dialog.members.toPlainText() != first
    while dialog.form.picked() != teams.DECIDE:
        ctrl(dialog.name, Qt.Key.Key_T)
    assert dialog.most_row.isVisible() and dialog.members.toPlainText() == teamforms.LEAD_LINE
    fill(dialog)
    dialog.most.setText("3")
    ctrl(dialog.name, Qt.Key.Key_S)
    [team] = opened.made
    assert team.decide == 3


def test_ctrl_b_runs_the_members_in_the_background_and_ctrl_o_in_worktrees(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    ctrl(dialog.name, Qt.Key.Key_B)
    assert dialog.form.background and "● the members in the background" in text_of(dialog.where)
    assert "background" in text_of(dialog.graph_card.title) or "background" in text_of(dialog.preview)
    ctrl(dialog.name, Qt.Key.Key_O)
    assert dialog.form.worktrees and "[x] own git worktrees" in text_of(dialog.where)
    assert "for worktrees, the top folder of a git repository" in text_of(dialog.preview)  # not a repository


def test_peers_each_run_in_a_tab(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    dialog.members.setPlainText("api: endpoints\nui: screens")
    ctrl(dialog.name, Qt.Key.Key_B)
    assert not dialog.form.background
    assert text_of(dialog.note).startswith("Peers each run in a tab")


def test_an_agy_member_is_drawn_with_the_chain_never_a_claude_model(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    dialog.members.setPlainText(f"{teamforms.LEAD_LINE}\nui: screens @agy @opus")
    drawn = text_of(dialog.drawn)
    ui = next(line for line in drawn.splitlines() if "-ui" in line or line.startswith("● ui") or " ui" in line)
    assert "agy · Neon" in ui and "opus" not in ui
    assert "never runs through agy" in drawn  # said, as where the team starts
    agy.note_out_of_quota(NEON, datetime.now(timezone.utc) + timedelta(hours=3))
    dialog.members.setPlainText(f"{teamforms.LEAD_LINE}\nui: screens @agy")
    assert "agy · Hydrogen" in text_of(dialog.drawn)
    agy.note_out_of_quota(HYDROGEN, datetime.now(timezone.utc) + timedelta(hours=3))
    dialog.members.setPlainText(f"{teamforms.LEAD_LINE}\nui: screens @agy @sonnet")
    drawn = text_of(dialog.drawn)
    assert "agy · no model now" in drawn and "sonnet" not in drawn.split("agy · no model now")[0].splitlines()[-1]


def test_ctrl_r_suggests_a_line_up_off_the_thread(qapp, world, opened):
    dialog = opened.make()
    ctrl(dialog.name, Qt.Key.Key_R)
    assert world.asked == [] and "Write the goal and the project folder first" in text_of(dialog.note)
    fill(dialog)
    world.gate = threading.Event()
    ctrl(dialog.name, Qt.Key.Key_R)
    until(qapp, lambda: world.asked)
    assert world.asked[0][1] is not MAIN
    assert "Asking Claude" in text_of(dialog.preview)
    ctrl(dialog.name, Qt.Key.Key_R)  # asked once at a time
    world.gate.set()
    until(qapp, lambda: not dialog.asking)
    assert len(world.asked) == 1
    assert dialog.members.toPlainText().splitlines()[0].startswith("api: endpoints")
    assert text_of(dialog.note).startswith("Claude suggests 2 members, as peers — because two parts")


def test_a_failed_suggestion_leaves_the_box_and_says_why(qapp, world, opened, monkeypatch):
    def broken(prompt, model, timeout=180, effort=""):
        raise OSError("claude is not on PATH")

    monkeypatch.setattr(summarize, "ask", broken)
    dialog = opened.make()
    fill(dialog)
    before = dialog.members.toPlainText()
    ctrl(dialog.name, Qt.Key.Key_R)
    until(qapp, lambda: not dialog.asking)
    assert dialog.members.toPlainText() == before
    assert text_of(dialog.note) == "No line-up suggested: claude is not on PATH"


def test_ctrl_e_rewrites_the_goal_off_the_thread(qapp, world, opened):
    dialog = opened.make()
    ctrl(dialog.name, Qt.Key.Key_E)
    assert world.improves == [] and "Ctrl+E needs a goal" in text_of(dialog.note)
    fill(dialog, goal="stripe checkout + tests")
    world.gate = threading.Event()
    ctrl(dialog.name, Qt.Key.Key_E)
    until(qapp, lambda: world.improves)
    assert world.improves[0][2] is not MAIN and dialog.goal.isReadOnly()
    world.gate.set()
    until(qapp, lambda: dialog.goal.toPlainText() == world.better)
    assert not dialog.goal.isReadOnly()
    assert text_of(dialog.note) == teamforms.IMPROVED.words
    dialog.goal.undo()
    assert dialog.goal.toPlainText() == "stripe checkout + tests"


def test_ctrl_l_keeps_the_line_up_under_a_name(qapp, world, opened):
    dialog = opened.make()
    dialog.members.setPlainText(f"{teamforms.LEAD_LINE}\napi: endpoints\nui: screens")
    ctrl(dialog.name, Qt.Key.Key_L)
    [asked] = dialog.findChildren(naming.NameDialog)
    asked.line.setText("api and screens")
    key(asked.line, Qt.Key.Key_Return)
    assert "api and screens" in dialog.form.lineups
    assert teams.saved_lineups()["api and screens"] == "api: endpoints\nui: screens"
    assert text_of(dialog.note).startswith("Kept the line-up api and screens")


def test_ctrl_g_shows_what_the_member_is_told(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    dialog.members.setPlainText(f"{teamforms.LEAD_LINE}\napi: endpoints")
    cursor = dialog.members.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    dialog.members.setTextCursor(cursor)
    ctrl(dialog.members, Qt.Key.Key_G)
    [shown] = dialog.findChildren(newteam.RoleDialog)
    assert "What checkout-api is told" in text_of(shown.heading)
    assert "endpoints" in shown.role.toPlainText()
    key(shown.role, Qt.Key.Key_Escape)
    assert closed(shown)


def test_a_team_at_work_is_added_to(qapp, world, opened):
    working = teams.new_team("shop", str(world.project.resolve()), "Run the shop",
                             teams.parse_members(f"{teamforms.LEAD_LINE}\napi: endpoints", lead_ok=True))
    world.answer = ("Decision: add to shop\nTeam size: 1, because the screens are new\nui: screens\n"
                    "To shop-lead: ui joins for the screens")
    dialog = opened.make(working=[(working, {teams.LEAD, "api"})])
    assert "shop (2 open) at work in this project" in text_of(dialog.at_work)
    fill(dialog, name="", goal="Add the screens for checkout")
    ctrl(dialog.name, Qt.Key.Key_R)
    until(qapp, lambda: not dialog.asking)
    assert dialog.form.toward is not None and dialog.members.toPlainText() == "ui: screens"
    assert text_of(dialog.title).startswith("Add to a team at work")
    ctrl(dialog.name, Qt.Key.Key_S)
    [joined] = opened.made
    assert isinstance(joined, JoinTeam)
    assert (joined.team, joined.to, joined.message) == ("shop", "shop-lead", "ui joins for the screens")


def test_esc_makes_nothing(qapp, world, opened):
    dialog = opened.make()
    fill(dialog)
    key(dialog.name, Qt.Key.Key_Escape)
    assert opened.made == [] and closed(dialog)
