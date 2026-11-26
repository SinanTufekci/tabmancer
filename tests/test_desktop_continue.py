"""c in the window (main.py, desktop/front.py), as the terminal dashboard's c: the sessions stopped at a usage limit
in its dialog (desktop/limit.py, the terminal's words: limitwords), and what becomes of the ticked ones through the
window's own Engine (Engine.choose, one copy): now opens a closed one with the continue prompt; the window types
into no tab till Sinan says it may (front.TYPES_INTO_TABS), so an open one's prompt is handed to you, on the
clipboard with its tab in front; the handover hands each over from its transcript, as h does. Continuing when the
limit lifts is the acting dashboard's: never offered here, and refused were it asked. Tell's message for a member
goes through the same one place (WindowFront.types_in). Offscreen over the made-up sessions of `tabmancer --demo`.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from test_desktop_front import busy_one, read, taking
from test_desktop_handover import until

from tabmancer import actions, claudeupdate, demo, launch, limitwords, shutdowns, store, typein
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, front, main
from tabmancer.desktop.limit import LimitDialog
from tabmancer.launch import Mode
from tabmancer.sessions import Exit, Status
from tabmancer.teamforms import TellTeam

SHOP = "shop"  # the demo's team: lead, api, ui, tests, every one open, all Claude Code (no team bus)
PROMPT = launch.continue_prompt(Status.LIMIT)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> SimpleNamespace:
    """The demo's made-up world (two closed sessions a session limit stopped); the tabs it would open, the tabs
    it would go to and what it would type are kept here: none opens, moves or is typed into."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    kept = SimpleNamespace(tabs=[], went=[], typed=[])
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: kept.tabs.extend(launches) or [])
    monkeypatch.setattr(typein, "type_into", lambda sid, text: kept.typed.append((sid, text)) or True)
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


def stopped(shown) -> list:
    return actions.stopped_at_limit(shown.snap.sessions)


def open_at_limit(shown):
    """An open session at work, then stopped at a limit and waiting there (its tab still open)."""
    stopped_one = replace(busy_one(shown), live_state="idle", resets=datetime.now() + timedelta(hours=2))
    taking(shown, stopped_one)
    assert VM.at_limit(shown.snap.sessions[stopped_one.session_id])
    return stopped_one


def asked_by_c(qapp, shown) -> LimitDialog:
    """c run from the event loop, as a key press comes: its dialog is then the active window."""
    QTimer.singleShot(0, shown.actions_by_key["c"].trigger)
    until(qapp, lambda: isinstance(QApplication.activeWindow(), LimitDialog))
    (asked,) = [d for d in shown.findChildren(LimitDialog) if d.isVisible()]
    return asked


def choose(qapp, asked: LimitDialog, way: str) -> None:
    """The way picked and Enter, every session ticked as the dialog starts."""
    asked.way = [w for w, _ in asked.ways].index(way)
    QTest.keyClick(asked, Qt.Key.Key_Return)
    qapp.processEvents()


def said(shown) -> str:
    return shown.bit_line.text()


def test_c_shows_the_stopped_with_only_now_and_the_handover_where_the_window_never_acts(qapp, window):
    shown = window()
    assert not shown.engine.state.acts and shown.actions_by_key["c"].isEnabled()
    names = {s.session_id for s in stopped(shown)}
    assert len(names) == 2  # the demo's two, closed at a session limit
    asked = asked_by_c(qapp, shown)
    assert set(asked.ticked) == names  # every one ticked
    assert sorted(way for way, _ in asked.ways) == ["handover", "now"]  # no "lift", no "off"
    assert asked.not_here.isVisibleTo(asked)  # why continuing when the limit lifts is not here
    asked.reject()


def test_c_with_nothing_at_a_limit_says_so_and_asks_nothing(qapp, window):
    shown = window()
    taking(shown, *[replace(s, resets=None) for s in stopped(shown)])
    shown.actions_by_key["c"].trigger()
    qapp.processEvents()
    assert said(shown) == actions.NONE_STOPPED.words and not shown.findChildren(LimitDialog)


