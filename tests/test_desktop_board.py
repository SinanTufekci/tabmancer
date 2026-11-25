"""The main window's Board (desktop/board.py): the sessions as cards in lanes by the cards' rules,
on made-up sessions. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import board, look
from tabmancer.sessions import Exit, Session, Status

look.fonts_for_offscreen()
ACCENT = "#d97757"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def made(sid: str, status: Status = Status.DONE, exit: Exit = Exit.CLEAN, live: str = "", project: str = "",
         minutes: int = 0, **more) -> Session:
    return Session(session_id=sid, path=None, cwd=f"C:/work/{project or sid}", status=status, exit=exit,
                   live_state=live, last_active=datetime.now() - timedelta(minutes=minutes), **more)


def running(sid: str, live: str, **more) -> Session:
    return made(sid, Status.RUNNING, Exit.RUNNING, live, **more)


def world(*more: Session, hidden: frozenset = frozenset(), pinned: frozenset = frozenset(), team: bool = True):
    """A made-up read of the dashboard: two waiting for you, one at work, one cut off, two done, and the
    team shop (lead and api at work, ui closed)."""
    sessions = [running("asks", "blocked", title="Rate limiting for the public API", model="claude-opus-5-5"),
                running("waits", "idle", title="Summarise yesterday's error logs"),
                running("busy", "busy", title="Add --json output to the list command"),
                made("cut", Status.CUT_OFF, minutes=5, title="Route the power plane"),
                made("done", minutes=9, title="Fix the login redirect loop"),
                made("old", minutes=60, title="Rewrite the getting-started guide"), *more]
    states = {}
    if team:
        lead, api = running("lead-1", "busy", project="shop"), running("api-1", "busy", project="shop")
        ui = made("ui-1", project="shop", minutes=20)
        sessions += [lead, api, ui]
        made_team = teams.new_team("shop", "C:/work/shop", "Add Stripe checkout with an end-to-end test",
                                   teams.parse_members("api: endpoints; ui: screens"))
        states = {"shop": teams.TeamState(made_team, {"lead": lead, "api": api, "ui": ui}, [])}
    by_id = {s.session_id: s for s in sessions}
    view = VM.build(by_id, hidden=set(hidden), pinned=set(pinned), git={}, team_states=states, tokens={}, limits=[],
                    armed={}, queued={})
    return SimpleNamespace(view=view, sessions=by_id, team_states=states, hidden=set(hidden), pinned=set(pinned))


@pytest.fixture
def shown_board(qapp):
    made_boards = []

    def make(snap=None):
        shown = board.Board(ACCENT)
        shown.resize(1100, 760)
        shown.show()
        made_boards.append(shown)
        if snap is not None:
            shown.set(snap)
            qapp.processEvents()
        return shown

    yield make
    for shown in made_boards:
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def cards_of(shown: board.Board) -> dict[str, list[str]]:
    return {key: [card.key for card in view.column.cards] for key, view in shown.views.items()}


def press(shown: board.Board, key: Qt.Key) -> None:
    QTest.keyClick(shown, key)


def test_the_lanes_are_the_view_models_and_each_card_is_drawn(qapp, shown_board):
    snap = world()
    shown = shown_board(snap)
    expected = VM.board(VM.visible(snap.sessions.values(), lambda s: False), snap.team_states, snap.pinned)
    assert [(lane.key, lane.items, lane.count) for lane in shown.lanes] == \
        [(lane.key, lane.items, lane.count) for lane in expected]
    assert cards_of(shown) == {"waiting": ["asks", "waits"], "open": ["busy", "team:shop"], "continue": ["cut"],
                               "done": []}  # DONE folded: a strip, no cards
    need = shown.views["waiting"]
    assert need.heading.isVisible() and need.heading.lane.count == 2
    assert need.heading.lines() == ["asks · waits"]
    assert shown.views["open"].heading.lines() == ["3 working"]  # lead and api with busy, on the team's card here
    assert shown.views["done"].strip.lane.away == {"shop": 1}  # ui is closed: counted in DONE, on the card in OPEN NOW
    done = shown.views["done"]
    assert done.strip.isVisible() and not done.scroller.isVisible() and done.width() == board.STRIP
    placed = {one.card.key: one for one in shown.views["waiting"].column.placed}
    assert 1 <= len(placed["asks"].title) <= board.TITLE_LINES
    assert " ".join(placed["asks"].title) == "Rate limiting for the public API"  # wrapped, not cut, where it fits
    card = shown.cards["asks"]
    assert card.head == "needs you" and card.meta[0] == [("asks", look.CYAN)]
    assert "".join(words for words, _ in card.meta[1]).startswith("opus 5.5")  # its model, as the list says it


def test_done_opens_with_a_click_and_folds_again_from_its_heading(qapp, shown_board):
    shown = shown_board(world())
    done = shown.views["done"]
    QTest.mouseClick(done.strip, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert done.scroller.isVisible() and not done.strip.isVisible()
    assert cards_of(shown)["done"] == ["done", "old"] and done.width() > board.STRIP
    assert done.heading.lines() == [done.heading.lane.caption, "1 on shop's card"]
    shown.set(world())  # the next read keeps it open
    assert not shown.views["done"].heading.lane.folded
    QTest.mouseClick(done.heading, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert done.strip.isVisible() and cards_of(shown)["done"] == []


def test_the_team_card_goes_where_its_most_urgent_member_is(qapp, shown_board):
    snap = world()
    shown = shown_board(snap)
    team = shown.cards["team:shop"]
    assert team.head == "team shop" and team.title.startswith("Add Stripe checkout")
    assert [words.split()[0] for words, _ in team.chips] == ["★lead", "api", "ui"]
    assert "api: " in team.tip and "Add Stripe checkout" in team.tip
    snap.sessions["api-1"].live_state = "blocked"  # api needs you
    shown.set(world_again(snap))
    assert "team:shop" in cards_of(shown)["waiting"] and "team:shop" not in cards_of(shown)["open"]
    chips = dict(shown.cards["team:shop"].chips)
    assert chips["api !"] == look.RED and shown.cards["team:shop"].edge == look.RED  # what needs you is red


def world_again(snap):
    """The same sessions read again (a member's state changed in place)."""
    view = VM.build(snap.sessions, hidden=snap.hidden, pinned=snap.pinned, git={}, team_states=snap.team_states,
                    tokens={}, limits=[], armed={}, queued={})
    return SimpleNamespace(view=view, sessions=snap.sessions, team_states=snap.team_states, hidden=snap.hidden,
                           pinned=snap.pinned)


def test_the_arrows_move_the_pick_lane_by_lane(qapp, shown_board):
    shown = shown_board(world())
    picks = []
    shown.picked.connect(picks.append)
    assert shown.pick == "asks"  # the first card, from the start
    press(shown, Qt.Key.Key_Down)
    assert shown.pick == "waits" and picks == ["waits"]
    press(shown, Qt.Key.Key_Down)
    assert shown.pick == "waits"  # the last in its lane: it stays
    press(shown, Qt.Key.Key_Right)
    assert shown.pick == "team:shop"  # the same row in the next lane, or its last
    press(shown, Qt.Key.Key_Right)
    assert shown.pick == "cut"
    press(shown, Qt.Key.Key_Right)
    assert shown.pick == "cut"  # DONE is folded: nothing to its right
    press(shown, Qt.Key.Key_Left)
    press(shown, Qt.Key.Key_Home)
    assert shown.pick == "busy"
    press(shown, Qt.Key.Key_End)
    assert shown.pick == "team:shop"
    assert shown.views["open"].column.picked == "team:shop" and shown.views["waiting"].column.picked == "team:shop"


def test_an_empty_lane_is_passed_over(qapp, shown_board):
    snap = world(team=False)
    snap.sessions["busy"].live_state = "blocked"  # OPEN NOW empties
    shown = shown_board(world_again(snap))
    assert cards_of(shown)["open"] == []
    press(shown, Qt.Key.Key_Right)
    assert shown.pick == "cut"


def test_a_click_picks_a_card(qapp, shown_board):
    shown = shown_board(world())
    column = shown.views["continue"].column
    spot = column.rect_of("cut").center()
    QTest.mouseClick(column, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(spot.x()), int(spot.y())))
    assert shown.pick == "cut" and column.picked == "cut"
    assert shown.views["waiting"].column.picked == "cut"  # no other card stays lit


