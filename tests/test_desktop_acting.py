"""The window acts (main.py, desktop/front.py, store.py), as the terminal dashboard does (Sinan's call, 2026-11-26):
at its start it takes the actor lock when it is free (MainWindow.mount: Engine.mount), looks at it every ACTOR_MS on
a thread of its own (tick_actor: engine.look, then Engine.seen on the window's thread), and while it holds it runs
engine.SCHEDULE's acting timers (a newer tabmancer, the sessions to continue at their reset, Wrapped), sends the
desktop notes while you are not looking at it, records the plan limits it reads and keeps the sessions to continue
itself. Lost, those stop and Bit says who acts; taken over, they start again; closed, the lock goes. The lock's
own steps are the engine's tests'. Offscreen over the made-up sessions of `tabmancer --demo`, the lock in the
test's own config folder.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
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
from PySide6.QtWidgets import QApplication, QWidget
from test_desktop_front import busy_one, read, taking
from test_desktop_handover import until

from tabmancer import accounts, actions, actor, alerts, demo, engine, launch, shutdowns, store, toast, usage
from tabmancer.config import merge_json
from tabmancer.desktop import frame, front, main
from tabmancer.desktop.limit import LimitDialog

HOUR = 3_600_000  # a poll timer this slow never reads by itself in a test: read() does


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> SimpleNamespace:
    """The demo's made-up world; the tabs it would open and the newer tabmancer it would find are kept here."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    kept = SimpleNamespace(tabs=[], looked=[])
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: kept.tabs.extend(launches) or [])
    monkeypatch.setattr(alerts, "newer_tabmancer", lambda told: kept.looked.append(threading.current_thread().name))
    return kept


@pytest.fixture
def window(qapp, world):
    made = []

    def make(start: bool = True):
        shown = main.MainWindow(start=start, poll_ms=HOUR)
        shown.resize(1500, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)
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


def said(shown) -> list[str]:
    """What Bit's line says now, and Bit's words waiting their turn behind it."""
    return [shown.bit_line.text(), *(words for words, _seconds, _colour in shown.front.waiting)]


def another_window_in_front(qapp) -> QWidget:
    """A window not the dashboard's is the active one: you are not looking at the dashboard."""
    other = QWidget()
    other.show()
    other.activateWindow()
    QTest.qWaitForWindowActive(other, 3000)
    qapp.processEvents()
    return other


def toasts_done() -> None:
    for thread in threading.enumerate():
        if thread.name == "tabmancer-toast":
            thread.join(5)


def test_at_its_start_a_free_lock_is_taken_and_what_acting_runs_starts(qapp, window, world, monkeypatch):
    wrapped = []
    monkeypatch.setattr(alerts, "wrapped_event", lambda at: wrapped.append(at))  # Friday or not: looked for
    shown = window()
    assert shown.engine.state.acts and actor.holds()
    assert shown.actor_timer.isActive() and shown.actor_timer.interval() == main.ACTOR_MS == 10_000
    assert {name: timer.interval() for name, timer in shown.acting.items()} == {
        "check_updates": engine.UPDATES_SECONDS * 1000, "continue_due": engine.DUE_SECONDS * 1000,
        "tell_wrapped": engine.WRAPPED_AFTER * 1000}  # Wrapped's after the first reading (Bit's hello), soon
    assert all(timer.isActive() and timer.parent() is shown for timer in shown.acting.values())
    shown.front.settle()
    assert world.looked == ["tabmancer-front"]  # a newer tabmancer looked for at once, off the window's thread
    shown.acting["tell_wrapped"].timeout.emit()
    assert len(wrapped) == 1 and shown.acting["tell_wrapped"].interval() == engine.WRAPPED_SECONDS * 1000
    shown.show_bit()
    assert shown.bit_line.text() == main.ACTS_HERE


def test_a_window_built_not_to_start_never_takes_the_lock_nor_looks(qapp, window, world):
    shown = window(start=False)
    assert not shown.engine.state.acts and actor.holder() is None and not actor.holds()
    assert shown.acting == {} and not shown.actor_timer.isActive() and world.looked == []


