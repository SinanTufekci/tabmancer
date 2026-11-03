"""Charts in real pixels, where the terminal draws them (Sixel or kitty graphics, as Bit is): the
sidebar's 14-day token chart, a bar a day, every bar as wide as the others with a gap between them,
rising from a baseline, from the quiet colour to the accent as it nears the busiest day; and the
TOKENS TODAY card's hours (draw_hours, widgets.HourChart). Elsewhere they are block characters: the
token chart is Textual's sparkline, whose days take one cell or two as the width shares out.

A chart's cells are left blank and its picture is sent over them by the host (sixel.SixelHost):
drawn again only when what it shows, its size or its colours change (PixelPicture).
"""

from __future__ import annotations

from rich.text import Text
from textual.color import Color
from textual.widgets import Sparkline

from . import sixel
from .bitart import Colour

LEVELS = 8  # shades between the quiet colour and the accent: a small palette, bars of a few colours
HOURS = 24  # the day's hours, each a place on the card's chart


def _mix(a: Colour, b: Colour, t: float) -> Colour:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def _bar(rows: list[bytearray], left: int, right: int, tall: int, colour: int) -> None:
    """A bar from the pixel above the baseline up, its top corners rounded off a pixel."""
    height = len(rows)
    top = height - 2 - tall
    for y in range(top, height - 2):
        row = rows[y]
        for x in range(left, right):
            row[x] = colour
    if right - left >= 4 and tall >= 3:
        rows[top][left] = rows[top][right - 1] = 0


def draw(values: list[float], width: int, height: int, quiet: Colour, loud: Colour,
         base: Colour) -> tuple[list[Colour | None], list[bytes]]:
    """The chart as a palette and rows of indices into it (0: nothing drawn), width x height pixels:
    a bar a value, as wide as one another, a gap between them, on a baseline a pixel high."""
    palette: list[Colour | None] = [None, base]
    shade = {}
    rows = [bytearray(width) for _ in range(height)]
    for x in range(width):
        rows[height - 1][x] = 1  # the baseline
    if not values or width < len(values) * 2 or height < 4:
        return palette, [bytes(row) for row in rows]
    peak = max(values) or 1.0
    pitch = width / len(values)
    gap = max(1, round(pitch / 4))
    room = height - 3  # under the top, above the baseline and a pixel off it
    for n, value in enumerate(values):
        if value <= 0:
            continue
        level = round(value / peak * (LEVELS - 1))
        if level not in shade:
            shade[level] = len(palette)
            palette.append(_mix(quiet, loud, level / (LEVELS - 1)))
        _bar(rows, round(n * pitch) + gap // 2, round((n + 1) * pitch) - (gap - gap // 2),
             max(2, round(value / peak * room)), shade[level])
    return palette, [bytes(row) for row in rows]


def draw_hours(hours: list[float], width: int, height: int, quiet: Colour, now: Colour,
               floor: Colour) -> tuple[list[Colour | None], list[bytes]]:
    """Today by the hour: a place for each of the day's hours, as wide as one another, the hours gone
    by (hours: from midnight to now) on a floor a pixel high, a bar for each with use, the hour now in
    its own colour. In block characters only the latest hours fit; in pixels the whole day does."""
    palette: list[Colour | None] = [None, floor, quiet, now]
    rows = [bytearray(width) for _ in range(height)]
    if not hours or width < HOURS * 2 or height < 4:
        return palette, [bytes(row) for row in rows]
    pitch = width / HOURS
    gap = max(1, round(pitch / 4))
    for x in range(min(width, round(len(hours) * pitch))):
        rows[height - 1][x] = 1  # the floor under the hours gone by
    peak = max(hours) or 1.0
    room = height - 3
    for hour, used in enumerate(hours[:HOURS]):
        if used > 0:
            _bar(rows, round(hour * pitch) + gap // 2, round((hour + 1) * pitch) - (gap - gap // 2),
                 max(2, round(used / peak * room)), 3 if hour == len(hours) - 1 else 2)
    return palette, [bytes(row) for row in rows]


def _rounded_box(rows: list[bytearray], left: int, top: int, width: int, height: int, radius: int,
                 colour: int) -> None:
    """A box with its corners rounded off by radius pixels, clipped to the rows."""
    for dy in range(height):
        y = top + dy
        if not 0 <= y < len(rows):
            continue
        row = rows[y]
        # How far in the row starts at this height: in from a corner's circle near the top and bottom.
        edge = min(dy, height - 1 - dy)
        cut = 0
        if edge < radius:
            off = radius - edge - 0.5
            cut = radius - int((radius * radius - off * off) ** 0.5 + 0.5)
        for x in range(max(0, left + cut), min(len(row), left + width - cut)):
            row[x] = colour


WEEK_LEVELS = 6  # shades of the hours of the week, from a quarter of the way to the accent to all of it


