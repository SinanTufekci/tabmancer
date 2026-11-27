"""a, g, d and H on a team in the window (main.py, desktop/front.py), as the terminal's team view does them
(teamkeys.TeamKeys, one copy; the dialogs in desktop/teamkeysdialogs.py): a on a team's card asks for new
members, who join the saved team and, the team at work, open; g shows its goal to change, saved, with the
note for its lead on the clipboard and you in the lead's tab; d twice takes the member picked in the Team
panel off (never the lead; a held lock is said, nothing changes); H hands the member alone over, asked how as
h asks. They are in a menu of their own, in the team view's words. Offscreen over the made-up sessions of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from dataclasses import replace
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_desktop_front import busy_one, read, taking
from test_desktop_handover import until

from tabmancer import claudeupdate, config, demo, launch, marks, shutdowns, teamkeys, teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, main
from tabmancer.desktop.handover import HandoverDialog
from tabmancer.desktop.teamkeysdialogs import AddMembersDialog, GoalDialog
from tabmancer.sessions import Exit

SHOP = "shop"  # the demo's team: lead, api, ui, tests, every one open, all Claude Code (no team bus)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> SimpleNamespace:
    """The demo's made-up world; the tabs it would open are kept here, none opens."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    kept = SimpleNamespace(tabs=[], went=[])
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: kept.tabs.extend(launches) or [])
    return kept


@pytest.fixture
def window(qapp, world, monkeypatch):
    made = []

    def make():
        shown = main.MainWindow(start=False)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
        monkeypatch.setattr(shown.opener, "go_to", world.went.append)  # the tab to the front: kept, none moves
        made.append(shown)
        read(qapp, shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def by_key(qapp, shown, key: str, kind):
    """key's action run from the event loop, as a key press comes: its dialog is then the active window."""
    QTimer.singleShot(0, shown.actions_by_key[key].trigger)
    until(qapp, lambda: isinstance(QApplication.activeWindow(), kind))
    (asked,) = [d for d in shown.findChildren(kind) if d.isVisible()]
    return asked


def press(qapp, shown, key: str) -> None:
    shown.actions_by_key[key].trigger()
    qapp.processEvents()


def said(shown) -> str:
    return shown.bit_line.text()


def members_of(name: str = SHOP) -> list[str]:
    return [member.name for member in teams.load()[name].members]


def on_the_team_panel(qapp, shown, member: str | None) -> None:
    """The Team panel in front (T, or Enter on a team's card), this member of shop picked in it (a click)."""
    shown.show_teams()
    qapp.processEvents()
    assert shown.team_panel_in_front() and shown.team_panel.team == SHOP
    if member is not None:
        shown.team_panel.pick_member(member)
    assert shown.team_panel.picked == member


def test_g_d_and_h_are_in_a_team_menu_in_the_team_views_words(qapp, window):
    shown = window()
    menu = shown.menus_by_group[main.TEAM_GROUP]
    assert menu.title() == "Te&am"
    assert [(a.data(), a.isEnabled()) for a in menu.actions()] == [("d", True), ("g", True), ("H", True),
                                                                   ("Del", True)]  # and the saved teams, to delete one
    assert shown.actions_by_key["g"].toolTip() == "the team's goal; the lead is told"
    assert [a.shortcuts()[0].toString() for a in menu.actions()] == ["D", "G", "Shift+H", "Del"]
    groups = [group for group, _ in main.WINDOW_KEYS]
    assert groups.index(main.TEAM_GROUP) == groups.index("Work with a session") + 1


def test_a_on_a_team_card_adds_members_who_open_while_it_is_at_work(qapp, window, world):
    shown = window()
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)
    asked = by_key(qapp, shown, "a", AddMembersDialog)
    QTest.keyClicks(asked.line, "qa: the made-up checks")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    qapp.processEvents()
    assert members_of() == ["lead", "api", "ui", "tests", "qa"]  # saved
    assert [tab.title for tab in world.tabs] == ["shop: qa"]  # at work: it opens and says hello to the lead


