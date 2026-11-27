"""The desktop team monitor (`tabmancer desktop --monitor`), offscreen over the made-up team of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import gc
import importlib
import os
import sys
import threading
import time
import weakref
from datetime import datetime, timedelta

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

import shiboken6
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from qt_later import later  # noqa: F401  (a fixture)

from tabmancer import actor, agy, cli, demo, errorlog, teambus, teams
from tabmancer import viewmodel as VM
from tabmancer.config import merge_json, read_json
from tabmancer.desktop import commandbar, look, monitor, teamgraph, widgets
from tabmancer.keys import MONITOR_KEYS

look.fonts_for_offscreen()

NEON = {"model": "gemini-4-neon-high", "name": "Neon", "left": 72.0, "span": "week", "back": None}
NBSP = chr(0xA0)  # what look.span keeps a second space as
HYDROGEN = {"model": "gemini-4-hydrogen-high", "name": "Hydrogen", "left": None, "span": "", "back": None}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The demo's made-up world (team "shop": lead, api, ui, tests), put back after the test."""
    for name in monitor.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    told: list[str] = []
    for module, name, value in demo.stand_ins(tmp_path, live, told.append):
        monkeypatch.setattr(module, name, value)
    return told


@pytest.fixture
def window(qapp, world):
    made = []

    def make(team="shop"):
        shown = monitor.MonitorWindow(team, start=False)
        shown.resize(1500, 950)
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