def draw_week(slots: list[list[float]], cell: tuple[int, int], width: int, height: int, low: Colour, high: Colour,
              empty: Colour) -> tuple[list[Colour | None], list[bytes]]:
    """The hours of the week as tiles: a row of cells a day, three cells an hour (as the hours
    written above), a square tile over the first two, from low to high as more time was worked
    then; a dot where none was. Laid out as the block characters are, so the words fit either."""
    palette: list[Colour | None] = [None, empty]
    rows = [bytearray(width) for _ in range(height)]
    cw, ch = cell
    peak = max((v for row in slots for v in row), default=0)
    side = max(2, min(2 * cw - 4, ch - 4))
    shade: dict[int, int] = {}
    for day, hours in enumerate(slots[:7]):
        top = day * ch + (ch - side) // 2
        for hour, value in enumerate(hours[:24]):
            left = hour * 3 * cw + (2 * cw - side) // 2
            if value <= 0 or peak <= 0:
                dot = max(2, cw // 5)
                _rounded_box(rows, left + (side - dot) // 2, top + (side - dot) // 2, dot, dot, 0, 1)
                continue
            level = round(value / peak * (WEEK_LEVELS - 1))
            if level not in shade:
                shade[level] = len(palette)
                palette.append(_mix(low, high, 0.25 + 0.75 * level / (WEEK_LEVELS - 1)))
            _rounded_box(rows, left, top, side, side, max(1, side // 5), shade[level])
    return palette, [bytes(row) for row in rows]


def draw_bars(values: list[float], cell: tuple[int, int], width: int, height: int,
              colour: Colour) -> tuple[list[Colour | None], list[bytes]]:
    """A bar a row of cells, as long as its value against the largest, the whole width for that,
    its ends rounded; nothing for a value of nothing, a few pixels for any other."""
    palette: list[Colour | None] = [None, colour]
    rows = [bytearray(width) for _ in range(height)]
    ch = cell[1]
    peak = max(values, default=0)
    thick = max(2, round(ch * 0.6))
    for n, value in enumerate(values):
        if value <= 0 or peak <= 0:
            continue
        long = max(3, round(value / peak * width))
        _rounded_box(rows, 0, n * ch + (ch - thick) // 2, long, thick, min(thick // 3, long // 2), 1)
    return palette, [bytes(row) for row in rows]


class PixelPicture:
    """Mixed into a widget whose content is a picture in real pixels where the terminal draws them:
    it says what the picture shows (picture_key) and draws it (picture_rows); this keeps the picture
    with the host, draws it again only when that, its size or its ground changes (a fade, a theme, a
    hover), and takes it off when the widget goes."""

    # Here, not in __init__: a Sparkline sets its data there, and its watcher can draw already.
    picture: sixel.Picture | None = None
    drawn: tuple | None = None  # what the picture shows: no need to draw it again

    def start_pixels(self) -> bool:
        """The picture, where the terminal draws pixels: whether there is one."""
        if self.picture is not None:
            return True
        host = self.app if isinstance(self.app, sixel.SixelHost) else None
        if host is None or sixel.terminal() is None:
            return False
        self.picture, self.drawn = sixel.Picture(self, self.cells), None
        host.add_picture(self.picture)
        return True

    def stop_pixels(self) -> None:
        """No picture any more (a kitty one stays until taken off)."""
        if self.picture is not None and isinstance(self.app, sixel.SixelHost):
            self.app.remove_picture(self.picture)
        self.picture = self.drawn = None

    def on_unmount(self) -> None:
        self.stop_pixels()

    def cells(self) -> tuple[int, int, int, int] | None:
        region = self.content_region
        if not self.is_attached or region.width < 2 or region.height < 1:
            return None
        return region.x, region.y, region.width, region.height

    def notify_style_update(self) -> None:
        """Its colours as they are now: after the dashboard has faded in (drawn during the fade, the
        ground stayed a shade darker than around it), in another theme, under the pointer."""
        super().notify_style_update()
        if self.picture is not None:
            self.call_after_refresh(self.draw_picture)

    def on_resize(self) -> None:
        self.draw_picture()

    def shown(self, colour: Color) -> Colour:
        """A colour as the terminal shows it (sixel.as_shown)."""
        hex6 = sixel.as_shown(self, colour.hex6)
        return int(hex6[1:3], 16), int(hex6[3:5], 16), int(hex6[5:7], 16)

    def picture_key(self) -> tuple:
        raise NotImplementedError

    def picture_rows(self, width: int, height: int) -> tuple[list[Colour | None], list[bytes]]:
        raise NotImplementedError

    def draw_picture(self) -> None:
        found = sixel.terminal()
        cells = self.cells()
        if self.picture is None or found is None or cells is None:
            return
        cell_w, cell_h = found.cell
        width = cells[2] * cell_w
        height = cells[3] * cell_h // 6 * 6 if not found.kitty else cells[3] * cell_h  # whole Sixel bands
        ground = self.shown(self.background_colors[1])
        key = (self.picture_key(), width, height, ground, found.kitty)
        if key == self.drawn:
            return
        self.drawn = key
        palette, rows = self.picture_rows(width, height)
        self.picture.data = (sixel.encode_kitty(palette, rows, self.picture.number) if found.kitty
                             else sixel.encode_indexed(palette, rows, ground))
        self.picture.fresh = True
        self.refresh()  # its blank cells written, the picture goes right after them


class TokenChart(PixelPicture, Sparkline):
    """The 14-day token chart: Textual's Sparkline, or a picture of it in real pixels."""

    def on_mount(self) -> None:
        if self.start_pixels():
            self.refresh()

    def watch_data(self) -> None:
        self.draw_picture()

    def colours(self) -> tuple[Colour, Colour, Colour]:
        """The quiet and the loud colour of the bars and the baseline's, as the terminal shows them:
        the sparkline's own (its CSS), so either way the chart looks one."""
        _, back = self.background_colors
        quiet = back + self.get_component_styles("sparkline--min-color").color
        loud = back + self.get_component_styles("sparkline--max-color").color
        return self.shown(quiet), self.shown(loud), self.shown(back.blend(quiet, 0.5))

    def picture_key(self) -> tuple:
        return tuple(round(float(v)) for v in (self.data or [])), self.colours()

    def picture_rows(self, width: int, height: int) -> tuple[list[Colour | None], list[bytes]]:
        return draw([float(v) for v in (self.data or [])], width, height, *self.colours())

    def render(self):
        if self.picture is not None:
            return Text("")  # the picture goes over these cells
        return super().render()
