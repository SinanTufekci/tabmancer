"""The desktop team monitor's team keys (s a d g h H x p, Enter on a closed member, D) act, as the terminal monitor's
do: through a dashboard window, the monitor's hands (the one M was pressed in, or one of its own that is never
shown: MainWindow with monitor), on its front's teamkeys.TeamKeys and in its dialogs. Stand-ins for the hands, over
the made-up team of `tabmancer --demo` (shop: lead, api, ui, tests); the last tests use real hands, never shown.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import importlib
import os
import subprocess
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_desktop_monitor import read

from tabmancer import actor, demo, launch, teams
from tabmancer.config import read_json
from tabmancer.desktop import commandbar, front, look, main, monitor, teamkeysdialogs
from tabmancer.desktop.naming import NameDialog
from tabmancer.sessions import Exit

look.fonts_for_offscreen()
HOUR = 3_600_000  # a timer this slow never goes off in a test


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test; the tabs it would open kept (opened)."""
    for name in monitor.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    opened: list = []
    monkeypatch.setattr(launch, "open_tabs", lambda launches, window=None: opened.append((launches, window)) or [])
    return SimpleNamespace(opened=opened)


class Keys:
    """teamkeys.TeamKeys standing in: each call kept (name, its arguments); remove gives changed."""

    def __init__(self, calls: list, changed: object = None) -> None:
        self.calls, self.changed_one = calls, changed

    def remove(self, *args):
        self.calls.append(("remove", *args))
        return self.changed_one

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


class Hands:
    """A dashboard window standing in for the monitor's hands: what the keys ask of it, kept."""

    def __init__(self, changed: object = None) -> None:
        self.calls: list = []
        self.heard: list = []
        keys = Keys(self.calls, changed)
        self.front = SimpleNamespace(team_keys=lambda: keys, heard=SimpleNamespace(connect=self.heard.append),
                                     start_team=lambda team, members: self.calls.append(("start_team", team, members)),
                                     handing_now=list)
        self.engine = SimpleNamespace(team_handover=lambda team, members: self.calls.append(("team_handover", team,
                                                                                               members)),
                                      state=SimpleNamespace(queued={}))

    def come_forward(self) -> None:
        self.calls.append(("come_forward",))


