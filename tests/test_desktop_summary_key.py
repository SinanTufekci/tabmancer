"""s on a session in the window (main.py, desktop/drawer.py): its short summary written on a thread of its own
(desktop/summaries.Summarizer: summarize.summarize, as the terminal dashboard's s), the drawer saying it is being
asked for and then showing it; what came of it said in the terminal's words (sessionwords.summary_note); one the
terminal wrote meanwhile shown when the card is drawn again; a session that ran on another organisation says what
goes where first. s on a team still starts it, the Team panel over the sessions taking its team, never the card
behind. Offscreen over the made-up sessions of `tabmancer --demo`; no model is asked (the summary is made up here).
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from test_desktop_front import busy_one, read, taking
from test_desktop_handover import until

from tabmancer import accountwords, claudeupdate, demo, launch, sessionwords, shutdowns, summarize
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, main
from tabmancer.sessions import Exit

SHOP = "shop"  # the demo's team: lead, api, ui, tests, every one open
MADE_UP = "A made-up summary of what was done."


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


@pytest.fixture
def model() -> Iterator[SimpleNamespace]:
    """The summary s asks for, made up here and held back till let go (asked: the sessions it was asked of);
    let go when the test ends, so no thread is left waiting."""
    kept = SimpleNamespace(asked=[], gate=threading.Event(), error=None)

    def run(session):
        kept.asked.append(session.session_id)
        kept.gate.wait(10)
        if kept.error is not None:
            raise summarize.SummaryError(kept.error)
        return summarize.Summary({"summary": MADE_UP, "size": session.size, "model": summarize.MODEL}, True)
    kept.run = run
    yield kept
    kept.gate.set()


def asking(shown, model) -> None:
    """The window's summaries asked of the made-up model."""
    shown.summarizer.run = model.run


def closed_one(shown):
    """A closed session, not a team's member, with no summary yet."""
    return next(s for s in shown.snap.sessions.values() if s.exit is not Exit.RUNNING
                and s.session_id not in shown.front.members and s.session_id not in shown.summarizer.summaries)


def press_s(qapp, shown) -> None:
    shown.actions_by_key["s"].trigger()
    qapp.processEvents()


def preview(shown) -> str:
    return shown.drawer.said.text()


def said(shown) -> str:
    return shown.bit_line.text()


def test_the_summaries_written_before_show_in_the_preview(qapp, window):
    shown = window()
    assert shown.board.reveal("pcb-router")  # the demo's, in summaries.json
    assert demo.SUMMARY[:40] in preview(shown)


def test_s_on_a_session_asks_on_a_thread_and_the_preview_shows_its_summary_when_it_comes(qapp, window, model):
    shown = window()
    asking(shown, model)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    assert dict(shown.drawer.offered)["s"] == "Summary" and shown.why_not("s") is None
    press_s(qapp, shown)
    until(qapp, lambda: model.asked == [session.session_id])
    assert f"asking Claude ({summarize.MODEL})…" in preview(shown)  # the window never waits for it
    press_s(qapp, shown)  # again while it is written: not asked twice
    model.gate.set()
    until(qapp, lambda: MADE_UP in preview(shown))
    assert model.asked == [session.session_id] and not shown.summarizer.summarizing
    assert said(shown) != main.START_ELSEWHERE


def test_a_summary_for_a_card_no_longer_picked_leaves_the_preview_on_the_one_picked(qapp, window, model):
    shown = window()
    asking(shown, model)
    first = closed_one(shown)
    assert shown.board.reveal(first.session_id)
    press_s(qapp, shown)
    other = busy_one(shown)
    assert shown.board.reveal(other.session_id)  # another card picked while the summary is written
    model.gate.set()
    until(qapp, lambda: not shown.summarizer.summarizing)
    assert shown.drawer.key == other.session_id and MADE_UP not in preview(shown)
    assert shown.board.reveal(first.session_id)
    assert MADE_UP in preview(shown)


