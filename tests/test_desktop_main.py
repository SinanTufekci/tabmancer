"""The dashboard's desktop window (`tabmancer desktop`), offscreen over the made-up sessions of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import gc
import importlib
import os
import re
import subprocess
import sys
import threading
import time
import weakref
from datetime import date, datetime, timedelta
from functools import partial

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

import shiboken6
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDockWidget, QMenu
from qt_later import later  # noqa: F401  (a fixture)

from tabmancer import (
    accounts,
    actions,
    actor,
    agy,
    cli,
    commands,
    demo,
    diskclean,
    focus,
    history,
    launch,
    marks,
    palette,
    rowwords,
    sessions,
    shutdowns,
    store,
    teambus,
    teams,
    usage,
)
from tabmancer import sessionwords as preview_words
from tabmancer import viewmodel as VM
from tabmancer.config import config_dir, merge_json, read_json
from tabmancer.desktop import (
    bitpet,
    commandbar,
    drawer,
    frame,
    look,
    main,
    monitor,
    opening,
    rail,
    teamgraph,
    teampanel,
)
from tabmancer.desktop.cleanup import CleanupDialog
from tabmancer.desktop.stats import StatsDialog
from tabmancer.desktop.table import COLUMN, PARTS
from tabmancer.desktop.teamkeysdialogs import AddMembersDialog
from tabmancer.desktop.wrapped import WrappedDialog
from tabmancer.keys import KEYS
from tabmancer.launch import Mode
from tabmancer.search import matches
from tabmancer.teamwords import colours, team_detail
from tabmancer.tokens import Report, human
from tabmancer.widgets import bar_text

look.fonts_for_offscreen()

NBSP = chr(0xA0)
# Times change between two readings a second apart: "2h10m" to "2h09m".
TIMES = re.compile(r"\b\d+h\d{2}m\b|\b\d+[dm]\b")
CLOSE_TABS = actions.close_tabs  # the real one: the demo's world stands in for it (nothing closes there)
OPEN_AT_SHUTDOWN = shutdowns.open_at_shutdown  # the real one: the world below ticks none at the start


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world, put back after the test; what its stand-ins were asked to do."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    told: list[str] = []
    for module, name, value in demo.stand_ins(tmp_path, live, told.append):
        monkeypatch.setattr(module, name, value)
    # None ticked at the start, as conftest's fake_home has the terminal dashboard's: the keys act on the
    # one picked. test_the_ones_open_at_the_last_shutdown_come_ticked turns the demo's own back on.
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
    return told


@pytest.fixture
def window(qapp, world):
    made = []

    def make(width=1500):
        shown = main.MainWindow(start=False)
        shown.resize(width, 950)
        shown.show()
        QTest.qWaitForWindowActive(shown, 3000)  # its keys are the active window's
        made.append(shown)
        return shown

    yield make
    for shown in made:
        shown.settle()  # no read left running once the world is put back
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def wait(qapp, ok, seconds=30.0):
    deadline = time.monotonic() + seconds
    while not ok():
        assert time.monotonic() < deadline, "waited too long"
        qapp.processEvents()
        time.sleep(0.02)


def read(qapp, shown):
    shown.poll()
    wait(qapp, lambda: not shown.waiting)
    qapp.processEvents()


def plain(html: str) -> str:
    made = QTextDocument()
    made.setHtml(html)
    return made.toPlainText().replace(NBSP, " ")


def test_it_opens_on_the_demo(qapp, window):
    shown = window()
    read(qapp, shown)
    assert shown.snap is not None and len(shown.snap.sessions) >= len(demo.SESSIONS)  # and the team's agy member
    listed = len(shown.snap.sessions)
    assert shown.info.text() == VM.info_words(listed, 0, 14)[0] == f"{listed} sessions, last 14 days"
    assert list(shown.docks) == ["Sessions", "Preview", "Projects", "Bit", "Team"]
    assert all(dock.isVisible() for dock in shown.docks.values())
    assert shown.windowTitle() == "tabmancer"
    assert [w for w in qapp.topLevelWidgets() if w.isVisible()] == [shown]  # no panel a window of its own
    assert qapp.activeWindow() is shown  # so nothing took its keys
    assert shown.engine.state.told_limit and " limit until " in shown.bit_line.text()  # the demo's stopped,
    shown.said_timer.timeout.emit()  # as the terminal says them; then who acts: none in its made-up world
    assert shown.bit_line.text() == main.NONE_ACTS
    shown.tick()
    assert re.fullmatch(r"\d\d:\d\d:\d\d", shown.clock.text()) and plain(shown.live.text()) == "● live"


def test_every_dashboard_key_is_in_the_menus(qapp, window):
    shown = window()
    in_menus = {action.data() for menu in shown.menus.findChildren(QMenu) for action in menu.actions()}
    for _group, keys in main.WINDOW_KEYS:  # keys.py's, and the terminal team view's g, d and H
        for key, _action, words in keys:
            made = shown.actions_by_key[key]
            assert key in in_menus
            assert made.isEnabled() is (key in main.HERE)
            assert made.toolTip() == (words if key in main.HERE else f"{words}. {main.done_where(key)}")
            assert made.shortcuts()  # its key is named beside it, greyed or not
    assert {key for _group, keys in main.WINDOW_KEYS for key, _a, _w in keys} == set(shown.actions_by_key)
    assert ({key for _group, keys in KEYS for key, _a, _w in keys} | {"g", "d", "H", "Del"}) - main.LEFT_OUT \
        == set(shown.actions_by_key)
    assert set(shown.keybar.keys) - {commandbar.KEY} <= set(shown.actions_by_key)  # the dashboard's keys, and the palette
    assert shown.actions_by_key["?"].shortcuts()[0].toString() == "?"


def bar_words(widget) -> str:
    return str(widget.render()).strip()


async def test_the_top_bar_says_what_the_dashboards_says(qapp, world, monkeypatch):
    """The seam: the terminal dashboard on the demo, then this window on the same world. The same
    accounts, plan limits and agy chain, from the same words (viewmodel.usage_parts, account_parts)."""
    # agy in use (the demo has no agy session), with a made-up saved quota: both read it through these.
    bar = {"parts": [("Neon out till 23:59", "out"), ("Hydrogen 31% left", "ok")], "all_out": None, "said": ""}
    monkeypatch.setattr(store, "uses_agy", lambda listed, team_states: True)
    monkeypatch.setattr(agy, "bar_view", lambda usage=None, now=None: dict(bar))
    tui = demo.picker()
    async with tui.run_test(size=(240, 40)) as pilot:
        counted, steady = -1, 0
        for _ in range(400):  # the bar filled in, and the list read whole (the same count a while)
            await pilot.pause(0.05)
            steady = steady + 1 if len(tui.sessions) == counted else 0
            counted = len(tui.sessions)
            if (bar_words(tui.query_one("#usage")) and bar_words(tui.query_one("#account"))
                    and counted >= len(demo.SESSIONS) and steady >= 10):
                break
        said = {part: bar_words(tui.query_one(f"#{part}")) for part in ("info", "account", "usage")}
    assert "session" in said["usage"] and "you@example.com" in said["account"]
    assert said["usage"].endswith("agy Neon out till 23:59 · Hydrogen 31% left")
    shown = main.MainWindow(start=False)
    try:
        shown.resize(1800, 950)
        shown.show()
        read(qapp, shown)
        seen = {"info": shown.info.text(), "account": plain(shown.account.text()), "usage": plain(shown.usage.text())}
    finally:
        shown.settle()
        shown.close()
        shown.deleteLater()
    assert {part: TIMES.sub("<t>", words) for part, words in seen.items()} == \
        {part: TIMES.sub("<t>", words) for part, words in said.items()}


def test_agy_and_the_limits_as_the_terminals_top_bar(qapp, window):
    """The agy chain after the plan limits, or first with agy the main CLI; a model out in bold red."""
    shown = window()
    read(qapp, shown)
    soon = datetime.now() + timedelta(days=3, hours=1)
    limits = [usage.Limit("session", 91, soon, ""), usage.Limit("week Fable", 20, None, ""),
              usage.Limit("week Opus", 70, soon, "")]
    bar = {"parts": [("Neon out till 23:59", "out"), ("Hydrogen 31% left", "ok")], "all_out": None, "said": ""}
    for first in (False, True):
        shown.take(shown.store, dataclasses.replace(shown.snap, limits=limits, agy_bar=bar), first, "")
        parts = VM.usage_parts(limits, bar["parts"], first)
        assert plain(shown.usage.text()) == bar_text(parts).plain  # the terminal's words
        words = plain(shown.usage.text())
        assert "week Fable" not in words  # a model's weekly limit only once it fills up
        assert words.startswith("agy Neon") is first
        assert "session ▰▰▰▰▰▰▰▱ 91% · 3d" in words and "week Opus ▰▰▰▰▰▰▱▱ 70% · 3d" in words
    html = shown.usage.text()
    assert f'color:{look.RED};font-weight:600;">Neon' in html and f'color:{look.AMBER};font-weight:600;"> 70%' in html


def test_short_of_room_the_plan_goes_then_the_email_is_cut(qapp, window):
    shown = window()
    read(qapp, shown)
    assert plain(shown.account.text()) == "you@example.com · Max 5x"
    shown.resize(860, 950)
    qapp.processEvents()
    shown.fit_accounts()
    narrow = plain(shown.account.text())
    assert narrow != "you@example.com · Max 5x" and "Max 5x" not in narrow and narrow  # Claude's stays


def test_the_layout_is_kept(qapp, window):
    shown = window()
    shown.docks["Preview"].close()
    shown.close()
    kept = read_json(frame.LAYOUT_FILE)
    assert set(kept["main"]) == {"geometry", "state", "docks"}
    assert kept["main"]["docks"] == sorted(dock.objectName() for dock in shown.docks.values())
    again = window()
    assert again.docks["Preview"].isHidden()
    again.reset_layout()
    assert not any(dock.isHidden() for dock in again.docks.values())


def test_a_panel_new_since_the_layout_was_kept_goes_where_it_starts(qapp, window):
    old = window()
    old.removeDockWidget(old.docks["Team"])  # its layout as kept before the Team panel
    old.close()
    kept = read_json(frame.LAYOUT_FILE)["main"]
    merge_json(frame.LAYOUT_FILE, main={"geometry": kept["geometry"], "state": kept["state"]})  # before it named them
    shown = window()
    team, listed = shown.docks["Team"], shown.docks["Sessions"]
    assert team in shown.tabifiedDockWidgets(listed) and not team.isHidden()  # not squeezed in beside the rest
    shown.show_teams()
    qapp.processEvents()
    assert team.width() > 400 and not team.visibleRegion().isEmpty()
    team.close()
    shown.close()
    again = window()
    assert again.docks["Team"].isHidden()  # known now, and closed: it stays so


def test_the_team_panel_is_a_tab_beside_the_list_and_t_brings_it(qapp, window):
    shown = window()
    read(qapp, shown)
    team, listed = shown.docks["Team"], shown.docks["Sessions"]
    assert team in shown.tabifiedDockWidgets(listed)
    assert team.visibleRegion().isEmpty() and not listed.visibleRegion().isEmpty()  # the list in front
    panel = shown.team_panel
    first = shown.snap.view.teams[0]
    assert panel.team == first.name and panel.graph.view is first
    assert panel.lanes.said == shown.snap.team_states[first.name].said
    assert panel.graph.hint == teampanel.HINT  # a click lights a member here: no tab, no actions
    assert panel.graph.breathing() and not panel.graph.breaths.isActive()  # behind the list: at rest
    shown.actions_by_key["T"].trigger()
    qapp.processEvents()
    assert not team.visibleRegion().isEmpty() and listed.visibleRegion().isEmpty()
    assert panel.graph.breaths.isActive()  # in front: its members breathe
    assert "T" not in shown.keybar.dim  # the key bar and the menus in step
    assert shown.keybar.dim == {key for key in shown.keybar.keys
                                if key != commandbar.KEY and not shown.actions_by_key[key].isEnabled()}


def test_the_sessions_panel_is_the_board(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, board = shown.snap, shown.board
    expected = VM.board(VM.visible(snap.sessions.values(), lambda s: s.session_id in snap.hidden),
                        snap.team_states, snap.pinned)
    assert [(lane.key, lane.items, lane.count) for lane in board.lanes] == \
        [(lane.key, lane.items, lane.count) for lane in expected]
    on_cards = {card.key for view in board.views.values() for card in view.column.cards}
    assert on_cards == {key for lane in expected if not lane.folded for key in lane.items}
    assert any(key.startswith(VM.TEAM_CARD) for key in on_cards)  # the demo's team, one card
    assert shown.docks["Sessions"].isAncestorOf(board)
    first = board.pick
    assert first == board.keys()[0] and board.hasFocus()  # the arrows pick from the start
    QTest.keyClick(board, Qt.Key.Key_Down)
    assert board.pick == board.keys()[1]
    views = {button.text(): button for button in shown.views.buttons()}
    assert list(views) == ["Board", "Table", "Live"] and views["Board"].isChecked()
    assert all(button.isEnabled() and button.toolTip() for button in views.values())


def test_the_table_and_the_live_view_list_as_the_terminal_does_and_keep_the_pick(qapp, window):
    shown = window()
    read(qapp, shown)
    board, table, drawer = shown.board, shown.table, shown.drawer
    cards = board.keys()
    session = next(key for key in cards if not key.startswith(VM.TEAM_CARD))
    board.move(session)
    views = {button.text(): button for button in shown.views.buttons()}
    QTest.mouseClick(views["Table"], Qt.MouseButton.LeftButton)
    assert shown.view == "table" and shown.sessions_shown.currentWidget() is table and table.hasFocus()
    assert views["Table"].isChecked() and shown.hint.text() == main.HINTS["table"]
    assert table.shown == shown.shown() and not table.live
    assert table.keys == [key for key, _ in rowwords.keys(table.listing)]  # the terminal's rows (test_rowwords)
    assert table.pick == session and drawer.key == session  # the pick came along
    QTest.keyClick(table, Qt.Key.Key_Down)
    assert drawer.key == main.drawer_key(table.pick) != session
    team = shown.snap.view.teams[0].name
    shown.run_command(commands.Command("team", team, target=team))  # Ctrl+K's jump, in the table
    assert table.pick == rowwords.TEAM_ROW + team and drawer.key == VM.TEAM_CARD + team
    press(shown, "e")  # e unfolds it, as in the terminal (Enter: its Team panel tab)
    assert team in table.unfolded and table.keys.index(rowwords.TEAM_ROW + team) < len(table.keys) - 1
    QTest.keyClick(shown, Qt.Key.Key_1)  # 1 2 3 narrow the table too
    assert shown.card == "open" and table.shown == shown.shown()
    QTest.keyClick(shown, Qt.Key.Key_1)
    QTest.keyClick(shown, Qt.Key.Key_L)  # l: the live view
    assert shown.view == "live" and table.live and views["Live"].isChecked()
    assert table.shown == shown.shown() == VM.visible(shown.snap.sessions.values(), lambda s: False, live=True)
    assert set(table.keys) == {s.session_id for s in table.shown}
    assert tuple(table.grid.horizontalHeaderItem(c).text() for c in range(table.grid.columnCount())) == \
        rowwords.labels(True, table.model_shown)
    shown.tick()  # what moves, moves
    QTest.keyClick(shown, Qt.Key.Key_L)  # l again: back where it was
    assert shown.view == "table" and not table.live
    picked = drawer.key
    QTest.mouseClick(views["Board"], Qt.MouseButton.LeftButton)
    assert shown.sessions_shown.currentWidget() is board and board.hasFocus() and board.pick == picked


def test_a_layout_kept_with_the_cards_panel_opens_without_it(qapp, window):
    old = window()
    cards = QDockWidget("Cards", old)  # the panel the board's lane headings took over
    cards.setObjectName("dock-cards")
    old.splitDockWidget(old.docks["Projects"], cards, Qt.Orientation.Vertical)
    old.close()
    assert "dock-cards" in read_json(frame.LAYOUT_FILE)["main"]["docks"]
    shown = window()
    read(qapp, shown)
    listed = shown.docks["Sessions"]
    assert not listed.isHidden() and listed.width() > 400 and not listed.visibleRegion().isEmpty()
    assert not any(dock.isHidden() for dock in shown.docks.values())
    panels = next(a.menu() for a in shown.menus.actions() if a.text() == "&Panels")
    assert "Cards" not in [action.text() for action in panels.actions()]


def test_views_says_how_much_moves(qapp, window):
    shown = window()
    read(qapp, shown)
    views = shown.menus_by_group["Views"]
    assert "Reduce motion" in [action.text() for action in views.actions()]
    picks = {str(action.data()): action for action in shown.motion.group.actions()}
    assert list(picks) == list(teamgraph.MOTIONS) and picks["full"].isChecked()
    graph = shown.team_panel.graph
    assert graph.mode == "full"
    picks["off"].trigger()
    assert read_json("config.json")["motion"] == "off" and graph.mode == "off" and shown.team_panel.lanes.mode == "off"
    assert picks["off"].isChecked() and not picks["full"].isChecked() and shown.table.motion == "off"
    picks["full"].trigger()
    assert graph.mode == "full" and shown.table.motion == "full"


def test_free_disk_space_in_settings_opens_the_clean_up_and_what_moved_reads_the_sessions_again(
        qapp, window, monkeypatch):
    """tabmancer cleanup in the window: a Settings entry, not a key. Nothing reaches the Recycle Bin."""
    moved = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: moved.append(path))
    monkeypatch.setattr(diskclean, "collect", list)  # no sessions on this disk
    shown = window()
    polled = []
    monkeypatch.setattr(shown, "poll", lambda: polled.append(True))
    (entry,) = [a for a in shown.menus_by_group["Settings and the rest"].actions() if a.text() == main.CLEAN_UP]
    assert entry.isEnabled() and entry.toolTip() == main.CLEAN_UP_TIP and diskclean.BIN in entry.toolTip()
    assert entry.data() is None  # no key of the dashboard's
    entry.trigger()
    (listed,) = [d for d in shown.findChildren(CleanupDialog) if d.isVisible()]
    assert listed.windowModality() == Qt.WindowModality.WindowModal
    listed.disk.settle()
    wait(qapp, lambda: not listed.busy)
    assert polled == []
    listed.changed.emit()  # some moved
    assert polled == [True] and moved == []
    listed.reject()
    demo_one = main.MainWindow(start=False, demo=True)
    try:
        (entry,) = [a for a in demo_one.menus_by_group["Settings and the rest"].actions()
                    if a.text() == main.CLEAN_UP]
        assert not entry.isEnabled() and entry.toolTip() == main.CLEAN_UP_DEMO
    finally:
        demo_one.settle()
        demo_one.close()
        demo_one.deleteLater()


def every_key(keys) -> list[tuple[str, str]]:
    return [(key, words) for _group, entries in keys for key, _action, words in entries]


def test_ctrl_k_lists_every_key_and_runs_the_menus_own_action(qapp, window):
    shown = window()
    read(qapp, shown)
    QTest.keyClick(shown, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    bar = shown.command_bar
    assert bar.isVisible() and bar.line.text() == ""
    assert [(c.key, c.said) for c in bar.shown] == every_key(main.WINDOW_KEYS)  # in the menus' order and words
    assert bar.rows.count() == len(bar.shown) and not bar.grab().isNull()
    assert {c.key for c in bar.shown if c.enabled} == main.HERE
    ran = []
    shown.actions_by_key["T"].triggered.connect(lambda *_: ran.append(1))
    QTest.keyClicks(bar.line, "teams")
    assert bar.picked().key == "T" and bar.note.text() == bar.picked().said
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert len(ran) == 1 and not bar.isVisible()  # the menu's own action ran (T: the Team panel)
    assert not shown.docks["Team"].visibleRegion().isEmpty()
    assert (commandbar.KEY, "commands") in main.KEY_BAR and commandbar.KEY not in shown.keybar.dim


def test_a_greyed_command_says_why_and_does_nothing(qapp, window):
    shown = window()
    read(qapp, shown)
    shown.open_commands()
    bar = shown.command_bar
    QTest.keyClicks(bar.line, "preview beside the list")
    assert bar.picked().key == "V" and not bar.picked().enabled  # the terminal's own: greyed here
    assert bar.note.text() == main.TERMINALS["V"]
    ran = []
    shown.actions_by_key["V"].triggered.connect(lambda *_: ran.append(1))
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert bar.isVisible() and ran == []
    QTest.keyClicks(bar.line, "zzqqx")
    assert bar.shown == [] and bar.note.text() == commandbar.NOTHING
    QTest.keyClick(bar.line, Qt.Key.Key_Return)  # nothing picked: nothing happens
    QTest.keyClick(bar.line, Qt.Key.Key_Escape)
    assert not bar.isVisible()


def test_u_and_w_open_the_stats_and_wrapped_over_the_counted_tokens(qapp, window, monkeypatch):
    asked = []

    def collect(days=None):  # the stats' period counted again: slow enough to be under way
        asked.append(days)
        time.sleep(0.5)
        return Report(date.today() - timedelta(days=(days or 60) - 1), days or 60)

    monkeypatch.setattr(history, "collect", collect)
    shown = window()
    read(qapp, shown)
    report = shown.snap.report
    assert report is not None and shown.actions_by_key["u"].isEnabled() and shown.actions_by_key["W"].isEnabled()
    shown.actions_by_key["u"].trigger()
    stats = [dialog for dialog in shown.findChildren(StatsDialog) if dialog.isVisible()]
    assert len(stats) == 1 and stats[0].report is report and stats[0].sessions == shown.snap.sessions
    assert stats[0].windowModality() == Qt.WindowModality.WindowModal
    wait(qapp, lambda: asked)  # what it was given shown, the period is counted again off the window's thread
    shown.settle()  # that count waited for too, as the window's own reads
    assert asked[0] == report.days and not any(thread.is_alive() for thread in stats[0].worker.workers)
    stats[0].reject()
    shown.actions_by_key["W"].trigger()
    cards = [dialog for dialog in shown.findChildren(WrappedDialog) if dialog.isVisible()]
    assert len(cards) == 1 and cards[0].week.last == report.last  # the last seven days of the store's count
    cards[0].reject()
    shown.snap = dataclasses.replace(shown.snap, report=None)  # before the store's first count
    for key in ("u", "W"):
        shown.actions_by_key[key].trigger()
        assert shown.bit_line.text() == main.NOT_COUNTED.format(key=key)
    assert not [dialog for dialog in shown.findChildren(QDialog) if dialog.isVisible()]


def test_a_session_or_a_team_by_name_picks_its_card(qapp, window):
    shown = window()
    read(qapp, shown)
    board, bar = shown.board, shown.command_bar
    done = next(lane for lane in board.lanes if lane.key == "done")
    assert done.folded and done.items
    target = done.items[0]
    title = next(view.title for view in shown.snap.view.sessions if view.id == target)
    shown.open_commands()
    QTest.keyClicks(bar.line, title)
    assert bar.picked() == next(c for c in bar.shown if c.kind == "session" and c.target == target)
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert board.pick == target and board.views["done"].scroller.isVisible()  # DONE opened to show it
    assert board.hasFocus()
    team = shown.snap.view.teams[0]
    shown.open_commands()
    QTest.keyClicks(bar.line, f"team {team.name}")
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert board.pick == VM.TEAM_CARD + team.name
    board.move(target)
    member = next(m.session for m in team.members if m.session)
    shown.run_command(commands.Command("session", "a member", target=member))
    assert board.pick == VM.TEAM_CARD + team.name  # a member's session is on its team's card


def lanes(board) -> list[tuple[str, list[str], int]]:
    return [(lane.key, list(lane.items), lane.count) for lane in board.lanes]


def lanes_over(snap, **filters) -> list[tuple[str, list[str], int]]:
    """The board over the terminal dashboard's list with these filters (its visible(): VM.visible)."""
    shown = VM.visible(snap.sessions.values(), lambda s: s.session_id in snap.hidden, **filters)
    return [(lane.key, list(lane.items), lane.count) for lane in VM.board(shown, snap.team_states, snap.pinned)]


