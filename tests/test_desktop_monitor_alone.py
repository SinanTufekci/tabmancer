"""The desktop team monitor says when no tabmancer dashboard is open (engine.ALONE), as the terminal monitor
did: it looks at the actor lock every ACTOR_MS on a thread of its own, says it once, and again only after a
dashboard was open meanwhile. Offscreen, over the made-up team of `tabmancer --demo`; the lock's holder is
stubbed. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
import time

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtWidgets import QApplication

from tabmancer import actor, demo, engine
from tabmancer.config import config_dir
from tabmancer.desktop import look, monitor

look.fonts_for_offscreen()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world (team "shop"), put back after the test."""
    for name in monitor.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, lambda words: None):
        monkeypatch.setattr(module, name, value)


@pytest.fixture
def held(monkeypatch):
    """The actor lock as a test says it is: running[0] True while a dashboard acts."""
    running = [False]
    monkeypatch.setattr(actor, "holder", lambda: {"pid": 1})
    monkeypatch.setattr(actor, "running", lambda record: running[0])
    return running


@pytest.fixture
def window(qapp, world):
    shown = monitor.MonitorWindow("shop", start=False)
    yield shown
    shown.settle()
    shown.close()
    shown.deleteLater()
    qapp.processEvents()


def looked(qapp, shown) -> None:
    """One tick of the actor timer, its look done and said."""
    shown.tick_actor()
    deadline = time.monotonic() + 10
    while shown.looker.is_alive():
        assert time.monotonic() < deadline, "the look took too long"
        time.sleep(0.01)
    qapp.processEvents()


def says(shown) -> str:
    return shown.bit_line.text()


def test_alone_it_says_so_once_and_again_after_a_dashboard_was_open(qapp, window, held):
    looked(qapp, window)
    assert says(window) == engine.ALONE.words and window.told_alone
    assert window.said_timer.isActive() and window.said_timer.interval() == engine.ALONE.seconds * 1000
    window.show_bit()  # the note's time is up: how the team stands again
    looked(qapp, window)
    assert says(window) != engine.ALONE.words  # said once, not at every look
    held[0] = True  # a dashboard opens
    looked(qapp, window)
    assert not window.told_alone and says(window) != engine.ALONE.words
    held[0] = False  # ...and closes
    looked(qapp, window)
    assert says(window) == engine.ALONE.words


def test_with_a_dashboard_open_nothing_is_said(qapp, window, held):
    held[0] = True
    window.show_bit()
    before = says(window)
    looked(qapp, window)
    assert says(window) == before and not window.told_alone


def test_a_look_that_fails_is_logged_and_the_next_one_looks_again(qapp, window, held, monkeypatch):
    def broken():
        raise OSError("actor.json held open")

    monkeypatch.setattr(actor, "holder", broken)
    window.show_bit()
    before = says(window)
    looked(qapp, window)
    assert says(window) == before and not window.told_alone
    assert "desktop.monitor.look_alone" in (config_dir() / "errors.log").read_text(encoding="utf-8")
    monkeypatch.setattr(actor, "holder", lambda: None)
    looked(qapp, window)
    assert says(window) == engine.ALONE.words


def test_one_look_at_a_time(qapp, window, monkeypatch):
    let_go = threading.Event()
    looks: list[int] = []

    def slow(monitor_, acts):
        looks.append(1)
        let_go.wait(10)
        return engine.Look("monitor", alone=False)

    monkeypatch.setattr(engine, "look", slow)
    window.tick_actor()
    first = window.looker
    window.tick_actor()  # the first still looks: no second one
    assert window.looker is first
    let_go.set()
    first.join(10)
    qapp.processEvents()
    assert looks == [1]


def test_the_lock_is_looked_at_as_often_as_the_terminal_monitor_did_and_stops_on_close(qapp, world, held):
    shown = monitor.MonitorWindow("shop")
    try:
        assert shown.actor_timer.isActive() and shown.actor_timer.interval() == engine.ACTOR_SECONDS * 1000
        shown.close()
        assert not shown.actor_timer.isActive()
    finally:
        shown.timer.stop()
        shown.settle()
        shown.deleteLater()
        qapp.processEvents()
