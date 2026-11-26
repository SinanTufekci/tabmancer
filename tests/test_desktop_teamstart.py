"""s on a team in the window (main.py, desktop/front.py): every member not open starts, a closed one resumed
as itself, the lead last, as the terminal dashboard's s starts them (teamkeys.TeamKeys.start, one copy); one
opened from here a moment ago is not opened again; with every member open it is said; its cache gone, s says
what going on costs first and s again starts it; while another tabmancer starts members (the opening lock
held) nothing opens and it is said. s on a session says where its summary is. A team tab picked while a new
team is coming keeps the panel on it. Offscreen over the made-up sessions of `tabmancer --demo`. Skipped
without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from test_desktop_front import busy_one, read, taking

from tabmancer import claudeupdate, config, demo, launch, shutdowns, teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame
from tabmancer.desktop.main import START_ELSEWHERE
from tabmancer.sessions import Exit

SHOP = "shop"  # the demo's team: lead, api, ui, tests, every one open


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
    kept = SimpleNamespace(tabs=[])
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: kept.tabs.extend(launches) or [])
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


def closing(shown, *names: str, **changes) -> dict:
    """These members of shop closed (as on /exit), with these changes, in a reading like the last one; its
    members as they were before."""
    members = shown.members_now(shown.snap.team_states[SHOP])
    taking(shown, *(replace(members[name], exit=Exit.CLEAN, **changes) for name in names))
    return members


def press_s(qapp, shown, key: str = VM.TEAM_CARD + SHOP) -> None:
    """s on this card (shop's, unless said)."""
    assert shown.board.reveal(key)
    assert shown.why_not("s") is None  # the window does it, on a team and on a session
    shown.actions_by_key["s"].trigger()
    qapp.processEvents()


def said(shown) -> str:
    return shown.bit_line.text()


def start_button(shown) -> QPushButton | None:
    """The drawer's s button on the card picked, as the terminal's footer words it on that row."""
    found = [b for b in shown.drawer.findChildren(QPushButton) if b.isVisible() and b.property("key") == "s"]
    return found[0] if found else None


def test_s_on_a_team_card_resumes_its_closed_members_lead_last_and_not_twice(qapp, window, world):
    shown = window()
    members = closing(shown, "lead", "api")
    shown.engine.state.kept_handovers[SHOP] = "a made-up handover"  # written by h twice, never taken
    press_s(qapp, shown)
    assert [tab.title for tab in world.tabs] == ["shop: api", "shop: lead"]  # the lead last: it finds them up
    assert [tab.session_id for tab in world.tabs] == [members["api"].session_id, members["lead"].session_id]
    assert SHOP not in shown.engine.state.kept_handovers  # the team goes on as it was
    press_s(qapp, shown)  # at once again, before a reading shows them open: opened from here a moment ago
    assert len(world.tabs) == 2 and said(shown) == "Every member of shop is open already, or opening."


def test_s_with_every_member_open_says_so(qapp, window, world):
    shown = window()
    press_s(qapp, shown)
    assert not world.tabs and said(shown) == "Every member of shop is open already, or opening."


def test_s_on_a_team_whose_cache_is_gone_says_so_first_and_s_again_starts_it(qapp, window, world):
    shown = window()
    members = closing(shown, "api", last_active=datetime.now() - timedelta(hours=2), context=400_000)
    press_s(qapp, shown)
    assert not world.tabs and said(shown).startswith("shop was last at work 2h ago and its cache is gone")
    assert shown.engine.state.team_presses["cold"][0] == SHOP  # kept with the window: the next s finds it
    button = start_button(shown)  # again by the drawer's button, the same s
    assert button is not None and button.text().startswith("Start rest") and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert [tab.session_id for tab in world.tabs] == [members["api"].session_id]


def test_s_while_another_tabmancer_starts_members_opens_nothing_and_says_so(qapp, window, world):
    shown = window()
    closing(shown, "ui")
    with config.locked(teams.OPENING_FILE):  # another window, or tabmancer team start, at it
        press_s(qapp, shown)
    assert not world.tabs
    assert said(shown) == ("shop was not started: another tabmancer window is starting members right now. Press s "
                           "again in a moment.")
    press_s(qapp, shown)  # it is let go: s starts it
    assert [tab.title for tab in world.tabs] == ["shop: ui"]


def test_s_on_a_session_says_where_its_summary_is(qapp, window, world):
    shown = window()
    closing(shown, "api")
    press_s(qapp, shown, busy_one(shown).session_id)
    assert not world.tabs and said(shown) == START_ELSEWHERE
    assert start_button(shown) is None and "s" not in dict(shown.drawer.offered)  # no summary button: not here


def test_a_team_tab_picked_while_a_new_team_comes_keeps_the_panel_on_it(qapp, window):
    shown = window()
    snap = shown.snap
    (view,) = snap.view.teams

    def teams_shown(*names: str) -> None:
        """A reading with these teams in the panel besides shop (made-up copies of its view)."""
        views = [view, *(replace(view, name=name) for name in names)]
        shown.take(shown.store, replace(snap, view=replace(snap.view, teams=views)), False, "")
        qapp.processEvents()

    teams_shown("made-up other")
    tabs = shown.team_panel.tabs
    shown.show_new_team("made-up new")  # T made it; no member has run yet
    assert shown.new_team_shown is not None and shown.team_panel.team == SHOP
    QTest.mouseClick(tabs, Qt.MouseButton.LeftButton, pos=tabs.tabRect(1).center())  # meanwhile, another team
    assert shown.team_panel.team == "made-up other" and shown.new_team_shown is None
    teams_shown("made-up other", "made-up new")  # it shows now: the panel stays where it was put
    assert shown.team_panel.team == "made-up other" and tabs.currentIndex() == 1
