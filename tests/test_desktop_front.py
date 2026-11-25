"""The window as the engine's front (desktop/front.py): each reading goes to the window's own Engine, which
says what changed, lights its row and unticks one open after all, from the terminal dashboard's one copy;
and a window, never acting, does nothing by itself. Offscreen over the made-up sessions of `tabmancer
--demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import (
    actions,
    actor,
    alerts,
    bgjobs,
    claudeupdate,
    demo,
    engine,
    handover,
    launch,
    shutdowns,
    summarize,
    toast,
    typein,
)
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, front, look, main
from tabmancer.sessions import Exit

# What a window that does not act may have its front do for a reading: say, light, untick, name.
ALLOWED = {"who", "cap_of", "monitoring", "tell", "say_note", "show_bit", "flash_changed", "untick_open",
           "rows_changed"}
NEVER = [(toast, "show"), (typein, "write_keys"), (launch, "open_tabs"), (launch, "run_in_background"),
         (actions, "save_queued"), (actions, "write_handover"), (actor, "first_to"), (handover, "write_itself"),
         (summarize, "ask"), (claudeupdate, "run"), (claudeupdate, "before_opening"), (bgjobs, "_run")]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test; none ticked at the start."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    return tmp_path


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


def read(qapp, shown):
    shown.poll()
    deadline = time.monotonic() + 30
    while shown.waiting:
        assert time.monotonic() < deadline, "waited too long"
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()


def taking(shown, *changed):
    """A reading like the last one with these sessions as they are now."""
    snap = shown.snap
    shown.take(shown.store, replace(snap, sessions={**snap.sessions, **{s.session_id: s for s in changed}}), False, "")


def spying(called: list[str], name: str, real):
    def spy(*args, **kwargs):
        called.append(name)
        return real(*args, **kwargs)

    return spy


def busy_one(shown):
    """An open session at work, not a team's member (cli-tool, in the demo)."""
    return next(s for s in shown.snap.sessions.values() if s.exit is Exit.RUNNING and VM.state_of(s)[2] == 2
                and s.session_id not in shown.front.members)


def test_a_reading_goes_to_the_windows_engine_which_says_and_lights_what_changed(qapp, window):
    shown = window()
    read(qapp, shown)
    assert isinstance(shown.front, front.WindowFront) and shown.front.parent() is shown
    assert shown.engine.front is shown.front and not shown.engine.state.acts
    assert shown.engine.state.sessions == shown.snap.sessions and not shown.table.flash  # the first look lights none
    busy = busy_one(shown)
    waiting = replace(busy, live_state="idle")
    taking(shown, waiting)
    assert shown.bit_line.text() == f"{busy.project}: {VM.state_of(waiting)[0]}"  # alerts' words, as the terminal's
    assert set(shown.table.flash) == {busy.session_id}
    assert shown.engine.state.sessions[busy.session_id] is waiting


def test_a_member_is_named_by_its_team_and_the_engine_unticks_one_open_after_all(qapp, window):
    shown = window()
    read(qapp, shown)
    member_id, (team, name) = next(iter(shown.front.members.items()))
    member = shown.snap.sessions[member_id]
    assert shown.front.who(member) == VM.who(member, shown.front.members) == f"{team}: {name}"
    fell = replace(member, exit=Exit.RUNNING, fell_back="Opus 5.5 took over from Fable 5.2")
    taking(shown, fell)
    assert shown.bit_line.text().startswith(f"{team}: {name}: Opus 5.5 took over")  # loud: amber
    assert look.AMBER in shown.bit_line.styleSheet()
    closed = next(s for s in shown.snap.sessions.values() if s.exit is not Exit.RUNNING
                  and s.session_id not in shown.snap.hidden and VM.tickable(s))
    shown.selected, shown.preselected = {closed.session_id}, {closed.session_id}
    taking(shown, replace(closed, exit=Exit.RUNNING, live_state="idle"))
    assert shown.selected == shown.preselected == set()  # open after all: unticked (Engine.live → untick_open)


def test_a_window_that_does_not_act_only_says_lights_and_unticks(qapp, window, world, monkeypatch):
    """Readings that would have the acting dashboard send a desktop note (one turned to need you, one
    stopped at a limit), and Bit speak (a model fell back, a crowded context): the window's engine only
    says and lights them. Nothing is written, opened, typed, asked of a model or queued."""
    shown = window()
    read(qapp, shown)
    called: list[str] = []
    for name in dir(engine.Front):
        if name.startswith("_") or not callable(getattr(front.WindowFront, name, None)):
            continue
        monkeypatch.setattr(shown.front, name, spying(called, name, getattr(shown.front, name)))

    def never(*args, **kwargs):
        raise AssertionError("a window that does not act did this by itself")

    for module, name in NEVER:
        monkeypatch.setattr(module, name, never)
    files = {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in world.rglob("*") if path.is_file()}
    threads = set(threading.enumerate())
    busy = busy_one(shown)
    now = datetime.now()
    readings = [replace(busy, live_state="blocked"),  # needs you: the acting one's desktop note
                replace(busy, live_state="busy"),
                replace(busy, live_state="idle", resets=now + timedelta(hours=2)),  # stopped at a limit
                replace(busy, live_state="busy", resets=None, fell_back="Opus 5.5 took over from Fable 5.2"),
                replace(busy, live_state="busy", context=10**8)]  # crowded
    for fresh in readings:
        taking(shown, fresh)
        qapp.processEvents()
    assert {"tell", "flash_changed", "untick_open"} <= set(called) <= ALLOWED, called
    assert not shown.engine.state.acts and not shown.engine.state.queued and not shown.engine.state.notes
    assert files == {path: (path.stat().st_mtime_ns, path.stat().st_size) for path in world.rglob("*") if path.is_file()}
    assert set(threading.enumerate()) <= threads  # nothing started on a thread of its own
    assert shown.engine.state.told_crowded >= {busy.session_id}  # said once, as in the terminal


def test_events_say_their_note_else_bits_words_in_bits_tone(qapp, window):
    shown = window()
    note = actions.Told("cli-tool: waiting for you", seconds=10)
    shown.front.tell(alerts.Event(alerts.DONE, "x", note, alerts.Bit("celebrate", "cli-tool")))
    assert shown.bit_line.text() == "cli-tool: waiting for you"  # one line: the note says more
    shown.front.tell(alerts.Event("idle", "", None, alerts.Bit("say", "3 background sessions are idle", tone="green")))
    assert shown.bit_line.text() == "cli-tool: waiting for you"  # Bit's words wait their turn: the note stays
    for words in ("a", "b", "c"):
        shown.front.show_bit(alerts.Bit("say", words))
    assert [said[0] for said in shown.front.waiting] == ["a", "b", "c"]  # the newest few wait
    shown.front.waiting[0] = ("3 background sessions are idle", 4.0, look.GREEN)
    shown.said_timer.start(1)  # the note had its time
    QTest.qWait(50)
    assert shown.bit_line.text() == "3 background sessions are idle" and look.GREEN in shown.bit_line.styleSheet()
    assert [said[0] for said in shown.front.waiting] == ["b", "c"]
    shown.said_timer.stop()
    shown.front.waiting.clear()
    shown.front.show_bit(alerts.Bit("say", "plain words", tone="plain"))  # nothing said: at once
    assert look.TEXT in shown.bit_line.styleSheet()
    assert shown.front.monitoring() is False and shown.front.tell_desktop([]) is None
