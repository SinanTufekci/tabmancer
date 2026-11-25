"""h twice on a team in the window (desktop/front.py, desktop/teamhandover.py): its lead writes the handover
and the line-up on a thread of their own, back by a Signal; the team handover dialog shows them in the
terminal dashboard's words; Ctrl+S starts the team's new generation through the window's engine
(Engine.team_start), opened as the terminal opens a team (teamkeys.TeamKeys.open_team); Esc leaves it
closed, and h shows the kept handover again. Offscreen over the made-up sessions of `tabmancer --demo`.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_desktop_front import read, taking
from test_desktop_handover import until

from tabmancer import actions, alerts, claudeupdate, demo, handoverwords, launch, picks, shutdowns, teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, look
from tabmancer.desktop.handover import parts_html
from tabmancer.desktop.teamhandover import TeamHandoverDialog
from tabmancer.sessions import Exit

MAIN = threading.main_thread()
LINEUP = "api: the endpoints @sonnet\nlead: plans the work\nqa: the tests @agy"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test; none ticked at the start. No tab opens and no
    claude update runs: what would have opened is kept in world.opened."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    opened: list[launch.Launch] = []
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: opened.extend(launches) or [])
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    return SimpleNamespace(home=tmp_path, opened=opened)


@pytest.fixture
def window(qapp, world):
    from tabmancer.desktop import main

    made = []

    def make():
        shown = main.MainWindow(start=False)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
        made.append(shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def made_up_team(background: bool = False) -> teams.Team:
    return teams.new_team("garden", "C:/work/garden", "a made-up goal", teams.parse_members(
        "api: the endpoints\nui: the pages"), background=background, cap="300k")


def resting_team(qapp, shown) -> teams.TeamState:
    """The demo's team (shop), every member waiting: a team at work cannot be handed over."""
    read(qapp, shown)
    name, state = next(iter(shown.snap.team_states.items()))
    taking(shown, *(replace(s, live_state="idle") for s in shown.members_now(state).values() if s is not None))
    return shown.snap.team_states[name]


def dialog_of(shown) -> TeamHandoverDialog:
    (found,) = [d for d in shown.findChildren(TeamHandoverDialog) if d.isVisible()]
    return found


def ctrl_s(dialog: TeamHandoverDialog) -> None:
    QTest.keyClick(dialog.lineup, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)


def test_the_dialog_shows_the_handover_and_the_lineup_in_the_terminals_words(qapp, world):
    parent = QWidget()
    parent.resize(1100, 700)
    parent.show()
    answers: list = []
    dialog = TeamHandoverDialog(parent, made_up_team(), "# Done\n<b>never markup</b>", LINEUP, True, answers.append,
                                look.ORANGE)
    dialog.open()
    qapp.processEvents()
    assert dialog.windowTitle() == "Hand the team over · garden"
    assert dialog.text.isReadOnly() and dialog.text.toPlainText() == "# Done\n<b>never markup</b>"  # plain text
    assert not dialog.lineup.isReadOnly() and dialog.focusWidget() is dialog.lineup
    assert [m.name for m in dialog.members()] == ["api", "qa"]  # the lead goes on as it was
    assert [m.cli for m in dialog.members()] == ["claude", "agy"]

    def preview(cap: str) -> str:
        parts = handoverwords.lineup_parts(dialog.members(), picks.cap_tokens(cap), dialog.problems())
        return parts_html(parts).replace("\n", "<br>")

    assert dialog.cap.value == "300k" and dialog.preview.text() == preview("300k")
    assert dialog.cap_words.text() == look.span(handoverwords.cap_words("300k"), look.MUTED)
    QTest.keyClick(dialog.cap, Qt.Key.Key_Right)  # ←→ move the cap, as in the terminal
    assert dialog.cap.value == "400k" and dialog.preview.text() == preview("400k")
    for _ in range(3):
        QTest.keyClick(dialog.cap, Qt.Key.Key_Left)
    assert dialog.cap.value == picks.NO_CAP and "with no cap" in dialog.preview.text()  # round to the last
    assert dialog.cap_words.text() == look.span(handoverwords.cap_words(picks.NO_CAP), look.MUTED)
    QTest.mouseClick(dialog.cap.group.button(1), Qt.MouseButton.LeftButton)  # a click picks
    assert dialog.cap.value == "300k" and dialog.cap.group.button(1).text() == "● 300k"
    dialog.lineup.setPlainText("lead: only the lead")
    ctrl_s(dialog)  # the line-up needs a member: said, and the dialog stays
    assert answers == [] and dialog.isVisible() and not dialog.warning.isHidden()
    assert "a member at least" in dialog.warning.text() and "needs a member at least" in dialog.preview.text()
    dialog.lineup.setPlainText(LINEUP)
    assert dialog.warning.isHidden()  # given since
    ctrl_s(dialog)
    assert answers == [("api: the endpoints @sonnet\nqa: the tests @agy", "300k")]  # the lead's line left out
    parent.close()