def test_a_summary_that_could_not_be_written_is_said_in_the_terminals_words(qapp, window, model):
    shown = window()
    asking(shown, model)
    model.error = "made-up: no quota left"
    assert shown.board.reveal(closed_one(shown).session_id)
    press_s(qapp, shown)
    model.gate.set()
    until(qapp, lambda: not shown.summarizer.summarizing)
    assert said(shown) == sessionwords.summary_note(None, "made-up: no quota left").words


def test_one_the_terminal_wrote_meanwhile_shows_when_the_card_is_drawn_again(qapp, window):
    shown = window()
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id) and MADE_UP not in preview(shown)
    summarize._store(session.session_id, {"summary": MADE_UP, "size": session.size, "model": "haiku"})
    shown.show_pick(session.session_id)
    assert MADE_UP in preview(shown)


def test_s_on_a_session_from_another_organisation_says_what_goes_where_first(qapp, window, model):
    shown = window()
    asking(shown, model)
    account = next(a for a in shown.snap.accounts if a.cli == "claude")
    assert account.org_id  # the demo's account, signed in to an organisation
    session = replace(closed_one(shown), org="a-made-up-other-org")
    taking(shown, session)
    assert shown.board.reveal(session.session_id)
    press_s(qapp, shown)
    note = accountwords.elsewhere_note(session, sessionwords.SUMMARY_GOES, account)
    assert note is not None and said(shown) == note.words
    until(qapp, lambda: model.asked == [session.session_id])  # said, not stopped: as the terminal's s


def test_the_drawers_summary_button_does_as_s(qapp, window, model):
    shown = window()
    asking(shown, model)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    qapp.processEvents()  # the drawer's new buttons show
    (button,) = [b for b in shown.drawer.findChildren(QPushButton) if b.isVisible() and b.property("key") == "s"]
    QTimer.singleShot(0, button.click)
    until(qapp, lambda: model.asked == [session.session_id])


def test_s_still_starts_a_team_and_the_team_panel_over_the_sessions_takes_its_team(qapp, window, model, monkeypatch):
    shown = window()
    asking(shown, model)
    started = []
    monkeypatch.setattr(shown.front, "start_team", lambda team, members: started.append(team.name))
    assert shown.board.reveal(VM.TEAM_CARD + SHOP)
    press_s(qapp, shown)
    assert started == [SHOP]
    assert shown.board.reveal(closed_one(shown).session_id)  # a session's card behind the Team panel
    shown.show_teams()
    qapp.processEvents()
    shown.team_panel.team = SHOP
    assert shown.team_panel_over_sessions()
    press_s(qapp, shown)
    assert started == [SHOP, SHOP] and model.asked == []
    shown.team_panel.team = None  # the panel over them with no team: the card behind is out of sight
    press_s(qapp, shown)
    assert started == [SHOP, SHOP] and model.asked == [] and said(shown) == main.START_ELSEWHERE


def test_s_with_the_team_panel_beside_the_sessions_summarizes_the_card_picked(qapp, window, model, monkeypatch):
    shown = window()
    asking(shown, model)
    started = []
    monkeypatch.setattr(shown.front, "start_team", lambda team, members: started.append(team.name))
    shown.show_teams()
    shown.docks["Team"].setFloating(True)  # beside them: both in sight
    shown.docks["Sessions"].show()
    shown.docks["Sessions"].raise_()
    qapp.processEvents()
    shown.team_panel.team = SHOP
    assert shown.team_panel_in_front() and not shown.team_panel_over_sessions()
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    press_s(qapp, shown)
    until(qapp, lambda: model.asked == [session.session_id])
    assert started == []
    shown.board.pick = None  # nothing picked: the panel's team, as a and g take it
    press_s(qapp, shown)
    assert started == [SHOP]


def test_s_with_nothing_picked_says_what_it_does(qapp, window, model):
    shown = window()
    asking(shown, model)
    shown.board.pick = None
    assert not shown.team_panel_in_front()
    press_s(qapp, shown)
    assert said(shown) == main.START_ELSEWHERE and model.asked == []