def press(qapp, shown, key, modifier=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(shown, key, modifier)
    qapp.processEvents()


def test_the_window_shows_the_team(qapp, window):
    shown = window()
    read(qapp, shown)
    assert shown.snap is not None
    assert list(shown.cards) == ["lead", "api", "ui", "tests"]
    assert "shop" in shown.head.text() and "4 of 4 open" in shown.head.text()
    assert shown.needs.isVisible() and "tests" in shown.needs.text()  # blocked: it needs you
    assert "needs you" in shown.bit_line.text()
    assert [r.title for r in shown.ring_widgets] == ["Claude session", "Claude week"]  # the demo's limits
    assert shown.windowTitle().startswith("shop")
    assert [w for w in qapp.topLevelWidgets() if w.isVisible()] == [shown]  # no pill or card a window of its own
    assert qapp.activeWindow() is shown  # so nothing took its keys
    card, member = shown.cards["tests"], shown.snap.view.members[3]
    assert card.state.text() == member.label == "needs you"
    assert card.bar.share == member.context and member.context


def test_a_member_done_with_its_cache_kept_warm_says_till_when(qapp, window):
    """Its card and the picked line, as the terminal's team view says it."""
    shown = window()
    read(qapp, shown)
    member = shown.snap.view.members[1]
    member.label, member.warm_until = "idle", datetime(2026, 11, 27, 14, 32)
    shown.show_cards(shown.snap.view)
    assert shown.cards[member.name].state.text() == "idle · warm to 14:32"
    shown.picked = member.name
    shown.refresh_pick()
    assert f"{member.name}  ·  idle · warm to 14:32  ·  " in plain(shown.picked_lines.text())


def test_ctrl_k_finds_the_monitors_keys_and_its_members(qapp, window):
    shown = window()
    read(qapp, shown)
    assert shown.picked is None
    QTest.keyClick(shown, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    bar = shown.command_bar
    assert bar.isVisible()
    keys = [(key, words) for _group, entries in MONITOR_KEYS for key, _action, words in entries]
    assert [(c.key, c.said) for c in bar.shown] == keys
    QTest.keyClicks(bar.line, "Enter")
    assert bar.picked().key == "Enter" and not bar.picked().enabled and bar.note.text() == monitor.PICK_FIRST
    bar.line.clear()
    QTest.keyClicks(bar.line, "api")
    assert bar.picked().kind == "member" and bar.picked().target == "api"
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert shown.picked == "api" and not bar.isVisible()
    shown.open_commands()
    assert next(c for c in bar.shown if c.key == "Enter").enabled  # someone is picked now
    ran = []
    shown.actions_by_key["j"][0].triggered.connect(lambda *_: ran.append(1))
    QTest.keyClicks(bar.line, "j")
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert len(ran) == 1  # the menu's own action
    shown.open_commands()
    QTest.keyClicks(bar.line, "next member")
    QTest.keyClick(bar.line, Qt.Key.Key_Return)  # ← →: the next member
    assert shown.picked is not None
    assert (commandbar.KEY, "commands") in monitor.KEY_BAR


def test_a_closed_monitor_goes_with_its_last_name(qapp, world):
    """Nothing in the monitor holds the monitor (its ← → lambdas and a kept callback did): closed and let
    go, it goes at once, not whenever the cycle collector next comes by."""
    gc.disable()
    try:
        shown = monitor.MonitorWindow("shop", start=False)
        shown.show()
        read(qapp, shown)
        shown.close()
        shown.settle()
        gone = weakref.ref(shown)
        del shown
        assert gone() is None
    finally:
        gc.enable()


def test_words_are_the_dashboards(qapp, window):
    """Models as the team view says them, limits and needs-you as the terminal monitor does."""
    shown = window()
    read(qapp, shown)
    lead = shown.cards["lead"].model.text()
    assert lead.startswith("opus 5.5") and "claude-" not in lead  # not the raw id
    pills = [shown.pills.itemAt(i).widget().text() for i in range(shown.pills.count())]
    assert pills == ["Claude session 34%", "week 61%"]
    assert shown.pills.itemAt(0).widget().toolTip().startswith("lifts ")
    assert plain(shown.needs.text()) == "NEEDS YOU  tests: needs you   j picks them in turn"
    shown.picked = "lead"
    shown.refresh_pick()
    assert "claude · opus 5.5" in plain(shown.picked_lines.text())


def test_a_team_watched_already_comes_forward(qapp, window, monkeypatch):
    """M again, or the command twice: the open window comes to the front, and no second one opens."""
    shown = window()
    came = []
    monkeypatch.setattr(shown, "come_forward", lambda: came.append("shop"))
    assert monitor.knock("shop") is True
    wait(qapp, lambda: came, seconds=5)
    assert monitor.knock("blog") is False and monitor.knock("") is False
    monkeypatch.setattr(monitor, "MonitorWindow", lambda *a, **kw: pytest.fail("a second window"))
    assert monitor.run("shop") == 0
    shown.close()
    qapp.processEvents()
    assert monitor.knock("shop") is False  # closed: its door with it


def test_switching_to_a_team_open_elsewhere_brings_that_one(qapp, window, monkeypatch):
    first = window()
    came = []
    monkeypatch.setattr(first, "come_forward", lambda: came.append(True))
    other = window(team="")
    other.switch_team("shop")
    wait(qapp, lambda: came, seconds=5)
    assert other.team == "" and other.store is None  # it stays as it was
    assert "shop is open in another window" in plain(other.bit_line.text())


def test_the_door_is_the_teams_and_the_folders(monkeypatch, tmp_path):
    shop = monitor.door("shop")
    assert shop == monitor.door("shop") != monitor.door("blog")
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "elsewhere"))  # the demo's, say
    assert monitor.door("shop") != shop


def plain(html: str) -> str:
    made = QTextDocument()
    made.setHtml(html)
    return made.toPlainText().replace(NBSP, " ")


def member(**kw) -> VM.MemberView:
    made = {"name": "docs", "lead": False, "focus": "the guide", "session": None, "state": "idle",
            "label": "idle", "tone": "done"}
    return VM.MemberView(**(made | kw))


def test_model_words():
    assert widgets.model_words(member(cli="agy", model="gemini-4-neon-high")) == "Neon (High)"
    assert widgets.model_words(member(cli="agy", model="gemini-4-hydrogen-high", effort="high")) == "Hydrogen (High)"
    assert widgets.model_words(member(model="claude-sonnet-5-5", effort="high")) == "sonnet 5.5 · high"
    assert widgets.model_words(member()) == "default model"
    assert widgets.model_words(member(cli="agy")) == "no model now"


