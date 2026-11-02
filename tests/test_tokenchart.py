"""The sidebar's token chart in real pixels: a bar a day, all as wide, apart, in proportion, on a
baseline; its cells blank where the terminal draws pixels, block characters where it does not."""

from __future__ import annotations

from textual.app import App
from textual.widgets._sparkline import SparklineRenderable

from tabmancer import bitart, sixel
from tabmancer.tokenchart import TokenChart, draw

QUIET, LOUD, BASE = (40, 40, 40), (220, 120, 80), (30, 30, 30)


def bars(rows: list[bytes]) -> list[tuple[int, int, int]]:
    """(left, right, height) of each bar, read off the picture's columns above the baseline."""
    height = len(rows)
    found, start = [], None
    columns = [sum(1 for y in range(height - 1) if rows[y][x]) for x in range(len(rows[0]))]
    for x, tall in enumerate([*columns, 0]):
        if tall and start is None:
            start = x
        elif not tall and start is not None:
            found.append((start, x, max(columns[start:x])))
            start = None
    return found


def test_a_bar_a_day_all_as_wide_apart_and_in_proportion():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    palette, rows = draw(values, 220, 78, QUIET, LOUD, BASE)
    found = bars(rows)
    assert len(found) == 14  # a gap between every two
    widths = {right - left for left, right, _ in found}
    assert max(widths) - min(widths) <= 1  # as wide as one another (Textual's took one cell or two)
    heights = [tall for _, _, tall in found]
    assert heights == sorted(heights) and heights[-1] == 78 - 3 and abs(heights[6] - heights[-1] / 2) <= 1
    assert all(n == 1 for n in rows[-1]) and palette[1] == BASE  # the baseline, all along
    above = rows[-3]  # a pixel above the baseline: the bars' feet
    assert palette[above[found[-1][0] + 2]] == LOUD and palette[above[found[0][0] + 2]] != LOUD


def test_a_day_with_nothing_has_no_bar_and_any_other_one_shows():
    _, rows = draw([0, 0, 5, 0, 1000], 100, 40, QUIET, LOUD, BASE)
    found = bars(rows)
    assert len(found) == 2 and found[0][2] >= 2  # five tokens beside a thousand still show
    _, empty = draw([0] * 14, 220, 78, QUIET, LOUD, BASE)
    assert bars(empty) == []


class Chart(App, sixel.SixelHost):
    def compose(self):
        yield TokenChart([0] * 14, id="activity")


async def test_where_the_terminal_draws_pixels_the_chart_is_a_picture(monkeypatch):
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    app = Chart()
    async with app.run_test(size=(40, 10)) as pilot:
        chart = app.query_one(TokenChart)
        chart.styles.height = 4
        await pilot.pause(0.1)
        assert chart.picture is not None and str(chart.render()) == ""  # its cells left blank
        before = chart.picture.data
        chart.data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
        await pilot.pause(0.1)
        assert chart.picture.data != before and chart.picture.fresh  # drawn again, to be sent
        assert chart.picture.data.startswith("\x1bP") and chart.picture.place() == (0, 0, 40, 4)


async def test_elsewhere_the_chart_is_textuals_sparkline(monkeypatch):
    monkeypatch.setattr(sixel, "_terminal", None)
    app = Chart()
    async with app.run_test(size=(40, 10)) as pilot:
        chart = app.query_one(TokenChart)
        await pilot.pause(0.1)
        assert chart.picture is None and isinstance(chart.render(), SparklineRenderable)


NOW, FLOOR = (230, 230, 230), (38, 38, 38)


def test_the_cards_hours_take_a_place_each_of_the_whole_day_the_hour_now_in_white():
    from tabmancer.tokenchart import HOURS, draw_hours

    hours = [0] * 9 + [500, 0, 1500]  # midnight to 11:00, used at 9 and now
    palette, rows = draw_hours(hours, 240, 60, QUIET, NOW, FLOOR)
    found = bars(rows)
    assert len(found) == 2 and found[1][2] == 60 - 3 and abs(found[0][2] - found[1][2] / 3) <= 1
    pitch = 240 / HOURS
    assert round(found[0][0] / pitch - 0.25) == 9 and round(found[1][0] / pitch - 0.25) == 11  # in their hours' places
    assert palette[rows[-3][found[1][0] + 2]] == NOW and palette[rows[-3][found[0][0] + 2]] == QUIET
    floor_ends = max(x for x, n in enumerate(rows[-1]) if n == 1)
    assert floor_ends == round(len(hours) * pitch) - 1 and palette[1] == FLOOR  # under the hours gone by only
    assert not any(any(row[floor_ends + 1:]) for row in rows)  # the hours to come: nothing yet


class Card(App, sixel.SixelHost):
    def compose(self):
        from tabmancer.widgets import HourChart

        yield HourChart("", id="beside")


async def test_beside_a_card_the_hours_are_a_picture_and_words_stay_words(monkeypatch):
    from rich.text import Text

    from tabmancer.widgets import HourBars, HourChart

    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    app = Card()
    async with app.run_test(size=(40, 10)) as pilot:
        beside = app.query_one(HourChart)
        beside.styles.height = 3
        beside.update(HourBars([0, 200, 900]))
        await pilot.pause(0.1)
        assert beside.picture is not None and beside.picture.data.startswith("\x1bP")
        assert str(beside.render()).strip() == ""  # its cells blank under the picture
        beside.update(Text("4 waiting for you"))  # another card's words: no picture
        await pilot.pause(0.1)
        assert beside.picture is None and "4 waiting for you" in str(beside.render())
    monkeypatch.setattr(sixel, "_terminal", None)  # no pixels: the hours in block characters
    app = Card()
    async with app.run_test(size=(40, 10)) as pilot:
        beside = app.query_one(HourChart)
        beside.update(HourBars([0, 200, 900]))
        await pilot.pause(0.1)
        assert beside.picture is None and isinstance(beside.content, HourBars)
