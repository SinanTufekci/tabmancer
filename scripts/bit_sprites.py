"""Bit for the README's moving pictures: the dashboard's own Bit (mascot.Mascot), driven through a
list of moves without a screen, every frame drawn in real pixels as the dashboard draws it; and
those frames as an SVG piece, a sprite sheet that moves frame by frame.

A frame is the 110x78 picture Windows Terminal shows. Frames that come round again (sitting,
dozing, breathing) are kept once: the sheet holds each different one, in rows of SHEET_COLUMNS,
as a PNG with a see-through ground, and the SVG shows one at a time (discrete steps, run-length
coded), moved along as Bit walks.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from tabmancer import bitart, card
from tabmancer.mascot import FPS, GREEN, Mascot

CELL = bitart.WT_CELL
FRAME_W, FRAME_H = bitart.size(CELL)
SHEET_COLUMNS = 10
GUTTER = 2  # see-through pixels between frames: scaled up, a frame's clip took in a sliver of the next


@dataclass
class Move:
    action: str
    frames: int
    direction: int = 0        # walking: right (1) or left (-1)
    jumps: int = 0            # hops
    eyes: str = ""            # a look held through the move
    burst: bool = False       # confetti


def play(moves: list[Move], accent: str, seed: int = 7) -> list[tuple[int, bytes]]:
    """Every frame of the moves: (Bit's cell along the strip, the frame's key into kept())."""
    bit = Mascot(seed=seed, accent=accent, fine=True)
    shown: list[tuple[int, tuple]] = []
    for move in moves:
        bit.start(move.action, 10 ** 6)  # the moves below decide when each ends
        if move.direction:
            bit.direction = move.direction
        bit.jumps_left = move.jumps
        if move.eyes:
            bit.look(move.eyes, move.frames)
        if move.burst:
            bit.burst([GREEN, accent, "#ffffff", "#7ee0a1"], 14)
        for _ in range(move.frames):
            bit.advance(200)  # room enough: Bit turns where the moves say, not at an edge
            cell = int(bit.x // 2)
            shift = round((bit.x / 2 - cell) * CELL[0])
            frame = bit.hires(cell, shift, CELL)
            shown.append((cell, (frame.pose, tuple((e.kind, round(e.x), round(e.y), e.size, round(e.alpha, 1),
                                                    e.colour) for e in frame.effects), shift)))
    return shown


@dataclass
class Sheet:
    png: bytes
    width: int
    height: int
    places: list[tuple[int, int]]  # each frame shown: its top left on the sheet
    cells: list[int]               # ...and Bit's cell along the strip then


def sheet(shown: list[tuple[int, tuple]]) -> Sheet:
    """The different frames once each, on one sheet with one palette (a see-through ground)."""
    order: dict[tuple, int] = {}
    for _, key in shown:
        order.setdefault(key, len(order))
    rows_down = (len(order) + SHEET_COLUMNS - 1) // SHEET_COLUMNS
    pitch_x, pitch_y = FRAME_W + GUTTER, FRAME_H + GUTTER
    canvas = card.Canvas(SHEET_COLUMNS * pitch_x, rows_down * pitch_y, (0, 0, 0))
    for (pose, effects, shift), n in order.items():
        frame = bitart.Frame(pose, [bitart.Effect(*e) for e in effects])
        palette, rows = bitart.indexed(frame, CELL, shift)
        canvas.blit((n % SHEET_COLUMNS) * pitch_x, (n // SHEET_COLUMNS) * pitch_y, palette, rows)
    places = [((order[key] % SHEET_COLUMNS) * pitch_x, (order[key] // SHEET_COLUMNS) * pitch_y) for _, key in shown]
    data = card.png(canvas.width, canvas.height, canvas.palette, canvas.rows, clear=True)
    return Sheet(data, canvas.width, canvas.height, places, [cell for cell, _ in shown])


def steps(values: list[str], seconds: float) -> str:
    """values one after the other, a frame each, as a discrete SMIL animation's values and
    keyTimes: a value held over frames written once."""
    kept, times = [], []
    for n, value in enumerate(values):
        if not kept or value != kept[-1]:
            kept.append(value)
            times.append(n / len(values))
    return (f'values="{";".join(kept)}" keyTimes="{";".join(f"{t:.5f}".rstrip("0").rstrip(".") or "0" for t in times)}" '
            f'calcMode="discrete" dur="{seconds:g}s" repeatCount="indefinite"')


def svg(shown: list[tuple[int, tuple]], x: float, y: float, scale: float, sprite_id: str = "bit") -> tuple[str, str, float]:
    """Bit's frames as an SVG piece at (x, y), each frame scale times its pixels: (the defs, the
    piece, the loop's seconds). The piece moves along as Bit walks, a cell's pixels at a time."""
    found = sheet(shown)
    seconds = len(shown) / FPS
    home = found.cells[0]
    walk = steps([f"{(cell - home) * CELL[0] * scale:g} 0" for cell in found.cells], seconds)
    flip = steps([f"{-left} {-top}" for left, top in found.places], seconds)
    picture = base64.b64encode(found.png).decode()
    defs = (f'<clipPath id="{sprite_id}-frame"><rect width="{FRAME_W}" height="{FRAME_H}"/></clipPath>')
    piece = (f'<g transform="translate({x:g} {y:g})"><g><animateTransform attributeName="transform" type="translate" '
             f'{walk}/><g transform="scale({scale:g})"><g clip-path="url(#{sprite_id}-frame)"><g>'
             f'<animateTransform attributeName="transform" type="translate" {flip}/>'
             f'<image width="{found.width}" height="{found.height}" href="data:image/png;base64,{picture}" '
             f'style="image-rendering:optimizeSpeed;image-rendering:pixelated"/></g></g></g></g></g>')
    return defs, piece, seconds