def test_every_key_of_the_monitor_is_in_the_menus(qapp, window):
    shown = window()
    tips = [a.toolTip() for actions in shown.actions_by_key.values() for a in actions]
    for _group, keys in MONITOR_KEYS:
        for key, _action, words in keys:
            assert key in shown.actions_by_key
            assert all(a.toolTip().startswith(words) for a in shown.actions_by_key[key])
    assert len(tips) == sum(len(keys) for _, keys in MONITOR_KEYS) + 1  # ← → is two: next and previous
    enabled = {key for key, actions in shown.actions_by_key.items() if all(a.isEnabled() for a in actions)}
    assert enabled == monitor.HERE
    assert not [tip for tip in tips if "terminal" in tip]  # every key works here: none sends you elsewhere
    assert set(shown.keybar.keys) == monitor.HERE | {commandbar.KEY}  # the keys that work here, and the palette
    assert [a.text() for a in shown.menuBar().actions()] == ["&Members", "&Team", "&Windows", "&View", "&Help"]


def test_keys_pick_members(qapp, window):
    shown = window()
    read(qapp, shown)
    press(qapp, shown, Qt.Key.Key_Right)
    assert shown.picked == "lead"
    press(qapp, shown, Qt.Key.Key_Left)
    assert shown.picked == "tests"
    press(qapp, shown, Qt.Key.Key_Escape)
    assert shown.picked is None
    press(qapp, shown, Qt.Key.Key_J)
    assert shown.picked == "tests"  # the one that needs you
    assert "tests" in shown.picked_lines.text()
    press(qapp, shown, Qt.Key.Key_Question, Qt.KeyboardModifier.ShiftModifier)
    shown_keys = [w for w in qapp.topLevelWidgets() if isinstance(w, monitor.KeysDialog) and w.isVisible()]
    assert shown_keys
    for dialog in shown_keys:
        dialog.close()


def test_enter_goes_to_the_tab_and_says_where_it_cannot(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    asked = []
    monkeypatch.setattr(monitor.focus, "focus_tab", lambda title: asked.append(title) or True)
    shown.go_to_tab()
    assert "Pick a member" in shown.bit_line.text()
    shown.picked = "api"
    shown.go_to_tab()
    wait(qapp, lambda: "Switched" in shown.bit_line.text())
    assert asked == [shown.snap.state.sessions["api"].title]


def test_a_tab_switch_ending_after_the_window_went_is_no_error(qapp, window, monkeypatch):
    """Enter's switch finding the tab as the monitor goes: its words have no window to go to, and that is no
    error for errors.log (as everywhere a window goes meanwhile)."""
    shown = window()
    read(qapp, shown)
    looking, gone = threading.Event(), threading.Event()

    def focus_tab(title):
        looking.set()
        gone.wait(10)
        return True

    monkeypatch.setattr(monitor.focus, "focus_tab", focus_tab)
    log = errorlog.config_dir() / errorlog.LOG
    own, stand_in = shown.relay, monitor.Relay()
    shown.relay = stand_in
    try:
        shown.picked = "api"
        shown.go_to_tab()
        assert looking.wait(10)
        switching = [thread for thread in threading.enumerate() if thread.name == "tabmancer-focus"]
        shiboken6.delete(stand_in)  # gone, as with its window
        gone.set()
        for thread in switching:
            thread.join(10)
    finally:
        gone.set()
        shown.relay = own  # the window's own, for its close
    written = log.read_text(encoding="utf-8") if log.exists() else ""
    assert switching and "desktop.monitor.switch" not in written


def test_it_never_acts(qapp, window, world, monkeypatch):
    """Read-only: no actor lock claimed, agy never run, nothing opened, sent or closed."""
    done = []
    for name in ("claim", "first_to", "release"):
        monkeypatch.setattr(actor, name, lambda *a, _name=name, **k: done.append(_name))
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: done.append("agy /usage"))
    for module, name in ((teams, "new_team"), (teams, "save"), (teambus, "send")):
        monkeypatch.setattr(module, name, lambda *a, _name=name, **k: done.append(_name))
    shown = window()
    for _ in range(3):
        read(qapp, shown)
    for key in (Qt.Key.Key_Right, Qt.Key.Key_J, Qt.Key.Key_S, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_X,
                Qt.Key.Key_H, Qt.Key.Key_P, Qt.Key.Key_G):
        press(qapp, shown, key)
    assert done == []
    assert world == []  # the demo's stand-ins were asked to open, start, type or rename nothing