def click_project(qapp, shown, key: str) -> None:
    projects = shown.rail.projects
    index = next(i for i, row in enumerate(projects.rows) if row.key == key)
    QTest.mouseClick(projects, Qt.MouseButton.LeftButton, pos=QPoint(30, int(projects.top_of(index) + rail.ROW / 2)))
    qapp.processEvents()


def test_the_rail_lists_the_projects_and_a_click_narrows_every_lane(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, projects = shown.snap, shown.rail.projects
    in_sight = [s for s in snap.sessions.values() if s.session_id not in snap.hidden]
    assert projects.rows == VM.project_rows(in_sight, None, VM.project_signs(snap.view.projects))  # the sidebar's
    assert projects.rows[0] == VM.ProjectRow(VM.ALL_PROJECTS, "All projects", len(in_sight))
    names = [row.name for row in projects.rows[1:]]
    assert names == sorted(names, key=str.casefold)
    assert {row.mark for row in projects.rows[1:]} >= {"waiting", "open", "continue"}  # the demo has each
    assert any(row.git for row in projects.rows) and not projects.grab().isNull()
    shop = next(row for row in projects.rows if row.name == "shop")
    click_project(qapp, shown, shop.key)
    assert shown.project == projects.pick == shop.key
    assert lanes(shown.board) == lanes_over(snap, project=shop.key)  # the terminal dashboard's rows
    assert {s.project for s in shown.shown()} == {"shop"} and shown.pills.text() == "in shop"
    assert "a click shows every project again" in projects.tip(shop)
    click_project(qapp, shown, shop.key)  # the same again: every project
    assert shown.project == VM.ALL_PROJECTS and lanes(shown.board) == lanes_over(snap)
    assert shown.pills.text() == ""
    click_project(qapp, shown, shop.key)
    click_project(qapp, shown, VM.ALL_PROJECTS)
    assert shown.project == VM.ALL_PROJECTS


def test_1_2_3_and_a_lane_heading_narrow_the_board_as_the_cards_count(qapp, window):
    shown = window()
    read(qapp, shown)
    board = shown.board
    for key, card in (("1", "open"), ("2", "waiting"), ("3", "continue")):
        QTest.keyClick(shown, key)
        assert shown.card == card and board.on == card
        assert lanes(board) == lanes_over(shown.snap, card=card)
        title = VM.CARDS[card][1]
        assert shown.pills.text() == f"only {title} · {key} shows all"
        assert board.views[card].heading.on and "every lane again" in board.views[card].heading.toolTip()
        if card == "open":  # the open ones: those that need you too
            counts = {lane[0]: lane[2] for lane in lanes(board)}
            assert counts["waiting"] and counts["open"] and not counts["continue"] and not counts["done"]
        QTest.keyClick(shown, key)  # again: everything
        assert shown.card is None and lanes(board) == lanes_over(shown.snap)
    heading = board.views["waiting"].heading
    QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
    assert shown.card == "waiting" and heading.on
    QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
    assert shown.card is None and not heading.on
    board.fold("done", False)  # DONE's heading still folds it: it is no card
    QTest.mouseClick(board.views["done"].heading, Qt.MouseButton.LeftButton)
    assert shown.card is None and next(lane for lane in board.lanes if lane.key == "done").folded


def test_slash_finds_by_words_and_by_what_was_said(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, box = shown.snap, shown.filter_box
    QTest.keyClick(shown, Qt.Key.Key_Slash)
    assert box.hasFocus()
    QTest.keyClicks(box, "login 2")  # typed into the box, not the window's keys
    assert shown.words == "login 2" and shown.card is None
    box.clear()
    QTest.keyClicks(box, "login")
    assert lanes(shown.board) == lanes_over(snap, text="login", said_in=shown.said_in)
    assert "Fix the login redirect loop" in {s.title for s in shown.shown()}
    assert shown.pills.text() == "“login”"
    api = next(s.session_id for s in snap.sessions.values() if s.title == "Rate limiting for the public API")
    box.clear()
    QTest.keyClicks(box, "bucket")  # only in what it answered: "a token bucket per API key"
    wait(qapp, lambda: api in {s.session_id for s in shown.shown()})  # once the conversations are read
    assert not matches(snap.sessions[api], "bucket")  # not in its title or prompts
    assert lanes(shown.board) == lanes_over(snap, text="bucket", said_in=shown.said_in)
    QTest.keyClick(box, Qt.Key.Key_Escape)  # clears it, and the board has the keys again
    assert shown.words == "" and box.text() == "" and shown.board.hasFocus()
    assert lanes(shown.board) == lanes_over(snap)


def test_a_project_by_name_narrows_the_board_and_a_jump_brings_back_a_card_it_hid(qapp, window):
    shown = window()
    read(qapp, shown)
    bar = shown.command_bar
    shown.open_commands()
    QTest.keyClicks(bar.line, "project nightly")
    picked = bar.picked()
    assert picked.kind == "project" and picked.words == "nightly-reports"
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert shown.project == picked.target and {s.project for s in shown.shown()} == {"nightly-reports"}
    api = next(s.session_id for s in shown.snap.sessions.values() if s.title == "Rate limiting for the public API")
    shown.toggle_card("continue")
    shown.run_command(commands.Command("session", "Rate limiting", target=api))  # kept out: the filters go
    assert shown.board.pick == api and not shown.narrowed() and shown.pills.text() == ""


def test_the_rail_draws_each_team_and_a_click_picks_its_card_and_its_tab(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, cards = shown.snap, shown.rail.teams.cards
    assert cards and [card.name for card in cards] == [view.name for view in snap.view.teams]
    view, card = snap.view.teams[0], cards[0]
    needing = sum(1 for member in view.members if member.needs_you)
    assert card.counts.text() == f"{view.open} open" + (f" · {needing} !" if needing else "")
    assert card.graph.compact and card.graph.view == view and not card.grab().isNull()
    assert card.graph.colours == colours(snap.team_states[view.name].team, shown.accent)
    assert "a click: its card on the board" in card.toolTip()
    other = next(row for row in shown.rail.projects.rows[1:] if row.name != view.project)
    shown.set_project(other.key)  # the team's card is off the board now
    assert VM.TEAM_CARD + view.name not in {item for lane in shown.board.lanes for item in lane.items}
    QTest.mouseClick(card.graph, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))  # the picture passes it on
    assert shown.board.pick == VM.TEAM_CARD + view.name and not shown.narrowed()
    assert shown.team_panel.team == view.name
    shown.motion.pick(next(a for a in shown.motion.group.actions() if a.data() == "off"))
    assert card.graph.mode == "off"  # View > Reduce motion: the rail's pictures too


def test_the_rails_week_is_the_terminals_chart_and_follows_the_project(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, week = shown.snap, shown.rail.week
    report = snap.report
    assert report is not None and len(week.days) == rail.WEEK_DAYS
    assert [entry.total for entry in week.days] == [use.total for use in report.daily()[-rail.WEEK_DAYS:]]
    assert week.days[-1].day == report.last and week.where == ""
    today = human(report.total(last_days=1).total)  # the terminal's TOKENS TODAY card
    assert shown.rail.tokens_heading.text() == f"TOKENS · TODAY {today}"
    shop = next(row for row in shown.rail.projects.rows if row.name == "shop")
    click_project(qapp, shown, shop.key)
    ids = VM.token_ids(report, snap.sessions, shop.key)
    assert ids and week.days == VM.week_tokens(report, ids) and week.where == "shop"
    assert all(mine.total <= every.total for mine, every in zip(week.days, VM.week_tokens(report), strict=True))
    assert week.tip(len(week.days) - 1).endswith("tokens in shop") and not week.grab().isNull()


def flat(rows) -> list:
    """Preview rows as (label, [(words, style)]), a time read a moment later the same ('2m' or '3m')."""
    return [(label, [(TIMES.sub("<t>", words), style) for words, style in ([(cell, "")] if isinstance(cell, str)
                                                                          else cell)]) for label, cell in rows]


def kinds_of(listed: dict, team_states: dict) -> dict[str, str]:
    """The demo's sessions the preview is read for: open, closed, at a limit, in the background, a member."""
    by_title = {s.title: s.session_id for s in listed.values()}
    members = {s.session_id for state in team_states.values() for s in state.sessions.values() if s is not None}
    return {
        "open": by_title["Add --json output to the list command"],
        "closed": by_title["Fix the login redirect loop"],
        "limit": next(sid for sid, s in listed.items() if VM.at_limit(s) and sid not in members),
        "background": next(sid for sid, s in listed.items() if s.status is sessions.Status.BACKGROUND),
        "member": next(sid for sid in sorted(members) if VM.counted_by(listed[sid], "waiting")),
    }


async def test_the_drawer_says_what_the_terminals_preview_says(qapp, world, monkeypatch):
    """The seam: the terminal dashboard's preview on the demo of an open, a closed, an at-limit and a
    background session, a member and a team; then this window's drawer on the same world. The same
    facts, what was said asked for with the same inputs, the same member's messages, what Enter does,
    and the team's first line and goal."""
    asked: dict[str, tuple[dict, object]] = {}  # session id -> said_rows' inputs but its room, and messages
    real_said, real_facts = preview_words.said_rows, preview_words.fact_rows
    facts: dict[str, list] = {}

    def said_rows(session, **inputs):
        room = {name: value for name, value in inputs.items() if name not in ("room", "more", "messages")}
        asked[session.session_id] = (room, inputs.get("messages"))
        return real_said(session, **inputs)

    def fact_rows(session, **inputs):
        facts[session.session_id] = real_facts(session, **inputs)
        return facts[session.session_id]

    monkeypatch.setattr(preview_words, "said_rows", said_rows)
    monkeypatch.setattr(preview_words, "fact_rows", fact_rows)
    terminal: dict[str, tuple] = {}
    tui = demo.picker()
    async with tui.run_test(size=(240, 50)) as pilot:
        read_so_far, steady = None, 0
        for _ in range(400):  # the list, its teams, tokens and git read whole (the same a while)
            await pilot.pause(0.05)
            now = (len(tui.sessions), len(tui.team_states), len(tui.session_tokens), len(tui.git))
            steady = steady + 1 if now == read_so_far else 0
            read_so_far = now
            if now[0] >= len(demo.SESSIONS) and all(now) and steady >= 10:
                break
        kinds = kinds_of(tui.sessions, tui.team_states)
        tui.action_fold_teams()  # the members' rows under their team's
        await pilot.pause(0.1)
        for kind, sid in kinds.items():
            tui.table.move_cursor(row=tui.table.get_row_index(sid))
            await pilot.pause(0.05)
            tui.update_detail()
            session = tui.sessions[sid]
            messages = None
            if kind == "member":  # its line, from the terminal's own team states
                team, who = tui.member_rows[sid]
                state = tui.team_states[team]
                messages = preview_words.messages(state.said, who, colours(state.team, tui.accent()))
            enter = preview_words.enter_words(session, partial(tui.launch_for, session), set())  # none ticked
            terminal[kind] = (flat(facts[sid]), asked[sid], enter, messages)
        name, state = next(iter(tui.team_states.items()))
        team_words = team_detail(state, state.tokens(tui.session_tokens), tui.accent(), tui.members_open(name))
        team_head, team_goal = ("".join(words for words, _ in parts) for parts in team_words[:2])
    assert all(terminal[kind][1][1] is None for kind in terminal if kind != "member")  # a member's messages
    asked.clear()
    shown = main.MainWindow(start=False)
    try:
        shown.resize(1800, 950)
        shown.show()
        read(qapp, shown)
        side = shown.drawer
        assert kinds_of(shown.snap.sessions, shown.snap.team_states) == kinds
        for kind, sid in kinds.items():
            shown.show_pick(sid)
            said = side.plain().replace(NBSP, " ")
            facts_seen, (inputs, messages), enter, member_line = terminal[kind]
            assert flat(side.rows[1]) == facts_seen, kind
            assert asked[sid][0] == inputs, kind  # the summary, found, the model, the accent: all but the room
            assert enter in said and preview_words.status_row(shown.snap.sessions[sid])[1] in said, kind
            if kind == "member":
                assert flat([("", asked[sid][1])]) == flat([("", member_line)])
                assert side.rows[0][0][0] == "Messages"
            assert ("c" in dict(side.offered)) == (kind == "limit"), kind  # Continue: what a limit stopped
        shown.show_pick(kinds["open"])
        assert dict(side.offered)["Enter"] == "Its tab" and "goes to its tab" in side.plain()
        shown.show_pick(kinds["background"])
        assert "attaches to it" in side.plain()
        shown.show_pick(VM.TEAM_CARD + name)
        said = side.plain().replace(NBSP, " ")
        # How many are open, as both lists have them: the list and the team reader both see the demo's four
        # open (its pid files and `claude agents` list the same, on any machine), and the preview, the rail
        # and the board go by the list.
        view = next(team for team in shown.snap.view.teams if team.name == name)
        assert view.open == len(view.members) == state.open_count() and not team_words.closed
        assert f"  ·  {view.open} of {len(view.members)} open  ·  " in team_head
        assert team_head in said and team_goal in said and "Closed. Enter starts it" not in said
    finally:
        shown.settle()
        shown.close()
        shown.deleteLater()


def test_the_drawer_shows_a_teams_card_and_its_buttons_wait_for_the_window(qapp, window):
    shown = window()
    read(qapp, shown)
    snap, side = shown.snap, shown.drawer
    view = snap.view.teams[0]
    assert shown.board.reveal(VM.TEAM_CARD + view.name)  # a pick on the board fills the drawer
    assert side.key == VM.TEAM_CARD + view.name and side.graph.isVisible() and side.graph.view == view
    said = side.plain()
    assert f"TEAM {view.name}" in said and all(member.name in said for member in view.members)
    needing = [member.name for member in view.members if member.needs_you]
    assert needing and f"needs you: {', '.join(needing)}" in said
    assert {"Enter": "Team view", "h": "Hand team over", "M": "Monitor", "x": "Close team"}.items() \
        <= dict(side.offered).items()
    assert side.rows == ([], []) and not side.facts_heading.isVisible()
    closed = next(s for s in snap.sessions.values() if s.title == "Fix the login redirect loop")
    assert shown.board.reveal(closed.session_id)
    assert side.key == closed.session_id and not side.graph.isVisible() and side.facts_heading.isVisible()
    buttons = [side.grid.itemAt(n).widget() for n in range(side.grid.count())]
    assert [b.property("key") for b in buttons] == [key for key, _ in side.offered]
    for b in buttons:  # the window's own keys work; the rest wait till it does them
        here = b.property("key") in main.HERE
        assert b.isEnabled() is here and b.toolTip() == ("" if here else main.ELSEWHERE)
    assert {b.property("key") for b in buttons if b.isEnabled()} == {"Enter", "s", "p", "h", "f", "x"}  # s: Summary
    pressed = []
    side.act.connect(pressed.append)
    side.set(shown.snap, closed.session_id, None, {key: None for key, _, _ in drawer.BUTTONS})  # all done here
    button = side.grid.itemAt(0).widget()
    assert button.isEnabled() and button.property("key") == "Enter" and button.toolTip() == ""
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert pressed == ["Enter"]
    shown.show_pick("")
    assert drawer.NOTHING in side.plain() and not side.buttons.isVisible() and side.rows == ([], [])
    member = view.members[0]  # done for now, a keep-warm sleep keeping its cache: said till when
    member.warm_until = datetime(2026, 11, 27, 14, 32)
    side.show_team(snap, view.name)
    assert f"{member.name}  ·  {member.label} · warm to 14:32  ·  " in side.plain()


def test_the_drawer_says_where_the_filters_words_were_said(qapp, window):
    shown = window()
    read(qapp, shown)
    api = next(s for s in shown.snap.sessions.values() if s.title == "Rate limiting for the public API")
    QTest.keyClicks(shown.filter_box, "bucket")  # only in what it answered: "a token bucket per API key"
    wait(qapp, lambda: api.session_id in {s.session_id for s in shown.shown()})
    shown.show_pick(api.session_id)
    found = dict(shown.drawer.rows[0])["Found"]
    assert [style.startswith("bold reverse") for _, style in found] == [False, True, False]
    assert found[1][0].casefold() == "bucket"
    shown.filter_box.clear()  # no words: nothing found
    assert "Found" not in dict(shown.drawer.rows[0])


def test_a_closed_window_goes_with_its_last_name(qapp, world):
    """Nothing in the window holds the window (a kept callback did): closed and let go, it goes at once,
    not whenever the cycle collector next comes by, mid-way through something else (a test run aborted)."""
    gc.disable()
    try:
        shown = main.MainWindow(start=False)
        shown.show()
        read(qapp, shown)
        shown.close()
        assert not gc.isenabled()  # off before it came: left off (frame.Collector)
        shown.settle()
        gone = weakref.ref(shown)
        del shown
        assert gone() is None
    finally:
        gc.enable()


def test_pythons_cycle_collector_runs_on_the_gui_thread_only_while_a_window_is_up(qapp, window):
    """Left automatic, the collector runs on whichever thread allocates past its threshold (the store's
    reader), and a cycle holding a Qt object freed there is an access violation (Windows CI). While a window
    is up it is off, and collects on the GUI thread (frame.Collector); the last window closed, it is on again."""
    shown = window()
    held = frame.collector()
    assert not gc.isenabled() and held.timer.isActive()
    freed = []

    class Cycle:
        def __del__(self):
            freed.append(threading.current_thread() is threading.main_thread())

    one = Cycle()
    one.me = one
    del one
    busy = [[] for _ in range(gc.get_threshold()[0] + 100)]  # past the youngest generation's threshold
    wait(qapp, lambda: freed, seconds=10)
    assert freed == [True] and busy
    shown.settle()
    shown.close()
    assert gc.isenabled() and not held.timer.isActive()  # the last window gone: as it was


def test_the_collector_keeps_gcs_own_schedule_counted_by_itself_on_any_python(qapp, monkeypatch):
    """Only the youngest generation's count and threshold are read (3.14's incremental collector counts no older
    one: (2000, 10, 0), its last count 0, so going by them would never collect all while a window is up): every
    MIDDLE_EVERY-th collection of it takes the middle one too, every FULL_EVERY-th all of it."""
    owner = QObject()  # never the application's: collector() finds that one
    held = frame.Collector(owner)
    collected = []
    counts = [(5, 0, 0)]
    monkeypatch.setattr(frame.gc, "collect", lambda generation=2: collected.append(generation) or 0)
    monkeypatch.setattr(frame.gc, "get_count", lambda: counts[0])
    monkeypatch.setattr(frame.gc, "get_threshold", lambda: (5, 10, 0))  # 3.14's shape
    held.check()
    assert collected == []  # not past the threshold yet
    counts[0] = (6, 0, 0)
    for _ in range(frame.FULL_EVERY):
        held.check()
    assert collected.count(0) == frame.FULL_EVERY
    assert collected.count(1) == frame.FULL_EVERY // frame.MIDDLE_EVERY - 1 and collected.count(2) == 1
    assert collected[:frame.MIDDLE_EVERY + 1] == [0] * frame.MIDDLE_EVERY + [1] and collected[-1] == 2


def test_the_readers_threads_hold_the_store_and_the_relay_never_the_window(qapp, window, monkeypatch):
    """A thread holding the window (its bound method) could drop the last reference to it there, off the GUI
    thread; the relay is the window's own, on its thread."""
    shown = window()
    started = []
    real = threading.Thread

    def kept(*args, **kwargs):
        if kwargs.get("name") in {"tabmancer-main", "tabmancer-search"}:  # its readers, not the pools they use
            started.append((kwargs.get("target"), kwargs.get("args", ())))
        return real(*args, **kwargs)

    monkeypatch.setattr(main.threading, "Thread", kept)
    read(qapp, shown)
    shown.index_words()
    wait(qapp, lambda: not shown.indexing)
    assert [target for target, _ in started] == [main.read_store, main.read_words]
    assert [one for _, args in started for one in args if isinstance(one, QObject)] == [shown.relay, shown.relay]
    assert shown.relay.parent() is shown


def test_a_broken_layout_file_is_left_alone(qapp, window):
    merge_json(frame.LAYOUT_FILE, main={"geometry": "not base64!", "state": 7}, monitor={"state": "kept"})
    shown = window()
    assert not any(dock.isHidden() for dock in shown.docks.values())
    shown.close()
    assert read_json(frame.LAYOUT_FILE)["monitor"] == {"state": "kept"}  # the monitor's is its own


def press(shown, key: str) -> None:
    """A key as the menus name it ('x', 'X', 'Space', 'Tab'), pressed in the window."""
    QTest.keySequence(shown, main.sequences(key)[0])
    QApplication.instance().processEvents()


def titled(snap, title: str):
    return next(s for s in snap.sessions.values() if s.title == title)


def saved_marks() -> dict:
    return read_json(marks.FILE) or {"hidden": [], "projects": [], "pinned": []}


def test_the_ones_open_at_the_last_shutdown_come_ticked_as_in_the_terminal(qapp, window, monkeypatch):
    monkeypatch.setattr(shutdowns, "open_at_shutdown", OPEN_AT_SHUTDOWN)  # the demo's made-up shutdown
    shown = window()
    read(qapp, shown)
    snap = shown.snap
    plane = titled(snap, "Route the power plane on the 4-layer board").session_id
    login = titled(snap, "Fix the login redirect loop").session_id
    assert snap.open_at_shutdown == {plane, login}
    assert shown.selected == shown.preselected == {plane, login} == shown.board.ticked == shown.drawer.ticked
    assert shown.bit_line.text() == "Hi! 2 were open when you shut down."
    assert "2 ticked · open at the last shutdown" in shown.pills.text()
    opened = dataclasses.replace(snap.sessions[plane], exit=sessions.Exit.RUNNING)  # in the background, say
    shown.take(shown.store, dataclasses.replace(snap, sessions={**snap.sessions, plane: opened}), False, "")
    assert shown.selected == shown.preselected == {login}  # open after all: not left open at the shutdown
    assert "1 ticked · open at the last shutdown" in shown.pills.text()
    read(qapp, shown)  # closed again: not ticked afresh, the start's ticks are the start's
    assert shown.selected == {login}


def test_a_row_whose_state_changed_since_the_last_reading_is_lit(qapp, window):
    shown = window()
    read(qapp, shown)
    assert not shown.table.flash  # the first reading lights none
    snap = shown.snap
    plane = titled(snap, "Route the power plane on the 4-layer board").session_id
    opened = dataclasses.replace(snap.sessions[plane], exit=sessions.Exit.RUNNING, status=sessions.Status.RUNNING,
                                 live_state="busy")
    new = dataclasses.replace(opened, session_id="made-up-new")  # new to the list: nothing seen changed
    shown.take(shown.store, dataclasses.replace(snap, sessions={**snap.sessions, plane: opened, new.session_id: new}),
               False, "")
    assert set(shown.table.flash) == {plane}
    views = {button.text(): button for button in shown.views.buttons()}
    QTest.mouseClick(views["Table"], Qt.MouseButton.LeftButton)
    shown.tick()
    table = shown.table
    parts = table.grid.item(table.keys.index(plane), COLUMN["status"]).data(PARTS)
    assert parts and all(look.ink(style).background for _, style in parts)  # lit behind its words


def test_space_ticks_f_pins_and_x_hides_as_marks_json_keeps_them(qapp, window):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id  # closed: Space ticks it
    board = shown.board
    assert board.reveal(login)
    press(shown, "Space")
    assert shown.selected == {login} and board.cards[login].tick == "✓" and "1 ticked" in shown.pills.text()
    press(shown, "f")  # the ticked one, wherever the pick went
    assert saved_marks()["pinned"] == [login] and login in shown.snap.pinned and board.cards[login].pinned
    assert shown.bit_line.text() == "Pinned “Fix the login redirect loop” above the rest."
    press(shown, "x")
    assert saved_marks()["hidden"] == [login] and shown.selected == set()  # out of sight: unticked
    assert login not in board.cards and shown.info.text().endswith("· 1 hidden (X shows them)")
    assert shown.bit_line.text().startswith("Hid “Fix the login redirect loop”. Nothing is deleted")
    read(qapp, shown)
    assert shown.snap.hidden == {login} and "Hid “Fix" in shown.bit_line.text()  # a read keeps what Bit says
    press(shown, "X")
    assert board.cards[login].title == "hidden · Fix the login redirect loop"
    assert shown.info.text().endswith("showing 1 hidden (x brings one back)")
    assert board.reveal(login)
    press(shown, "x")
    assert saved_marks()["hidden"] == [] and shown.bit_line.text() == "Back in sight: “Fix the login redirect loop”."
    shown.said_timer.timeout.emit()
    assert shown.bit_line.text() == main.NONE_ACTS  # then who acts again
    open_one = next(s for s in shown.snap.sessions.values()  # its own card, not a team's
                    if s.exit is sessions.Exit.RUNNING and board.card_of(s.session_id) == s.session_id)
    assert board.reveal(open_one.session_id) and board.pick == open_one.session_id
    press(shown, "Space")
    assert shown.selected == set()  # open in a terminal: nothing to tick
    press(shown, "x")
    assert shown.bit_line.text() == marks.OPEN_ALWAYS_SHOW and saved_marks()["hidden"] == []


def test_a_mark_made_while_a_read_is_under_way_and_by_another_dashboard_stays(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id
    docs = titled(shown.snap, "Rewrite the getting-started guide").session_id
    marks.Marks(pinned={docs}).save()  # the terminal dashboard pinned one since the window read them
    real, came, go = shown.store.poll, threading.Event(), threading.Event()

    def slow():
        snap = real()  # marks.json read now, before the window changes it
        came.set()
        go.wait(10)
        return snap

    monkeypatch.setattr(shown.store, "poll", slow)
    shown.poll()
    assert came.wait(10)
    assert shown.board.reveal(login)
    press(shown, "f")
    assert saved_marks()["pinned"] == sorted([docs, login])  # the other's pin kept
    go.set()
    wait(qapp, lambda: not shown.waiting)
    qapp.processEvents()
    assert shown.snap.pinned == {docs, login}  # the read that was under way shows it too


def test_f_and_x_leave_a_marks_json_they_cannot_read_as_it_is_and_say_so(qapp, window):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id
    marks.Marks(pinned={"theirs"}).save()
    path = config_dir() / marks.FILE
    half = path.read_bytes()[:-9]  # cut off: what another tool was writing in place
    path.write_bytes(half)
    assert shown.board.reveal(login)
    press(shown, "Space")
    press(shown, "f")
    assert shown.bit_line.text() == marks.UNREAD.words and path.read_bytes() == half  # not saved over: kept
    assert login not in shown.snap.pinned and shown.selected == {login}
    press(shown, "x")
    assert shown.bit_line.text() == marks.UNREAD.words and login in shown.board.cards
    assert path.read_bytes() == half
    press(shown, "Tab")
    projects = shown.rail.projects
    for _ in range(next(n for n, row in enumerate(projects.rows) if n and row.mark not in ("open", "waiting"))):
        QTest.keyClick(projects, Qt.Key.Key_Down)
    picked = shown.project
    press(shown, "x")  # a project, from the rail: nothing changed, so it stays picked
    assert shown.bit_line.text() == marks.UNREAD.words and shown.project == picked and path.read_bytes() == half


def test_a_pin_that_cannot_be_saved_changes_nothing_and_says_so(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id

    def held(*args, **kwargs):  # another process holds marks.json all the while
        raise PermissionError(13, "The process cannot access the file", marks.FILE)

    monkeypatch.setattr(marks, "write_json", held)
    assert shown.board.reveal(login)
    press(shown, "f")
    assert shown.bit_line.text() == marks.UNSAVED.words and login not in shown.snap.pinned
    assert saved_marks()["pinned"] == [] and not shown.board.cards[login].pinned


def test_tab_puts_the_keys_on_the_rail_where_x_hides_a_project(qapp, window):
    shown = window()
    read(qapp, shown)
    projects = shown.rail.projects
    press(shown, "Tab")
    assert projects.hasFocus()
    press(shown, "x")
    assert shown.bit_line.text().startswith("Pick a project on the rail") and saved_marks()["projects"] == []
    at = next(n for n, row in enumerate(projects.rows) if n and row.mark not in ("open", "waiting"))
    for _ in range(at):  # down to a project with none open (an open one keeps it on the rail)
        QTest.keyClick(projects, Qt.Key.Key_Down)
    picked = projects.rows[at]
    assert shown.project == picked.key == projects.pick != VM.ALL_PROJECTS
    narrowed = shown.shown()
    assert narrowed and all(sessions.folder_key(s.cwd) == picked.key for s in narrowed)
    press(shown, "x")
    assert saved_marks()["projects"] == [picked.key] and shown.project == VM.ALL_PROJECTS
    assert picked.key not in [row.key for row in projects.rows]
    assert shown.bit_line.text().startswith(f"Hid the project {picked.name} (")
    press(shown, "X")
    assert picked.key in [row.key for row in projects.rows]  # the hidden ones listed: x brings it back
    QTest.keyClick(projects, Qt.Key.Key_End)
    QTest.keyClick(projects, Qt.Key.Key_Home)
    assert shown.project == VM.ALL_PROJECTS
    QTest.keyClick(projects, Qt.Key.Key_Return)
    assert shown.board.hasFocus()
    press(shown, "Tab")
    press(shown, "Tab")
    assert shown.board.hasFocus()


def test_the_table_ticks_with_space_and_its_box_and_e_and_shift_e_unfold_teams(qapp, window):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id
    data = titled(shown.snap, "Speed up the nightly CSV import").session_id
    team = shown.snap.view.teams[0].name
    press(shown, "E")
    assert shown.bit_line.text().startswith("The board shows each team as one card")
    shown.set_view("table")
    table = shown.table

    def box(key):
        return "".join(words for words, _ in table.grid.item(table.keys.index(key), COLUMN["sel"]).data(PARTS))

    assert table.show_key(login)
    press(shown, "Space")
    assert shown.selected == {login} and box(login) == "[x]" and table.pick != login
    spot = table.visualRect(table.grid.index(table.keys.index(data), COLUMN["sel"])).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=spot)
    assert shown.selected == {login, data} and box(data) == "[x]" and table.pick == data
    press(shown, "a")  # every one shown, then none
    tickable = {s.session_id for s in shown.shown() if VM.tickable(s)}
    assert shown.selected == tickable and shown.pills.text().endswith(f"{len(tickable)} ticked")
    press(shown, "a")
    assert shown.selected == set()
    state = shown.snap.team_states[team]
    lead = state.sessions[state.team.lead.name].session_id
    press(shown, "E")
    assert table.unfolded == {team} and lead in table.keys
    assert table.show_key(lead)
    press(shown, "E")  # folded again: the pick on its team's row
    assert table.unfolded == set() and table.pick == rowwords.TEAM_ROW + team
    press(shown, "e")
    assert table.unfolded == {team}
    press(shown, "e")
    assert table.unfolded == set()
    press(shown, "a")  # on a team's row: its new members asked for (teamkeysdialogs.ask_add), none here
    (asked,) = [d for d in shown.findChildren(AddMembersDialog) if d.isVisible()]
    asked.reject()
    shown.activateWindow()  # its keys again
    assert QTest.qWaitForWindowActive(shown, 3000)
    press(shown, "x")  # an open team: x again closes its members' tabs (hidden: none)
    assert shown.bit_line.text().startswith("Press x again to close the") and saved_marks()["hidden"] == []
    shown.engine.state.team_presses.clear()  # x's first press forgotten
    assert table.show_key(login)
    press(shown, "e")
    assert shown.docks["Preview"].isVisible()  # how it ended: the drawer says it


def test_esc_clears_the_words_then_the_card_then_the_project_then_closes(qapp, window):
    shown = window()
    read(qapp, shown)
    key = shown.rail.projects.rows[1].key
    shown.toggle_card("continue")  # first: in that project alone, nothing to continue would keep the card off
    shown.set_project(key)
    press(shown, "/")
    QTest.keyClicks(shown.filter_box, "fix x a")  # typed: never the keys x and a
    assert shown.words == "fix x a" and shown.selected == set() and saved_marks()["hidden"] == []
    press(shown, "Esc")
    assert (shown.words, shown.card, shown.project) == ("", "continue", key) and shown.board.hasFocus()
    press(shown, "Esc")
    assert (shown.card, shown.project) == (None, key)
    press(shown, "Esc")
    assert shown.project == VM.ALL_PROJECTS and shown.isVisible()
    press(shown, "Esc")
    assert not shown.isVisible()


def recorded(monkeypatch, left=()):
    """What the window asks of the world, kept: the tabs to open (launch.open_tabs; left: the commands
    no terminal took) and the tabs to bring to the front (focus.focus_tab)."""
    asked = {"opened": [], "focused": []}

    def open_tabs(launches, window=None):
        asked["opened"].append(list(launches))
        return list(left)

    def focus_tab(title):
        asked["focused"].append(title)
        return True

    monkeypatch.setattr(launch, "open_tabs", open_tabs)
    monkeypatch.setattr(focus, "focus_tab", focus_tab)
    return asked


def settled(qapp, shown):
    shown.opener.settle()  # what Enter and j do runs on threads of its own
    qapp.processEvents()


def test_enter_opens_the_picked_one_once_as_m_says(qapp, window, monkeypatch):
    asked = recorded(monkeypatch)
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop")
    data = titled(shown.snap, "Speed up the nightly CSV import")
    assert shown.board.reveal(login.session_id)
    assert "resumes it in a new tab · m changes how" in shown.drawer.enter.text()
    press(shown, "Enter")
    settled(qapp, shown)
    [launches] = asked["opened"]
    assert [(one.session_id, one.mode) for one in launches] == [(login.session_id, Mode.RESUME)]
    assert shown.bit_line.text() == f"Opened {launches[0].title} in a new tab." and shown.after_open.isActive()
    press(shown, "Enter")  # still starting up: not opened twice
    assert shown.bit_line.text() == opening.ALREADY.words and len(asked["opened"]) == 1
    assert shown.board.reveal(data.session_id)
    assert shown.opener.mode(data) is Mode.AUTO  # unanswered: it carries on by itself
    press(shown, "m")
    assert shown.opener.mode(data) is Mode.FORK
    assert "forks it: a new session in a new tab" in shown.drawer.enter.text()
    shown.set_view("table")
    table = shown.table
    assert table.pick == data.session_id
    mode = table.grid.item(table.keys.index(data.session_id), COLUMN["mode"]).data(PARTS)
    assert "".join(words for words, _ in mode) == "fork"  # the Open as column says it
    press(shown, "Enter")
    settled(qapp, shown)
    assert [(one.session_id, one.mode) for one in asked["opened"][-1]] == [(data.session_id, Mode.FORK)]
    open_one = next(s for s in shown.snap.sessions.values() if s.status is sessions.Status.RUNNING)
    assert table.show_key(open_one.session_id)
    press(shown, "m")
    assert shown.bit_line.text().startswith("m changes how a closed session opens")
    assert open_one.session_id not in shown.opener.modes


def test_enter_opens_the_ticked_ones_goes_to_an_open_ones_tab_and_shows_a_teams_panel(qapp, window, monkeypatch):
    asked = recorded(monkeypatch)
    shown = window()
    read(qapp, shown)
    board = shown.board
    login = titled(shown.snap, "Fix the login redirect loop").session_id
    docs = titled(shown.snap, "Rewrite the getting-started guide").session_id
    for one in (login, docs):
        assert board.reveal(one)
        press(shown, "Space")
    assert shown.selected == {login, docs} and "opens the 2 ticked sessions" in shown.drawer.enter.text()
    press(shown, "Enter")
    settled(qapp, shown)
    assert {one.session_id for one in asked["opened"][0]} == {login, docs} and shown.selected == set()
    assert shown.bit_line.text() == "Opened 2 sessions in new tabs." and "ticked" not in shown.pills.text()
    open_one = next(s for s in shown.snap.sessions.values()
                    if s.status is sessions.Status.RUNNING and board.card_of(s.session_id) == s.session_id)
    assert board.reveal(open_one.session_id)
    press(shown, "Enter")
    settled(qapp, shown)
    assert asked["focused"] == [open_one.title]
    assert shown.bit_line.text() == f"Switched to {open_one.project}. Type your prompt there."
    team = shown.snap.view.teams[0].name
    assert board.reveal(VM.TEAM_CARD + team)
    press(shown, "Enter")  # a team's card: its Team panel tab, as Enter on its row opens its view there
    panel = shown.team_panel
    assert shown.docks["Team"].isVisible() and panel.names[panel.tabs.currentIndex()] == team
    assert len(asked["opened"]) == 1 and len(asked["focused"]) == 1


def test_j_goes_to_the_next_one_waiting_and_with_no_terminal_the_commands_go_to_the_clipboard(qapp, window,
                                                                                           monkeypatch):
    asked = recorded(monkeypatch, left=["claude --resume made-up"])
    shown = window()
    read(qapp, shown)
    first = actions.next_waiting(shown.snap.sessions.values())
    second = actions.next_waiting(shown.snap.sessions.values(), first.group, first.turn)
    assert first.group == "needs" and first.session is not None and second.session is not None
    press(shown, "j")
    settled(qapp, shown)
    assert asked["focused"] == [first.session.title]
    assert shown.board.pick == shown.board.card_of(first.session.session_id)  # picked: its card, or its team's
    press(shown, "j")
    settled(qapp, shown)
    assert asked["focused"] == [first.session.title, second.session.title]
    login = titled(shown.snap, "Fix the login redirect loop").session_id
    assert shown.board.reveal(login)
    press(shown, "Enter")
    settled(qapp, shown)
    assert len(asked["opened"]) == 1 and QApplication.clipboard().text() == "claude --resume made-up"
    assert shown.bit_line.text() == opening.NO_TERMINAL and not shown.after_open.isActive()


def test_x_twice_on_a_team_with_members_open_closes_their_tabs(qapp, window, monkeypatch):
    closed = []

    def close_tab(name):
        closed.append(name)
        return True

    monkeypatch.setattr(focus, "close_tab", close_tab)
    monkeypatch.setattr(actions, "close_tabs", CLOSE_TABS)  # as outside the demo: the tabs are looked for
    shown = window()
    read(qapp, shown)
    state = next(state for state in shown.snap.team_states.values()
                 if VM.members_open(state, shown.snap.sessions))
    team = state.team.name
    members = {name: shown.snap.sessions.get(s.session_id, s) if s is not None else None
               for name, s in state.sessions.items()}
    names = actions.open_names(state.team, members)
    assert names and shown.board.reveal(VM.TEAM_CARD + team)
    press(shown, "x")
    assert shown.bit_line.text() == actions.closing_note(state.team, members) and closed == []
    assert shown.front.team_keys().held("close") == team  # the terminal's x (TeamKeys.close), its press kept
    shown.engine.state.team_presses["close"] = (team, 0.0)  # too late: x again only says it again
    press(shown, "x")
    assert closed == [] and shown.bit_line.text().startswith(f"Press x again to close the {len(names)} open")
    press(shown, "x")
    settled(qapp, shown)
    assert closed == names and shown.front.team_keys().held("close") is None and saved_marks()["hidden"] == []
    assert shown.bit_line.text() == f"The team's members: closed {len(names)} tabs." and shown.after_open.isActive()


def test_x_twice_in_the_demo_says_which_tabs_would_have_closed_and_looks_for_none(qapp, window, monkeypatch):
    asked = []
    monkeypatch.setattr(focus, "close_tab", asked.append)
    shown = window()
    read(qapp, shown)
    state = next(state for state in shown.snap.team_states.values()
                 if VM.members_open(state, shown.snap.sessions))
    members = {name: shown.snap.sessions.get(s.session_id, s) if s is not None else None
               for name, s in state.sessions.items()}
    names = actions.open_names(state.team, members)
    assert len(names) > 1 and shown.board.reveal(VM.TEAM_CARD + state.team.name)
    press(shown, "x")
    press(shown, "x")
    settled(qapp, shown)
    tabs = f"{', '.join(names[:-1])} and {names[-1]}"
    assert shown.bit_line.text() == (f"Nothing closes in the demo. For real, the tabs of {tabs} would close, as on "
                                      "/exit; the sessions stay.") and asked == []


def test_m_opens_the_picked_teams_monitor_and_again_brings_it_to_the_front(qapp, window):
    shown = window()
    read(qapp, shown)
    login = titled(shown.snap, "Fix the login redirect loop").session_id  # in no team
    assert shown.board.reveal(login)
    press(shown, "M")
    assert shown.bit_line.text().startswith("M opens a team's monitor: pick a team") and shown.monitors == {}
    team, state = next(iter(shown.snap.team_states.items()))
    assert shown.board.reveal(VM.TEAM_CARD + team)
    press(shown, "M")
    watching = shown.monitors[team]
    assert isinstance(watching, monitor.MonitorWindow) and watching.isVisible() and watching.team == team
    assert shown.bit_line.text() == f"The monitor of {team} opened in a window of its own."
    shown.activateWindow()  # the monitor took the keys: back to the dashboard's
    assert QTest.qWaitForWindowActive(shown, 3000)
    shown.set_view("table")
    press(shown, "E")  # every team unfolded in the Table
    member = next(s.session_id for s in state.sessions.values() if s is not None)
    assert shown.table.show_key(member)
    press(shown, "M")  # a member's row: its team's monitor, the one open
    assert shown.monitors == {team: watching} and shown.bit_line.text() == f"The monitor of {team} is in front."
    watching.close()
    assert not watching.timer.isActive()
    shown.activateWindow()
    assert QTest.qWaitForWindowActive(shown, 3000)
    press(shown, "M")  # closed since: the same one again, reading again
    assert shown.monitors == {team: watching} and watching.isVisible() and watching.timer.isActive()
    shown.settle()
    shown.close()  # the monitors M opened go with the window
    assert not watching.isVisible()


def menu_items(shown) -> list[tuple[str, bool, str]]:
    """The right-click menu shown: each item's words (its key after a tab), whether it can be picked, and a
    greyed key's tip (why not)."""
    return [(item.text(), item.isEnabled(), "" if item.isEnabled() or item.data() is None else item.toolTip())
            for item in shown.popup.actions() if not item.isSeparator()]


def right_click(widget, spot: QPoint) -> None:
    QApplication.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, spot, widget.mapToGlobal(spot)))


def menu_key(widget) -> None:
    corner = QPoint(0, 0)
    QApplication.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, corner, widget.mapToGlobal(corner)))


