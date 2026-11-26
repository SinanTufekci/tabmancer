"""The seam: the terminal dashboard (run_test) and the desktop window side by side over one made-up world
(`tabmancer --demo`'s), in one process, as they run beside each other. A pin or a hide made in either
shows in the other within one poll; the actor lock stays with the terminal dashboard, whatever the window
does; both tick the sessions open at the made-up shutdown when they start; the window's Table lists the
terminal's rows, row for row, in the same words, ticks and all; and once M's monitor has taken the keys,
the window's keys work again when it is clicked back. Skipped without the desktop
extra (pip install "tabmancer[desktop]")."""

from __future__ import annotations

import importlib
import os
import re
import time
import traceback
from pathlib import Path

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import actor, demo, marks
from tabmancer import viewmodel as VM
from tabmancer.app import SessionPicker
from tabmancer.desktop import frame, look, main, monitor
from tabmancer.desktop.table import PARTS
from tabmancer.rowwords import COLUMNS, HEADING, STAR

look.fonts_for_offscreen()

TIMES = re.compile(r"\b\d+h\d{2}m\b|\b\d+[dhms]\b")  # "3m" then "4m": the two read a moment apart
TURNING = re.compile(f"[{re.escape(STAR)}]")  # a member at work's star, in the frame of the moment it was drawn
WAIT_SECONDS = 60.0  # the bound on each wait below, reached only when something is wrong: a wait ends as
# soon as its condition holds, however late the terminal's poll threads land with other suites running beside


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


@pytest.fixture
def side_by_side(qapp, tmp_path, monkeypatch):
    """The made-up world, with every actor.claim recorded by whether a window's code asked for it."""
    for name in frame.DEMO_ENV:  # demo.prepare points these at its own home
        monkeypatch.setenv(name, os.environ.get(name, ""))
    live = demo.prepare(tmp_path)
    for module, name, value in demo.stand_ins(tmp_path, live, lambda words: None):
        monkeypatch.setattr(module, name, value)
    claims: list[bool] = []
    real = actor.claim

    def claim() -> bool:
        steps = [Path(step.filename) for step in traceback.extract_stack()]  # its store reads on a thread of its own
        claims.append(any("desktop" in step.parts or step.name == "store.py" for step in steps))
        return real()

    monkeypatch.setattr(actor, "claim", claim)
    made = []

    def window(width: int = 1500):
        shown = main.MainWindow(start=False)
        shown.resize(width, 950)
        shown.show()
        assert active(shown), "the window never became the active one: its keys would do nothing"
        made.append(shown)
        return shown

    yield window, claims
    for shown in made:
        shown.settle()  # no read left running once the world is put back
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def active(shown) -> bool:
    return QTest.qWaitForWindowActive(shown, int(WAIT_SECONDS * 1000))


def read(qapp, shown, seconds: float = WAIT_SECONDS) -> None:
    """One poll of the window: read on its thread, then shown."""
    shown.poll()
    deadline = time.monotonic() + seconds
    while shown.waiting:
        assert time.monotonic() < deadline, "waited too long"
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()


def press(shown, key: str) -> None:
    QTest.keySequence(shown, main.sequences(key)[0])
    QApplication.instance().processEvents()


