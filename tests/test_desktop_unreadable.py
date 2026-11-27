"""The window says how many transcripts could not be read (sessions.unreadable_words) once its first list is up,
as the terminal dashboard does: in amber, after Bit's hello when there is one, never again on later readings.
Over the made-up world of `tabmancer --demo`. Offscreen. Skipped without the desktop extra
(pip install "tabmancer[desktop]")."""

import importlib
import os

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_desktop_main import read

from tabmancer import demo, sessions, shutdowns
from tabmancer.desktop import frame, look, main
from tabmancer.desktop.front import TONES

OPEN_AT_SHUTDOWN = shutdowns.open_at_shutdown  # the real one: the demo's made-up shutdown ticks two


@pytest.fixture(scope="module")
def qapp():
    look.fonts_for_offscreen()
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture(autouse=True)
def counted():
    sessions.UNREADABLE.clear()
    yield sessions.UNREADABLE
    sessions.UNREADABLE.clear()


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """The window over the demo's made-up world (test_desktop_main's), put back after the test."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
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


def unread(count: int) -> str:
    return f"{count} transcripts could not be read and are left out of the list: tabmancer doctor says more."


def turn_of(shown, words: str) -> None:
    """Each thing said has its time (said_timer) until these words are on Bit's line; none covered them."""
    for _ in range(len(shown.front.waiting) + 1):
        if shown.bit_line.text() == words:
            return
        shown.said_timer.stop()
        shown.said_timer.timeout.emit()
    raise AssertionError(f"never said: {words!r} (on the line: {shown.bit_line.text()!r})")


def test_the_first_list_says_what_was_left_out_in_amber(qapp, window, counted):
    counted["TypeError: made up"] += 2
    shown = window()
    read(qapp, shown)  # the demo's limit note may come first: then it waits its turn
    assert [words for words, _, _ in shown.front.waiting].count(unread(2)) + (shown.bit_line.text() == unread(2)) == 1
    turn_of(shown, unread(2))
    assert TONES["amber"] in shown.bit_line.styleSheet()
    assert shown.said_timer.isActive() and shown.said_timer.interval() == 15_000


def test_after_the_shutdown_hello_it_waits_its_turn(qapp, window, counted, monkeypatch):
    monkeypatch.setattr(shutdowns, "open_at_shutdown", OPEN_AT_SHUTDOWN)
    counted["TypeError: made up"] += 2
    shown = window()
    read(qapp, shown)
    assert shown.bit_line.text() == "Hi! 2 were open when you shut down."  # the hello first, not covered
    assert unread(2) in [words for words, _, _ in shown.front.waiting]
    turn_of(shown, unread(2))


def test_a_later_reading_does_not_say_it_again_and_none_unreadable_says_nothing(qapp, window, counted):
    shown = window()
    read(qapp, shown)
    assert "could not be read" not in shown.bit_line.text()
    counted["TypeError: made up"] += 1  # found on a later reading: the doctor's to tell, not a new note
    shown.said_timer.stop()
    shown.front.waiting = []
    read(qapp, shown)
    assert "could not be read" not in shown.bit_line.text()
    assert not any("could not be read" in words for words, _, _ in shown.front.waiting)