def test_g_changes_the_goal_and_the_note_for_the_lead_is_on_the_clipboard_with_you_in_its_tab(qapp, window, world):
    shown = window()
    lead = shown.members_now(shown.snap.team_states[SHOP])["lead"]
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)
    asked = by_key(qapp, shown, "g", GoalDialog)
    assert asked.goal.toPlainText() == teams.load()[SHOP].goal
    asked.goal.setPlainText("A made-up checkout, with its tests")
    QTest.keyClick(asked.goal, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert teams.load()[SHOP].goal == "A made-up checkout, with its tests"
    assert said(shown).startswith("The goal is changed. A note for the lead is on the clipboard")
    assert "A made-up checkout, with its tests" in QApplication.clipboard().text()  # the window types nothing
    assert [session.session_id for session in world.went] == [lead.session_id]


def test_g_without_a_team_card_takes_the_team_panels_team_in_front_else_says_how(qapp, window):
    shown = window()
    assert shown.board.reveal(busy_one(shown).session_id)  # a session's card, not a team's
    press(qapp, shown, "g")
    assert said(shown) == main.GOAL_PICK and not shown.findChildren(GoalDialog)
    on_the_team_panel(qapp, shown, None)
    asked = by_key(qapp, shown, "g", GoalDialog)
    assert asked.goal.toPlainText() == teams.load()[SHOP].goal
    asked.reject()


def test_s_with_the_team_panel_in_front_starts_its_team(qapp, window, world):
    shown = window()
    members = shown.members_now(shown.snap.team_states[SHOP])
    taking(shown, replace(members["ui"], exit=Exit.CLEAN))
    assert shown.board.reveal(busy_one(shown).session_id)  # a session's card picked, behind the panel
    on_the_team_panel(qapp, shown, None)
    press(qapp, shown, "s")
    assert [tab.title for tab in world.tabs] == ["shop: ui"]


def test_d_twice_takes_the_member_picked_in_the_team_panel_off_and_tells_the_lead(qapp, window, world):
    shown = window()
    lead = shown.members_now(shown.snap.team_states[SHOP])["lead"]
    on_the_team_panel(qapp, shown, "api")
    press(qapp, shown, "d")
    assert said(shown).startswith("Press d again to take api off the team shop. Its sessions stay.")
    assert "api" in members_of()
    press(qapp, shown, "d")
    assert members_of() == ["lead", "ui", "tests"]
    assert shown.team_panel.picked is None  # everyone's again
    until(qapp, lambda: [m.name for m in shown.snap.team_states[SHOP].team.members] == ["lead", "ui", "tests"])
    assert said(shown).startswith("api is off the team. A note for the lead is on the clipboard")
    assert [session.session_id for session in world.went] == [lead.session_id]


def test_d_takes_nobody_off_with_none_picked_on_the_lead_or_while_another_tabmancer_changes_the_teams(qapp, window):
    shown = window()
    assert shown.board.reveal(busy_one(shown).session_id)
    press(qapp, shown, "d")
    assert said(shown) == main.PICK_MEMBER
    on_the_team_panel(qapp, shown, "lead")
    press(qapp, shown, "d")
    assert said(shown).startswith("The lead stays: a team has one.")
    shown.team_panel.pick_member("ui")
    press(qapp, shown, "d")
    with config.locked(teams.FILE):  # another window, or the terminal, changing the teams
        press(qapp, shown, "d")
    assert said(shown).startswith("ui is still on the team")
    assert members_of() == ["lead", "api", "ui", "tests"]


def test_h_hands_the_member_whose_row_is_picked_alone_over_asked_how_as_h_asks(qapp, window, monkeypatch):
    shown = window()
    ui = shown.members_now(shown.snap.team_states[SHOP])["ui"]  # waiting: handed over at once, no queue
    shown.set_view("table")
    shown.table.fold(SHOP)
    assert shown.table.show_key(ui.session_id)
    written = []
    monkeypatch.setattr(shown.front, "write", lambda session, how: written.append((session.session_id, how)))
    asked = by_key(qapp, shown, "H", HandoverDialog)
    assert ui.title[:20] in asked.findChildren(type(asked.about))[0].text()  # its title heads the dialog
    QTest.keyClick(asked.ways, Qt.Key.Key_Return)  # the first way: a full handover
    until(qapp, lambda: bool(written))
    assert written == [(ui.session_id, "self")]


def with_docs(shown) -> None:
    """A second made-up team, docs, in the window's reading (shop's line-up under another name)."""
    shop = shown.snap.team_states[SHOP]
    shown.snap.team_states["docs"] = replace(shop, team=replace(shop.team, name="docs"))


def test_the_team_panel_over_the_sessions_is_at_hand_not_the_card_behind_it(qapp, window, monkeypatch):
    shown = window()
    with_docs(shown)
    handed, closing = [], []
    monkeypatch.setattr(shown.engine, "team_handover", lambda team, members: handed.append(team.name))
    monkeypatch.setattr(shown, "close_team", lambda state: closing.append(state.team.name))
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)  # shop's card picked on the board
    shown.show_teams()
    qapp.processEvents()
    shown.team_panel.team = "docs"  # the panel over the sessions shows docs (its tab clicked)
    assert shown.team_panel_over_sessions() and shown.team_at_hand().team.name == "docs"
    press(qapp, shown, "h")
    press(qapp, shown, "x")
    assert handed == ["docs"] and closing == ["docs"]
    asked = []
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: asked.append(session))
    closed = next(s for s in shown.snap.sessions.values()  # x hides a closed one (an open one always shows)
                  if s.exit is not Exit.RUNNING and s.session_id not in shown.front.members)
    assert shown.board.reveal(closed.session_id)  # a session's card behind, the panel with no team
    shown.show_teams()
    qapp.processEvents()
    shown.team_panel.team = None
    press(qapp, shown, "h")
    press(qapp, shown, "x")
    assert asked == [] and not marks.Marks.load().hidden  # what is out of sight is not handed over nor hidden
    shown.docks["Sessions"].raise_()  # the sessions in front again: x hides the card picked there
    qapp.processEvents()
    assert not shown.team_panel_over_sessions()
    press(qapp, shown, "x")
    assert marks.Marks.load().hidden == {closed.session_id}


