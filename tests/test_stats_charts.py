"""The stats screen's charts, BY DAY and the hours of the week, in real pixels where the terminal
draws them: tiles and bars in their places, their cells left blank for the picture; elsewhere, and
while a picture cannot be shown (scrolled partly out of sight), the block characters of
`tabmancer stats`."""

from __future__ import annotations

import pytest

from tabmancer import bitart, demo, sixel
from tabmancer.tokenchart import draw_bars, draw_week

LOW, HIGH, NONE, ACCENT = (26, 26, 26), (217, 119, 87), (58, 58, 58), (217, 119, 87)
CELL = (10, 20)


def test_the_hours_of_the_week_are_tiles_in_their_hours_places_and_dots_where_none_was():
    slots = [[0.0] * 24 for _ in range(7)]
    slots[0][9] = 1800.0  # Monday at nine
    slots[4][15] = 3600.0  # Friday at three, the most
    palette, rows = draw_week(slots, CELL, 720, 138, LOW, HIGH, NONE)
    assert len(rows) == 138 and len(rows[0]) == 720

    def at(x, y):
        return palette[rows[y][x]]

    side = 16  # two cells across less a gap, as high as a row less a gap
    for day, hour in ((0, 9), (4, 15)):
        left, top = hour * 30 + 2, day * 20 + 2  # over the first two of the hour's three cells
        assert at(left + side // 2, top + side // 2) not in (None, NONE)
        assert at(left, top) is None and at(left + side - 1, top + side - 1) is None  # corners rounded off
        assert at(left + side // 2, top - 1) is None and at(left + side, top + side // 2) is None  # a gap round it
    assert at(15 * 30 + 10, 4 * 20 + 10) == HIGH  # the busiest in the accent
    monday = at(9 * 30 + 10, 10)
    assert monday != HIGH and all(low < c < high for low, c, high in zip(LOW, monday, HIGH))  # half of it: between
    assert at(3 * 30 + 10, 2 * 20 + 10) == NONE and at(3 * 30 + 4, 2 * 20 + 4) is None  # a small dot, no tile
    _, empty = draw_week([[0.0] * 24 for _ in range(7)], CELL, 720, 138, LOW, HIGH, NONE)
    assert sum(1 for row in empty for n in row if n) == 7 * 24 * 4  # nothing worked: a dot an hour


def test_the_bars_are_as_long_as_their_values_a_row_each_with_round_ends():
    palette, rows = draw_bars([10.0, 5.0, 0.0, 2.5], CELL, 240, 78, ACCENT)

    def length(row_of_cells):
        middle = rows[row_of_cells * 20 + 10]
        return max((x + 1 for x, n in enumerate(middle) if n), default=0)

    assert [length(n) for n in range(4)] == [240, 120, 0, 60]
    assert palette[rows[10][5]] == ACCENT
    thick = [y for y in range(20) if rows[y][100]]
    assert len(thick) == 12 and thick[0] == 4  # the middle of its row of cells: rows apart
    assert not rows[thick[0]][239] and rows[10][239]  # its end rounded off
    _, tiny = draw_bars([1000.0, 0.01], CELL, 240, 38, ACCENT)
    assert any(tiny[30])  # any value above nothing shows


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "restored after the test")  # prepare() sets it too
    live = demo.prepare(tmp_path)
    app = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, app.tell):
        monkeypatch.setattr(module, name, value)
    return app


async def open_stats(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if app.token_report is not None:
            break
    await pilot.press("u")
    await pilot.pause(0.3)
    return app.screen


async def test_in_pixels_the_charts_are_pictures_over_blank_cells(in_demo, monkeypatch):
    from tabmancer.statsview import BarsPicture, WeekPicture

    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    app = in_demo
    async with app.run_test(size=(160, 60)) as pilot:
        screen = await open_stats(app, pilot)
        days, projects = screen.query_one("#tokens-scroll").query(BarsPicture)  # BY DAY, BY PROJECT
        for bars in (days, projects):
            assert bars.picture is not None and bars.picture.data.startswith("\x1bP")
            assert bars.in_sight() and str(bars.render()) == ""
        assert days.picture.place()[2:] == (24, 14) and projects.picture.place()[2] == 16
        await pilot.press("right")  # when you work
        await pilot.pause(0.3)
        week = screen.query_one(WeekPicture)
        assert week.picture is not None and week.picture.data.startswith("\x1bP") and week.picture.place()[2:] == (72, 7)
        assert week.in_sight() and str(week.render()) == ""
        assert not days.in_sight() and days.render().plain.strip()  # on a tab not shown: its blocks


async def test_without_pixels_the_charts_are_the_block_characters_of_tabmancer_stats(in_demo):
    from tabmancer.statsview import (
        BarChart,
        BarsPicture,
        WeekChart,
        WeekPicture,
        bar,
        tools_parts,
        work_parts,
    )

    app = in_demo
    async with app.run_test(size=(160, 60)) as pilot:
        screen = await open_stats(app, pilot)
        await pilot.press("right")
        await pilot.pause(0.3)
        parts = work_parts(screen.report, screen.sessions, screen.accent)
        week = next(p for p in parts if isinstance(p, WeekChart))
        days = next(p for p in parts if isinstance(p, BarChart))
        shown = screen.query_one(WeekPicture)
        assert shown.picture is None
        assert shown.render().plain.split("\n") == [week.tiles(day, screen.accent).plain for day in range(7)]
        bars = screen.query_one("#work-scroll").query_one(BarsPicture)
        assert bars.render().plain.split("\n") == [bar(value, max(days.values)) for value in days.values]
        tools = next(p for p in tools_parts(screen.report, screen.sessions, screen.accent) if isinstance(p, BarChart))
        shown = screen.query_one("#tools-scroll").query_one(BarsPicture)
        assert shown.render().plain.split("\n") == [bar(v, max(tools.values), 20) for v in tools.values]


async def test_scrolled_partly_out_of_sight_the_part_in_sight_is_shown_and_under_a_dialog_the_blocks(
        in_demo, monkeypatch):
    from tabmancer.keysview import KeysScreen
    from tabmancer.statsview import WeekPicture

    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    app = in_demo
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await open_stats(app, pilot)
        await pilot.press("right")
        await pilot.pause(0.3)
        week = screen.query_one(WeekPicture)
        scroll = screen.query_one("#work-scroll")

        async def sent():
            """What the host does after each update Textual writes (not written in a test: headless)."""
            app.send_pictures(None)
            await pilot.pause(0.2)

        await sent()
        x = week.picture.place()[0]
        assert week.picture.seen and week.drawn[-4:] == (0, 0, 720, 138)  # all of it
        scroll.scroll_to(y=week.region.y - scroll.content_region.y + 3, animate=False)  # its top three rows above
        await pilot.pause(0.3)
        await sent()
        assert week.picture.seen and week.picture.place() == (x, scroll.content_region.y, 72, 4)  # the rest in sight
        assert week.drawn[-4:] == (0, 60, 720, 78)  # the picture of the rows in sight, from the fourth
        assert week.in_sight() and str(week.render()) == ""
        app.push_screen(KeysScreen())
        await pilot.pause(0.3)
        await sent()
        assert not week.picture.seen and not week.in_sight() and "██" in week.render().plain  # the blocks under it
