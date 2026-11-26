""", in the window (desktop/clisettings.py): which CLIs are listed and the main one, picked as the terminal
dashboard's , dialog picks them, in clis.py's words; nothing saved till Enter; once other CLIs are listed,
the window lists its sessions again with them (a made-up Codex session joins the list, and goes when Codex
is turned off). Offscreen over the made-up world of `tabmancer --demo`. Skipped without the desktop extra
(pip install "tabmancer[desktop]")."""

import importlib
import os

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_clis import event, meta, rollout
from test_desktop_front import read
from test_desktop_handover import until

from tabmancer import claudeupdate, clis, demo, launch, shutdowns
from tabmancer.config import NOT_SAVED, config_dir
from tabmancer.desktop import frame, look
from tabmancer.desktop.clisettings import CliSettingsDialog, parts_html
from tabmancer.desktop.formparts import keys_html


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> Path:
    """The demo's made-up world, with a made-up Codex session in its Codex folder (listed once Codex is on)."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: [])
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    rollout(Path(os.environ["CODEX_HOME"]), [meta("codex-1"), event("user_message", message="Port the parser"),
                                             event("task_complete")], sid="codex-1")
    return tmp_path


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


def shown_dialogs(shown) -> list[CliSettingsDialog]:
    return [d for d in shown.findChildren(CliSettingsDialog) if d.isVisible()]


def press_comma(qapp, shown) -> CliSettingsDialog:
    shown.actions_by_key[","].trigger()
    qapp.processEvents()
    (asked,) = shown_dialogs(shown)
    return asked


def press(qapp, asked: CliSettingsDialog, *keys: Qt.Key) -> None:
    for key in keys:
        QTest.keyClick(asked, key)
        qapp.processEvents()


def lit(asked: CliSettingsDialog) -> list[int]:
    return [at for at, row in enumerate(asked.rows) if asked.accent in row.styleSheet()]


def test_comma_shows_each_cli_as_the_terminals_dialog_words_it(qapp, window):
    shown = window()
    read(qapp, shown)
    asked = press_comma(qapp, shown)
    texts = [widget.text() for widget in asked.findChildren(type(asked.main))]
    assert look.span(clis.SETTINGS_TITLE, f"bold {look.TEXT}") in texts
    assert look.span(clis.SETTINGS_ABOUT, look.MUTED) in texts
    assert [row.text() for row in asked.rows] == [parts_html(clis.option_parts(name, name == "claude"))
                                                   for name in clis.CLIS]
    assert "<br>" in asked.rows[0].text() and lit(asked) == [0]
    assert asked.main.text() == parts_html(clis.main_parts("claude"))
    assert asked.keys.text() == keys_html(clis.SETTINGS_KEYS) and asked.isModal()


def test_space_m_and_esc_change_nothing_saved(qapp, window):
    shown = window()
    read(qapp, shown)
    asked = press_comma(qapp, shown)
    press(qapp, asked, Qt.Key.Key_Down)
    assert lit(asked) == [1]
    press(qapp, asked, Qt.Key.Key_Space)  # Codex on
    assert asked.rows[1].text() == parts_html(clis.option_parts("codex", True))
    QTest.keyClick(asked, Qt.Key.Key_M, Qt.KeyboardModifier.ShiftModifier)  # M is not m
    assert asked.main.text() == parts_html(clis.main_parts("claude"))
    press(qapp, asked, Qt.Key.Key_M)  # agy main, and on
    assert asked.main.text() == parts_html(clis.main_parts("agy"))
    assert asked.rows[2].text() == parts_html(clis.option_parts("agy", True))
    press(qapp, asked, Qt.Key.Key_Up, Qt.Key.Key_Up)  # round to the last
    assert lit(asked) == [2]
    press(qapp, asked, Qt.Key.Key_Escape)
    assert shown_dialogs(shown) == [] and clis.enabled() == ["claude"] and clis.main_cli() == "claude"
    assert "Listing" not in shown.bit_line.text()


def test_enter_saves_and_the_list_is_read_again_with_the_clis_listed_now(qapp, window):
    shown = window()
    read(qapp, shown)
    assert "codex-1" not in shown.snap.sessions
    asked = press_comma(qapp, shown)
    saved: list = []
    asked.saved.connect(saved.append)
    press(qapp, asked, Qt.Key.Key_Down, Qt.Key.Key_Space)
    QTest.keyClick(asked, Qt.Key.Key_Return)
    QTest.keyClick(asked, Qt.Key.Key_Return)  # once is enough
    qapp.processEvents()
    assert shown_dialogs(shown) == [] and clis.enabled() == ["claude", "codex"] and len(saved) == 1
    assert shown.bit_line.text() == "Listing sessions from Claude Code, Codex."
    until(qapp, lambda: "codex-1" in shown.snap.sessions)  # read again by the window itself: no poll asked here
    assert shown.snap.sessions["codex-1"].cli == "codex" and shown.board.reveal("codex-1")
    asked = press_comma(qapp, shown)
    asked.rows[1].clicked.emit(1)  # a click: Codex off again
    assert lit(asked) == [1] and asked.rows[1].text() == parts_html(clis.option_parts("codex", False))
    press(qapp, asked, Qt.Key.Key_Return)
    read(qapp, shown)
    assert clis.enabled() == ["claude"] and "codex-1" not in shown.snap.sessions


def test_m_saves_agy_as_the_main_cli_and_a_config_json_it_cannot_read_is_said_and_left(qapp, window):
    shown = window()
    read(qapp, shown)
    asked = press_comma(qapp, shown)
    press(qapp, asked, Qt.Key.Key_M, Qt.Key.Key_Return)
    assert clis.main_cli() == "agy" and clis.enabled() == ["claude", "agy"]
    assert shown.bit_line.text() == "Listing sessions from Claude Code, Antigravity."
    read(qapp, shown)
    held = config_dir() / "config.json"
    held.write_text("{cut short", encoding="utf-8")
    polls = shown.store.polls
    asked = press_comma(qapp, shown)
    assert asked.main.text() == parts_html(clis.main_parts("claude"))  # as config.json reads now
    press(qapp, asked, Qt.Key.Key_Down, Qt.Key.Key_Space, Qt.Key.Key_Return)
    assert shown.bit_line.text() == NOT_SAVED and look.AMBER in shown.bit_line.styleSheet()
    assert held.read_text(encoding="utf-8") == "{cut short" and not shown.store.relisting
    shown.settle()
    assert shown.store.polls == polls  # nothing listed otherwise: not read again