def test_agy_chain_on_the_bar_and_in_rings(qapp, window):
    shown = window()
    read(qapp, shown)
    quota = {"read_at": datetime.now(), "unreadable": False, "chain": [NEON, HYDROGEN],
             "pick": "gemini-4-neon-high", "why": ""}
    bar = {"parts": [("Neon 72% left", "ok"), ("Hydrogen ?", "unknown")], "all_out": None, "said": ""}
    shown.take(shown.store, dataclasses.replace(shown.snap, agy_quota=quota), bar, "")
    pills = [shown.pills.itemAt(i).widget().text() for i in range(shown.pills.count())]
    assert pills[-2:] == ["Neon 72% left", "Hydrogen ?"]
    assert [r.title for r in shown.ring_widgets][-2:] == ["agy Neon", "agy Hydrogen"]
    assert (shown.ring_widgets[-2].middle, shown.ring_widgets[-2].under) == ("72%", "left · week")
    out = {**NEON, "left": 0.0, "back": datetime.now().replace(hour=23, minute=59)}
    shown.take(shown.store, dataclasses.replace(shown.snap, agy_quota={**quota, "chain": [out, HYDROGEN]}), bar, "")
    assert (shown.ring_widgets[-2].middle, shown.ring_widgets[-2].under) == ("out", "till 23:59")


def test_bit_says_when_all_of_agy_is_out(qapp, window):
    shown = window()
    read(qapp, shown)
    calm = dataclasses.replace(shown.snap, view=dataclasses.replace(
        shown.snap.view, members=[dataclasses.replace(m, needs_you=False) for m in shown.snap.view.members]))
    said = "agy: Neon and Hydrogen out until 14:00"
    shown.take(shown.store, calm, {"parts": [], "all_out": datetime.now(), "said": said}, "")
    assert shown.bit_line.text() == said


def test_agy_bar_comes_from_agy(monkeypatch):
    monkeypatch.setattr(agy, "bar_view", lambda: {"parts": [("Neon 10% left", "low")]}, raising=False)
    assert monitor.agy_bar() == {"parts": [("Neon 10% left", "low")]}
    monkeypatch.delattr(agy, "bar_view", raising=False)
    assert monitor.agy_bar() == {}


def test_a_read_of_the_team_left_is_dropped(qapp, window):
    shown = window()
    read(qapp, shown)
    old, snap = shown.store, shown.snap
    shown.switch_team("other")
    wait(qapp, lambda: not shown.waiting)
    qapp.processEvents()
    shown.take(old, snap, {}, "")
    assert shown.snap is None and shown.cards == {}
    assert "no team other" in shown.bit_line.text()
    assert shown.actions_by_key["s"][0].isEnabled()  # its keys wait for a team, never send you elsewhere


def test_the_orbit_in_the_middle_and_the_cards_a_panel(qapp, window):
    """The team as the picture that moves, in the middle; the cards a tab behind the messages, in as
    many columns as the panel has room for."""
    shown = window()
    read(qapp, shown)
    graph = shown.graph
    assert shown.centralWidget().isAncestorOf(graph) and graph.isVisible()
    assert graph.view is shown.snap.view and graph.names == ["lead", "api", "ui", "tests"]
    members = shown.docks["members"]
    assert members.isAncestorOf(shown.cards_area) and members.windowTitle() == "Members"
    assert shown.tabifiedDockWidgets(members) == [shown.docks["messages"]]
    assert shown.cards_area.visibleRegion().isEmpty()  # the messages in front (a tab behind: off the screen)
    members.raise_()
    shown.resizeDocks([members], [400], Qt.Orientation.Horizontal)
    qapp.processEvents()
    assert not shown.cards_area.visibleRegion().isEmpty() and shown.columns == 1
    shown.resizeDocks([members], [1000], Qt.Orientation.Horizontal)
    qapp.processEvents()
    assert shown.columns == shown.cards_area.viewport().width() // monitor.CARD_WIDTH >= 2  # the panel, not the window