async def wait_for(pilot, condition, what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"{what}: not after {WAIT_SECONDS:.0f} s"
        await pilot.pause(0.05)


def tui_keys(app) -> list[str]:
    return [row.key.value for row in app.table.ordered_rows]


async def cursor_on(pilot, app, key: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        app.table.move_cursor(row=tui_keys(app).index(key))
        await pilot.pause(0.05)
        if app.cursor_key() == key:
            return
        assert time.monotonic() < deadline, f"the cursor never stayed on {key!r}"


def tui_words(app, key: str) -> list[str]:
    return [getattr(cell, "plain", str(cell)) for cell in app.table.get_row(key)]


def window_words(table, key: str) -> list[str]:
    row = table.keys.index(key)
    return ["".join(words for words, _style in table.grid.item(row, column).data(PARTS) or [])
            for column in range(len(COLUMNS))]


def same_words(theirs: list[str], ours: list[str]) -> bool:
    """A row's words, the title as far as both have room for it, the times as read a moment apart."""

    def plain(words: str) -> str:
        return TURNING.sub("*", TIMES.sub("·", " ".join(words.split())))

    for column, (one, other) in enumerate(zip(theirs, ours, strict=True)):
        one, other = plain(one), plain(other)
        if COLUMNS[column] == "title":
            one, other = one.rstrip("…"), other.rstrip("…")
            if not (one.startswith(other) or other.startswith(one)):
                return False
        elif one != other:
            return False
    return True


def rows_differ(app, table) -> str:
    """Where the two lists differ, or "" where they agree, row for row."""
    if tui_keys(app) != table.keys:
        return f"the rows differ: the terminal's {tui_keys(app)!r}, the window's {table.keys!r}"
    for key in table.keys:
        if not same_words(tui_words(app, key), window_words(table, key)):
            return (f"row {key!r} differs: the terminal's {tui_words(app, key)!r}, "
                    f"the window's {window_words(table, key)!r}")
    return ""


async def rows_agree(pilot, qapp, app, table) -> None:
    """The two lists, row for row, in the same words, once both have read what the other has (a team's
    members resting, say: the terminal's own poll of its teams lands a moment after its first rows). Each
    side goes on meanwhile: the terminal's loop, and the window's events."""
    deadline = time.monotonic() + WAIT_SECONDS
    while differs := rows_differ(app, table):
        assert time.monotonic() < deadline, differs
        qapp.processEvents()
        await pilot.pause(0.05)


async def test_pins_and_hides_cross_within_a_poll_and_the_lock_stays_with_the_terminal(qapp, side_by_side):
    window, claims = side_by_side
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await wait_for(pilot, lambda: len(app.sessions) >= len(demo.SESSIONS), "the terminal's first rows")
        assert app.engine.state.acts and actor.holds() and claims == [False]  # the terminal's, at its start
        shown = window()
        read(qapp, shown)
        assert shown.selected == shown.preselected == app.selected == app.preselected != set()  # the same ticks
        assert shown.bit_line.text() == f"Hi! {len(app.preselected)} were open when you shut down."
        shown.show_bit()  # after its hello
        assert shown.bit_line.text() == "The terminal dashboard acts: this window watches."
        app.selected = set()  # f and x take the ticked ones: unticked in both, they take the one picked
        shown.selected = set()
        shown.show_ticked()
        login = next(s.session_id for s in shown.snap.sessions.values() if s.title == "Fix the login redirect loop")
        docs = next(s.session_id for s in shown.snap.sessions.values() if s.title == "Rewrite the getting-started guide")

        assert shown.board.reveal(login)
        press(shown, "f")  # pinned in the window...
        assert login in shown.snap.pinned and login not in app.marks.pinned
        app.apply_live_updates({})  # ...shows in the terminal at its next poll
        assert login in app.marks.pinned and tui_keys(app).index(login) < tui_keys(app).index(f"{HEADING}rest")

        await cursor_on(pilot, app, docs)
        await pilot.press("x")  # hidden in the terminal...
        assert docs not in tui_keys(app) and docs not in shown.snap.hidden
        read(qapp, shown)  # ...gone from the window at its next poll
        assert docs in shown.snap.hidden and docs not in shown.board.cards

        await cursor_on(pilot, app, login)
        await pilot.press("f")  # unpinned in the terminal: the window follows
        read(qapp, shown)
        assert login not in shown.snap.pinned and not shown.board.cards[login].pinned
        assert shown.board.reveal(login)
        press(shown, "x")  # hidden in the window: the terminal follows
        app.apply_live_updates({})
        assert login not in tui_keys(app)
        assert marks.Marks.load() == marks.Marks(hidden={docs, login}) == app.marks

        for key in ("Enter", "M", "x", "X", "f", "m", "j"):  # whatever the window does, the lock stays put
            press(shown, key)
        shown.settle()
        assert claims == [False] and actor.holds() and app.engine.state.acts


async def test_the_windows_table_lists_the_terminals_rows_row_for_row(qapp, side_by_side):
    window, _claims = side_by_side
    app = SessionPicker(days=14)
    async with app.run_test(size=(280, 50)) as pilot:  # room for the Model column beside the sidebar and preview
        await wait_for(pilot, lambda: len(app.sessions) >= len(demo.SESSIONS), "the terminal's first rows")
        shown = window(1700)  # its Model column too, whatever a runner's monospace font measures
        read(qapp, shown)
        assert shown.selected == app.selected == app.preselected != set()  # open at the made-up shutdown: ticked
        shown.set_view("table")
        table = shown.table
        await rows_agree(pilot, qapp, app, table)
        assert any(key.startswith(HEADING) for key in table.keys) and app.model_shown and table.model_shown
        team = next(iter(shown.snap.team_states))
        await cursor_on(pilot, app, f"\x01{team}")
        folded = list(table.keys)
        await pilot.press("e")  # its members unfolded in the terminal, and in the window
        assert table.show_key(VM.TEAM_CARD + team)
        press(shown, "e")
        await rows_agree(pilot, qapp, app, table)
        assert len(table.keys) > len(folded)


def test_after_ms_monitor_took_the_keys_the_window_has_them_again_when_clicked_back(qapp, side_by_side):
    window, _claims = side_by_side
    shown = window()
    read(qapp, shown)
    shown.selected = set()  # the ones ticked at the start aside: f takes the one picked
    shown.show_ticked()
    team = next(iter(shown.snap.team_states))
    assert shown.board.reveal(VM.TEAM_CARD + team)
    press(shown, "M")
    watching = shown.monitors[team]
    assert isinstance(watching, monitor.MonitorWindow)
    active(watching)
    assert QApplication.activeWindow() is watching  # it took the keys, as a window opened in front does
    login = next(s.session_id for s in shown.snap.sessions.values() if s.title == "Fix the login redirect loop")
    assert shown.board.reveal(login)
    press(shown, "f")  # the keys are the monitor's now: the window's do nothing
    assert login not in shown.snap.pinned and marks.Marks.load().pinned == set()
    # Clicked back: the window manager makes a clicked window the active one (offscreen there is none, so
    # the test asks for what the click does), and the window's keys are its own again.
    shown.activateWindow()
    active(shown)
    assert QApplication.activeWindow() is shown
    press(shown, "f")
    assert login in shown.snap.pinned and marks.Marks.load().pinned == {login}
    assert shown.board.reveal(VM.TEAM_CARD + team)
    press(shown, "M")  # and M brings the monitor in front again
    assert shown.bit_line.text() == f"The monitor of {team} is in front." and shown.monitors == {team: watching}