def test_a_right_click_on_a_card_or_a_row_picks_it_and_offers_the_drawers_buttons(qapp, window):
    shown = window()
    read(qapp, shown)
    board, snap = shown.board, shown.snap
    login = titled(snap, "Fix the login redirect loop").session_id  # closed: its lane folds till revealed
    assert board.reveal(login) and board.reveal(next(key for key in board.cards if key != login))

    def card_spot(key: str):
        lane = next(view for view in board.views.values() if view.column.rect_of(key) is not None)
        return lane.column, lane.column.rect_of(key).center().toPoint()

    right_click(*card_spot(login))
    assert board.pick == login and shown.popup is not None and shown.popup.isVisible()
    items = menu_items(shown)
    assert items[0] == ("Fix the login redirect loop", False, "")  # the heading: what it is for
    assert items[1:] == [(f"{words}\t{key}", shown.why_not(key) is None, shown.why_not(key) or "")
                         for key, words in shown.drawer.offered]  # the drawer's buttons, greyed as there
    pin = next(item for item in shown.popup.actions() if item.data() == "f")
    assert pin.isEnabled()
    pin.trigger()
    assert saved_marks()["pinned"] == [login] and shown.bit_line.text().startswith("Pinned “Fix the login")
    shown.popup.close()
    assert shown.popup is None
    team = next(iter(snap.team_states))
    right_click(*card_spot(VM.TEAM_CARD + team))  # a team's card: its menu, M in it
    items = menu_items(shown)
    assert board.pick == VM.TEAM_CARD + team and items[0] == (f"Team {team}", False, "")
    assert any(words.endswith("\tM") and enabled for words, enabled, _ in items)
    shown.set_view("table")
    table = shown.table
    open_one = next(key for key in table.keys if key in snap.sessions and snap.sessions[key].exit is sessions.Exit.RUNNING)

    def row_spot(key: str) -> QPoint:
        return table.visualRect(table.grid.index(table.keys.index(key), COLUMN["title"])).center()

    shown.popup.close()
    picked = table.pick
    right_click(table.viewport(), row_spot(table.keys[0]))  # the open ones' heading: nothing
    assert table.keys[0].startswith(rowwords.HEADING) and table.pick == picked and shown.popup is None
    right_click(table.viewport(), row_spot(open_one))
    items = menu_items(shown)
    assert table.pick == open_one and items[0][0] == actions.clip(snap.sessions[open_one].title, main.MENU_TITLE)
    assert all(enabled and tip == "" for _, enabled, tip in items[1:])  # p too: its tab comes to the front
    assert any(words.endswith("\tp") for words, _, _ in items[1:])
    first = shown.popup
    menu_key(table)  # the menu key: the picked row's
    assert shown.popup is not first and menu_items(shown)[0] == items[0] and not first.isVisible()
    shown.set_view("board")
    menu_key(board)  # on the board: the picked card's (the pick is one across the views)
    assert board.pick == open_one and menu_items(shown)[0] == items[0]
    shown.close()
    assert shown.popup is None


