"""B in the window (desktop/backgrounds.py): the background sessions listed as the terminal dashboard's B lists
them, read off the window's thread; x, d and c on a second press, run on a thread too; Enter attaches; the
list read again while shown. In backgroundwords.py's words. Offscreen over the made-up world of `tabmancer
--demo`, with made-up background sessions. Skipped without the desktop extra (pip install
"tabmancer[desktop]")."""

import importlib
import os
import threading
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
from PySide6.QtWidgets import QApplication
from test_desktop_front import read
from test_desktop_handover import until

from tabmancer import backgroundwords, bgjobs, claudeupdate, demo, launch, shutdowns
from tabmancer.bgjobs import Job
from tabmancer.desktop import frame, look
from tabmancer.desktop.backgrounds import BackgroundDialog
from tabmancer.desktop.formparts import keys_html
from tabmancer.launch import Mode

MAIN = threading.main_thread()
NOW = datetime.now()


def made_up_jobs() -> list[Job]:
    """One of each kind, in the order `claude agents --json` is sorted into: needs you, working, idle, stopped."""
    return [Job("bg-1", "s-needs", "Answer the reviewer", "C:/work/shop", "blocked", True, NOW - timedelta(hours=2)),
            Job("bg-2", "s-work", "Nightly tests", "C:/work/api", "working", True, NOW - timedelta(minutes=30)),
            Job("bg-3", "s-idle", "Docs pass", "C:/work/docs", "idle", True, NOW - timedelta(minutes=5)),
            Job("bg-4", "s-old", "Old spike", "C:/work/lab", "done", False, NOW - timedelta(days=2))]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world with made-up background sessions: what reading and acting on them did, and
    on which thread, kept."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    kept = SimpleNamespace(found=made_up_jobs(), reads=[], ran=[], gate=None,  # gate: an Event the CLI waits on
                           failing=set())  # ids `claude rm` refuses

    def wait():
        if kept.gate is not None:
            assert kept.gate.wait(10)

    def jobs():
        kept.reads.append(threading.current_thread())
        wait()
        return None if kept.found is None else list(kept.found)

    def stop(job_id):
        kept.ran.append(("stop", job_id, threading.current_thread()))
        wait()
        kept.found = [j if j.id != job_id else Job(j.id, j.session_id, j.name, j.cwd, "done", False, j.started)
                      for j in kept.found]
        return "stopped"

    def remove(job_id):
        if job_id in kept.failing:
            raise OSError("it has unpushed work")
        kept.ran.append(("rm", job_id, threading.current_thread()))
        kept.found = [j for j in kept.found if j.id != job_id]
        return "removed"

    monkeypatch.setattr(bgjobs, "jobs", jobs)
    monkeypatch.setattr(bgjobs, "stop", stop)
    monkeypatch.setattr(bgjobs, "remove", remove)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: [])
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
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
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def shown_lists(shown) -> list[BackgroundDialog]:
    return [d for d in shown.findChildren(BackgroundDialog) if d.isVisible()]


def press_b(qapp, shown) -> BackgroundDialog:
    """B, and the list once read: the dialog, the active window (its keys are its shortcuts)."""
    shown.actions_by_key["B"].trigger()
    until(qapp, lambda: len(shown_lists(shown)) == 1)
    qapp.processEvents()
    (listed,) = shown_lists(shown)
    return listed


def cells(listed: BackgroundDialog, row: int) -> list[str]:
    return [listed.table.item(row, column).text() for column in range(listed.table.columnCount())]


def press(qapp, listed: BackgroundDialog, key: Qt.Key) -> None:
    QTest.keyClick(listed.table, key)
    qapp.processEvents()


def test_b_lists_the_background_sessions_read_on_a_thread(qapp, window, world):
    shown = window()
    read(qapp, shown)
    listed = press_b(qapp, shown)
    assert world.reads and all(thread is not MAIN for thread in world.reads)
    assert listed.heading.text() == (look.span("Background sessions", f"bold {look.TEXT}")
                                     + look.span("  4", look.MUTED) + look.span(" · 2 idle or stopped", look.MUTED))
    assert listed.table.rowCount() == 4 and listed.table.currentRow() == 0
    assert cells(listed, 0) == ["! needs you", "shop", "Answer the reviewer", "2h ago", "bg-1"]
    assert cells(listed, 3)[0] == "○ stopped" and listed.table.item(0, 0).font().bold()
    assert listed.table.item(0, 0).foreground().color().name() == look.colour_of(bgjobs.LABELS["needs"][1])[0].lower()
    assert listed.keys.text() == keys_html(backgroundwords.KEYS) and listed.focusWidget() is listed.table
    assert listed.note.text() == ""


