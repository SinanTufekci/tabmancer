"""h in the window (desktop/handover.py, desktop/front.py): its dialog asks how in the terminal dashboard's
words; the window's engine hands it over, the handover written on a thread and back by a Signal, the fresh
session opened as the engine opens one; a busy one queued here is handed over by this window alone, and one
queued in a terminal dashboard by that one alone. Offscreen over the made-up sessions of `tabmancer --demo`.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
import time
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_desktop_front import busy_one, read, taking

from tabmancer import (
    actions,
    actor,
    alerts,
    claudeupdate,
    clis,
    config,
    demo,
    engine,
    handover,
    handoverwords,
    launch,
    shutdowns,
)
from tabmancer.desktop import frame, front, look, main, opening
from tabmancer.desktop.closing import CloseDialog
from tabmancer.desktop.handover import HandoverDialog
from tabmancer.sessions import Session

MAIN = threading.main_thread()


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


def until(qapp, done, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not done():
        assert time.monotonic() < deadline, "waited too long"
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def closed_one(shown) -> Session:
    """A closed Claude Code session, not a team's member ('Fix the login redirect loop', in the demo)."""
    return next(s for s in shown.snap.sessions.values() if s.title == "Fix the login redirect loop")


def dialog_of(shown) -> HandoverDialog:
    (found,) = [d for d in shown.findChildren(HandoverDialog) if d.isVisible()]
    return found