def test_a_click_on_a_member_picks_it_and_brings_its_tab(qapp, window, monkeypatch):
    shown = window()
    read(qapp, shown)
    asked = []
    monkeypatch.setattr(monitor.focus, "focus_tab", lambda title: asked.append(title) or True)
    graph = shown.graph
    wait(qapp, lambda: "api" in graph.drawn)  # painted once: where each one stands
    QTest.mouseClick(graph, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, graph.drawn["api"].toPoint())
    assert shown.picked == "api" == graph.picked
    assert "api" in shown.picked_lines.text()
    wait(qapp, lambda: "Switched" in shown.bit_line.text())
    assert asked == [shown.snap.state.sessions["api"].title]
    QTest.mouseClick(graph, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1, graph.height() - 2))
    qapp.processEvents()
    assert shown.picked == "api" and len(asked) == 1  # nobody there: nothing happens


def test_a_right_click_on_a_member_offers_its_actions(qapp, window):
    shown = window()
    read(qapp, shown)
    graph = shown.graph
    wait(qapp, lambda: "ui" in graph.drawn)
    spot = graph.drawn["ui"].toPoint()
    qapp.sendEvent(graph, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, spot, graph.mapToGlobal(spot)))
    menu = shown.member_popup
    assert menu is not None and menu.isVisible() and shown.picked == "ui" == graph.picked
    title, *offered = [a for a in menu.actions() if not a.isSeparator()]
    assert title.text() == "ui" and not title.isEnabled()
    assert offered == [a for key in monitor.MEMBER_KEYS for a in shown.actions_by_key[key]]
    assert [a.isEnabled() for a in offered] == [key in monitor.HERE for key in monitor.MEMBER_KEYS]
    assert all(a.isEnabled() for a in offered)  # every one works here
    other = graph.drawn["api"].toPoint()
    qapp.sendEvent(graph, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, other, graph.mapToGlobal(other)))
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # the first one deleted
    again = shown.member_popup
    assert again is not None and again is not menu and again.isVisible() and shown.picked == "api"
    again.close()
    assert shown.member_popup is None
    corner = QPoint(1, graph.height() - 2)
    qapp.sendEvent(graph, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, corner, graph.mapToGlobal(corner)))
    assert shown.member_popup is None  # nobody there: no menu


def test_a_new_message_flies_in_the_picture(qapp, window):
    shown = window()
    read(qapp, shown)
    graph = shown.graph
    assert graph.flights == []  # the first look: what was said before stays put
    said = list(shown.snap.state.said)
    new = teams.Message(said[-1].at + timedelta(seconds=1), "api", "ui", "made-up: the endpoint is in")
    snap = dataclasses.replace(shown.snap, state=dataclasses.replace(shown.snap.state, said=[*said, new]))
    shown.take(shown.store, snap, {}, "")
    assert [(f.sender, f.receiver, f.words) for f in graph.flights] == [("api", "ui", new.text)]
    assert graph.frames.isActive()
    assert shown.lanes.drawing is not None and shown.lanes.drawing[0] is new  # and draws itself below
    assert shown.lanes.rows[-1].message is new


def test_view_says_how_much_moves(qapp, window):
    shown = window()
    read(qapp, shown)
    view = next(a.menu() for a in shown.menuBar().actions() if a.text() == "&View")
    assert "Reduce motion" in [a.text() for a in view.actions()]
    picks = {str(a.data()): a for a in shown.motions.actions()}
    assert list(picks) == list(teamgraph.MOTIONS) and picks["full"].isChecked() and shown.graph.mode == "full"
    picks["off"].trigger()
    assert read_json("config.json")["motion"] == "off" and shown.graph.mode == "off"
    assert picks["off"].isChecked() and not picks["full"].isChecked()
    picks["full"].trigger()
    assert shown.graph.mode == "full" and picks["full"].text() == teamgraph.MOTIONS["full"]
    merge_json("config.json", mascot="calm")  # b in the dashboard: Bit calm
    read(qapp, shown)
    assert shown.graph.mode == "calm"  # calm with it
    shown.motion.show()
    assert picks["full"].isChecked() and "calm while Bit is calm" in picks["full"].text()