def test_the_pick_stays_on_its_card_across_reads_or_goes_to_the_one_in_its_place(qapp, shown_board):
    snap = world()
    shown = shown_board(snap)
    press(shown, Qt.Key.Key_Down)
    assert shown.pick == "waits"
    shown.set(world_again(snap))
    assert shown.pick == "waits"  # read again: still on it
    snap.sessions["waits"].exit = Exit.CLEAN  # it closed: off NEED YOU
    shown.set(world_again(snap))
    assert shown.pick == "busy"  # the card that took its place
    shown.set(SimpleNamespace(view=VM.build({}, hidden=set(), pinned=set(), git={}, team_states={}, tokens={},
                                            limits=[], armed={}, queued={}),
                              sessions={}, team_states={}, hidden=set(), pinned=set()))
    assert shown.pick is None and cards_of(shown) == {"waiting": [], "open": [], "continue": [], "done": []}


def test_hidden_sessions_are_not_on_the_board_and_pinned_ones_come_first(qapp, shown_board):
    shown = shown_board(world(hidden=frozenset({"asks"}), pinned=frozenset({"waits"})))
    assert cards_of(shown)["waiting"] == ["waits"]
    assert shown.cards["waits"].pinned
    shown.fold("done", False)
    shown.set(world(pinned=frozenset({"old"})))
    assert cards_of(shown)["done"][0] == "old"


def test_a_title_wraps_to_two_lines_at_most():
    metrics = board.metrics(board.TITLE)
    lines = board.wrapped("one two three four five six seven eight nine ten eleven twelve", metrics, 90, 2)
    assert len(lines) == 2 and lines[-1].endswith("…") and all(metrics.horizontalAdvance(line) <= 90 for line in lines)
    assert board.wrapped("", metrics, 90, 2) == [""]
    assert board.wrapped("short", metrics, 900, 2) == ["short"]