def test_x_stops_one_on_a_second_press_on_a_thread_and_moving_forgets_the_first(qapp, window, world, monkeypatch):
    shown = window()
    read(qapp, shown)
    polled: list = []
    monkeypatch.setattr(shown, "poll", lambda: polled.append(1))
    listed = press_b(qapp, shown)
    press(qapp, listed, Qt.Key.Key_X)  # bg-1 waits for you: asked
    assert world.ran == [] and listed.note.text() == look.span(
        "shop is waiting for you: press x again to stop it. Its conversation is kept.", look.AMBER)
    press(qapp, listed, Qt.Key.Key_Down)  # another row: the first press forgotten
    assert listed.note.text() == look.span("", look.MUTED) and listed.confirm.armed is None
    press(qapp, listed, Qt.Key.Key_Up)
    press(qapp, listed, Qt.Key.Key_X)
    press(qapp, listed, Qt.Key.Key_X)
    until(qapp, lambda: listed.note.text() == look.span("Stopped shop.", look.GREEN))
    ((what, job_id, thread),) = world.ran
    assert (what, job_id) == ("stop", "bg-1") and thread is not MAIN and polled == [1]
    assert cells(listed, 0)[0] == "○ stopped"  # read again after it
    press(qapp, listed, Qt.Key.Key_X)
    assert listed.note.text() == look.span("bg-1 is not running.", look.MUTED)


def test_d_removes_and_c_clears_the_idle_and_stopped_ones(qapp, window, world):
    shown = window()
    read(qapp, shown)
    listed = press_b(qapp, shown)
    press(qapp, listed, Qt.Key.Key_Down)  # bg-2, at work
    press(qapp, listed, Qt.Key.Key_D)
    assert world.ran == [] and "Press d again to stop and remove bg-2." in listed.note.text()
    press(qapp, listed, Qt.Key.Key_D)
    until(qapp, lambda: "Removed api." in listed.note.text())
    assert [(what, job_id) for what, job_id, _ in world.ran] == [("stop", "bg-2"), ("rm", "bg-2")]
    assert listed.table.rowCount() == 3 and cells(listed, listed.table.currentRow())[4] != "bg-2"
    world.ran.clear()
    press(qapp, listed, Qt.Key.Key_C)
    assert world.ran == [] and "Press c again to remove 2 idle or stopped" in listed.note.text()
    press(qapp, listed, Qt.Key.Key_C)
    until(qapp, lambda: "Removed 2 background sessions." in listed.note.text())
    assert [(what, job_id) for what, job_id, _ in world.ran] == [("stop", "bg-3"), ("rm", "bg-3"), ("rm", "bg-4")]
    assert listed.table.rowCount() == 1
    press(qapp, listed, Qt.Key.Key_C)
    assert listed.note.text() == look.span(backgroundwords.NOTHING_TO_CLEAN, look.MUTED)
    world.failing = {"bg-1"}
    press(qapp, listed, Qt.Key.Key_D)
    press(qapp, listed, Qt.Key.Key_D)
    until(qapp, lambda: not listed.busy)
    assert listed.note.text() == look.span("Removed 0 of 1. bg-1: it has unpushed work", look.RED)
    assert cells(listed, 0)[0] == "○ stopped"  # stopped before `claude rm` refused


def test_enter_attaches_to_the_picked_one_in_a_tab(qapp, window, world, monkeypatch):
    shown = window()
    read(qapp, shown)
    opened: list = []
    monkeypatch.setattr(shown.engine, "open", opened.append)
    listed = press_b(qapp, shown)
    press(qapp, listed, Qt.Key.Key_Down)
    press(qapp, listed, Qt.Key.Key_Return)
    assert shown_lists(shown) == []
    ((launched,),) = opened
    assert (launched.mode, launched.session_id, launched.cwd) == (Mode.ATTACH, "bg-2", "C:/work/api")


