"""Delete team… in the window: a team card's menu, the drawer's button, Del and the palette open the saved teams'
list (teamkeysdialogs.DeleteTeamDialog), the picked team first in view; d twice deletes it (TeamKeys.delete, as
the terminal's T list does), its sessions kept. Over the made-up world of `tabmancer --demo` (its team: shop).
Offscreen. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

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
from PySide6.QtWidgets import QApplication, QWidget
from test_desktop_main import menu_items, read, right_click
from test_desktop_teamkeysdialogs import opened_from_the_loop, shown_words

from tabmancer import demo, shutdowns, teamkeys, teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import frame, look, main
from tabmancer.desktop.teamkeysdialogs import DeleteTeamDialog, ask_delete


@pytest.fixture(scope="module")
def qapp():
    look.fonts_for_offscreen()
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """The window over the demo's made-up world (test_desktop_main's), put back after the test."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, print):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(shutdowns, "open_at_shutdown", lambda listed, times: set())
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


class Surface:
    def __init__(self) -> None:
        self.said: list = []
        self.kept: dict = {}

    def say(self, note) -> None:
        self.said.append(note.words)

    def kept_handovers(self) -> dict:
        return self.kept


def save(*names: str) -> None:
    teams.save({name: teams.new_team(name, f"C:/{name}", "a goal", teams.parse_members("api: x")) for name in names})


def dialogs(parent) -> list[DeleteTeamDialog]:
    return [d for d in parent.findChildren(DeleteTeamDialog) if d.isVisible()]


def test_d_twice_in_the_list_deletes_the_picked_team_and_says_so(qapp):
    save("docs", "shop", "web")
    surface, presses = Surface(), {}
    parent = QWidget()
    dialog = opened_from_the_loop(qapp, lambda: ask_delete(parent, look.ORANGE,
                                                           lambda: teamkeys.TeamKeys(surface, lambda: 1.0, presses),
                                                           "shop"))
    try:
        assert dialog.windowTitle() == teamkeys.DELETE_TITLE
        assert [dialog.teams.item(i).text() for i in range(dialog.teams.count())] == ["docs", "shop", "web"]
        assert dialog.picked() == "shop"  # the card's team first in view
        assert "d d" in shown_words(dialog) and "sessions kept" in shown_words(dialog)
        QTest.keyClick(dialog, Qt.Key.Key_D)
        assert teamkeys.delete_again("shop") in shown_words(dialog) and set(teams.load()) == {"docs", "shop", "web"}
        assert surface.said == [teamkeys.delete_again("shop")]  # on the window's line too
        QTest.keyClick(dialog, Qt.Key.Key_Delete)  # Delete counts as d
        assert set(teams.load()) == {"docs", "web"}
        assert [dialog.teams.item(i).text() for i in range(dialog.teams.count())] == ["docs", "web"]
        assert teamkeys.deleted("shop") in shown_words(dialog)
        QTest.keyClick(dialog, Qt.Key.Key_Down)
        QTest.keyClick(dialog, Qt.Key.Key_D)  # another team: a first press on that one
        assert set(teams.load()) == {"docs", "web"} and teamkeys.delete_again(dialog.picked()) in shown_words(dialog)
    finally:
        dialog.reject()
        parent.deleteLater()
        qapp.processEvents()


def test_with_no_team_saved_the_list_says_so_and_d_does_nothing(qapp):
    surface = Surface()
    parent = QWidget()
    dialog = opened_from_the_loop(qapp, lambda: ask_delete(parent, look.ORANGE,
                                                           lambda: teamkeys.TeamKeys(surface, lambda: 1.0, {})))
    try:
        assert teamkeys.NO_TEAM_TO_DELETE in shown_words(dialog) and dialog.picked() is None
        QTest.keyClick(dialog, Qt.Key.Key_D)
        assert surface.said == []
    finally:
        dialog.reject()
        parent.deleteLater()
        qapp.processEvents()


def test_a_team_card_offers_delete_team_and_it_opens_on_that_team(qapp, window):
    shown = window()
    read(qapp, shown)
    team = next(iter(shown.snap.team_states))
    card = VM.TEAM_CARD + team
    board = shown.board
    assert board.reveal(card)
    assert ("Del", teamkeys.DELETE_BUTTON) in shown.drawer.offered  # the drawer's button
    lane = next(view for view in board.views.values() if view.column.rect_of(card) is not None)
    right_click(lane.column, lane.column.rect_of(card).center().toPoint())
    assert (f"{teamkeys.DELETE_BUTTON}\tDel", True, "") in menu_items(shown)  # and its menu's
    chosen = next(item for item in shown.popup.actions() if item.data() == "Del")
    chosen.trigger()
    QApplication.processEvents()
    [asked] = dialogs(shown)
    assert asked.picked() == team
    asked.reject()


def test_del_and_the_palette_open_it_and_a_session_card_has_no_such_button(qapp, window):
    shown = window()
    read(qapp, shown)
    session = next(key for key in shown.board.cards if not key.startswith(VM.TEAM_CARD))
    assert shown.board.reveal(session)
    assert "Del" not in dict(shown.drawer.offered)  # a session's card: no team to delete
    assert "Del" in main.HERE and shown.actions_by_key["Del"].isEnabled()
    shown.open_commands()
    entry = next(c for c in shown.command_bar.entries if c.kind == "key" and c.key == "Del")
    assert entry.enabled and entry.words == "Delete a saved team"
    shown.command_bar.close()
    QTest.keySequence(shown, main.sequences("Del")[0])
    QApplication.processEvents()
    [asked] = dialogs(shown)
    assert asked.picked() == min(teams.load())  # nothing of a team picked: the first saved one
    asked.reject()