def test_now_opens_a_closed_one_with_the_prompt_and_hands_you_an_open_ones(qapp, window, world):
    shown = window()
    closed = stopped(shown)
    still = open_at_limit(shown)
    choose(qapp, asked_by_c(qapp, shown), "now")
    assert sorted(t.session_id for t in world.tabs) == sorted(s.session_id for s in closed)  # each in a new tab
    assert {(t.mode, t.prompt) for t in world.tabs} == {(Mode.AUTO, PROMPT)}
    assert world.typed == []  # the window types into no tab
    assert QApplication.clipboard().text() == PROMPT and [s.session_id for s in world.went] == [still.session_id]
    assert said(shown).startswith(f"Type continue in {still.project}'s tab: it is in front, the prompt on the clipboard")
    assert shown.engine.state.armed == {} and store.read_armed() == {}  # nothing set to go on by itself


def test_once_the_window_may_type_in_an_open_ones_prompt_is_typed_on_a_thread(qapp, window, world, monkeypatch):
    monkeypatch.setattr(front, "TYPES_INTO_TABS", True)  # Sinan's yes: the one line
    shown = window()
    still = open_at_limit(shown)
    QApplication.clipboard().setText("left alone")
    asked = asked_by_c(qapp, shown)
    asked.ticked = [still.session_id]
    choose(qapp, asked, "now")
    shown.front.settle()
    until(qapp, lambda: bool(world.typed) and "back at work" in said(shown))
    assert world.typed == [(still.session_id, PROMPT)] and world.tabs == [] and world.went == []
    assert QApplication.clipboard().text() == "left alone"


def test_the_handover_hands_each_ticked_over_from_its_transcript_as_h_does(qapp, window, monkeypatch):
    shown = window()
    handed = []
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: handed.append((session.session_id, how)))
    names = sorted(s.session_id for s in stopped(shown))
    choose(qapp, asked_by_c(qapp, shown), "handover")
    assert sorted(handed) == [(sid, "transcript") for sid in names]


def test_lift_and_off_are_refused_here_and_nothing_is_set_or_saved(qapp, window, world):
    shown = window()
    asked = limitwords.ask(shown.snap.sessions, [], None, [], {})
    ids = [s.session_id for s in asked.stopped]
    for way in ("lift", "off"):  # never offered here; asked anyway (the act API, say), refused
        shown.continue_chosen(asked, way, ids)
        assert said(shown).startswith(actions.ACTS_ELSEWHERE.words[:40])
    assert shown.engine.state.armed == {} and store.read_armed() == {} and world.tabs == []


def test_where_a_window_acted_lift_would_be_kept_and_the_rows_read_again(qapp, window, monkeypatch):
    shown = window()
    monkeypatch.setattr(shown.engine.state, "acts", True)  # never so today: what c's answer does there
    polled = []
    monkeypatch.setattr(shown, "poll", lambda: polled.append(True))
    asked = limitwords.ask(shown.snap.sessions, [], None, [], {})
    shown.continue_chosen(asked, "lift", [s.session_id for s in asked.stopped])
    assert set(shown.engine.state.armed) == {s.session_id for s in asked.stopped} and polled == [True]


def test_the_words_for_open_ones_name_each_tab_once_the_first_in_front():
    note = front.continue_handed(["api-server", "web-app", "api-server", "shop: ui"])
    assert note.words == ("Type continue in api-server's tab: it is in front, the prompt on the clipboard (Ctrl+V, "
                          "Enter). Then in web-app, shop: ui.")
    assert front.continue_handed(["web-app"]).words.endswith("(Ctrl+V, Enter).")


def test_the_drawers_continue_on_a_stopped_card_asks_as_c_does(qapp, window):
    shown = window()
    first = stopped(shown)[0]
    shown.show_pick(first.session_id)
    (button,) = [b for b in shown.drawer.findChildren(QPushButton) if b.property("key") == "c"]
    assert button.isEnabled()
    QTimer.singleShot(0, button.click)
    until(qapp, lambda: isinstance(QApplication.activeWindow(), LimitDialog))
    (asked,) = [d for d in shown.findChildren(LimitDialog) if d.isVisible()]
    asked.reject()


