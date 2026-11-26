"""n in the window (desktop/newsession.py): folders ticked, the first message, the rows and what opens, as the
terminal's n dialog starts them, in newforms.py's words; the router, the rewrite and agy's quota asked off
the window's thread; agy's Model row never a Claude model, never Argon. Offscreen, with a made-up router,
rewrite and quota. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid
from test_desktop_handover import until

from tabmancer import agy, clis, improve, newforms, picks, router
from tabmancer.desktop import newsession
from tabmancer.newforms import Project, Request
from tabmancer.sessions import folder_key

MAIN = threading.main_thread()
NEON, HYDROGEN = agy.DEFAULT_CHAIN


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two projects that exist, Claude Code and agy on, and a made-up router, rewrite and agy quota that keep
    what they were asked and on which thread; gate: an Event they wait on."""
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    clis.set_enabled(["claude", "agy"])
    shop, api = tmp_path / "shop", tmp_path / "api"
    shop.mkdir()
    api.mkdir()
    now = datetime.now()
    kept = SimpleNamespace(found=[Project(str(shop), 4, now - timedelta(hours=1)),
                                  Project(str(api), 2, now - timedelta(days=1), {"agy"})],
                           routes=[], improves=[], quotas=[], gate=None, better="Fix the login redirect loop, with a test",
                           route=router.Route(picks.Pick("opus", "high"), "a careful change"), failing="")

    def wait():
        if kept.gate is not None:
            assert kept.gate.wait(10)

    def suggest(message, folders, cli="claude"):
        kept.routes.append((message, folders, cli, threading.current_thread()))
        wait()
        if kept.failing:
            raise OSError(kept.failing)
        return kept.route if cli == "claude" else router.Route(picks.Pick(HYDROGEN), "cheaper", "agy")

    def improve_message(message, folders):
        kept.improves.append((message, folders, threading.current_thread()))
        wait()
        return kept.better

    def refresh_quota(timeout=25, now=None):
        kept.quotas.append(threading.current_thread())
        return True

    def resolve(model="", usage=None, now=None):
        return (model if model in agy.DEFAULT_CHAIN else NEON), ""

    monkeypatch.setattr(router, "suggest", suggest)
    monkeypatch.setattr(improve, "improve_message", improve_message)
    monkeypatch.setattr(agy, "refresh_quota", refresh_quota)
    monkeypatch.setattr(agy, "resolve", resolve)
    kept.shop, kept.api = str(shop), str(api)
    return kept


@pytest.fixture
def opened(qapp, world):
    """The dialog on a parent window, shown; what it asked to open kept; the asker settled after."""
    parent = QWidget()
    parent.resize(1200, 800)
    parent.show()
    asker = newsession.Asker(parent)
    made = []

    def make(ticked=frozenset()):
        dialog = newsession.NewSessionDialog(parent, world.found, set(ticked), asker, "#00b4d8")
        dialog.requested.connect(made.append)
        dialog.open()
        qapp.processEvents()
        return dialog

    yield SimpleNamespace(make=make, made=made, asker=asker, parent=parent)
    if world.gate is not None:
        world.gate.set()
    asker.settle()
    qapp.processEvents()
    parent.close()
    parent.deleteLater()
    qapp.processEvents()


def text_of(widget) -> str:
    """A label's words without its HTML."""
    doc = QTextDocument()
    doc.setHtml(widget.text())
    return doc.toPlainText().replace("\xa0", " ")


def closed(dialog) -> bool:
    """Closed: hidden, or already deleted (it deletes itself once it has finished)."""
    return not isValid(dialog) or not dialog.isVisible()


def key(widget, which, modifier=Qt.KeyboardModifier.NoModifier) -> None:
    QTest.keyClick(widget, which, modifier)
    QApplication.processEvents()