def test_the_h_dialog_asks_how_in_the_terminals_words(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    answers: list[str] = []
    asked = HandoverDialog(shown, session, answers.append, shown.accent)
    asked.open()
    assert asked.ids() == [way.id for way in handoverwords.ways(session)] == ["self", "transcript"]
    assert asked.ways.currentRow() == 0 and asked.focusWidget() is asked.ways  # the most complete way first, for Enter
    assert "A fresh session takes over, with a full handover" in asked.about.text()
    QTest.keyClick(asked.ways, Qt.Key.Key_Down)
    QTest.keyClick(asked.ways, Qt.Key.Key_Return)
    assert answers == ["transcript"] and not asked.isVisible()
    cancelled = HandoverDialog(shown, session, answers.append, shown.accent)
    cancelled.open()
    QTest.keyClick(cancelled.ways, Qt.Key.Key_Escape)
    assert answers == ["transcript"] and not cancelled.isVisible()  # Esc: not handed over
    monkeypatch.setattr(clis, "installed", lambda name: False)
    agy = replace(session, cli="agy", path=None)  # no transcript to read, and no agy here to resume it
    assert HandoverDialog(shown, agy, answers.append, shown.accent).ids() == ["transcript"]


def test_h_asks_then_the_handover_is_written_on_a_thread_and_the_fresh_session_opens(qapp, window, world, monkeypatch):
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    ran: dict[str, threading.Thread] = {}

    def written(old, how, now, member_of):
        ran["write"] = threading.current_thread()
        assert (old.session_id, how) == (session.session_id, "transcript")
        return actions.Handed("THE HANDOVER")

    def ready(old, words, left_open=""):
        ran["ready"] = threading.current_thread()
        real(old, words, left_open)

    real = shown.engine.handover_ready
    monkeypatch.setattr(actions, "write_handover", written)
    monkeypatch.setattr(shown.engine, "handover_ready", ready)
    assert shown.board.reveal(session.session_id)
    QTest.keyClick(shown, Qt.Key.Key_H)
    asked = dialog_of(shown)
    asked.ways.setCurrentRow(1)  # from the transcript
    QTest.keyClick(asked.ways, Qt.Key.Key_Return)
    assert shown.bit_line.text() == handoverwords.started_note(session, "transcript").words
    until(qapp, lambda: world.opened)
    assert ran["write"] is not MAIN and ran["ready"] is MAIN  # written off the window's thread, back by a Signal
    (fresh,) = world.opened
    assert fresh.cwd == session.cwd and fresh.prompt.endswith("THE HANDOVER") and fresh.title.endswith("handed over")
    assert shown.bit_line.text() == handoverwords.handed_note(session).words  # Bit's words wait their turn
    assert shown.front.waiting[-1][0] == f"{session.project}: handed over"
    shown.front.settle()


def test_a_handover_that_fails_is_said_and_its_claim_given_back(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    let_go: list[str] = []

    def fails(old, how, now, member_of):
        raise actions.ActionError(f"{old.project} was not handed over: no transcript.")

    monkeypatch.setattr(actions, "write_handover", fails)
    monkeypatch.setattr(actor, "let_go", let_go.append)
    shown.engine.handover(session, "transcript")
    until(qapp, lambda: let_go)
    shown.front.settle()
    qapp.processEvents()
    assert let_go == [f"handover-{session.session_id}"]  # a queued one may be queued afresh
    assert shown.bit_line.text() == f"{session.project} was not handed over: no transcript."
    assert look.RED in shown.bit_line.styleSheet()


def test_the_thread_asks_the_window_how_the_session_is_now(qapp, window, monkeypatch):
    """Just before the old session closes, its thread asks the window's thread how the session and the
    open ones are now; a window that does not answer has it left open."""
    shown = window()
    read(qapp, shown)
    waiting = replace(busy_one(shown), live_state="idle")
    taking(shown, waiting)
    go, seen = threading.Event(), {}

    def written(old, how, now, member_of):
        assert go.wait(10)
        seen["now"] = now()
        return actions.Handed("THE HANDOVER")

    def answer(asked):
        seen["answered on"] = threading.current_thread()
        real(asked)

    real = shown.front.answer
    monkeypatch.setattr(actions, "write_handover", written)
    assert shown.front.asked.disconnect(real)  # the spy answers instead
    shown.front.asked.connect(answer)
    shown.engine.handover(waiting, "transcript")
    renamed = replace(waiting, title="Renamed while its handover was written")
    taking(shown, renamed)  # the list has it otherwise now
    go.set()
    until(qapp, lambda: "now" in seen)
    now, open_ones = seen["now"]
    assert now is renamed and seen["answered on"] is MAIN
    assert open_ones == shown.engine.open_ones() and renamed in open_ones
    shown.front.settle()
    qapp.processEvents()
    monkeypatch.setattr(front, "NOW_SECONDS", 0.2)
    caught: list[Exception] = []

    def unanswered():
        try:
            shown.front.now(renamed)
        except handover.HandoverError as error:
            caught.append(error)

    thread = threading.Thread(target=unanswered)
    thread.start()
    thread.join(5)  # the window's thread waits here, so nothing answers
    assert caught and "not closed" in str(caught[0])
    qapp.processEvents()  # the late question answered, to no one: harmless


class Terminal(engine.Front):
    """A terminal dashboard's engine's front, as far as these readings and h go: what it hands over and says."""

    default_cap = 300_000

    def __init__(self) -> None:
        self.handed: list[str] = []
        self.said: list[str] = []

    def who(self, session):
        return session.project

    def cap_of(self, session):
        return self.default_cap

    def monitoring(self):
        return False

    def tell(self, event):
        self.said.append(event.note.words if event.note else event.bit.words if event.bit else event.kind)

    def say_note(self, note):
        self.said.append(note.words)

    def hand_over(self, session, how):
        self.handed.append(session.session_id)

    def update_row(self, session): ...
    def tell_desktop(self, turned): ...
    def flash_changed(self, updates): ...
    def untick_open(self, updates): ...
    def rows_changed(self, ids): ...


def test_a_handover_queued_here_is_fired_by_this_window_alone_and_one_queued_in_a_terminal_by_that_one(
        qapp, window, monkeypatch):
    """Three working sessions: x queued with h in the window, y in a terminal dashboard, z in both. As they
    finish, the window hands over x and z (the first to claim it), the terminal y alone, and says z is
    another window's."""
    shown = window()
    read(qapp, shown)
    busy = busy_one(shown)
    x, y, z = (replace(busy, session_id=f"{name}-1", title=f"Work queued {name}") for name in ("x", "y", "z"))
    taking(shown, x, y, z)
    handed: list[str] = []
    monkeypatch.setattr(shown.front, "hand_over", lambda session, how: handed.append(session.session_id))
    terminal_front = Terminal()
    terminal = engine.Engine(terminal_front, datetime.now)
    terminal.live({s.session_id: s for s in (x, y, z)}, False, set())
    shown.engine.handover(x, "transcript")
    shown.engine.handover(z, "transcript")
    terminal.handover(y, "self")
    terminal.handover(z, "self")
    assert shown.engine.state.queued == {"x-1": "transcript", "z-1": "transcript"}
    assert terminal.state.queued == {"y-1": "self", "z-1": "self"}
    assert shown.bit_line.text() == actions.queued_note(z, "transcript").words
    read(qapp, shown)  # the reading update_row asked for, done before they finish
    done = [replace(s, live_state="idle") for s in (x, y, z)]
    taking(shown, *done)
    terminal.live({s.session_id: s for s in done}, False, set())
    assert sorted(handed) == ["x-1", "z-1"] and terminal_front.handed == ["y-1"]
    assert f"{z.project} finished: another tabmancer window hands it over." in terminal_front.said
    assert not shown.engine.state.queued and not terminal.state.queued
    assert shown.engine.state.unasked == {"x-1", "z-1"}  # their fresh sessions open unasked


def test_claude_update_runs_on_a_thread_before_the_fresh_session_opens(qapp, window, world, monkeypatch):
    shown = window()
    read(qapp, shown)
    fresh = handover.fresh(closed_one(shown), "THE HANDOVER")
    ran: dict[str, threading.Thread] = {}

    def update():
        ran["update"] = threading.current_thread()
        assert not world.opened  # the update first, the tab after it
        return claudeupdate.Outcome(True, "2.1.0", "2.1.1"), None

    monkeypatch.setattr(alerts, "update_due", lambda updating, clis: not updating)
    monkeypatch.setattr(alerts, "run_claude_update", update)
    shown.engine.open([fresh])
    assert shown.engine.state.updating_claude and not world.opened  # it waits for the update
    until(qapp, lambda: world.opened)
    assert ran["update"] is not MAIN and world.opened == [fresh] and not shown.engine.state.updating_claude
    shown.front.settle()
    world.opened.clear()
    shown.front.update = False  # the demo's window: never the real claude update
    monkeypatch.setattr(alerts, "run_claude_update", lambda: pytest.fail("claude update ran over made-up sessions"))
    shown.engine.open([fresh])
    assert world.opened == [fresh] and not shown.engine.state.updating_claude


def test_the_opened_sessions_and_the_leftovers_are_the_windows_way(qapp, window, world, monkeypatch):
    """The fresh session's tab: kept with Enter's (one copy of what opened from here), unticked, and with
    no terminal that opens tabs, its command on the clipboard."""
    shown = window()
    read(qapp, shown)
    fresh = handover.fresh(closed_one(shown), "THE HANDOVER")
    assert shown.engine.state.openings is shown.opener.openings
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: ["claude --resume made-up"])
    shown.engine.open([fresh])
    assert QApplication.clipboard().text() == "claude --resume made-up"
    assert shown.bit_line.text() == opening.NO_TERMINAL and look.AMBER in shown.bit_line.styleSheet()
    assert shown.isVisible()  # the window stays: the terminal dashboard exits with them instead


def close_dialog_of(shown) -> CloseDialog:
    (found,) = [d for d in shown.findChildren(CloseDialog) if d.isVisible()]
    return found


def test_closing_with_handovers_queued_here_asks_first_and_keeps_them_unless_closed_anyway(qapp, window, monkeypatch):
    """h on a busy one queues its handover in this window, which alone hands it over: closing asks, by name.
    Esc, Enter or Keep it open keep the window and the queue; Close anyway drops them, for every window."""
    shown = window()
    read(qapp, shown)
    busy = busy_one(shown)
    monkeypatch.setattr(shown.front, "hand_over", lambda session, how: pytest.fail("handed over on closing"))
    shown.engine.handover(busy, "transcript")
    assert shown.engine.state.queued == {busy.session_id: "transcript"}
    assert actions.read_queued() == {busy.session_id: "transcript"}  # the other windows show it queued
    for key in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
        shown.close()
        shown.close()  # asked once, however often
        asked = close_dialog_of(shown)
        assert handoverwords.queued_on_closing(1) in asked.question.text()
        assert shown.front.who(busy) in asked.names.text() and asked.focusWidget() is asked.keep
        QTest.keyClick(asked.focusWidget(), key)
        qapp.processEvents()
        assert not asked.isVisible() and shown.isVisible()
        assert shown.engine.state.queued == {busy.session_id: "transcript"}
    shown.close()
    close_dialog_of(shown).leave.click()
    qapp.processEvents()
    assert not shown.isVisible() and not shown.engine.state.queued and actions.read_queued() == {}


def test_closing_anyway_while_another_window_saves_the_queue_waits_only_a_moment(qapp, window, monkeypatch):
    """Close anyway runs on the UI thread: with the queue's lock held elsewhere it waits UI_TRIES (~75 ms), not
    ~1 s, and closes; the queue is left as it is (its entries by this window go once its process has)."""
    shown = window()
    read(qapp, shown)
    busy = busy_one(shown)
    shown.engine.handover(busy, "transcript")
    shown.close()
    waits: list[float] = []
    monkeypatch.setattr(config, "time", SimpleNamespace(time=time.time, sleep=waits.append))  # config's clock only
    with config.locked(actions.QUEUE):  # another window saving it
        close_dialog_of(shown).leave.click()
    qapp.processEvents()
    assert not shown.isVisible() and not shown.engine.state.queued
    assert waits == [config.BUSY_WAIT] * (config.UI_TRIES - 1)
    assert actions.read_queued() == {busy.session_id: "transcript"}  # not saved: left for its process to end

def test_closing_while_a_handover_is_written_waits_for_it_and_its_fresh_session(qapp, window, world, monkeypatch):
    """The handover's thread is never cut short (a session closed to write its own would lose it): the window
    says so, stays, and closes once it is written and its fresh session opened, after the claude update the
    fresh session waited for."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    written_now, updated_now = threading.Event(), threading.Event()

    def written(old, how, now, member_of):
        assert written_now.wait(10)
        return actions.Handed("THE HANDOVER")

    def update():
        assert updated_now.wait(10)
        return claudeupdate.Outcome(True, "2.1.0", "2.1.1"), None

    monkeypatch.setattr(actions, "write_handover", written)
    shown.engine.handover(session, "transcript")
    shown.close()
    assert shown.isVisible() and shown.close_after
    written_now.set()
    until(qapp, lambda: not shown.isVisible())  # no update due: closed as its fresh session opened
    assert [fresh.prompt.endswith("THE HANDOVER") for fresh in world.opened] == [True]
    world.opened.clear()
    written_now.clear()
    shown = window()
    read(qapp, shown)
    monkeypatch.setattr(alerts, "update_due", lambda updating, clis: not updating)
    monkeypatch.setattr(alerts, "run_claude_update", update)
    shown.engine.handover(session, "transcript")
    assert shown.front.handing_now() == [session.project]
    shown.close()
    assert shown.isVisible() and shown.close_after
    assert shown.bit_line.text() == handoverwords.finishing_note([session.project]).words
    written_now.set()
    until(qapp, lambda: not shown.front.handing_now() and shown.engine.state.updating_claude)
    assert shown.isVisible() and not world.opened  # written; its fresh session waits for the update
    updated_now.set()
    until(qapp, lambda: not shown.isVisible())
    (fresh,) = world.opened
    assert fresh.prompt.endswith("THE HANDOVER")


def test_settle_waits_for_a_handover_however_long_and_cuts_the_rest_short(qapp, window, monkeypatch):
    """settle is the last wait before the process ends, and these threads with it: a session's handover and a
    team's run to their end; any other thread is waited for up to the seconds given."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    team = next(iter(shown.snap.team_states.values())).team
    go, other = threading.Event(), threading.Event()

    def written(old, how, now, member_of):
        assert go.wait(10)
        return actions.Handed("THE HANDOVER")

    def team_written(team, members, now, close_tabs):
        assert go.wait(10)

    monkeypatch.setattr(actions, "write_handover", written)
    monkeypatch.setattr(actions, "write_team_handover", team_written)
    shown.engine.handover(session, "transcript")
    shown.front.hand_over_team(team, {})
    shown.front.start(other.wait, 10)  # an ordinary thread
    assert sorted(shown.front.handing_now()) == sorted([session.project, f"team {team.name}"])
    threading.Timer(0.3, go.set).start()
    shown.front.settle(0.05)
    handing = [t for t in shown.front.workers if t.name == front.HANDOVER_THREAD]
    assert len(handing) == 2 and not any(t.is_alive() for t in handing) and not shown.front.handing_now()
    assert [t.is_alive() for t in shown.front.workers if t.name != front.HANDOVER_THREAD] == [True]  # cut short
    other.set()
    shown.front.settle()
    qapp.processEvents()  # the handover's Signal, late: the fresh session opens all the same
