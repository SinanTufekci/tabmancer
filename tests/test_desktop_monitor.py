"""The desktop team monitor (`tabmancer desktop --monitor`), offscreen over the made-up team of
`tabmancer --demo`. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import dataclasses
import importlib
import os
import sys
import time
from datetime import datetime

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import actor, agy, cli, demo, teambus, teams
from tabmancer import viewmodel as VM
from tabmancer.config import read_json
from tabmancer.desktop import look, monitor, widgets
from tabmancer.monitorview import MONITOR_KEYS

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
    assert "tabmancer team watch shop" in shown.actions_by_key["s"][0].toolTip()  # where it is done
    assert set(shown.keybar.keys) == monitor.HERE  # the key bar under the window: the keys that work here
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
    assert "team watch other" in shown.actions_by_key["s"][0].toolTip()


def test_the_layout_is_kept(qapp, window):
    shown = window()
    shown.docks["messages"].close()
    shown.close()
    kept = read_json(monitor.LAYOUT_FILE)
    assert set(kept["monitor"]) == {"geometry", "state"}
    again = window()
    assert again.docks["messages"].isHidden()
    again.reset_layout()
    assert not any(dock.isHidden() for dock in again.docks.values())


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


def test_cli_opens_the_window_till_it_closes(qapp, world, monkeypatch):
    """The seam: `tabmancer desktop --monitor shop` to a window that reads the team, then closes."""
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, monitor.MonitorWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append(list(windows[0].cards))
            qapp.quit()
        else:
            QTimer.singleShot(100, look_then_quit)

    QTimer.singleShot(100, look_then_quit)
    QTimer.singleShot(30_000, qapp.quit)  # never hangs the suite
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop", "--monitor", "shop"])
    assert out.value.code == 0
    assert seen == [["lead", "api", "ui", "tests"]]
    assert "monitor" in read_json(monitor.LAYOUT_FILE)  # closed as a window closes


def test_the_demo_puts_the_world_back(qapp, monkeypatch, tmp_path):
    """`tabmancer desktop --monitor --demo`: the made-up team, and afterwards nothing of it left."""
    before = {name: os.environ.get(name) for name in monitor.DEMO_ENV}
    agents = sys.modules["tabmancer.sessions"]._agents_json
    seen = []

    def look_then_quit():
        windows = [w for w in qapp.topLevelWidgets() if isinstance(w, monitor.MonitorWindow) and w.isVisible()]
        if windows and windows[0].snap is not None:
            seen.append((windows[0].team, list(windows[0].cards)))
            qapp.quit()
        else:
            QTimer.singleShot(100, look_then_quit)

    QTimer.singleShot(100, look_then_quit)
    QTimer.singleShot(30_000, qapp.quit)
    assert monitor.run("", demo=True) == 0
    assert seen == [("shop", ["lead", "api", "ui", "tests"])]
    assert {name: os.environ.get(name) for name in monitor.DEMO_ENV} == before
    assert sys.modules["tabmancer.sessions"]._agents_json is agents
