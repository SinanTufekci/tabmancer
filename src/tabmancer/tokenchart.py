"""The sidebar's token chart in real pixels, where the terminal draws them (Sixel or kitty graphics,
as Bit is): a bar a day, every bar as wide as the others with a gap between them, rising from a
baseline, from the quiet colour to the accent as it nears the busiest day. Elsewhere it is Textual's
sparkline in block characters, whose days take one cell or two as the width shares out.

Its cells are left blank and its picture is sent over them by the host (sixel.SixelHost): drawn
again only when the numbers, its size or the colours change.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Sparkline

from . import sixel
from .bitart import Colour

LEVELS = 8  # shades between the quiet colour and the accent: a small palette, bars of a few colours


def _mix(a: Colour, b: Colour, t: float) -> Colour:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


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
        left, right = round(n * pitch) + gap // 2, round((n + 1) * pitch) - (gap - gap // 2)
        tall = max(2, round(value / peak * room))
        level = round(value / peak * (LEVELS - 1))
        if level not in shade:
            shade[level] = len(palette)
            palette.append(_mix(quiet, loud, level / (LEVELS - 1)))
        colour = shade[level]
        top = height - 2 - tall
        for y in range(top, height - 2):
            row = rows[y]
            for x in range(left, right):
                row[x] = colour
        if right - left >= 4 and tall >= 3:  # the top corners rounded off a pixel
            rows[top][left] = rows[top][right - 1] = 0
    return palette, [bytes(row) for row in rows]


class TokenChart(Sparkline):
    """The 14-day token chart: Textual's Sparkline, or a picture of it in real pixels."""

    # Here, not in __init__: the Sparkline sets its data there, and its watcher (watch_data) runs.
    picture: sixel.Picture | None = None
    drawn: tuple | None = None  # what the picture shows: no need to draw it again

    def on_mount(self) -> None:
        host = self.app if isinstance(self.app, sixel.SixelHost) else None
        if host is not None and sixel.terminal() is not None:
            self.picture = sixel.Picture(self, self.cells)
            host.add_picture(self.picture)
            self.refresh()

    def on_unmount(self) -> None:
        """A kitty picture stays until taken off: not after the dashboard."""
        if self.picture is not None and isinstance(self.app, sixel.SixelHost):
            self.app.remove_picture(self.picture)

    def cells(self) -> tuple[int, int, int, int] | None:
        region = self.content_region
        if not self.is_attached or region.width < 2 or region.height < 1:
            return None
        return region.x, region.y, region.width, region.height

    def watch_data(self) -> None:
        self.draw_picture()

    def notify_style_update(self) -> None:
        """Its colours as they are now: after the dashboard has faded in (drawn during the fade, the
        ground stayed a shade darker than the sidebar), or in another theme."""
        super().notify_style_update()
        if self.picture is not None:
            self.call_after_refresh(self.draw_picture)

    def on_resize(self) -> None:
        self.draw_picture()

    def colours(self) -> tuple[Colour, Colour, Colour, Colour]:
        """The quiet and the loud colour of the bars, the baseline's, and the ground behind them, as
        the terminal shows them: the sparkline's own (its CSS), so either way the chart looks one."""
        _, back = self.background_colors
        quiet = back + self.get_component_styles("sparkline--min-color").color
        loud = back + self.get_component_styles("sparkline--max-color").color
        shown = [sixel.as_shown(self, colour.hex6) for colour in (quiet, loud, back.blend(quiet, 0.5), back)]
        return tuple(tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in shown)  # type: ignore[return-value]

    def draw_picture(self) -> None:
        found = sixel.terminal()
        cells = self.cells()
        if self.picture is None or found is None or cells is None:
            return
        cell_w, cell_h = found.cell
        width = cells[2] * cell_w
        height = cells[3] * cell_h // 6 * 6 if not found.kitty else cells[3] * cell_h  # whole Sixel bands
        values = [float(v) for v in (self.data or [])]
        quiet, loud, base, ground = self.colours()
        key = (tuple(round(v) for v in values), width, height, quiet, loud, base, ground, found.kitty)
        if key == self.drawn:
            return
        self.drawn = key
        palette, rows = draw(values, width, height, quiet, loud, base)
        self.picture.data = (sixel.encode_kitty(palette, rows, self.picture.number) if found.kitty
                             else sixel.encode_indexed(palette, rows, ground))
        self.picture.fresh = True
        self.refresh()  # its blank cells written, the picture goes right after them

    def render(self):
        if self.picture is not None:
            return Text("")  # the picture goes over these cells
        return super().render()
