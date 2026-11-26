"""w and o, in the window (desktop/workspacelist.py, main.py): w saves the ticked sessions' folders as a
workspace, its name asked for; o lists the workspaces saved, Enter opens one's folders in new tabs (on a
thread), d deletes one. In workspacewords.py's words, the terminal dashboard's. Offscreen over the made-up
world of `tabmancer --demo`, with made-up folders. Skipped without the desktop extra
(pip install "tabmancer[desktop]")."""

import importlib
import os

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

from tabmancer import claudeupdate, config, demo, launch, shutdowns, workspaces, workspacewords
from tabmancer.config import config_dir
from tabmancer.desktop import frame, look
from tabmancer.desktop.clisettings import parts_html
from tabmancer.desktop.formparts import keys_html
from tabmancer.desktop.naming import NameDialog
from tabmancer.desktop.workspacelist import WorkspacesDialog
from tabmancer.launch import Mode
from tabmancer.workspaces import Entry, Workspace


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch) -> list:
    """The demo's made-up world; the tabs it would open are kept here (the list), none opens."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    monkeypatch.setattr(claudeupdate, "due", lambda today=None: False)
    opened: list = []
    monkeypatch.setattr(launch, "open_tabs", lambda launches, *rest: opened.extend(launches) or [])
    return opened


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
        read(qapp, shown)
        return shown

    yield make
    for shown in made:
        shown.settle()
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def shown_of(shown, kind):
    return [d for d in shown.findChildren(kind) if d.isVisible()]


def press(qapp, asked, *keys: Qt.Key) -> None:
    for key in keys:
        QTest.keyClick(asked, key)
        qapp.processEvents()


def said(shown) -> str:
    return shown.bit_line.text()


def lit(asked: WorkspacesDialog) -> list[int]:
    return [at for at, row in enumerate(asked.rows) if asked.accent in row.styleSheet()]


def saved_two(tmp_path) -> tuple[Workspace, Workspace]:
    pcb, web = tmp_path / "made-up pcb", tmp_path / "made-up web"
    pcb.mkdir()
    web.mkdir()
    one, two = Workspace("PCB work", [Entry(str(pcb))]), Workspace("Web", [Entry(str(web), workspaces.NEW)])
    assert workspaces.keep(one) and workspaces.keep(two)
    return one, two


def test_w_with_nothing_ticked_says_to_tick_first(qapp, window):
    shown = window()
    shown.selected = set()
    shown.actions_by_key["w"].trigger()
    qapp.processEvents()
    assert said(shown) == workspacewords.tick_first_note().words and look.AMBER in shown.bit_line.styleSheet()
    assert not shown_of(shown, NameDialog)


def test_w_saves_the_ticked_sessions_folders_as_m_set_them_under_the_name_given(qapp, window):
    shown = window()
    first, second = [s for s in shown.snap.sessions.values() if s.cwd][:2]
    shown.selected = {first.session_id, second.session_id}
    shown.opener.modes[second.session_id] = Mode.NEW  # m on its row: a fresh session there
    shown.actions_by_key["w"].trigger()
    (asked,) = shown_of(shown, NameDialog)
    assert asked.heading.text() == look.span(workspacewords.name_heading(2), f"bold {look.TEXT}")
    assert asked.line.placeholderText() == workspacewords.PLACEHOLDER
    assert asked.keys.text() == keys_html(workspacewords.NAME_KEYS)
    QTest.keyClicks(asked.line, "Made-up work")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    qapp.processEvents()
    kept = workspaces.load()["Made-up work"]
    listed = [s for s in shown.snap.sessions.values() if s.session_id in (first.session_id, second.session_id)]
    assert kept.entries == [Entry(s.cwd, workspaces.NEW if s is second else workspaces.CONTINUE) for s in listed]
    assert said(shown) == workspacewords.saved_note(kept).words and not shown.workspace_from


def test_w_not_kept_is_said_in_red_and_an_unreadable_workspaces_json_left_as_it_is(qapp, window):
    shown = window()
    path = config_dir() / workspaces.FILE
    path.write_text('{"workspaces": [', encoding="utf-8")
    picked = next(s for s in shown.snap.sessions.values() if s.cwd)
    shown.selected = {picked.session_id}
    shown.actions_by_key["w"].trigger()
    (asked,) = shown_of(shown, NameDialog)
    assert asked.heading.text() == look.span(workspacewords.name_heading(1), f"bold {look.TEXT}")
    asked.line.setText("Made-up work")
    QTest.keyClick(asked.line, Qt.Key.Key_Return)
    qapp.processEvents()
    assert said(shown) == workspacewords.not_kept_note("Made-up work").words
    assert look.RED in shown.bit_line.styleSheet() and path.read_text(encoding="utf-8") == '{"workspaces": ['


def test_o_with_none_saved_says_so(qapp, window):
    shown = window()
    shown.actions_by_key["o"].trigger()
    qapp.processEvents()
    assert said(shown) == workspacewords.none_yet_note().words and not shown_of(shown, WorkspacesDialog)


def test_o_lists_them_as_the_terminal_does_and_d_deletes_the_picked_one_at_once(qapp, window, tmp_path):
    shown = window()
    one, two = saved_two(tmp_path)
    shown.actions_by_key["o"].trigger()
    (asked,) = shown_of(shown, WorkspacesDialog)
    assert asked.windowTitle() == workspacewords.TITLE and asked.keys.text() == keys_html(workspacewords.LIST_KEYS)
    assert [row.text() for row in asked.rows] == [parts_html(workspacewords.option_parts(w)) for w in (one, two)]
    assert lit(asked) == [0]
    press(qapp, asked, Qt.Key.Key_Down)
    assert lit(asked) == [1]
    press(qapp, asked, Qt.Key.Key_Down)  # round the list
    assert lit(asked) == [0]
    press(qapp, asked, Qt.Key.Key_Down, Qt.Key.Key_D)
    assert workspaces.load() == {"PCB work": one} and said(shown) == workspacewords.deleted_note("Web").words
    assert [row.text() for row in asked.rows] == [parts_html(workspacewords.option_parts(one))] and lit(asked) == [0]
    press(qapp, asked, Qt.Key.Key_Delete)  # the last one gone: closed
    assert workspaces.load() == {} and not asked.isVisible()


def test_d_while_another_tabmancer_changes_workspaces_json_is_said_and_the_row_kept(qapp, window, tmp_path):
    shown = window()
    one, two = saved_two(tmp_path)
    shown.actions_by_key["o"].trigger()
    (asked,) = shown_of(shown, WorkspacesDialog)
    path = config_dir() / workspaces.FILE
    before = path.read_text(encoding="utf-8")
    with config.locked(workspaces.FILE):
        press(qapp, asked, Qt.Key.Key_D)
    assert said(shown) == workspacewords.not_kept_note("PCB work", deleting=True).words
    assert look.RED in shown.bit_line.styleSheet() and len(asked.rows) == 2 and path.read_text(encoding="utf-8") == before
    press(qapp, asked, Qt.Key.Key_Escape)
    assert not asked.isVisible() and workspaces.load() == {"PCB work": one, "Web": two}


def test_enter_opens_the_picked_workspaces_folders_in_new_tabs_once(qapp, window, world, tmp_path):
    shown = window()
    _, two = saved_two(tmp_path)
    shown.actions_by_key["o"].trigger()
    (asked,) = shown_of(shown, WorkspacesDialog)
    press(qapp, asked, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Return)
    until(qapp, lambda: world)
    shown.opener.settle()
    qapp.processEvents()
    # Web: a fresh session in its folder (its row was set to new), and nothing else.
    assert [(tab.cwd, tab.mode) for tab in world] == [(two.entries[0].cwd, Mode.NEW)] and not asked.isVisible()


def test_a_workspace_with_nothing_to_open_says_why_folder_by_folder(qapp, window, world, tmp_path):
    shown = window()
    gone = tmp_path / "made-up gone"
    assert workspaces.keep(Workspace("Gone", [Entry(str(gone))]))
    shown.actions_by_key["o"].trigger()
    (asked,) = shown_of(shown, WorkspacesDialog)
    row = asked.rows[0]
    QTest.mouseDClick(row, Qt.MouseButton.LeftButton)  # a double click opens it too
    until(qapp, lambda: "folder not found" in said(shown))
    assert said(shown) == f"made-up gone: folder not found ({gone})" and look.AMBER in shown.bit_line.styleSheet()
    assert not world
