"""The dashboard's desktop window (`tabmancer desktop`), offscreen over the made-up sessions of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import gc
import importlib
import os
import re
import threading
import time
import weakref
from datetime import datetime, timedelta
from functools import partial

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget, QMenu

from tabmancer import (
    accounts,
    actions,
    actor,
    agy,
    cli,
    commands,
    demo,
    focus,
    launch,
    marks,
    rowwords,
    sessions,
    store,
    teambus,
    teams,
    usage,
)
from tabmancer import sessionwords as preview_words
from tabmancer import viewmodel as VM
from tabmancer.config import config_dir, merge_json, read_json
from tabmancer.desktop import commandbar, drawer, frame, look, main, opening, rail, teamgraph, teampanel
from tabmancer.desktop.table import COLUMN, PARTS
from tabmancer.keys import KEYS
from tabmancer.launch import Mode
from tabmancer.search import matches
from tabmancer.teamwords import colours, team_detail
from tabmancer.tokens import human
from tabmancer.widgets import bar_text

look.fonts_for_offscreen()

NBSP = chr(0xA0)
# Times change between two readings a second apart: "2h10m" to "2h09m".
TIMES = re.compile(r"\b\d+h\d{2}m\b|\b\d+[dm]\b")


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
    assert "watches" in shown.bit_line.text()
    shown.tick()
    assert re.fullmatch(r"\d\d:\d\d:\d\d", shown.clock.text()) and plain(shown.live.text()) == "● live"


def test_every_dashboard_key_is_in_the_menus(qapp, window):
    shown = window()
    in_menus = {action.data() for menu in shown.menus.findChildren(QMenu) for action in menu.actions()}
    for _group, keys in KEYS:
        for key, _action, words in keys:
            made = shown.actions_by_key[key]
            assert key in in_menus
            assert made.isEnabled() is (key in main.HERE)
            assert made.toolTip() == (words if key in main.HERE else f"{words}. {main.ELSEWHERE}")
            assert made.shortcuts()  # its key is named beside it, greyed or not
    assert {key for _group, keys in KEYS for key, _a, _w in keys} == set(shown.actions_by_key)
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


def every_key(keys) -> list[tuple[str, str]]:
    return [(key, words) for _group, entries in keys for key, _action, words in entries]


def test_ctrl_k_lists_every_key_and_runs_the_menus_own_action(qapp, window):
    shown = window()
    read(qapp, shown)
    QTest.keyClick(shown, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    bar = shown.command_bar
    assert bar.isVisible() and bar.line.text() == ""
    assert [(c.key, c.said) for c in bar.shown] == every_key(KEYS)  # keys.py's, in its order, as its menus name them
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
    QTest.keyClicks(bar.line, "hand")
    assert bar.picked().key == "h" and not bar.picked().enabled  # Hand over first, greyed here
    assert bar.note.text() == main.ELSEWHERE
    ran = []
    shown.actions_by_key["h"].triggered.connect(lambda *_: ran.append(1))
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert bar.isVisible() and ran == []
    QTest.keyClicks(bar.line, "zzqqx")
    assert bar.shown == [] and bar.note.text() == commandbar.NOTHING
    QTest.keyClick(bar.line, Qt.Key.Key_Return)  # nothing picked: nothing happens
    QTest.keyClick(bar.line, Qt.Key.Key_Escape)
    assert not bar.isVisible()


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
        # How many are open, as both lists have them: the demo's team reader has its members' copies
        # closed while the list has them open, and the preview, the rail and the board go by the list.
        view = next(team for team in shown.snap.view.teams if team.name == name)
        assert view.open == len(view.members) > state.open_count() and not team_words.closed
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
    assert {b.property("key") for b in buttons if b.isEnabled()} == {"Enter", "f", "x"}
    pressed = []
    side.act.connect(pressed.append)
    side.set(shown.snap, closed.session_id, None, {key: None for key, _, _ in drawer.BUTTONS})  # all done here
    button = side.grid.itemAt(0).widget()
    assert button.isEnabled() and button.property("key") == "Enter" and button.toolTip() == ""
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert pressed == ["Enter"]
    shown.show_pick("")
    assert drawer.NOTHING in side.plain() and not side.buttons.isVisible() and side.rows == ([], [])


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
        shown.settle()
        gone = weakref.ref(shown)
        del shown
        assert gone() is None
    finally:
        gc.enable()


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
    assert "watches" in shown.bit_line.text()  # then who acts again
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
    assert shown.bit_line.text() == marks.UNREAD and path.read_bytes() == half  # not saved over: kept
    assert login not in shown.snap.pinned and shown.selected == {login}
    press(shown, "x")
    assert shown.bit_line.text() == marks.UNREAD and login in shown.board.cards and path.read_bytes() == half
    press(shown, "Tab")
    projects = shown.rail.projects
    for _ in range(next(n for n, row in enumerate(projects.rows) if n and row.mark not in ("open", "waiting"))):
        QTest.keyClick(projects, Qt.Key.Key_Down)
    picked = shown.project
    press(shown, "x")  # a project, from the rail: nothing changed, so it stays picked
    assert shown.bit_line.text() == marks.UNREAD and shown.project == picked and path.read_bytes() == half


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
    press(shown, "a")
    assert shown.bit_line.text().startswith("Adding members is in the terminal dashboard for now")
    press(shown, "x")  # an open team: its tabs close in the terminal dashboard (x twice)
    assert shown.bit_line.text().startswith("Closing a team's tabs") and saved_marks()["hidden"] == []
    assert table.show_key(login)
    press(shown, "e")
    assert shown.docks["Preview"].isVisible()  # how it ended: the drawer says it


def test_esc_clears_the_words_then_the_card_then_the_project_then_closes(qapp, window):
    shown = window()
    read(qapp, shown)
    key = shown.rail.projects.rows[1].key
    shown.set_project(key)
    shown.toggle_card("continue")
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
    for _group, keys in KEYS:
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


def test_cli_opens_the_window_till_it_closes(qapp, world, monkeypatch):
    """The seam: `tabmancer desktop` to a window that reads the sessions, then closes."""
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append(len(windows[0].snap.sessions))
            qapp.quit()
        else:
            QTimer.singleShot(100, look_then_quit)

    QTimer.singleShot(100, look_then_quit)
    QTimer.singleShot(30_000, qapp.quit)  # never hangs the suite
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop"])
    assert out.value.code == 0
    assert seen and seen[0] >= len(demo.SESSIONS)
    assert "main" in read_json(frame.LAYOUT_FILE)  # closed as a window closes


def test_the_demo_puts_the_world_back(qapp, monkeypatch):
    """`tabmancer desktop --demo`: the made-up sessions, and afterwards nothing of them left; the days
    and --all given reach the window's store and its info line."""
    before = {name: os.environ.get(name) for name in frame.DEMO_ENV}
    agents = sessions._agents_json
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            shown = windows[0]
            seen.append((shown.brand.text(), len(shown.snap.sessions), shown.store.days, shown.store.include_all,
                         shown.info.text()))
            qapp.quit()
        else:
            QTimer.singleShot(100, look_then_quit)

    QTimer.singleShot(100, look_then_quit)
    QTimer.singleShot(30_000, qapp.quit)
    assert main.run(demo=True, days=30, include_all=True) == 0
    assert len(seen) == 1 and seen[0][0] == "◆ tabmancer demo" and seen[0][1] >= len(demo.SESSIONS)
    assert seen[0][2:4] == (30, True) and "last 30 days" in seen[0][4]
    assert {name: os.environ.get(name) for name in frame.DEMO_ENV} == before
    assert sessions._agents_json is agents
