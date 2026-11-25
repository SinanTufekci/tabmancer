"""Charts as pictures, with no UI at all: a palette and rows of indices into it, width x height
pixels, for a host to show (the terminal's Sixel or kitty graphics through tokenchart.PixelPicture, or
a desktop window's image). The 14-day token chart (draw), the TOKENS TODAY card's hours (draw_hours),
the hours of the week (draw_week) and the stats' bars (draw_bars).
"""

from __future__ import annotations

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