def test_another_team_starts_the_picture_afresh(qapp, window):
    shown = window()
    read(qapp, shown)
    graph = shown.graph
    assert graph.view is not None and graph.seen is not None
    shown.switch_team("other")
    assert (graph.view, graph.seen, graph.flights, graph.names, graph.drawn) == (None, None, [], [], {})
    assert (shown.lanes.team, shown.lanes.rows, shown.lanes.keys) == (None, [], None)
    wait(qapp, lambda: not shown.waiting)


def test_the_layout_is_kept(qapp, window):
    shown = window()
    shown.docks["messages"].close()
    shown.close()
    kept = read_json(monitor.LAYOUT_FILE)
    assert set(kept["monitor"]) == {"geometry", "state", "docks"}
    again = window()
    assert again.docks["messages"].isHidden()
    again.reset_layout()
    assert not any(dock.isHidden() for dock in again.docks.values())


def test_the_members_go_behind_the_messages_in_a_layout_kept_before_them(qapp, window):
    from tabmancer.config import merge_json

    old = window()
    old.removeDockWidget(old.docks["members"])  # its layout as kept before the Members panel
    old.close()
    kept = read_json(monitor.LAYOUT_FILE)["monitor"]
    merge_json(monitor.LAYOUT_FILE, monitor={"geometry": kept["geometry"], "state": kept["state"]})
    shown = window()
    members, messages = shown.docks["members"], shown.docks["messages"]
    assert members in shown.tabifiedDockWidgets(messages) and not members.isHidden()
    assert members.visibleRegion().isEmpty() and not messages.visibleRegion().isEmpty()


def test_a_broken_layout_file_is_left_alone(qapp, window):
    from tabmancer.config import merge_json

    merge_json(monitor.LAYOUT_FILE, monitor={"geometry": "not base64!", "state": 7})
    shown = window()
    assert not any(dock.isHidden() for dock in shown.docks.values())


def test_short_and_sequences():
    assert monitor.short("take the picked member off the team (twice); its sessions stay") == \
        "Take the picked member off the team"
    assert monitor.short("add members") == "Add members"
    assert [s.toString() for s in monitor.sequences("T")] == ["Shift+T"]
    assert [s.toString() for s in monitor.sequences("j")] == ["J"]
    assert [s.toString() for s in monitor.sequences("Enter")] == ["Return", "Enter"]


def test_cli_opens_the_window_till_it_closes(qapp, world, monkeypatch, later):  # noqa: F811
    """The seam: `tabmancer desktop --monitor shop` to a window that reads the team, then closes."""
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, monitor.MonitorWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append(list(windows[0].cards))
            qapp.quit()
        else:
            later(100, look_then_quit)

    later(100, look_then_quit)
    later(30_000, qapp.quit)  # never hangs the suite
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop", "--monitor", "shop"])
    assert out.value.code == 0
    assert seen == [["lead", "api", "ui", "tests"]]
    assert "monitor" in read_json(monitor.LAYOUT_FILE)  # closed as a window closes


def test_the_demo_puts_the_world_back(qapp, monkeypatch, tmp_path, later):  # noqa: F811
    """`tabmancer desktop --monitor --demo`: the made-up team, and afterwards nothing of it left."""
    before = {name: os.environ.get(name) for name in monitor.DEMO_ENV}
    agents = sys.modules["tabmancer.sessions"]._agents_json
    seen, kept = [], []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, monitor.MonitorWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append((windows[0].team, list(windows[0].cards), gc.isenabled()))
            kept.append(windows[0])
            qapp.quit()
        else:
            later(100, look_then_quit)

    later(100, look_then_quit)
    later(30_000, qapp.quit)
    assert monitor.run("", demo=True) == 0
    assert seen == [("shop", ["lead", "api", "ui", "tests"], False)]  # the cycle collector on its thread only
    assert {name: os.environ.get(name) for name in monitor.DEMO_ENV} == before
    assert sys.modules["tabmancer.sessions"]._agents_json is agents
    assert not shiboken6.isValid(kept[0]) and gc.isenabled()  # deleted on the GUI thread as run ends
