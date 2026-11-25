"""r in the window (desktop/naming.py): refused, asked and said in the terminal dashboard's words (rename.py);
a closed session renamed on a thread of its own and back by a Signal, read again with its new name; an open one
handed /rename on the clipboard, its tab brought forward, to paste (renamed from outside, its next turn would
write the old name back). Offscreen over the made-up sessions of `tabmancer --demo`. Skipped without the
desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import json
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

from tabmancer import claudeupdate, demo, launch, rename, shutdowns
from tabmancer.desktop import frame, look
from tabmancer.desktop.formparts import keys_html
from tabmancer.desktop.naming import NameDialog

MAIN = threading.main_thread()


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test (its stand-in refuses to rename); no tab opens."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: [])
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    return SimpleNamespace(home=tmp_path)


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


def name_dialog_of(shown) -> NameDialog:
    (found,) = [d for d in shown.findChildren(NameDialog) if d.isVisible()]
    return found


def no_name_dialog(shown) -> bool:
    return not any(d.isVisible() for d in shown.findChildren(NameDialog))


def press_r(qapp, shown) -> None:
    shown.activateWindow()
    QTest.qWaitForWindowActive(shown, 3000)  # offscreen, a dialog closed leaves the window inactive
    QTest.keyClick(shown, Qt.Key.Key_R)
    qapp.processEvents()


def test_r_asks_a_closed_sessions_name_and_renames_it_on_a_thread(qapp, window, monkeypatch):
    """The old name there and picked (typing replaces it); Enter: /rename run in it on a thread, read again
    with the name Claude wrote into its transcript, said; the row read again."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    ran: dict[str, object] = {}

    def renaming(old, name):  # what Claude itself writes
        ran["thread"], ran["asked"] = threading.current_thread(), (old.session_id, name)
        with open(old.path, "a", encoding="utf-8") as transcript:
            transcript.write(json.dumps({"type": "custom-title", "customTitle": name}) + "\n")

    monkeypatch.setattr(rename, "rename", renaming)
    assert shown.board.reveal(session.session_id)
    press_r(qapp, shown)
    asked = name_dialog_of(shown)
    assert asked.heading.text() == look.span(rename.heading(session), f"bold {look.TEXT}")
    assert asked.line.text() == asked.line.selectedText() == session.title and asked.focusWidget() is asked.line
    assert asked.about.isHidden() and asked.keys.text() == keys_html([("Enter", rename.enter_words(session)),
                                                                       ("Esc", "cancel")])
    QTest.keyClicks(asked.line, "Fresh name")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    assert no_name_dialog(shown)
    until(qapp, lambda: shown.bit_line.text() == rename.renamed_note("Fresh name").words)
    assert ran["thread"] is not MAIN and ran["asked"] == (session.session_id, "Fresh name")
    read(qapp, shown)
    assert shown.snap.sessions[session.session_id].title == "Fresh name"


def test_r_hands_an_open_session_the_command_to_paste_into_its_tab(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    session = busy_one(shown)
    went: list = []
    monkeypatch.setattr(rename, "rename", lambda *args: pytest.fail("an open session renamed from outside"))
    monkeypatch.setattr(shown.opener, "go_to", went.append)
    assert shown.board.reveal(session.session_id)
    press_r(qapp, shown)
    asked = name_dialog_of(shown)
    assert not asked.about.isHidden() and rename.OPEN_WORDS in asked.about.text()
    assert asked.keys.text() == keys_html([("Enter", "copy /rename and go to its tab, to paste"), ("Esc", "cancel")])
    asked.line.setText("  Other  ")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    told = rename.open_told(session, "Other")
    assert QApplication.clipboard().text() == "/rename Other" and went == [session]
    assert shown.bit_line.text() == told.words


def test_r_refuses_what_cannot_be_renamed_and_does_nothing_unasked(qapp, window, monkeypatch):
    """Another CLI, a team's member: said, no dialog. Esc, the same name or none: nothing renamed or said."""
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    renamed: list = []
    monkeypatch.setattr(rename, "rename", lambda old, name: renamed.append(name))
    codex = replace(session, cli="codex")
    taking(shown, codex)
    monkeypatch.setattr(shown, "picked_now", lambda: session.session_id)
    shown.rename_key()
    assert no_name_dialog(shown) and shown.bit_line.text() == rename.refusal(codex)
    assert look.AMBER in shown.bit_line.styleSheet()
    member_id = next(iter(shown.front.members))
    monkeypatch.setattr(shown, "picked_now", lambda: member_id)
    shown.rename_key()
    assert no_name_dialog(shown) and shown.bit_line.text() == "Team members keep their names: the team finds them by it."
    taking(shown, session)
    monkeypatch.setattr(shown, "picked_now", lambda: session.session_id)
    said = shown.bit_line.text()
    for answer in ("esc", "same", "none"):
        shown.rename_key()
        asked = name_dialog_of(shown)
        if answer == "none":
            asked.line.clear()
        QTest.keyClick(asked.line, Qt.Key.Key_Escape if answer == "esc" else Qt.Key.Key_Return)
        qapp.processEvents()
        assert no_name_dialog(shown)
    shown.renamer.settle()
    qapp.processEvents()
    assert renamed == [] and shown.bit_line.text() == said


def test_in_the_demo_nothing_is_renamed_and_it_says_so(qapp, window):
    shown = window()
    read(qapp, shown)
    session = closed_one(shown)
    assert shown.board.reveal(session.session_id)
    press_r(qapp, shown)
    asked = name_dialog_of(shown)
    asked.line.setText("Made-up name")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    until(qapp, lambda: "was not renamed" in shown.bit_line.text())
    assert shown.bit_line.text() == f"{session.project} was not renamed: Nothing is renamed in the demo."
    assert look.RED in shown.bit_line.styleSheet()