def test_esc_answers_none_once(qapp, world):
    parent = QWidget()
    parent.show()
    answers: list = []
    dialog = TeamHandoverDialog(parent, made_up_team(), "THE HANDOVER", "", False, answers.append, look.ORANGE)
    dialog.open()
    qapp.processEvents()
    assert dialog.lineup.toPlainText() == ""  # the lead suggested none and the team had none
    QTest.keyClick(dialog.lineup, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert answers == [None]
    parent.close()


def test_h_twice_on_a_team_writes_its_handover_on_a_thread_and_ctrl_s_starts_the_new_generation(qapp, window,
                                                                                                 world, monkeypatch):
    shown = window()
    state = resting_team(qapp, shown)
    team, members = state.team, shown.members_now(state)
    ran: dict = {"writes": 0}

    def writes(given, those, now, close):
        ran.update(writes=ran["writes"] + 1, write=threading.current_thread(), members=those, close=close)
        return actions.TeamHanded(those[given.lead.name], "THE TEAM HANDOVER", LINEUP, True)

    ready = shown.engine.team_handover_ready

    def readied(*args):
        ran["ready"] = threading.current_thread()
        ready(*args)

    monkeypatch.setattr(actions, "write_team_handover", writes)
    monkeypatch.setattr(shown.engine, "team_handover_ready", readied)
    assert shown.board.reveal(VM.TEAM_CARD + team.name) and shown.why_not("h") is None  # its card's h is here
    QTest.keyClick(shown, Qt.Key.Key_H)
    assert shown.bit_line.text() == actions.handing_note(team, members) and ran["writes"] == 0  # once: what twice does
    QTest.keyClick(shown, Qt.Key.Key_H)
    assert shown.bit_line.text() == handoverwords.team_handing_note(team).words
    until(qapp, lambda: shown.findChildren(TeamHandoverDialog))
    assert ran["write"] is not MAIN and ran["ready"] is MAIN  # written off the window's thread, back by a Signal
    assert ran["members"] == members and ran["close"] is actions.close_tabs  # the demo's: nothing closes
    dialog = dialog_of(shown)
    assert dialog.text.toPlainText() == "THE TEAM HANDOVER" and dialog.lineup.toPlainText() == LINEUP
    assert team.name in shown.engine.state.kept_handovers
    QTest.mouseClick(dialog.cap.group.button(2), Qt.MouseButton.LeftButton)  # 400k
    ctrl_s(dialog)
    fresh = teams.load()[team.name]
    assert [(m.name, m.cli) for m in fresh.members] == [("lead", "claude"), ("api", "claude"), ("qa", "agy")]
    assert fresh.cap == "400k" and team.name not in shown.engine.state.kept_handovers
    titles = [opened.title for opened in world.opened]
    assert titles == [f"{team.name}: api", f"{team.name}: qa", f"{team.name}: lead"]  # its members first
    assert "THE TEAM HANDOVER" in world.opened[-1].prompt  # the fresh lead starts from the handover
    assert shown.bit_line.text() == handoverwords.team_handed_note(team, fresh).words
    assert shown.front.waiting[-1][0] == alerts.handed_event(team.name).bit.words  # Bit's words wait their turn
    shown.front.settle()


def test_esc_leaves_the_team_closed_and_h_shows_the_kept_handover_again(qapp, window, world, monkeypatch):
    shown = window()
    state = resting_team(qapp, shown)
    team = state.team
    written: list[str] = []

    def writes(given, those, now, close):
        written.append(given.name)
        return actions.TeamHanded(those[given.lead.name], "THE TEAM HANDOVER", LINEUP, True)

    monkeypatch.setattr(actions, "write_team_handover", writes)
    assert shown.board.reveal(VM.TEAM_CARD + team.name)
    QTest.keyClick(shown, Qt.Key.Key_H)
    QTest.keyClick(shown, Qt.Key.Key_H)
    until(qapp, lambda: shown.findChildren(TeamHandoverDialog))
    QTest.keyClick(dialog_of(shown).lineup, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert shown.bit_line.text() == handoverwords.team_not_started_note(team).words and not world.opened
    taking(shown, *(replace(s, exit=Exit.CLEAN) for s in shown.members_now(state).values() if s is not None))
    assert shown.board.reveal(VM.TEAM_CARD + team.name)
    shown.activateWindow()  # offscreen, the dialog's window does not hand the focus back by itself
    QTest.qWaitForWindowActive(shown, 3000)
    QTest.keyClick(shown, Qt.Key.Key_H)  # once: the handover kept, the team still closed
    qapp.processEvents()
    assert dialog_of(shown).text.toPlainText() == "THE TEAM HANDOVER" and written == [team.name]  # not written again
    QTest.keyClick(dialog_of(shown).lineup, Qt.Key.Key_Escape)
    qapp.processEvents()
    shown.front.settle()


def test_a_team_handover_that_fails_is_said_in_red(qapp, window, monkeypatch):
    shown = window()
    team = resting_team(qapp, shown).team

    def fails(given, those, now, close):
        raise actions.ActionError(f"{given.name} was not handed over: the lead's answer held no handover.")

    monkeypatch.setattr(actions, "write_team_handover", fails)
    assert shown.board.reveal(VM.TEAM_CARD + team.name)
    QTest.keyClick(shown, Qt.Key.Key_H)
    QTest.keyClick(shown, Qt.Key.Key_H)
    until(qapp, lambda: shown.bit_line.text().startswith(f"{team.name} was not handed over"))
    assert look.RED in shown.bit_line.styleSheet() and not shown.findChildren(TeamHandoverDialog)
    shown.front.settle()


def test_a_teams_background_members_start_on_a_thread_after_claude_update_then_its_lead_opens(qapp, window, world,
                                                                                               monkeypatch):
    """open_team as the terminal's (teamkeys.TeamKeys.open_team): while claude update runs it waits and comes
    back through the window's front once it is done; claude --bg for the members off the window's thread,
    what came of it said, and then the lead's tab."""
    shown = window()
    read(qapp, shown)
    team = made_up_team(background=True)
    launches = teams.start(team, {}, fresh=True)
    started: list = []

    def starts(command):
        started.append((threading.current_thread(), command))

    monkeypatch.setattr(launch, "run_in_background", starts)
    shown.engine.state.updating_claude = True
    shown.front.open_team(team, launches)
    assert shown.bit_line.text() == alerts.updating_note(f"{team.name} starts").words and not started
    shown.engine.claude_updated(claudeupdate.Outcome(True, "", ""), None)
    until(qapp, lambda: world.opened)
    assert [opened.title for opened in world.opened] == [f"{team.name}: lead"]  # the lead in a tab, the rest not
    assert len(started) == 2 and all(thread is not MAIN for thread, _ in started)
    shown.front.settle()
