"""1 2 3 (or a click on a lane's heading) with nothing that card counts in the list: the window says
"Nothing for X right now." as the terminal dashboard does, and keeps the list as it was rather than narrow it to
nothing. Over the made-up world of `tabmancer --demo`. Offscreen. Skipped without the desktop extra
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
from test_desktop_main import lanes, press, read

from tabmancer import demo, shutdowns
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, look, main


@pytest.fixture(scope="module")
def qapp():
    look.fonts_for_offscreen()
    return QApplication.instance() or QApplication(["tabmancer-tests"])


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


def counted(shown, card: str) -> int:
    return sum(1 for s in shown.shown() if VM.counted_by(s, card))


def a_project_without(shown, card: str) -> str:
    """A project in the rail whose sessions that card counts none of (the demo has one)."""
    for row in list(shown.rail.projects.rows):
        if row.key == VM.ALL_PROJECTS:
            continue
        shown.set_project(row.key)
        if shown.shown() and not counted(shown, card):
            return row.key
    raise AssertionError(f"the demo has no project without {card}")


@pytest.mark.parametrize(("key", "card"), [("1", "open"), ("2", "waiting"), ("3", "continue")])
def test_a_card_with_nothing_says_so_and_keeps_the_list(qapp, window, key, card):
    shown = window()
    read(qapp, shown)
    project = a_project_without(shown, card)
    before, board = [s.session_id for s in shown.shown()], lanes(shown.board)
    shown.said_timer.stop()
    press(shown, key)
    assert shown.card is None and (shown.project, [s.session_id for s in shown.shown()]) == (project, before)
    assert lanes(shown.board) == board  # not narrowed to nothing
    assert shown.bit_line.text() == f"Nothing for {VM.CARDS[card][1]} right now."


def test_a_card_with_something_still_narrows_and_the_same_key_again_shows_all(qapp, window):
    shown = window()
    read(qapp, shown)
    everything = len(shown.shown())
    assert 0 < counted(shown, "continue") < everything
    press(shown, "3")
    assert shown.card == "continue" and len(shown.shown()) == counted(shown, "continue")
    assert "Nothing for" not in shown.bit_line.text()
    press(shown, "3")
    assert shown.card is None and len(shown.shown()) == everything


def test_another_card_with_nothing_keeps_the_card_already_picked(qapp, window):
    shown = window()
    read(qapp, shown)
    project = a_project_without(shown, "open")
    assert counted(shown, "continue")  # the demo's: something to continue there
    press(shown, "3")
    kept = [s.session_id for s in shown.shown()]
    press(shown, "1")
    assert (shown.card, shown.project) == ("continue", project) and [s.session_id for s in shown.shown()] == kept
    assert shown.bit_line.text() == f"Nothing for {VM.CARDS['open'][1]} right now."