def test_tell_goes_through_the_same_place_handed_to_you_till_the_window_may_type(qapp, window, world, monkeypatch):
    shown = window()
    lead = shown.members_now(shown.snap.team_states[SHOP])["lead"]
    taking(shown, replace(lead, live_state="idle"))
    shown.team_made(TellTeam(SHOP, "shop-lead", "A made-up request"))
    assert QApplication.clipboard().text() == "A made-up request" and world.typed == []
    assert said(shown) == main.TELL_ELSEWHERE.format(team=SHOP, to="shop-lead")
    shown.front.type_note(lead, "shop-lead", "Another made-up one")  # a note due, were there one: handed too
    assert QApplication.clipboard().text() == "Another made-up one" and world.typed == []
    assert [s.session_id for s in world.went] == [lead.session_id]
    assert said(shown).startswith("The message for shop-lead is on the clipboard and its tab in front")
    monkeypatch.setattr(front, "TYPES_INTO_TABS", True)  # Sinan's yes: the terminal's Tell (TeamKeys.tell_team)
    shown.team_made(TellTeam(SHOP, "shop-lead", "A made-up request"))
    shown.front.settle()
    until(qapp, lambda: said(shown) == "Sent to shop-lead.")
    assert world.typed == [(lead.session_id, "A made-up request")] and shown.engine.state.notes == {}


def test_a_closed_member_told_opens_with_the_message_once_the_window_may_type(qapp, window, world, monkeypatch):
    monkeypatch.setattr(front, "TYPES_INTO_TABS", True)
    shown = window()
    ui = shown.members_now(shown.snap.team_states[SHOP])["ui"]
    taking(shown, replace(ui, exit=Exit.CLEAN))
    shown.team_made(TellTeam(SHOP, "shop-ui", "A made-up request"))
    assert [(t.title, t.prompt) for t in world.tabs] == [("shop: ui", "A made-up request")] and world.typed == []


def waiting_words(shown) -> list[str]:
    """Bit's words waiting their turn behind what was said (WindowFront.waiting)."""
    return [words for words, _seconds, _colour in shown.front.waiting]


def test_now_on_one_the_acting_dashboard_continues_at_its_reset_leaves_that_there_and_says_so(qapp, window, world):
    shown = window()
    first, second = sorted(stopped(shown), key=lambda s: s.session_id)
    at = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    assert actions.save_armed({first.session_id: at})  # set in the dashboard that acts
    read(qapp, shown)
    assert shown.snap.armed == {first.session_id: at}  # the window reads it, read-only
    choose(qapp, asked_by_c(qapp, shown), "now")
    assert sorted(t.session_id for t in world.tabs) == sorted([first.session_id, second.session_id])
    assert main.STAYS_ARMED.format(names=first.project) in waiting_words(shown) + [said(shown)]
    assert store.read_armed() == {first.session_id: at}  # the window changes nothing of that dashboard's


def test_the_handover_of_one_the_acting_dashboard_continues_at_its_reset_waits_for_c_there(qapp, window, monkeypatch):
    shown = window()
    first, second = sorted(stopped(shown), key=lambda s: s.session_id)
    at = (datetime.now() + timedelta(hours=2)).replace(microsecond=0)
    assert actions.save_armed({first.session_id: at})
    read(qapp, shown)
    handed = []
    monkeypatch.setattr(shown.engine, "handover", lambda session, how=None: handed.append((session.session_id, how)))
    choose(qapp, asked_by_c(qapp, shown), "handover")
    assert handed == [(second.session_id, "transcript")]  # the other one only
    assert main.HANDOVER_ARMED.format(names=first.project) in waiting_words(shown) + [said(shown)]
    shown.continue_chosen(limitwords.ask(shown.snap.sessions, [], None, [], shown.snap.armed), "handover",
                          [first.session_id])
    assert handed == [(second.session_id, "transcript")] and store.read_armed() == {first.session_id: at}