def test_the_lock_is_looked_at_off_the_windows_thread_lost_and_taken_over_again(qapp, window, monkeypatch):
    """Another holds the lock now: what acting runs stops and Bit says who acts. The next look finds it free: the
    window takes it over, its timers start again (Wrapped's too), and it says so. One look at a time."""
    shown = window()
    held = {"pid": 4242, "started": "made-up", "command": "tabmancer"}
    looked: list[tuple[bool, bool, bool]] = []
    gate = threading.Event()

    def look(monitor: bool, acts: bool) -> engine.Look:
        looked.append((threading.current_thread() is threading.main_thread(), monitor, acts))
        gate.wait(5)
        return engine.Look("acting", held=held, kept=False) if acts else engine.Look("took")

    monkeypatch.setattr(engine, "look", look)
    shown.tick_actor()
    until(qapp, lambda: looked)
    shown.tick_actor()  # the first still under way: no second
    gate.set()
    until(qapp, lambda: not shown.engine.state.acts)
    assert looked == [(False, False, True)] and shown.acting == {}
    assert front.who_acts(held) in said(shown) and "(pid 4242)" in front.who_acts(held)
    shown.snap = replace(shown.snap, acting=held)  # as the next reading has it
    shown.show_bit()
    assert shown.bit_line.text() == main.ACTS_THERE
    shown.looker.join(5)
    shown.tick_actor()
    until(qapp, lambda: shown.engine.state.acts)
    assert looked[-1] == (False, False, False)
    assert set(shown.acting) == {"check_updates", "continue_due", "tell_wrapped"}
    assert engine.TOOK_OVER.words in said(shown)


def test_closed_the_lock_goes_and_what_acting_runs_stops_never_on_a_close_it_asks_about(qapp, window):
    shown = window()
    assert actor.holds()
    timers = list(shown.acting.values())
    shown.engine.state.queued = {next(iter(shown.snap.sessions)): "self"}  # h on a busy one: closing asks first
    shown.close()
    assert shown.isVisible() and actor.holds() and shown.acting and shown.actor_timer.isActive()
    shown.close_anyway()
    assert not shown.isVisible() and actor.holder() is None and not actor.holds()
    assert shown.acting == {} and not shown.actor_timer.isActive() and not any(t.isActive() for t in timers)
    assert shown.engine.state.acts  # it acted till the end: nothing more runs (unmount lets the lock go)


def test_the_desktop_note_goes_only_from_the_window_that_acts_and_never_while_you_look_at_it(qapp, window,
                                                                                            monkeypatch):
    sent = []
    monkeypatch.setattr(toast, "show", lambda title, body, dashboard: sent.append(
        (title, body, dashboard, threading.current_thread().name)) or toast.SHOWN)
    shown = window()
    busy = busy_one(shown)
    assert shown.front.looked_at()
    taking(shown, replace(busy, live_state="idle"))  # it waits for you now
    toasts_done()
    assert sent == []  # you are looking at the window
    other = another_window_in_front(qapp)
    assert not shown.front.looked_at()
    taking(shown, busy)  # at work again, then waiting for you
    taking(shown, replace(busy, live_state="idle"))
    again = busy
    until(qapp, lambda: sent)
    title, body, dashboard, thread = sent[0]
    assert (dashboard, thread) == (front.DASHBOARD_TAB, "tabmancer-toast") and again.project in title + body
    shown.front.stop_acting()  # another dashboard took the lock over
    shown.front.send_toast("A note", "from the one that acts only")
    assert merge_json("config.json", desktop_notifications=False)
    shown.engine.state.acts = True
    shown.front.tell_desktop([again])  # notes turned off (tabmancer notifications off)
    toasts_done()
    assert len(sent) == 1
    other.close()
    monkeypatch.setattr(front.QApplication, "activeWindow", staticmethod(lambda: shown))
    assert shown.front.looked_at()
    shown.showMinimized()  # minimised, even while Qt still names it the active window
    qapp.processEvents()
    assert not shown.front.looked_at()
    shown.showNormal()


