"""The Sessions panel's Table and Live views (desktop/table.py): the terminal list's rows and words from
rowwords, drawn by Qt, on made-up sessions. Skipped without the desktop extra (pip install
"tabmancer[desktop]")."""

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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tabmancer import rowwords, teams
from tabmancer import viewmodel as VM
from tabmancer.desktop import look, table
from tabmancer.palette import mix
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


def world(pinned: frozenset = frozenset()):
    """A made-up read of the dashboard: one that needs you, one waiting, one at work, one cut off, two
    done, and the team shop (lead and api at work, ui closed)."""
    sessions = [running("asks", "blocked", title="Rate limiting for the public API", model="claude-opus-5-5"),
                running("waits", "idle", title="Summarise yesterday's error logs"),
                running("busy", "busy", title="Add --json output to the list command"),
                made("cut", Status.CUT_OFF, minutes=5, title="Route the power plane"),
                made("done", minutes=9, title="Fix the login redirect loop"),
                made("old", minutes=60, title="Rewrite the getting-started guide")]
    lead, api = running("lead-1", "busy", project="shop"), running("api-1", "busy", project="shop")
    ui = made("ui-1", project="shop", minutes=20)
    sessions += [lead, api, ui]
    shop = teams.new_team("shop", "C:/work/shop", "Add Stripe checkout with an end-to-end test",
                          teams.parse_members("api: endpoints; ui: screens"))
    states = {"shop": teams.TeamState(shop, {"lead": lead, "api": api, "ui": ui}, [])}
    return SimpleNamespace(sessions={s.session_id: s for s in sessions}, team_states=states, hidden=set(),
                           pinned=set(pinned), armed={}, queued={}, session_tokens={})


def shown_of(snap, live: bool = False) -> list[Session]:
    return VM.visible(snap.sessions.values(), lambda s: s.session_id in snap.hidden, live=live)


@pytest.fixture
def shown_table(qapp):
    made_tables = []

    def make(snap, width=1300, live=False):
        shown = table.Table(ACCENT)
        shown.resize(width, 600)
        shown.set_live(live)
        shown.show()
        shown.set(snap, shown_of(snap, live))
        qapp.processEvents()
        made_tables.append(shown)
        return shown

    yield make
    for shown in made_tables:
        shown.close()
        shown.deleteLater()
    qapp.processEvents()


def rows_of(shown: table.Table) -> list[tuple[str, list]]:
    grid = shown.grid
    return [(grid.item(row, 0).data(table.KEY), [grid.item(row, c).data(table.PARTS) for c in range(grid.columnCount())])
            for row in range(grid.rowCount())]


def labels_of(shown: table.Table) -> tuple[str, ...]:
    grid = shown.grid
    return tuple(grid.horizontalHeaderItem(c).text() for c in range(grid.columnCount()))


def test_the_rows_are_rowwords_rows_over_the_sessions_shown(qapp, shown_table):
    snap = world(pinned=frozenset({"old"}))
    shown = shown_table(snap)
    listing = shown.listing
    expected = [(key, list(rowwords.row(listing, key, session).cells)) for key, session in rowwords.keys(listing)]
    assert rows_of(shown) == expected
    keys = [key for key, _ in expected]
    assert keys[0] == f"{rowwords.HEADING}open" and f"{rowwords.HEADING}pinned" in keys
    assert f"{rowwords.TEAM_ROW}shop" in keys and "api-1" not in keys  # folded, as the list starts
    assert labels_of(shown) == rowwords.labels(False, True)
    assert listing.width == shown.width_now and shown.model_shown


def test_the_cursor_skips_headings_and_a_teams_row_unfolds(qapp, shown_table):
    shown = shown_table(world())
    assert shown.pick == shown.keys[1]  # the first row under OPEN NOW, not its heading
    seen = set()
    for _ in range(len(shown.keys) + 2):
        QTest.keyClick(shown, Qt.Key.Key_Down)
        seen.add(shown.pick)
    assert not any(key.startswith(rowwords.HEADING) for key in seen)
    shown.show_key(VM.TEAM_CARD + "shop")
    assert shown.pick == f"{rowwords.TEAM_ROW}shop"

    def double_click() -> None:  # on its title (the ▸ is a click of its own), as the view says it
        shown.doubleClicked.emit(shown.grid.index(shown.keys.index(f"{rowwords.TEAM_ROW}shop"), 2))

    double_click()
    assert {"lead-1", "api-1", "ui-1"} <= set(shown.keys) and "shop" in shown.unfolded
    assert shown.pick == f"{rowwords.TEAM_ROW}shop"  # the pick stays on the team's row
    double_click()
    assert "api-1" not in shown.keys
    QTest.keyClick(shown, Qt.Key.Key_Return)  # Enter is the window's (its Team panel tab): no fold here
    assert "api-1" not in shown.keys
    row = shown.keys.index(f"{rowwords.TEAM_ROW}shop")  # its ▸, clicked
    QTest.mouseClick(shown.viewport(), Qt.MouseButton.LeftButton,
                     pos=shown.visualRect(shown.grid.index(row, 0)).center())
    assert "api-1" in shown.keys