def test_the_demo_window_says_what_would_have_opened(qapp, monkeypatch):
    for name in frame.DEMO_ENV:  # made_up_world points these at its own home, and puts them back
        monkeypatch.setenv(name, os.environ.get(name, ""))
    told = main.Told()
    with frame.made_up_world(True, told.said.emit):
        shown = main.MainWindow(start=False, demo=True)
        told.said.connect(shown.demo_told)
        try:
            shown.show()
            QTest.qWaitForWindowActive(shown, 3000)
            read(qapp, shown)
            assert shown.board.reveal(titled(shown.snap, "Fix the login redirect loop").session_id)
            press(shown, "Enter")
            settled(qapp, shown)
            wait(qapp, lambda: "Nothing opens" in shown.bit_line.text())
            qapp.processEvents()
            assert shown.bit_line.text().startswith("Nothing opens in the demo. For real, a new tab would run: ")
            assert not shown.opener.update  # no claude update over made-up sessions
        finally:
            shown.settle()
            shown.close()


def test_it_never_acts(qapp, window, world, monkeypatch):
    """No actor lock claimed, no plan limit or account recorded, agy never run, nothing sent or closed,
    whatever key is pressed. What a key asks for past the window is done (Enter opens: the demo's
    stand-in says what would have), and nothing more."""
    done = []
    for name in ("claim", "first_to", "release"):
        monkeypatch.setattr(actor, name, lambda *a, _name=name, **k: done.append(_name))
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: done.append("agy /usage"))
    for module, name in ((teams, "new_team"), (teams, "save"), (teambus, "send"), (usage, "record"),
                         (accounts, "remember")):
        monkeypatch.setattr(module, name, lambda *a, _name=name, **k: done.append(_name))
    shown = window()
    for _ in range(3):
        read(qapp, shown)
    for _group, keys in main.WINDOW_KEYS:
        for key, _action, _words in keys:
            if key in ("q", "?", "Esc"):
                continue
            for sequence in main.sequences(key):
                QTest.keySequence(shown, sequence)
    settled(qapp, shown)
    assert done == []
    # the demo's stand-ins were asked to start, type or rename nothing; Enter's tabs alone
    assert all(words.startswith("Nothing opens in the demo.") for words in world)
    assert shown.isVisible()