@pytest.fixture
def window(qapp, world):
    made = []

    def make(hands=None, own=False):
        shown = monitor.MonitorWindow("shop", start=False, hands=(lambda: hands), own_hands=own, demo=True)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)  # its keys are the active window's
        made.append(shown)
        read(qapp, shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.leaving = True
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def press(qapp, shown, key, modifier=Qt.KeyboardModifier.NoModifier):
    """A key in the monitor, in front again first (a dialog closed leaves no window active offscreen)."""
    shown.activateWindow()
    QTest.qWaitForWindowActive(shown, 3000)
    QTest.keyClick(shown, key, modifier)
    qapp.processEvents()


def dialogs(shown, kind):
    return [d for d in shown.findChildren(kind) if d.isVisible()]


def pick(qapp, shown, name):
    shown.picked = name
    shown.refresh_pick()
    qapp.processEvents()


def test_every_key_works_here_and_none_sends_you_to_the_terminal(qapp, window):
    shown = window(Hands())
    assert monitor.HERE == {key for _group, keys in monitor.MONITOR_KEYS for key, _a, _w in keys}
    assert all(a.isEnabled() for actions in shown.actions_by_key.values() for a in actions)
    assert set(shown.keybar.keys) == monitor.HERE | {commandbar.KEY}
    tips = [a.toolTip() for actions in shown.actions_by_key.values() for a in actions]
    assert not [tip for tip in tips if "terminal" in tip or "team watch" in tip]
    shown.open_commands()
    offered = {c.key: c for c in shown.command_bar.entries if c.kind == "key"}
    assert {key for key, c in offered.items() if not c.enabled} == {"Enter", "d", "H"}  # nobody picked: those wait
    assert {c.why for c in offered.values() if not c.enabled} == {monitor.PICK_FIRST}
    shown.command_bar.close()


def test_the_team_keys_act_through_the_hands_on_the_team_as_read(qapp, window):
    hands = Hands()
    shown = window(hands)
    members = ["lead", "api", "ui", "tests"]
    for key in (Qt.Key.Key_S, Qt.Key.Key_X, Qt.Key.Key_H):
        press(qapp, shown, key)
    assert [(name, team.name, list(on)) for name, team, on in hands.calls] == [
        ("start_team", "shop", members), ("close", "shop", members), ("team_handover", "shop", members)]
    assert hands.heard == [shown.hear]  # its notes said here too: listened to once
    hands.calls.clear()
    press(qapp, shown, Qt.Key.Key_H, Qt.KeyboardModifier.ShiftModifier)  # H on nobody: said, nothing asked
    assert hands.calls == [] and monitor.PICK_FIRST in shown.bit_line.text()
    pick(qapp, shown, "ui")
    press(qapp, shown, Qt.Key.Key_H, Qt.KeyboardModifier.ShiftModifier)
    press(qapp, shown, Qt.Key.Key_D)
    (handed, member, session), (removed, team, gone, its) = [call for call in hands.calls]
    assert (handed, member.name, session.session_id) == ("hand_over", "ui", shown.snap.state.sessions["ui"].session_id)
    assert (removed, team.name, gone.name, its) == ("remove", "shop", "ui", shown.snap.state.sessions["ui"])


def test_d_twice_takes_the_member_off_and_the_lead_hears(qapp, window):
    changed = object()
    hands = Hands(changed)
    shown = window(hands)
    pick(qapp, shown, "api")
    press(qapp, shown, Qt.Key.Key_D)  # the stand-in's remove says it is done (a real first press says what d does)
    assert [call[0] for call in hands.calls] == ["remove", "changed"]
    assert hands.calls[1][1] is changed and list(hands.calls[1][2]) == ["lead", "api", "ui", "tests"]
    assert shown.picked is None  # nobody picked any more: it is gone at the next reading


def test_a_and_g_ask_in_the_windows_dialogs_over_the_monitor(qapp, window):
    hands = Hands()
    shown = window(hands)
    press(qapp, shown, Qt.Key.Key_A)
    [asked] = dialogs(shown, teamkeysdialogs.AddMembersDialog)
    assert asked.parent() is shown and "shop" in asked.windowTitle()
    asked.named.emit("docs: the README @agy")
    assert [(c[0], c[1].name, c[2], c[3]) for c in hands.calls] == [("add", "shop", "docs: the README @agy", True)]
    asked.reject()
    hands.calls.clear()
    press(qapp, shown, Qt.Key.Key_G)
    [goal] = dialogs(shown, teamkeysdialogs.GoalDialog)
    assert goal.parent() is shown and goal.goal.toPlainText() == shown.snap.state.team.goal
    goal.saved.emit("a new goal")
    [(changing, team, words, members)] = hands.calls
    assert (changing, team.name, words, list(members)) == ("change_goal", "shop", "a new goal",
                                                           ["lead", "api", "ui", "tests"])
    goal.reject()


def test_p_writes_to_the_picked_member_or_else_the_lead(qapp, window):
    hands = Hands()
    shown = window(hands)
    team = shown.snap.state.team
    press(qapp, shown, Qt.Key.Key_P)
    [asked] = dialogs(shown, NameDialog)
    assert asked.windowTitle() == "A message for lead"
    asked.named.emit("how far along are we?")
    assert hands.calls == [("tell_team", "shop", team.session_name(team.lead), "how far along are we?")]
    asked.reject()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    hands.calls.clear()
    pick(qapp, shown, "api")
    press(qapp, shown, Qt.Key.Key_P)
    [asked] = dialogs(shown, NameDialog)
    assert asked.windowTitle() == "A message for api"
    asked.named.emit("   ")  # nothing to send
    assert hands.calls == []
    asked.reject()


def test_enter_opens_a_closed_member_through_the_hands_and_goes_to_an_open_one(qapp, window, monkeypatch):
    hands = Hands()
    shown = window(hands)
    switched = []
    monkeypatch.setattr(monitor, "switch_tab", lambda relay, title, name: switched.append(name))
    state = shown.snap.state
    state.sessions["ui"] = dataclasses.replace(state.sessions["ui"], exit=Exit.CLEAN)
    pick(qapp, shown, "ui")
    press(qapp, shown, Qt.Key.Key_Return)
    [(opening, team, member, session)] = hands.calls
    assert (opening, team.name, member.name, session) == ("open_member", "shop", "ui", state.sessions["ui"])
    pick(qapp, shown, "api")  # open: its tab to the front, as before, never the hands
    press(qapp, shown, Qt.Key.Key_Return)
    QTest.qWait(200)
    assert switched == ["api"] and len(hands.calls) == 1


def test_d_brings_the_dashboard_forward_and_never_the_hidden_hands(qapp, window, monkeypatch):
    hands = Hands()
    shown = window(hands)  # opened by M in a dashboard window: that one
    press(qapp, shown, Qt.Key.Key_D, Qt.KeyboardModifier.ShiftModifier)
    assert hands.calls == [("come_forward",)]
    own = Hands()
    alone = window(own, own=True)  # opened on its own: the dashboard open already, else one started
    knocked = []
    monkeypatch.setattr(main, "knock", lambda: knocked.append(1) or len(knocked) == 1)
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **how: started.append((command, how)))
    alone.dashboard()
    assert monitor.DASHBOARD_FRONT in alone.bit_line.text() and started == []
    alone.dashboard()  # none open; the demo starts nothing
    assert monitor.DASHBOARD_DEMO in alone.bit_line.text() and started == []
    alone.demo = False
    alone.dashboard()
    [(command, how)] = started
    assert command[-3:] == ["-m", "tabmancer", "desktop"] and how["stdin"] is subprocess.DEVNULL
    assert monitor.DASHBOARD_OPENS in alone.bit_line.text()
    assert own.calls == []  # its hands were never asked to show


