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

# The drawing itself is in pixelcharts.py, UI-free: named here for the widgets and older callers.
from .pixelcharts import HOURS, LEVELS, WEEK_LEVELS, draw, draw_bars, draw_hours, draw_week  # noqa: F401


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