def test_h_on_a_team_gone_meanwhile_hands_nothing_over_and_says_it_is_gone(qapp, window, monkeypatch):
    shown = window()
    asked = []
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: asked.append(session))
    monkeypatch.setattr(shown.engine, "team_handover", lambda team, members: asked.append(team))
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)
    del shown.snap.team_states[SHOP]  # gone meanwhile: its card not yet taken away by the next reading
    press(qapp, shown, "h")
    assert asked == [] and said(shown) == "There is no team shop any more."  # not "pick one": a card is picked


def test_x_on_a_team_with_none_open_hides_it_from_its_card_never_from_the_team_panel(qapp, window):
    """The terminal's team view: x closes the open members' tabs, and with none open says so; the team's
    closed sessions stay in sight. On the team's card (the dashboard's row) x hides them, as the terminal's."""
    shown = window()
    shop = shown.snap.team_states[SHOP]
    closed = {name: replace(s, session_id=f"closed-{name}", exit=Exit.CLEAN)  # none open: closed copies
              for name, s in shop.sessions.items() if s is not None}
    shown.snap.team_states[SHOP] = replace(shop, sessions=closed)
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)  # its card picked behind the panel
    on_the_team_panel(qapp, shown, None)
    assert shown.team_panel_over_sessions() and not VM.members_open(shown.team_at_hand(), shown.snap.sessions)
    press(qapp, shown, "x")
    press(qapp, shown, "x")
    assert said(shown) == teamkeys.NOBODY_OPEN and not marks.Marks.load().hidden
    shown.docks["Sessions"].raise_()  # the sessions in front: x on its card hides its closed members
    qapp.processEvents()
    assert not shown.team_panel_over_sessions() and shown.picked_team() == SHOP
    press(qapp, shown, "x")
    assert marks.Marks.load().hidden == {s.session_id for s in closed.values()}


def test_the_team_panel_beside_the_sessions_leaves_what_is_picked_there_at_hand(qapp, window, monkeypatch):
    shown = window()
    with_docs(shown)
    handed = []
    monkeypatch.setattr(shown.engine, "team_handover", lambda team, members: handed.append(team.name))
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: handed.append(session.session_id))
    shown.show_teams()
    shown.docks["Team"].setFloating(True)  # beside them: both in sight
    shown.docks["Sessions"].show()
    shown.docks["Sessions"].raise_()
    qapp.processEvents()
    shown.team_panel.team = "docs"
    assert shown.team_panel_in_front() and not shown.team_panel_over_sessions()
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)
    assert shown.team_at_hand().team.name == SHOP
    press(qapp, shown, "h")
    busy = busy_one(shown)
    assert shown.board.reveal(busy.session_id)  # a session's card: h hands it over, never the panel's team
    press(qapp, shown, "h")
    assert handed == [SHOP, busy.session_id]
    assert shown.team_at_hand().team.name == "docs"  # a, s and g: none picked, the panel's in sight


def test_d_with_the_team_panel_over_the_sessions_and_nobody_picked_there_takes_nobody_off(qapp, window):
    shown = window()
    ui = shown.members_now(shown.snap.team_states[SHOP])["ui"]
    shown.set_view("table")
    shown.table.fold(SHOP)
    assert shown.table.show_key(ui.session_id)  # ui's row picked, then out of sight
    on_the_team_panel(qapp, shown, None)
    press(qapp, shown, "d")
    press(qapp, shown, "d")
    assert said(shown) == main.PICK_MEMBER and members_of() == ["lead", "api", "ui", "tests"]


def test_d_off_a_tables_row_keeps_the_pick_of_another_team_in_the_panel(qapp, window):
    shown = window()
    with_docs(shown)
    api = shown.members_now(shown.snap.team_states[SHOP])["api"]
    shown.team_panel.team, shown.team_panel.picked = "docs", "api"  # docs' api, picked earlier in the panel
    shown.set_view("table")
    shown.table.fold(SHOP)
    assert shown.table.show_key(api.session_id) and not shown.team_panel_in_front()
    press(qapp, shown, "d")
    press(qapp, shown, "d")
    assert members_of() == ["lead", "ui", "tests"] and shown.team_panel.picked == "api"


def test_h_alone_on_a_member_asks_how_and_the_engines_way_writes(qapp, window, monkeypatch):
    shown = window()
    ui = shown.members_now(shown.snap.team_states[SHOP])["ui"]
    asked, written = [], []
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: asked.append((session.session_id, how)))
    monkeypatch.setattr(shown.front, "start", lambda work, *args, handing="": written.append(args))
    shown.front.hand_over(ui)  # teamkeys' Surface.hand_over: asked first
    shown.front.hand_over(ui, "transcript")  # the engine's, the way given: written, never asked again
    assert asked == [(ui.session_id, None)] and written == [(ui, "transcript")]