def test_without_hands_the_keys_say_so(qapp, window):
    shown = window(None)
    press(qapp, shown, Qt.Key.Key_S)
    assert monitor.NO_HANDS in shown.bit_line.text()


def test_the_hands_notes_are_said_here_while_you_are_in_this_monitor(qapp, window):
    shown = window(Hands())
    shown.hear("x again closes every open member's tab.", 6, "information")
    assert "x again closes" in shown.bit_line.text()
    other = QWidget()
    other.show()
    other.activateWindow()
    QTest.qWaitForWindowActive(other, 3000)
    shown.hear("a note of the dashboard's own", 6, "warning")
    assert "dashboard's own" not in shown.bit_line.text()  # you are elsewhere: the dashboard says it
    alone = window(Hands(), own=True)
    other.activateWindow()
    QTest.qWaitForWindowActive(other, 3000)
    alone.hear("Could not open: no terminal", 15, "error")  # its own hands, never shown: said here always
    assert "no terminal" in alone.bit_line.text() and look.RED in alone.bit_line.styleSheet()
    other.close()


def test_closing_a_monitor_whose_own_hands_have_handovers_queued_says_so_first(qapp, window):
    assert monitor.quit_words(0, []) == ""
    assert monitor.quit_words(1, []) == ("q again closes the monitor, and loses 1 handover queued here for when its "
                                         "member finishes.")
    assert "team shop being written still ends first" in monitor.quit_words(0, ["team shop"])
    hands = Hands()
    hands.engine.state.queued = {"s-api": "self"}
    alone = window(hands, own=True)
    alone.hands()  # the first team key made them
    alone.close()
    assert alone.isVisible() and "loses 1 handover" in alone.bit_line.text()
    alone.close()  # again: closed
    assert not alone.isVisible()
    shown = window(hands)  # opened by M: the queue stays with that window, nothing is asked
    shown.hands()
    shown.close()
    assert not shown.isVisible()


# --- real hands: a MainWindow with monitor, never shown --------------------------------------------------------


@pytest.fixture
def real_hands(qapp, world):
    made = []

    def make():
        hands = main.MainWindow(monitor=True, demo=True, poll_ms=HOUR)
        made.append(hands)
        return hands

    yield make
    for hands in made:
        hands.settle()
        hands.leaving = True
        hands.close()
        hands.deleteLater()
    qapp.processEvents()


def test_a_monitors_own_hands_never_act_never_show_and_keep_no_door(qapp, world, real_hands):
    hands = real_hands()
    assert hands.monitor and hands.front.monitoring() and not hands.isVisible()
    assert not hands.engine.state.acts and actor.holder() is None  # it claimed nothing, though the lock was free
    assert not hands.actor_timer.isActive()  # nor looks to take it over later
    assert not main.knock()  # `tabmancer` would never find it and stay away
    team = teams.load()["shop"]
    member = next(m for m in team.members if m.name == "ui")
    opening = teams.launch(team, member)
    hands.front.open_in_dashboard_window([opening])
    QTest.qWait(100)
    assert world.opened == [([opening], "")]  # no window named: launch's own (monitor.run's gui_tabs)
    hands.leaving = True
    hands.close()
    assert "main" not in (read_json(monitor.LAYOUT_FILE) or {})  # the dashboard's layout left as it was


def test_x_in_a_monitor_on_its_own_says_through_real_hands_what_x_again_does(qapp, world, real_hands):
    made: list = []

    def hands_once():  # as monitor.run's: made at the first key, the same one after
        return made[0] if made else made.append(real_hands()) or made[0]

    shown = monitor.MonitorWindow("shop", start=False, hands=hands_once, own_hands=True, demo=True)
    try:
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
        read(qapp, shown)
        press(qapp, shown, Qt.Key.Key_X)
        hands = shown.hands_ref()
        assert hands is not None and not hands.isVisible()
        assert hands.engine.state.team_presses.get("close", ("",))[0] == "shop"  # the first press, kept there
        assert "x again" in shown.bit_line.text()  # what x again does, said here
        press(qapp, shown, Qt.Key.Key_H)  # h on the team: why not now (the demo's members work), said here
        assert "hand the team over once" in shown.bit_line.text() and hands.engine.state.handing_team is None
        assert made == [hands]  # one pair of hands for every key
        assert front.WindowFront.over(hands.front) is shown  # its dialogs (h's, H's) show over this monitor
    finally:
        shown.settle()
        shown.leaving = True
        shown.close()
        shown.deleteLater()
        qapp.processEvents()
