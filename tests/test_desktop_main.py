"""The dashboard's desktop window (`tabmancer desktop`), offscreen over the made-up sessions of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import importlib
import os
import re
import time
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from tabmancer import accounts, actor, agy, cli, demo, sessions, store, teambus, teams, usage
from tabmancer import viewmodel as VM
from tabmancer.config import merge_json, read_json
from tabmancer.desktop import frame, look, main, teampanel
from tabmancer.keys import KEYS
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
    assert list(shown.docks) == ["Sessions", "Preview", "Projects", "Cards", "Bit", "Team"]
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
    assert set(shown.keybar.keys) <= set(shown.actions_by_key)  # the key bar names the dashboard's keys
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
    assert shown.keybar.dim == {key for key in shown.keybar.keys if not shown.actions_by_key[key].isEnabled()}


def test_a_broken_layout_file_is_left_alone(qapp, window):
    merge_json(frame.LAYOUT_FILE, main={"geometry": "not base64!", "state": 7}, monitor={"state": "kept"})
    shown = window()
    assert not any(dock.isHidden() for dock in shown.docks.values())
    shown.close()
    assert read_json(frame.LAYOUT_FILE)["monitor"] == {"state": "kept"}  # the monitor's is its own


def test_it_never_acts(qapp, window, world, monkeypatch):
    """Read-only: no actor lock claimed, no plan limit or account recorded, agy never run, nothing
    opened, sent or closed, whatever key is pressed."""
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
    qapp.processEvents()
    assert done == []
    assert world == []  # the demo's stand-ins were asked to open, start, type or rename nothing
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
    """`tabmancer desktop --demo`: the made-up sessions, and afterwards nothing of them left."""
    before = {name: os.environ.get(name) for name in frame.DEMO_ENV}
    agents = sessions._agents_json
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, main.MainWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append((windows[0].brand.text(), len(windows[0].snap.sessions)))
            qapp.quit()
        else:
            QTimer.singleShot(100, look_then_quit)

    QTimer.singleShot(100, look_then_quit)
    QTimer.singleShot(30_000, qapp.quit)
    assert main.run(demo=True) == 0
    assert len(seen) == 1 and seen[0][0] == "◆ tabmancer demo" and seen[0][1] >= len(demo.SESSIONS)
    assert {name: os.environ.get(name) for name in frame.DEMO_ENV} == before
    assert sessions._agents_json is agents