def test_the_list_is_read_again_while_shown_but_not_under_a_first_press(qapp, window, world):
    """Every few seconds (the ticker): a session started meanwhile shows; never while a second press is
    awaited; a read for the list shown opens no second one."""
    shown = window()
    read(qapp, shown)
    listed = press_b(qapp, shown)
    assert listed.ticker.isActive() and listed.ticker.interval() == backgroundwords.REFRESH_SECONDS * 1000
    press(qapp, listed, Qt.Key.Key_Down)  # bg-2 picked
    world.found = [Job("bg-5", "s-new", "Fresh one", "C:/work/new", "blocked", True, NOW), *made_up_jobs()]
    reads = len(world.reads)
    listed.refresh()
    until(qapp, lambda: listed.table.rowCount() == 5)
    assert len(world.reads) == reads + 1 and len(shown_lists(shown)) == 1
    assert cells(listed, listed.table.currentRow())[4] == "bg-2"  # still picked, a row further down
    press(qapp, listed, Qt.Key.Key_X)  # armed
    world.found = made_up_jobs()
    listed.refresh()
    shown.jobs.settle()
    qapp.processEvents()
    assert len(world.reads) == reads + 1 and listed.table.rowCount() == 5


def test_a_read_under_way_when_x_is_pressed_does_not_replace_the_list(qapp, window, world):
    shown = window()
    read(qapp, shown)
    listed = press_b(qapp, shown)
    world.gate = threading.Event()
    listed.refresh()  # the CLI still answering
    world.found = [*made_up_jobs(), Job("bg-5", "s-new", "Fresh one", "C:/work/new", "working", True, NOW)]
    press(qapp, listed, Qt.Key.Key_X)
    world.gate.set()
    shown.jobs.settle()
    qapp.processEvents()
    assert listed.table.rowCount() == 4 and listed.confirm.armed == ("stop", "bg-1") and not listed.reading
    assert "press x again" in listed.note.text()


def test_while_commands_run_no_key_acts_and_the_list_is_not_read_again(qapp, window, world):
    shown = window()
    read(qapp, shown)
    listed = press_b(qapp, shown)
    press(qapp, listed, Qt.Key.Key_Down)
    press(qapp, listed, Qt.Key.Key_Down)  # bg-3, idle: x stops it at once
    world.gate = threading.Event()
    reads = len(world.reads)
    press(qapp, listed, Qt.Key.Key_X)
    assert listed.busy and listed.note.text() == look.span(backgroundwords.WORKING, look.MUTED)
    for key in (Qt.Key.Key_X, Qt.Key.Key_D, Qt.Key.Key_D, Qt.Key.Key_C, Qt.Key.Key_C):
        press(qapp, listed, key)
    listed.refresh()
    world.gate.set()
    until(qapp, lambda: not listed.busy)
    assert [(what, job_id) for what, job_id, _ in world.ran] == [("stop", "bg-3")]
    assert len(world.reads) == reads + 1  # once, by the commands run
    assert listed.note.text() == look.span("Stopped docs.", look.GREEN)


def test_b_twice_before_the_list_is_read_opens_one_list_and_enter_twice_attaches_once(qapp, window, world,
                                                                                      monkeypatch):
    shown = window()
    read(qapp, shown)
    opened: list = []
    monkeypatch.setattr(shown.engine, "open", opened.append)
    world.gate = threading.Event()
    reads = len(world.reads)
    shown.actions_by_key["B"].trigger()
    shown.actions_by_key["B"].trigger()
    world.gate.set()
    until(qapp, lambda: len(shown_lists(shown)) == 1)
    shown.jobs.settle()
    qapp.processEvents()
    (listed,) = shown_lists(shown)
    assert len(world.reads) == reads + 1
    QTest.keyClick(listed.table, Qt.Key.Key_Return)
    QTest.keyClick(listed.table, Qt.Key.Key_Return)
    qapp.processEvents()
    assert [[launched.session_id for launched in launches] for launches in opened] == [["bg-1"]]


def test_no_answer_from_the_cli_is_said_and_none_listed_is_said_in_the_list(qapp, window, world):
    shown = window()
    read(qapp, shown)
    world.found = None
    shown.actions_by_key["B"].trigger()
    until(qapp, lambda: shown.bit_line.text() == backgroundwords.NO_ANSWER)
    assert shown_lists(shown) == [] and look.AMBER in shown.bit_line.styleSheet()
    world.found = []
    listed = press_b(qapp, shown)
    assert listed.table.rowCount() == 0 and listed.note.text() == look.span(backgroundwords.EMPTY, look.MUTED)
    press(qapp, listed, Qt.Key.Key_Return)
    press(qapp, listed, Qt.Key.Key_X)
    assert shown_lists(shown) == [listed] and world.ran == []