def test_the_pick_goes_out_in_the_drawers_words(qapp, shown_table):
    shown = shown_table(world())
    said = []
    shown.picked.connect(said.append)
    assert shown.show_key("cut") and said[-1] == "cut"
    assert shown.show_key(VM.TEAM_CARD + "shop") and said[-1] == "team:shop"
    assert shown.show_key("api-1") and said[-1] == "api-1" and "shop" in shown.unfolded  # a member: its team opens
    assert not shown.show_key("nowhere")
    shown.set(shown.snap, shown_of(shown.snap))  # read again: the pick stays
    assert shown.pick == "api-1" and said[-1] == "api-1"


def test_the_live_view_lists_the_open_sessions_in_its_columns(qapp, shown_table):
    snap = world()
    shown = shown_table(snap, live=True)
    assert labels_of(shown) == rowwords.labels(True, True)
    assert all(not key.startswith((rowwords.HEADING, rowwords.TEAM_ROW)) for key in shown.keys)
    assert set(shown.keys) == {s.session_id for s in shown_of(snap, live=True)}
    asks = dict(rows_of(shown))["asks"]
    assert asks[0] == [(" ! ", "bold red")]  # needs you


def test_a_narrow_table_cuts_the_title_and_leaves_the_model_out(qapp, shown_table):
    snap = world()
    wide = shown_table(snap)
    narrow = shown_table(snap, width=700)
    assert narrow.width_now < wide.width_now and not narrow.model_shown
    assert labels_of(narrow)[table.COLUMN["model"]] == ""
    assert all(parts == [] for _, cells in rows_of(narrow) for parts in [cells[table.COLUMN["model"]]])


def test_what_moves_moves_as_the_window_allows(qapp, shown_table):
    shown = shown_table(world())
    status, title = table.COLUMN["status"], table.COLUMN["title"]
    busy, team = shown.keys.index("busy"), shown.keys.index(f"{rowwords.TEAM_ROW}shop")
    still_busy = shown.grid.item(busy, status).data(table.PARTS)
    frames = []
    for seconds in (0.0, 0.3):
        shown.tick(seconds)
        frames.append((shown.grid.item(busy, status).data(table.PARTS), shown.grid.item(team, title).data(table.PARTS)))
    assert frames[0] != frames[1]  # the star turned, on the session's row and in the team's chips
    assert frames[0][0][0][0] == f"{rowwords.star_at(0.0)} " and frames[1][1] != frames[0][1]
    shown.set_motion("off")
    assert shown.grid.item(busy, status).data(table.PARTS) == still_busy
    shown.tick(0.9)
    assert shown.grid.item(busy, status).data(table.PARTS) == still_busy


def test_a_badge_is_drawn_behind_its_words(qapp, shown_table):
    shown = shown_table(world())
    shown.set_motion("off")  # the pulse at its middle shade
    row = shown.keys.index("asks")
    rect = shown.visualRect(shown.grid.index(row, table.COLUMN["status"]))
    picture = shown.viewport().grab().toImage()
    behind = QColor(mix(*rowwords.PULSE, 0.5))
    colours = {picture.pixelColor(x, rect.center().y()).name() for x in range(rect.left() + 2, rect.right() - 2)}
    assert behind.name() in colours
    cut = shown.keys.index("cut")  # a closed one cut off: its amber badge
    rect = shown.visualRect(shown.grid.index(cut, table.COLUMN["status"]))
    style = rows_of(shown)[cut][1][table.COLUMN["status"]][0][1]
    tint = look.ink(style).background
    assert tint and tint in {picture.pixelColor(x, rect.center().y()).name()
                             for x in range(rect.left() + 2, rect.right() - 2)}