def test_the_agy_row_is_the_chain_never_claude_never_argon(qapp, world, opened):
    dialog = opened.make()
    agy_row = dialog.rows["agy-model"]
    assert agy_row.words == [newforms.AGY_CHAIN, NEON, HYDROGEN]
    assert not [w for w in agy_row.words if agy.is_claude(w) or "argon" in w.lower()]
    assert dialog.rows["model"].isVisible() and not agy_row.isVisible()  # Claude Code first: its own model row
    key(dialog.folders, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)  # Ctrl+O: agy
    assert dialog.form.cli == "agy"
    assert agy_row.isVisible() and not dialog.rows["model"].isVisible()
    assert not dialog.rows["effort"].isEnabled()  # Claude Code's rows: off for agy
    until(qapp, lambda: world.quotas)
    assert world.quotas[0] is not MAIN  # agy's quota read off the window's thread, once
    assert dialog.pick() == picks.Pick(NEON)  # the chain: Neon while it has quota
    agy_row.pick(HYDROGEN)
    assert dialog.pick() == picks.Pick(HYDROGEN)
    assert "Hydrogen" in text_of(dialog.pick_words)
    key(dialog.folders, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)  # and back: no second quota read
    key(dialog.folders, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    qapp.processEvents()
    assert len(world.quotas) == 1


def test_space_ticks_and_enter_starts_one_for_each_line(qapp, world, opened):
    dialog = opened.make()
    assert dialog.folders.count() == 2
    dialog.folders.setFocus()
    dialog.folders.setCurrentRow(0)
    key(dialog.folders, Qt.Key.Key_Space)
    assert dialog.form.ticked == {folder_key(world.shop)}
    assert "1 folder ticked" in text_of(dialog.title)
    assert dialog.folders.item(0).text().startswith("✓ shop")
    dialog.message.setPlainText("Fix the login\nWrite the docs")
    assert "Opens 2 tabs" in text_of(dialog.opens_card.title)
    assert "shop  Fix the login" in text_of(dialog.opens)
    key(dialog.folders, Qt.Key.Key_Return)
    assert opened.made == [Request([world.shop], "claude", "Fix the login\nWrite the docs", picks.Pick(
        cap=picks.default_cap()), False)]
    assert closed(dialog)


def test_enter_with_nothing_ticked_starts_the_picked_one(qapp, world, opened):
    dialog = opened.make()
    dialog.folders.setFocus()
    dialog.folders.setCurrentRow(1)
    key(dialog.folders, Qt.Key.Key_Return)
    assert [r.folders for r in opened.made] == [[world.api]]


def test_a_typed_folder_is_ticked_first_and_the_next_enter_starts(qapp, world, opened, tmp_path):
    other = tmp_path / "lab"
    other.mkdir()
    dialog = opened.make()
    dialog.filter.setFocus()
    dialog.filter.setText(str(other))
    key(dialog.filter, Qt.Key.Key_Return)
    assert opened.made == []
    assert folder_key(str(other)) in dialog.form.ticked
    assert dialog.filter.text() == "" and dialog.folders.hasFocus()
    key(dialog.folders, Qt.Key.Key_Return)
    assert [r.folders for r in opened.made] == [[str(other)]]


def test_typing_in_the_list_filters_and_nothing_found_refuses(qapp, world, opened):
    dialog = opened.make()
    dialog.folders.setFocus()
    key(dialog.folders, Qt.Key.Key_Q)
    key(dialog.filter, Qt.Key.Key_Z)
    assert dialog.filter.text() == "qz" and dialog.filter.hasFocus()
    assert "Nothing matches “qz”" in dialog.folders.item(0).text()
    key(dialog.filter, Qt.Key.Key_Return)
    assert opened.made == []
    assert text_of(dialog.note) == newforms.TICK_FIRST


def test_enter_in_the_message_box_is_a_line_more_and_ctrl_s_starts(qapp, world, opened):
    dialog = opened.make({folder_key(world.api)})
    dialog.message.setFocus()
    QTest.keyClicks(dialog.message, "one")
    key(dialog.message, Qt.Key.Key_Return)
    QTest.keyClicks(dialog.message, "two")
    assert opened.made == [] and dialog.message.toPlainText() == "one\ntwo"
    key(dialog.message, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert [(r.folders, r.message) for r in opened.made] == [([world.api], "one\ntwo")]


def test_ctrl_l_links_the_lines_as_peers_in_one_folder(qapp, world, opened):
    dialog = opened.make({folder_key(world.shop)})
    dialog.message.setPlainText("Fix the login\nWrite the docs")
    assert dialog.link.isVisible()
    key(dialog.folders, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    assert "linked, as peers" in text_of(dialog.link)
    assert "Opens 2 tabs, linked" in text_of(dialog.opens_card.title)
    key(dialog.folders, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert [r.linked for r in opened.made] == [True]


def test_ctrl_r_asks_the_router_off_the_thread_and_takes_its_pick(qapp, world, opened):
    dialog = opened.make({folder_key(world.shop)})
    key(dialog.folders, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert world.routes == [] and "Ctrl+R needs the first message" in text_of(dialog.note)
    world.gate = threading.Event()
    dialog.message.setPlainText("Fix the login redirect loop")
    key(dialog.folders, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: world.routes)
    message, folders, cli, thread = world.routes[0]
    assert (message, folders, cli) == ("Fix the login redirect loop", ["shop"], "claude") and thread is not MAIN
    assert dialog.route_line.isVisible() and "asking" in text_of(dialog.route_line).lower()
    world.gate.set()
    until(qapp, lambda: dialog.rows["model"].value == "opus")
    assert dialog.rows["effort"].value == "high"
    assert "the router's pick" in text_of(dialog.route_line)


def test_a_router_failure_is_said_and_ctrl_r_asks_again(qapp, world, opened):
    world.failing = "the router stopped"
    dialog = opened.make({folder_key(world.shop)})
    dialog.message.setPlainText("Fix the login redirect loop")
    key(dialog.folders, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: "router stopped" in text_of(dialog.route_line))
    world.failing = ""
    key(dialog.folders, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: dialog.rows["model"].value == "opus")
    assert len(world.routes) == 2


def test_the_router_for_agy_picks_in_the_chain_only(qapp, world, opened):
    dialog = opened.make({folder_key(world.shop)})
    key(dialog.folders, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)  # agy
    dialog.message.setPlainText("Fix the login redirect loop")
    key(dialog.folders, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: dialog.rows["agy-model"].value == HYDROGEN)
    assert [cli for _, _, cli, _ in world.routes] == ["agy"]
    key(dialog.folders, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert [(r.cli, r.pick.model) for r in opened.made] == [("agy", HYDROGEN)]


def test_ctrl_e_rewrites_off_the_thread_as_one_edit(qapp, world, opened):
    dialog = opened.make({folder_key(world.shop)})
    key(dialog.folders, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert world.improves == [] and "Ctrl+E needs a first message" in text_of(dialog.note)
    world.gate = threading.Event()
    dialog.message.setPlainText("fix login loop pls")
    key(dialog.folders, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: world.improves)
    assert world.improves[0][:2] == ("fix login loop pls", ["shop"]) and world.improves[0][2] is not MAIN
    assert dialog.message.isReadOnly() and "improving with" in text_of(dialog.improve_line)
    key(dialog.folders, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)  # asked once at a time
    world.gate.set()
    until(qapp, lambda: dialog.message.toPlainText() == world.better)
    assert len(world.improves) == 1 and not dialog.message.isReadOnly()
    assert "✓ improved (4 → 8 words)" in text_of(dialog.improve_line)
    assert text_of(dialog.note) == newforms.IMPROVED.words
    dialog.message.undo()  # Ctrl+Z in the box: yours back
    assert dialog.message.toPlainText() == "fix login loop pls"


def test_a_rewrite_for_text_changed_meanwhile_is_not_put_in(qapp, world, opened):
    world.gate = threading.Event()
    dialog = opened.make()
    dialog.message.setPlainText("fix login loop pls")
    key(dialog.folders, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: world.improves)
    dialog.message.setPlainText("something else entirely now")  # as a paste would, read-only or not
    world.gate.set()
    until(qapp, lambda: "changed meanwhile" in text_of(dialog.improve_line))
    assert dialog.message.toPlainText() == "something else entirely now"


def test_a_late_answer_never_lands_in_the_next_dialog(qapp, world, opened):
    world.gate = threading.Event()
    first = opened.make({folder_key(world.shop)})
    first.message.setPlainText("fix login loop pls")
    key(first.folders, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    until(qapp, lambda: world.improves)
    key(first.folders, Qt.Key.Key_Escape)
    assert opened.made == []
    second = opened.make()
    second.message.setPlainText("fix login loop pls")
    world.gate.set()
    opened.asker.settle()
    qapp.processEvents()
    assert second.message.toPlainText() == "fix login loop pls"
    assert second.improving is None


def test_esc_starts_nothing(qapp, world, opened):
    dialog = opened.make({folder_key(world.shop)})
    key(dialog.folders, Qt.Key.Key_Escape)
    assert opened.made == [] and closed(dialog)
