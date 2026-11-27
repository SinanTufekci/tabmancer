"""n and T in the window (main.py): n's dialog (desktop/newsession.py) opens with the folders the sessions are
in and the rail's project ticked, and what it asks for opens: a fresh session in each folder for each line, or
the lines linked as a team of peers; T brings the Team panel and T again makes a new team (desktop/newteam.py),
saved, started and its tab picked once it shows. Nothing is saved while another tabmancer changes the teams:
said, and nothing opens. Offscreen over the made-up world of `tabmancer --demo`, with made-up folders. Skipped
without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import time
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
from test_desktop_front import read, taking
from test_desktop_handover import until

from tabmancer import claudeupdate, config, demo, launch, shutdowns, teams, workspaces
from tabmancer.desktop import frame, look, teampanel
from tabmancer.desktop.newsession import NewSessionDialog
from tabmancer.desktop.newteam import NewTeamDialog
from tabmancer.newforms import home_as_tilde
from tabmancer.sessions import Exit, folder_key
from tabmancer.teamforms import JoinTeam, TellTeam
from tabmancer.workspaces import Entry, Workspace

CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> SimpleNamespace:
    """The demo's made-up world; the tabs it would open, and the background sessions it would start, are kept
    here, none opens."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    kept = SimpleNamespace(tabs=[], quiet=[])
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: kept.tabs.extend(launches) or [])
    monkeypatch.setattr(launch, "run_in_background", lambda one: kept.quiet.append(one) or "")
    return kept


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
        read(qapp, shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def made_up_lab(tmp_path):
    """A made-up project folder, listed by n as a workspace's folder (it has no sessions)."""
    here = tmp_path / "made-up lab"
    here.mkdir()
    assert workspaces.keep(Workspace("Lab", [Entry(str(here))]))
    return here


def by_key(qapp, shown, key, kind):
    """key pressed from the event loop, as a key press comes: its dialog is then the active window, so its
    shortcuts work offscreen too."""
    QTimer.singleShot(0, shown.actions_by_key[key].trigger)
    until(qapp, lambda: isinstance(QApplication.activeWindow(), kind))
    (asked,) = open_of(shown, kind)
    return asked


def open_of(shown, kind) -> list:
    return [d for d in shown.findChildren(kind) if d.isVisible()]


def said(shown) -> str:
    return shown.bit_line.text()


def in_front(shown) -> bool:
    return not shown.docks["Team"].visibleRegion().isEmpty()


def test_n_starts_a_fresh_session_for_each_line_in_the_rails_project_ticked(qapp, window, world, tmp_path):
    here = made_up_lab(tmp_path)
    shown = window()
    before = set(teams.load())
    shown.set_project(folder_key(str(here)))
    asked = by_key(qapp, shown, "n", NewSessionDialog)
    assert asked.form.ticked == {folder_key(str(here))} and asked.form.cli == "claude"
    assert asked.asker is shown.asker  # one for the window, settled with it
    asked.message.setPlainText("Fix the made-up login\nWrite the made-up docs")
    QTest.keyClick(asked.folders, Qt.Key.Key_S, CTRL)
    until(qapp, lambda: len(world.tabs) >= 2)
    assert [(folder_key(t.cwd), t.cli, t.prompt) for t in world.tabs] == [
        (folder_key(str(here)), "claude", "Fix the made-up login"), (folder_key(str(here)), "claude", "Write the made-up docs")]
    assert not open_of(shown, NewSessionDialog) and set(teams.load()) == before and shown.new_team_shown is None  # apart


def test_n_linked_lines_start_as_a_team_of_peers_shown_in_the_team_panel(qapp, window, world, tmp_path):
    here = made_up_lab(tmp_path)
    shown = window()
    before = set(teams.load())
    shown.set_project(folder_key(str(here)))
    asked = by_key(qapp, shown, "n", NewSessionDialog)
    asked.message.setPlainText("api: the made-up endpoints\nui: the made-up screens")
    QTest.keyClick(asked.folders, Qt.Key.Key_L, CTRL)
    assert asked.form.linked
    QTest.keyClick(asked.folders, Qt.Key.Key_S, CTRL)
    until(qapp, lambda: len(world.tabs) >= 2)
    (name,) = set(teams.load()) - before
    team = teams.load()[name]
    assert folder_key(team.cwd) == folder_key(str(here)) and sorted(m.name for m in team.members) == ["api", "ui"]
    assert sorted(t.title for t in world.tabs) == [f"{name}: api", f"{name}: ui"]  # peers: no lead
    assert in_front(shown) and shown.new_team_shown is not None and shown.new_team_shown[0] == name  # shows once run


def test_n_linked_while_another_tabmancer_changes_the_teams_starts_nothing_and_says_so(qapp, window, world, tmp_path):
    here = made_up_lab(tmp_path)
    shown = window()
    before = teams.load()
    shown.set_project(folder_key(str(here)))
    asked = by_key(qapp, shown, "n", NewSessionDialog)
    asked.message.setPlainText("api: the made-up endpoints\nui: the made-up screens")
    QTest.keyClick(asked.folders, Qt.Key.Key_L, CTRL)
    with config.locked(teams.FILE):
        QTest.keyClick(asked.folders, Qt.Key.Key_S, CTRL)
    qapp.processEvents()
    assert said(shown) == teams.not_saved_words("Nothing was started", config.Busy(teams.FILE))
    assert look.AMBER in shown.bit_line.styleSheet() and not world.tabs and teams.load() == before


def test_t_brings_the_team_panel_and_t_again_makes_a_new_team_started_members_first(qapp, window, world, tmp_path):
    here = made_up_lab(tmp_path)
    shown = window()
    shop = shown.snap.team_states["shop"]
    running = {name for name, s in shop.sessions.items() if s is not None and s.exit is Exit.RUNNING}
    gone = min(running)
    taking(shown, replace(shop.sessions[gone], exit=Exit.CLEAN))  # one of shop's members closed since
    snap = shown.snap
    assert not in_front(shown)
    shown.actions_by_key["T"].trigger()
    qapp.processEvents()
    assert in_front(shown) and said(shown) == "The Team panel: T again starts a new team."
    assert not open_of(shown, NewTeamDialog)
    picked = next(s for s in snap.sessions.values() if s.cwd and s.title == "Fix the login redirect loop")
    shown.board.reveal(picked.session_id)
    asked = by_key(qapp, shown, "T", NewTeamDialog)
    assert asked.folder.text() == home_as_tilde(picked.cwd) and asked.form.taken == set(teams.load())
    assert [(team.name, names) for team, names in asked.form.working] == [("shop", running - {gone})]  # at work
    asked.name.setText("made-up-team")
    asked.folder.setText(str(here))
    asked.goal.setPlainText("Add a made-up checkout with tests")
    asked.members.setPlainText("lead: hands out the work\napi: the made-up endpoints")
    QTest.keyClick(asked.name, Qt.Key.Key_S, CTRL)
    until(qapp, lambda: len(world.tabs) >= 2)
    team = teams.load()["made-up-team"]
    assert folder_key(team.cwd) == folder_key(str(here)) and not world.quiet
    assert [t.title for t in world.tabs] == ["made-up-team: api", "made-up-team: lead"]  # the lead last
    assert team.members[0].lead and in_front(shown) and shown.new_team_shown[0] == "made-up-team"


def test_a_new_team_while_another_tabmancer_changes_the_teams_is_not_made_and_said(qapp, window, world, tmp_path):
    here = made_up_lab(tmp_path)
    shown = window()
    before = teams.load()
    shown.show_teams()
    qapp.processEvents()
    asked = by_key(qapp, shown, "T", NewTeamDialog)
    asked.name.setText("made-up-team")
    asked.folder.setText(str(here))
    asked.goal.setPlainText("Add a made-up checkout with tests")
    with config.locked(teams.FILE):
        QTest.keyClick(asked.name, Qt.Key.Key_S, CTRL)
    qapp.processEvents()
    assert said(shown) == teams.not_saved_words("made-up-team was not made", config.Busy(teams.FILE))
    assert look.AMBER in shown.bit_line.styleSheet() and teams.load() == before and not world.tabs
    assert shown.new_team_shown is None


def test_with_typing_off_a_goal_a_team_at_work_has_or_adds_to_is_said_and_put_on_the_clipboard(qapp, window, world,
                                                                                               monkeypatch):
    from tabmancer.desktop import front, main

    monkeypatch.setattr(front, "TYPES_INTO_TABS", False)  # on, Tell and Join are the terminal's (test_desktop_continue)
    shown = window()
    before = teams.load()
    shown.team_made(TellTeam("shop", "shop-lead", "A made-up request from the user"))
    assert QApplication.clipboard().text() == "A made-up request from the user"
    assert said(shown) == main.TELL_ELSEWHERE.format(team="shop", to="shop-lead")
    shown.team_made(JoinTeam("shop", "docs: the made-up docs", "shop-lead", "Docs joins", "the made-up docs"))
    assert QApplication.clipboard().text() == "the made-up docs"
    assert said(shown) == main.JOIN_ELSEWHERE.format(team="shop") and look.AMBER in shown.bit_line.styleSheet()
    assert teams.load() == before and not world.tabs and shown.new_team_shown is None


def test_a_team_just_made_has_its_tab_picked_once_a_reading_shows_it_for_a_while(qapp, window, monkeypatch):
    shown = window()
    panel = shown.team_panel
    picked = []
    monkeypatch.setattr(shown, "team_tab", picked.append)
    assert panel.names == ["shop"] and teampanel.NO_TEAM.startswith("No team yet. T starts one")
    shown.show_new_team("made-up later")  # no member has run yet: not in the panel
    read(qapp, shown)
    assert shown.new_team_shown[0] == "made-up later" and not picked and in_front(shown)  # still waited for
    shown.new_team_shown = ("shop", time.monotonic() + 30)  # as if made a moment ago: the next reading picks it
    read(qapp, shown)
    assert picked == ["shop"] and shown.new_team_shown is None
    shown.new_team_shown = ("shop", time.monotonic() - 1)  # too late: forgotten, nothing picked
    read(qapp, shown)
    assert picked == ["shop"] and shown.new_team_shown is None
    shown.show_new_team("shop")  # shown now: picked at once, and forgotten
    assert picked == ["shop", "shop"] and shown.new_team_shown is None
