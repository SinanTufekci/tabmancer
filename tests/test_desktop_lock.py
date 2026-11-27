"""The window and the actor lock at its edges (main.py, engine.py), beside test_desktop_acting's taking, losing and
handing it back: a window that finds the lock held at its start watches from the first moment; one that cannot
read its own start time never acts and says why; the look at the lock holds the relay, never the window (a window
freed on a reader's thread crashed the Windows CI once); a close that waits for a handover keeps the lock; and a
window closed while its look takes the lock leaves it free, and starts nothing after. Offscreen over the made-up
sessions of `tabmancer --demo`, the lock in the test's own config folder.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from dataclasses import replace
from datetime import datetime

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from test_desktop_acting import qapp, said, window, world  # noqa: F401 - the fixtures, shared

from tabmancer import actor, claudeupdate, engine, store
from tabmancer.desktop import front, main

HELD = {"pid": 4242, "started": "made-up", "command": "tabmancer"}  # another dashboard's lock


def test_a_window_that_finds_the_lock_held_watches_from_its_start(qapp, window, monkeypatch):  # noqa: F811
    """Not a moment of acting: no acting timers, the sessions to continue as the one that acts saved them, Bit says
    who acts, claude update left to that one; the lock still looked at, to take over when it goes."""
    monkeypatch.setattr(claudeupdate, "_elsewhere", False)  # put back after: the module keeps it
    armed = {"made-up-id": datetime(2026, 11, 27, 9, 0)}
    monkeypatch.setattr(actor, "claim", lambda: False)
    monkeypatch.setattr(actor, "holder", lambda: dict(HELD))
    monkeypatch.setattr(store, "read_armed", lambda: dict(armed))
    shown = window()
    assert not shown.engine.state.acts and shown.acting == {} and shown.actor_timer.isActive()
    assert shown.engine.state.armed == armed
    assert front.who_acts(HELD) in said(shown)
    assert claudeupdate._elsewhere and not claudeupdate.due()


def test_a_window_that_cannot_read_its_own_start_time_never_acts_and_says_why(qapp, window, monkeypatch):  # noqa: F811
    monkeypatch.setattr(actor, "nameless", lambda: True)
    shown = window()
    assert not shown.engine.state.acts and actor.holder() is None and shown.acting == {}
    assert main.NAMELESS in said(shown)
    shown.tick_actor()  # nor do its looks take the lock, free as it is
    shown.looker.join(5)
    qapp.processEvents()
    assert not shown.engine.state.acts and actor.holder() is None and shown.acting == {}
    shown.snap = replace(shown.snap, acting=None)
    shown.show_bit()
    assert shown.bit_line.text() == main.NAMELESS_REST


def test_the_look_at_the_lock_holds_the_relay_never_the_window(qapp, window, monkeypatch):  # noqa: F811
    """A thread that held the window could drop its last reference there: Qt objects freed off the window's thread
    (the Windows CI's access violation). The relay is the window's child, freed with it on its own thread."""
    gate = threading.Event()
    monkeypatch.setattr(engine, "look", lambda monitor, acts: gate.wait(5) and engine.Look("acting", kept=True))
    shown = window()
    made: list[dict] = []
    real = threading.Thread

    def recording(*args, **kwargs) -> threading.Thread:
        made.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(main.threading, "Thread", recording)
    shown.tick_actor()
    try:
        (asked,) = [kwargs for kwargs in made if kwargs.get("name") == "tabmancer-actor"]
        assert asked["target"] is main.look_at_lock and asked["args"] == (True, shown.relay) and asked["daemon"]
        assert shown.relay.parent() is shown
    finally:
        gate.set()
        shown.looker.join(5)


def test_a_close_that_waits_for_a_handover_being_written_keeps_the_lock(qapp, window):  # noqa: F811
    shown = window()
    shown.front.handing["made-up"] = "demo"  # h: a handover still being written
    shown.close()
    assert shown.isVisible() and actor.holds() and shown.acting and shown.actor_timer.isActive()
    shown.front.handing.clear()  # written: the window closes now (finish_closing)
    shown.close()
    assert not shown.isVisible() and actor.holder() is None and shown.acting == {}


def test_a_window_closed_while_its_look_takes_the_lock_leaves_it_free_and_starts_nothing(qapp, window,  # noqa: F811
                                                                                        monkeypatch):
    """It watched (another held the lock at its start, gone since); its next look takes the lock on its thread just
    as the window closes, before what it found is back. The close waits for the look (LOOK_WAIT): the lock it took
    goes with the window, and what the look found, arriving after, starts no acting in a closed window."""
    with monkeypatch.context() as held:
        held.setattr(actor, "claim", lambda: False)
        shown = window()
    assert not shown.engine.state.acts and actor.holder() is None  # the other one is gone: free
    shown.tick_actor()  # engine.look: actor.claim, on the look's thread
    shown.close()  # at once: what the look found is still on its way back
    assert not shown.isVisible() and not shown.looker.is_alive()
    assert actor.holder() is None
    qapp.processEvents()  # what the look found, back now
    assert shown.acting == {} and not shown.actor_timer.isActive() and actor.holder() is None