def test_the_plan_limits_it_reads_are_recorded_and_each_reading_said(qapp, window, monkeypatch):
    recorded = []
    monkeypatch.setattr(usage, "record", lambda limits, now=None, account="": recorded.append(list(limits)))
    near = [usage.Limit("session", 91, datetime.now() + timedelta(hours=1), "")]
    monkeypatch.setattr(usage, "read_usage", lambda: list(near))
    shown = window()
    assert shown.store.acts and shown.snap.limits == near and recorded == [near] and shown.snap.limits_read == 1
    careful = "Careful: session limit at 91%"
    assert careful in said(shown)
    shown.front.waiting.clear()
    shown.say("something else", 1)
    read(qapp, shown)
    assert shown.snap.limits_read == 1 and recorded == [near] and careful not in said(shown)  # read every five minutes
    shown.front.refresh_usage()  # signed in with another account: read again at once
    until(qapp, lambda: not shown.waiting)
    read(qapp, shown)
    assert shown.snap.limits_read == 2 and recorded == [near, near] and careful in said(shown)


def test_a_window_that_watches_records_no_plan_limit(qapp, window, monkeypatch):
    recorded = []
    monkeypatch.setattr(usage, "record", lambda limits, now=None, account="": recorded.append(list(limits)))
    shown = window(start=False)
    assert not shown.store.acts and shown.snap.limits and recorded == []


def test_the_limits_as_they_stand_after_each_change_kept_where_it_acts(qapp, window, monkeypatch):
    kept: list[dict] = []
    monkeypatch.setattr(accounts, "keep_limited", lambda limited, tries=None: kept.append(dict(limited)))
    shown = window()
    at = {s.session_id for s in actions.stopped_at_limit(shown.snap.sessions)}
    assert at and shown.engine.state.told_limit == at and kept  # Bit said which a limit stopped, once each
    watching = window(start=False)
    kept.clear()
    taking(watching, *watching.snap.sessions.values())
    assert watching.engine.state.told_limit == at and kept == []  # said there too; kept by the one that acts


def test_a_newer_tabmancer_is_told_once_and_stays_beside_the_name(qapp, window, world, monkeypatch):
    monkeypatch.setattr(alerts, "newer_tabmancer", lambda told: None if told else ("9.9.9", "uv tool upgrade tabmancer"))
    shown = window()
    until(qapp, lambda: shown.engine.state.told_update == "9.9.9")
    assert "↑&nbsp;9.9.9" in shown.brand.text() or "↑ 9.9.9" in shown.brand.text()
    assert any(words.startswith("tabmancer 9.9.9 is out") for words in said(shown))
    shown.front.waiting.clear()
    shown.say("something else", 1)
    shown.acting["check_updates"].timeout.emit()
    shown.front.settle()
    qapp.processEvents()
    assert not any("9.9.9" in words for words in said(shown))  # told once


def test_c_where_the_window_acts_keeps_the_sessions_to_continue_itself_and_continues_them(qapp, window, world):
    shown = window()
    stopped = sorted(actions.stopped_at_limit(shown.snap.sessions), key=lambda s: s.session_id)
    ids = {s.session_id for s in stopped}
    QTimer.singleShot(0, shown.actions_by_key["c"].trigger)
    until(qapp, lambda: isinstance(QApplication.activeWindow(), LimitDialog))
    (asked,) = [d for d in shown.findChildren(LimitDialog) if d.isVisible()]
    assert "lift" in [way for way, _ in asked.ways]  # offered here: this window acts
    asked.way = [way for way, _ in asked.ways].index("lift")
    QTest.keyClick(asked, Qt.Key.Key_Return)
    qapp.processEvents()
    assert set(store.read_armed()) == ids == set(shown.engine.state.armed)
    read(qapp, shown)
    assert set(shown.snap.armed) == ids
    first = stopped[0]
    shown.engine.state.armed[first.session_id] = datetime.now() - timedelta(hours=1)  # its limit lifted
    shown.acting["continue_due"].timeout.emit()
    until(qapp, lambda: world.tabs)
    assert [t.session_id for t in world.tabs] == [first.session_id]
    assert set(store.read_armed()) == ids - {first.session_id}
    shown.continue_chosen(asked.asked, "now", [stopped[1].session_id])
    assert store.read_armed() == {} and not any(main.STAYS_ARMED.split("}")[1] in w for w in said(shown))
