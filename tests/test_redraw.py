"""Each frame touches only the cells that animate, and the table draws again only their rows:
DataTable's update_cell draws the whole table anew, twelve times a second for a turning star."""

import asyncio
import sys

import pytest

from tabmancer import sessions
from tabmancer.app import TIMER_THREADS, SessionPicker


@pytest.mark.skipif(sys.platform != "win32", reason="Textual sleeps in threads on Windows only")
async def test_on_windows_every_timer_gets_a_thread_of_its_own(fake_home):
    # Textual sleeps out each timer in a thread of asyncio's default pool, cores + 4 threads: on a
    # 2-core machine the dashboard's timers outnumbered them and these tests ran 2.7 times slower.
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert asyncio.get_running_loop()._default_executor._max_workers == TIMER_THREADS


async def settled(pilot, app, states: dict[str, str]) -> None:
    for _ in range(100):
        await pilot.pause(0.05)
        if all(app.sessions.get(sid) and app.sessions[sid].live_state == state for sid, state in states.items()):
            break
    app.flash.clear()  # the highlight of a change animates too, for a moment


def live(monkeypatch, states: dict[str, str]) -> None:
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": sid, "kind": "interactive", "status": state} for sid, state in states.items()])


async def test_each_frame_touches_only_the_cells_that_animate(fake_home, monkeypatch):
    states = {"one": "idle", "two": "busy"}
    live(monkeypatch, states)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await settled(pilot, app, states)
        touched = []
        update = app.table.update_cells
        monkeypatch.setattr(app.table, "update_cells", lambda changes: touched.extend(row for row, _, _ in changes)
                            or update(changes))
        for _ in range(5):
            app.spin()
        assert touched.count("two") == 5  # working: its star turns
        assert "one" not in touched  # waiting for you: nothing changes, nothing is redrawn


async def test_a_frame_draws_again_only_the_rows_it_changed_and_shows_them_as_they_are_now(fake_home, monkeypatch):
    states = {"one": "idle", "two": "busy"}
    live(monkeypatch, states)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await settled(pilot, app, states)
        table = app.table
        await pilot.pause(1.0)  # every row drawn once, after the list has faded in (its colours drawn afresh then)
        drawn = []
        render_cell = table._render_cell
        monkeypatch.setattr(table, "_render_cell",
                            lambda row, column, *a, **k: drawn.append(row) or render_cell(row, column, *a, **k))
        two = table.get_row_index("two")
        y = two + table.header_height - round(table.scroll_offset.y)
        before = table.render_line(y).text
        for _ in range(3):  # a frame on which the star has turned
            app.spin()
            await pilot.pause(0.05)
            if table.render_line(y).text != before:
                break
        assert drawn and set(drawn) == {two}  # the other rows kept their drawing
        now = table.render_line(y).text
        assert now != before  # not an old frame kept
        table._clear_caches()
        assert table.render_line(y).text == now  # as the whole table drawn afresh shows it


async def test_behind_a_dialog_nothing_animates_and_after_it_all_does_again(fake_home, monkeypatch):
    # With a dialog in front, Textual sends the whole screen to the terminal again for any change
    # behind it: a turning star, Bit's frames and the live dot sent 700 KB a second.
    from tabmancer.keysview import KeysScreen
    from tabmancer.mascot import Mascot
    from tabmancer.widgets import TopBar

    states = {"two": "busy"}
    live(monkeypatch, states)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await settled(pilot, app, states)
        touched, drawn = [], []
        update = app.table.update_cells
        monkeypatch.setattr(app.table, "update_cells", lambda changes: touched.append(changes) or update(changes))
        bit = app.query_one(Mascot)
        lines = bit.lines
        monkeypatch.setattr(bit, "lines", lambda width: drawn.append(width) or lines(width))
        bar = app.query_one(TopBar)
        app.push_screen(KeysScreen())
        await pilot.pause(0.2)
        dot = bar.query_one("#live").content.style
        touched.clear(), drawn.clear()
        for _ in range(5):
            app.spin()
            bit.tick()
            bar.started -= 0.3  # the dot's breath on by a little
            bar.tick()
        assert touched == [] and drawn == [] and bar.query_one("#live").content.style == dot
        await pilot.press("escape")
        await pilot.pause(0.2)
        for _ in range(3):
            app.spin()
            bit.tick()
            bar.started -= 0.3
            bar.tick()
        assert touched and drawn and bar.query_one("#live").content.style != dot  # it breathes again


async def test_the_bar_lays_the_screen_out_again_only_when_a_part_changes_width(fake_home):
    from textual.widgets import Static

    from tabmancer.widgets import TopBar

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.2)
        bar = app.query_one(TopBar)
        clock = bar.query_one("#clock", Static)
        asked = []
        update = clock.update
        clock.update = lambda content, layout=True: asked.append(layout) or update(content, layout=layout)
        bar.put("clock", "12:00:01")  # as wide as the time it showed: drawn, the screen not laid out anew
        bar.put("clock", "12:00:01")  # the same: not drawn again
        bar.put("clock", "12:00:02")
        bar.put("clock", "9:00")  # narrower: laid out
        assert asked == [False, False, True]
