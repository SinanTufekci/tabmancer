"""`tabmancer cleanup` in the window (desktop/cleanup.py): the sessions on disk measured off the window's
thread, picked with Space, o and a as the terminal's screen picks them, Enter asking first, y moving them on a
thread, what it came to said in the terminal's words. Made-up sessions in a made-up Claude folder;
diskclean._trash_one faked: nothing reaches the Recycle Bin. Offscreen. Skipped without the desktop extra."""

import importlib
import json
import os
import time

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import cleanupwords, diskclean
from tabmancer.desktop.cleanup import CleanupDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A made-up Claude folder (as test_cleanup's, without the terminal's screen): add(sid) writes a session,
    days_old back, with file snapshots of history_bytes."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = tmp_path / "projects" / "-work-demo"
    project.mkdir(parents=True)

    def add(sid, days_old=0, history_bytes=0):
        records = [{"type": "user", "cwd": "/work/demo", "entrypoint": "cli", "message": {"content": sid}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}],
                                                     "stop_reason": "end_turn"}}]
        path = project / f"{sid}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        if history_bytes:
            (tmp_path / "file-history" / sid).mkdir(parents=True)
            (tmp_path / "file-history" / sid / "snap").write_bytes(b"x" * history_bytes)
        stamp = time.time() - days_old * 86400
        os.utime(path, (stamp, stamp))

    return tmp_path, add


@pytest.fixture
def trashed(monkeypatch):
    moved: list[str] = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: moved.append(path.name))
    return moved


def settled(qapp, dialog: CleanupDialog) -> None:
    dialog.disk.settle(5)
    for _ in range(20):
        qapp.processEvents()


def opened(qapp) -> CleanupDialog:
    dialog = CleanupDialog(None, "#d97757")
    settled(qapp, dialog)
    return dialog


def press(qapp, dialog: CleanupDialog, *keys) -> None:
    for key in keys:
        if isinstance(key, str):
            QTest.keyClicks(dialog.table, key)
        else:
            QTest.keyClick(dialog.table, key)
        settled(qapp, dialog)


def marks(dialog: CleanupDialog) -> list[str]:
    return [dialog.table.item(at, 0).text() for at in range(dialog.table.rowCount())]


def test_it_lists_them_biggest_first_with_the_line_and_the_detail(qapp, home, trashed):
    _, add = home
    add("small")
    add("big", history_bytes=500)
    dialog = opened(qapp)
    assert dialog.order == ["big", "small"] and dialog.table.rowCount() == 2
    assert "2 sessions use" in dialog.heading.text() and "0 selected" in dialog.heading.text()
    assert "On disk:" in dialog.detail.text() and "file-history" in dialog.detail.text()
    assert dialog.windowTitle() == cleanupwords.TITLE and dialog.disk.workers[0].name == "tabmancer-cleanup"
    dialog.close()


def test_space_o_and_a_pick_as_the_terminal_does(qapp, home, trashed):
    _, add = home
    add("old", days_old=20, history_bytes=100)
    add("new")
    dialog = opened(qapp)
    press(qapp, dialog, "o")
    assert dialog.selected == {"old"} and marks(dialog) == ["[x]", "[ ]"]
    press(qapp, dialog, "a")
    assert dialog.selected == {"old", "new"}
    press(qapp, dialog, "a")
    assert dialog.selected == set()
    press(qapp, dialog, Qt.Key.Key_Space)  # the first row, then on to the next
    assert dialog.selected == {"old"} and dialog.table.currentRow() == 1
    assert "1 selected" in dialog.heading.text() and trashed == []
    dialog.close()


def test_enter_asks_first_and_n_or_esc_moves_nothing(qapp, home, trashed):
    _, add = home
    add("old", days_old=20)
    dialog = opened(qapp)
    press(qapp, dialog, Qt.Key.Key_Return)
    assert "Select sessions with Space first." in dialog.note.text()
    press(qapp, dialog, "o", Qt.Key.Key_Return)
    assert "Move 1 sessions" in dialog.note.text() and dialog.asked is not None
    press(qapp, dialog, "n")
    assert dialog.asked is None and trashed == [] and dialog.selected == {"old"}
    press(qapp, dialog, Qt.Key.Key_Return, Qt.Key.Key_Escape)
    assert dialog.asked is None and trashed == [] and dialog.selected == {"old"}  # Esc: not asked, kept
    dialog.close()


def test_y_moves_them_on_a_thread_and_says_what_it_came_to(qapp, home, trashed):
    _, add = home
    add("old", days_old=20, history_bytes=100)
    add("new")
    dialog = opened(qapp)
    changed: list = []
    dialog.changed.connect(lambda: changed.append(True))
    press(qapp, dialog, "o", Qt.Key.Key_Return, "y")
    assert sorted(trashed) == ["old", "old.jsonl"]
    assert dialog.order == ["new"] and dialog.table.rowCount() == 1
    assert dialog.selected == set() and changed == [True]
    assert "Moved 1 sessions" in dialog.note.text() and diskclean.BIN in dialog.note.text()
    dialog.close()


def test_a_problem_is_said_as_plain_words(qapp, home, monkeypatch):
    _, add = home
    add("old", days_old=20)

    def refuse(path):
        raise OSError("denied <b>[/b]</b>")

    monkeypatch.setattr(diskclean, "_trash_one", refuse)
    dialog = opened(qapp)
    press(qapp, dialog, "o", Qt.Key.Key_Return, "y")
    assert "Moved 0 sessions" in dialog.note.text()
    assert "denied &lt;b&gt;[/b]&lt;/b&gt;" in dialog.note.text()  # escaped: shown, never read as HTML
    assert dialog.selected == {"old"} and dialog.table.rowCount() == 1  # not moved: still there, still picked
    dialog.close()


def test_an_open_session_is_never_picked(qapp, home, trashed, monkeypatch):
    _, add = home
    add("busy", days_old=20)
    monkeypatch.setattr(diskclean, "live_sessions", lambda: {"busy": {"pid": 1}})
    monkeypatch.setattr(diskclean, "mark_live", lambda session, live: setattr(session, "exit", diskclean.Exit.RUNNING)
                        if live else None)
    dialog = opened(qapp)
    press(qapp, dialog, "o", "a", Qt.Key.Key_Space)
    assert dialog.selected == set() and marks(dialog) == [" · "]
    assert cleanupwords.OPEN_NOW in dialog.detail.text()
    dialog.close()
