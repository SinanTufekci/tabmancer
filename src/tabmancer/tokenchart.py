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
