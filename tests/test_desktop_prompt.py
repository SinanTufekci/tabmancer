"""p in the window (desktop/naming.py's PromptDialog, the Opener's thread): asked and said in the terminal
dashboard's words (prompting.py); a closed session opened with the prompt in a new tab (Enter) or in the
background (Ctrl+B), off the window's thread; an open one's tab brought forward instead, one in the
background refused. Offscreen over the made-up sessions of `tabmancer --demo`. Skipped without the desktop
extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from dataclasses import replace
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
from test_desktop_handover import closed_one, until

from tabmancer import claudeupdate, demo, launch, prompting, shutdowns
from tabmancer.desktop import frame, look
from tabmancer.desktop.formparts import keys_html
from tabmancer.desktop.naming import PromptDialog
from tabmancer.sessions import Status

MAIN = threading.main_thread()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test; what would open or start in the background kept
    (with the thread it came on) instead."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    opened: list = []
    started: list = []

    def open_tabs(launches, *rest):
        opened.append((threading.current_thread(), launches))
        return []

    def run_in_background(one):
        started.append((threading.current_thread(), one))
        return f"Started in the background: {one.session_id[:8]}."

    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(launch, "open_tabs", open_tabs)
    monkeypatch.setattr(launch, "run_in_background", run_in_background)
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    return SimpleNamespace(home=tmp_path, opened=opened, started=started)


@pytest.fixture
def window(qapp, world):
    from tabmancer.desktop import main

    made = []

    def make(demo=False):
        shown = main.MainWindow(start=False, demo=demo)
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


def prompt_dialog_of(shown) -> PromptDialog:
    (found,) = [d for d in shown.findChildren(PromptDialog) if d.isVisible()]
    return found


def no_prompt_dialog(shown) -> bool:
    return not any(d.isVisible() for d in shown.findChildren(PromptDialog))


def press_p(qapp, shown) -> None:
    shown.activateWindow()
    QTest.qWaitForWindowActive(shown, 3000)  # offscreen, a dialog closed leaves the window inactive
    QTest.keyClick(shown, Qt.Key.Key_P)
    qapp.processEvents()


def test_p_opens_a_closed_session_with_the_prompt_in_a_new_tab(qapp, window, world):
    """The heading, an empty line and the keys; Enter on nothing stays; Enter: the session resumed with the
    prompt, its tab opened on a thread, said."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    press_p(qapp, shown)
    asked = prompt_dialog_of(shown)
    assert asked.heading.text() == look.span(prompting.heading(session), f"bold {look.TEXT}")
    assert asked.line.text() == "" and asked.line.placeholderText() == prompting.PLACEHOLDER
    assert asked.focusWidget() is asked.line and asked.about.isHidden()
    assert asked.keys.text() == keys_html([("Enter", "open in a new tab"), ("Ctrl+B", "run in the background"),
                                           ("Esc", "cancel")])
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    qapp.processEvents()
    assert prompt_dialog_of(shown) is asked  # nothing to send yet
    QTest.keyClicks(asked.line, "Add the tests")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    assert no_prompt_dialog(shown)
    until(qapp, lambda: shown.bit_line.text() == f"Opened {session.project} in a new tab with your prompt.")
    ((thread, (launched,)),) = world.opened
    assert thread is not MAIN and world.started == []
    assert (launched.session_id, launched.prompt, launched.cli) == (session.session_id, "Add the tests", "claude")
    assert shown.after_open.isActive()  # read again soon: it shows open


def test_ctrl_b_starts_it_in_the_background(qapp, window, world):
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    press_p(qapp, shown)
    asked = prompt_dialog_of(shown)
    QTest.keyClicks(asked.line, "Go on")
    QTest.keyClick(asked.line, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert no_prompt_dialog(shown)
    until(qapp, lambda: shown.bit_line.text().startswith("Started in the background"))
    ((thread, launched),) = world.started
    assert thread is not MAIN and world.opened == [] and launched.prompt == "Go on"
    assert launched.session_id == session.session_id


def test_another_clis_session_is_asked_in_its_name_and_opens_in_a_tab(qapp, window, world, monkeypatch):
    """Codex has no background sessions: no Ctrl+B offered, and pressed anyway, a tab opens, said why."""
    shown = window()
    read(qapp, shown)
    session = replace(closed_one(shown), cli="codex")
    taking(shown, session)
    monkeypatch.setattr(shown, "picked_now", lambda: session.session_id)
    shown.prompt_key()
    qapp.processEvents()  # the dialog becomes the active window: Ctrl+B is its shortcut
    asked = prompt_dialog_of(shown)
    assert asked.line.placeholderText() == "What should Codex do next?"
    assert asked.keys.text() == keys_html([("Enter", "open in a new tab"), ("Esc", "cancel")])
    QTest.keyClicks(asked.line, "Go on")
    QTest.keyClick(asked.line, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: shown.bit_line.text().startswith("Codex has no background sessions: opening a tab instead."))
    ((_, (launched,)),) = world.opened
    assert launched.cli == "codex" and world.started == [] and look.AMBER in shown.bit_line.styleSheet()


def test_p_on_an_open_session_goes_to_its_tab_and_one_in_the_background_is_refused(qapp, window, world,
                                                                                     monkeypatch):
    """Nothing types into an open one: its tab comes forward. One in the background: attached to, said. Esc
    sends nothing."""
    shown = window()
    read(qapp, shown)
    went: list = []
    monkeypatch.setattr(shown.opener, "go_to", went.append)
    busy = busy_one(shown)
    monkeypatch.setattr(shown, "picked_now", lambda: busy.session_id)
    shown.prompt_key()
    assert no_prompt_dialog(shown) and went == [busy]
    session = closed_one(shown)
    background = replace(session, status=Status.BACKGROUND)
    taking(shown, background)
    monkeypatch.setattr(shown, "picked_now", lambda: session.session_id)
    shown.prompt_key()
    assert no_prompt_dialog(shown) and shown.bit_line.text() == prompting.ATTACH
    assert look.AMBER in shown.bit_line.styleSheet()
    taking(shown, session)
    said = shown.bit_line.text()
    shown.prompt_key()
    asked = prompt_dialog_of(shown)
    QTest.keyClicks(asked.line, "Never sent")
    QTest.keyClick(asked.line, Qt.Key.Key_Escape)
    qapp.processEvents()
    shown.opener.settle()
    qapp.processEvents()
    assert no_prompt_dialog(shown) and world.opened == world.started == [] and shown.bit_line.text() == said


def test_a_session_opened_meanwhile_gets_no_second_process(qapp, window, world, monkeypatch):
    """Open by the time Enter is pressed (a Claude process has it now): nothing opens, said."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    monkeypatch.setattr("tabmancer.desktop.opening.live_ids", lambda background: {session.session_id})
    monkeypatch.setattr(shown, "picked_now", lambda: session.session_id)
    shown.prompt_key()
    asked = prompt_dialog_of(shown)
    asked.line.setText("Go on")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    until(qapp, lambda: shown.bit_line.text() == f"{session.project} is open in another tab now: write to it there.")
    assert world.opened == []


def test_in_the_demo_the_demo_says_what_would_have_opened(qapp, window, world):
    """The demo's tell says it (nothing opens there): not 'Opened … with your prompt'."""
    shown = window(demo=True)
    read(qapp, shown)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    said = shown.bit_line.text()
    press_p(qapp, shown)
    asked = prompt_dialog_of(shown)
    asked.line.setText("Go on")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    until(qapp, lambda: len(world.opened) == 1)
    shown.opener.settle()
    qapp.processEvents()
    assert shown.bit_line.text() == said