def test_question_mark_lists_the_keys_and_q_closes(qapp, window):
    shown = window()
    QTest.keyClick(shown, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
    qapp.processEvents()
    dialogs = [w for w in qapp.topLevelWidgets() if isinstance(w, frame.KeysDialog) and w.isVisible()]
    assert dialogs
    for dialog in dialogs:
        dialog.close()
    shown.activateWindow()
    QTest.qWaitForWindowActive(shown, 3000)
    QTest.keyClick(shown, Qt.Key.Key_Q)
    qapp.processEvents()
    assert not shown.isVisible()


def keys_dialog(qapp, shown) -> frame.KeysDialog:
    """? pressed: the list it opens."""
    shown.actions_by_key["?"].trigger()
    qapp.processEvents()
    (dialog,) = [w for w in qapp.topLevelWidgets() if isinstance(w, frame.KeysDialog) and w.isVisible()]
    return dialog


def keys_list(qapp, shown) -> str:
    """What ? lists, as plain text (the dialog closed again)."""
    dialog = keys_dialog(qapp, shown)
    text = dialog.plain()
    dialog.close()
    return text


def pick_line(dialog: frame.KeysDialog, key: str) -> None:
    rows = dialog.rows
    (item,) = [rows.item(n) for n in range(rows.count()) if rows.item(n).data(frame.KEY_ROLE) == key]
    rows.setCurrentItem(item)
    rows.scrollToItem(item)  # in sight, for a click on it


def test_one_look_here_t_in_no_list_and_the_theme_picked_in_the_terminal_never_read(qapp, window):
    """Sinan's call (2026-11-26): no theme in the window. t is in no menu, nor in ?, nor in the palette, and the
    accent is the dashboard's own orange whatever config.json's theme (the terminal's t) is."""
    assert merge_json("config.json", theme="tabmancer-black-green")
    shown = window()
    orange = palette.THEMES[palette.DEFAULT_THEME][1]
    assert palette.saved_theme() == "tabmancer-black-green" and look.accent() == shown.accent == orange
    in_menus = {action.data() for menu in shown.menus.findChildren(QMenu) for action in menu.actions()}
    assert "t" not in shown.actions_by_key and "t" not in in_menus
    assert "colour theme" not in keys_list(qapp, shown)
    shown.open_commands()
    assert "t" not in {entry.key for entry in shown.command_bar.entries if entry.kind == "key"}
    shown.command_bar.hide()


def test_v_is_the_terminals_own_said_so_wherever_it_shows_and_j_has_its_place_on_the_key_bar(qapp, window):
    """V lays the terminal's list and preview out; here the panels drag, split and float (Sinan's call,
    2026-11-26). Greyed with those words in its menu, in ? and in the palette (never "yet"), and off the key
    bar, whose place is j's. b, the last key still to come, is the window's own since the pet (2026-11-27)."""
    shown = window()
    own = main.TERMINALS["V"]
    made = shown.actions_by_key["V"]
    assert not made.isEnabled() and made.toolTip().endswith(f". {own}")
    assert "V" not in dict(main.KEY_BAR) and dict(main.KEY_BAR)["j"] == "needs you" and "j" not in shown.keybar.dim
    lines = keys_list(qapp, shown).splitlines()
    (v_line,) = [line for line in lines if line.startswith("V ")]
    (b_line,) = [line for line in lines if line.startswith("b ")]
    assert v_line.endswith(f"({own})") and b_line == f"{'b':<6}{main.OWN_WORDS['b']}"
    shown.open_commands()
    by_key = {entry.key: entry for entry in shown.command_bar.entries if entry.kind == "key"}
    assert not by_key["V"].enabled and by_key["V"].why == own
    assert by_key["b"].enabled
    shown.command_bar.hide()


def test_enter_on_a_line_of_question_mark_runs_its_key_as_the_terminals_menu_does(qapp, window):
    """Sinan's call (2026-11-27): ? runs the picked key, as the terminal's ? menu. ↑↓ steps over the groups'
    headings and gaps; Enter on l's line: the list goes and the live view comes, l again (a double click) goes
    back; Enter on ?'s own line only closes it."""
    shown = window()
    before = shown.view
    assert before != "live"
    dialog = keys_dialog(qapp, shown)
    assert dialog.picked() is not None and dialog.rows.hasFocus()
    for _ in range(dialog.rows.count()):
        QTest.keyClick(dialog.rows, Qt.Key.Key_Down)
        assert dialog.picked() is not None  # never a heading nor a gap
    pick_line(dialog, "l")
    assert dialog.note.text() == frame.KEYS_NOTE
    QTest.keyClick(dialog.rows, Qt.Key.Key_Return)
    qapp.processEvents()
    assert not dialog.isVisible() and shown.view == "live"
    dialog = keys_dialog(qapp, shown)
    pick_line(dialog, "l")
    rows = dialog.rows
    at = rows.visualItemRect(rows.currentItem()).center()
    QTest.mouseClick(rows.viewport(), Qt.MouseButton.LeftButton, pos=at)  # picked (it was): nothing runs yet
    qapp.processEvents()
    assert dialog.isVisible() and shown.view == "live"
    QTest.mouseDClick(rows.viewport(), Qt.MouseButton.LeftButton, pos=at)
    qapp.processEvents()
    assert not dialog.isVisible() and shown.view == before
    dialog = keys_dialog(qapp, shown)
    pick_line(dialog, "?")
    assert dialog.note.text() == frame.KEYS_SELF
    QTest.keyClick(dialog.rows, Qt.Key.Key_Return)
    qapp.processEvents()
    assert not [w for w in qapp.topLevelWidgets() if isinstance(w, frame.KeysDialog) and w.isVisible()]


def test_a_greyed_line_of_question_mark_says_why_and_runs_nothing(qapp, window, monkeypatch):
    """V (the terminal's own): picked, the line under the list says where it is done; Enter says it louder and runs
    nothing, the list staying."""
    shown = window()
    ran = []
    monkeypatch.setattr(shown, "run_key", ran.append)
    dialog = keys_dialog(qapp, shown)
    pick_line(dialog, "V")
    assert dialog.note.text() == main.TERMINALS["V"] and look.AMBER not in dialog.note.styleSheet()
    QTest.keyClick(dialog.rows, Qt.Key.Key_Return)
    qapp.processEvents()
    assert dialog.isVisible() and ran == [] and look.AMBER in dialog.note.styleSheet()
    pick_line(dialog, "j")
    QTest.keyClick(dialog.rows, Qt.Key.Key_Return)
    qapp.processEvents()
    assert ran == ["j"]  # what is done here goes to the window's own run_key


def test_b_makes_bit_lively_calm_or_away_and_the_terminals_bit_too(qapp, window):
    """b in the window (Sinan's call, 2026-11-27: the pet): lively, calm, away and lively again, saved as the
    terminal's b saves it (config.json "mascot"), said on Bit's line. Calm makes the team views calm too
    (teamgraph.motion); away leaves no Bit, its line staying. The terminal's "blocks" reads lively here."""
    merge_json("config.json", mascot="blocks")
    shown = window()
    pet = shown.pet
    assert shown.bit_line is pet.line and pet.way == "pixels" and not pet.at.isNull()
    for way in ("calm", "away", "pixels"):
        shown.actions_by_key["b"].trigger()
        assert pet.way == way and read_json("config.json")["mascot"] == way
        assert shown.bit_line.text() == bitpet.SAID[way]
        assert pet.life.calm == (way == "calm") and (teamgraph.motion() == "calm") == (way == "calm")
        assert pet.at.isNull() == (way == "away")
        if way == "away":
            assert not any(timer.isActive() for timer in pet.timers) and pet.line.x() == 0


def test_bit_knows_what_the_sessions_do_after_each_reading(qapp, window):
    """Each reading tells Bit how many sessions work and how many need you (the terminal's counts): it calls
    while one needs you."""
    shown = window()
    read(qapp, shown)
    snap = shown.snap
    working, waiting = bitpet.mood(snap.sessions.values(), snap.hidden)
    assert (shown.pet.life.working, shown.pet.life.waiting) == (working, waiting)
    shown.pet.set_mood(working, waiting + 1)
    assert shown.pet.life.waiting == waiting + 1 and (waiting or shown.pet.life.action == "call")


def test_q_and_question_mark_close_the_keys_list_and_never_quit(qapp, window):
    """In the list, q and ? close it, as the terminal's: typed there, never the window's own q (quit)."""
    shown = window()
    for key, held in ((Qt.Key.Key_Q, Qt.KeyboardModifier.NoModifier),
                      (Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)):
        dialog = keys_dialog(qapp, shown)
        QTest.keyClick(dialog.rows, key, held)
        qapp.processEvents()
        assert not dialog.isVisible() and shown.isVisible()


def test_a_second_open_brings_the_first_forward(qapp, window, monkeypatch):
    shown = window()
    came = []
    monkeypatch.setattr(shown, "come_forward", lambda: came.append(True))
    assert main.knock() is True
    wait(qapp, lambda: came, seconds=5)
    assert main.door() != frame.door("monitor", "main")  # never a team called main's monitor
    monkeypatch.setattr(main, "MainWindow", lambda *a, **kw: pytest.fail("a second window"))
    assert main.run() == 0
    shown.close()
    qapp.processEvents()
    assert main.knock() is False  # closed: its door with it


KNOCK = "import sys; from tabmancer.desktop import frame, main; sys.exit(not frame.knock(main.door(), *sys.argv[1:]))"


def knocking(*said: str) -> subprocess.Popen:
    """A knock from another process, as `tabmancer live` knocks (same tabmancer folder, so the same door): the
    window's events go on meanwhile. From this process it would wait on the window it knocks at, and a thread
    of it has no Qt events to send its word with."""
    return subprocess.Popen([sys.executable, "-c", KNOCK, *said], env=dict(os.environ))


def test_live_with_the_window_open_switches_it_to_live(qapp, window, monkeypatch):
    """`tabmancer live` while the window is open: the knock names the view, and the open window shows it; a
    plain knock (`tabmancer` again) and a word it does not know leave the view as it is."""
    shown = window()
    came = []
    monkeypatch.setattr(shown, "come_forward", lambda: came.append(True))
    assert shown.view != "live"
    knocked = knocking("live")
    wait(qapp, lambda: knocked.poll() == 0 and shown.view == "live" and came, seconds=20)
    shown.set_view("table")
    for said in ((), ("nonsense board-ish",)):
        knocked = knocking(*said)
        wait(qapp, lambda knocked=knocked: knocked.poll() == 0, seconds=20)
    wait(qapp, lambda: len(came) == 3, seconds=5)
    for _ in range(20):
        qapp.processEvents()
    assert shown.view == "table"  # nothing named that it knows
    knocked = knocking("board")
    wait(qapp, lambda: knocked.poll() == 0 and shown.view == "board", seconds=20)


def test_run_with_a_view_and_the_window_open_names_it_at_the_door(monkeypatch):
    """run(view=) knocks with the view: an open window takes it, and no second one opens."""
    asked = []
    monkeypatch.setattr(main, "knock", lambda view="": asked.append(view) or True)
    monkeypatch.setattr(main, "MainWindow", lambda *a, **kw: pytest.fail("a second window"))
    assert main.run(view="live") == 0 and main.run() == 0
    assert asked == ["live", ""]


def test_cli_opens_the_window_till_it_closes(qapp, world, monkeypatch, later):  # noqa: F811
    """The seam: `tabmancer desktop` to a window that reads the sessions, then closes, and is deleted on the
    GUI thread (never left for the cycle collector to free on another one)."""
    seen, shown = [], []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append(len(windows[0].snap.sessions))
            shown.append(windows[0])
            qapp.quit()
        else:
            later(100, look_then_quit)

    later(100, look_then_quit)
    later(30_000, qapp.quit)  # never hangs the suite
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop"])
    assert out.value.code == 0
    assert seen and seen[0] >= len(demo.SESSIONS)
    assert "main" in read_json(frame.LAYOUT_FILE)  # closed as a window closes
    assert not shiboken6.isValid(shown[0]) and gc.isenabled()


def test_run_opens_on_the_view_asked_for(qapp, world, later):  # noqa: F811
    """`tabmancer live`, the window now: run(view="live") opens it on the live view, and l goes back to the
    board (the view it had before)."""
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if not windows:
            later(100, look_then_quit)
            return
        shown = windows[0]
        seen.append((shown.view, shown.views.checkedButton().text(), shown.table.live))
        shown.toggle_live()
        seen.append((shown.view, shown.views.checkedButton().text(), shown.table.live))
        qapp.quit()

    later(100, look_then_quit)
    later(30_000, lambda: qapp.exit(1))  # never hangs the suite
    assert main.run(view="live") == 0
    assert seen == [("live", "Live", True), ("board", "Board", False)]


def test_the_demo_puts_the_world_back(qapp, monkeypatch, later):  # noqa: F811
    """`tabmancer desktop --demo`: the made-up sessions, and afterwards nothing of them left; the days
    and --all given reach the window's store and its info line."""
    before = {name: os.environ.get(name) for name in frame.DEMO_ENV}
    agents = sessions._agents_json
    seen, kept = [], []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            shown = windows[0]
            seen.append((shown.brand.text(), len(shown.snap.sessions), shown.store.days, shown.store.include_all,
                         shown.info.text()))
            kept.append(shown)
            qapp.quit()
        else:
            later(100, look_then_quit)

    later(100, look_then_quit)
    later(30_000, qapp.quit)
    assert main.run(demo=True, days=30, include_all=True) == 0
    assert len(seen) == 1 and seen[0][0] == "◆ tabmancer demo" and seen[0][1] >= len(demo.SESSIONS)
    assert seen[0][2:4] == (30, True) and "last 30 days" in seen[0][4]
    assert {name: os.environ.get(name) for name in frame.DEMO_ENV} == before
    assert sessions._agents_json is agents
    assert not shiboken6.isValid(kept[0])  # deleted on the GUI thread as run ends
