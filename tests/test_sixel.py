"""Bit in real pixels: asking the terminal, the Sixel images themselves, and sending them right
after Textual's updates, never over a dialog."""

import re
from types import SimpleNamespace

import pytest
from rich.color import ColorSystem
from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from textual._compositor import ChopsUpdate, LayoutUpdate
from textual.geometry import Region, Size
from textual.strip import Strip

from tabmancer import bitart, bitmoves, sixel
from tabmancer import mascot as M
from tabmancer.app import SessionPicker


def decode(data: str) -> tuple[list[list[tuple[int, int, int]]], int, int]:
    """A Sixel image back to its pixels (as percent colours), to check the encoding."""
    body = data[data.index("q") + 1:data.rindex("\x1b\\")]
    width, height = (int(v) for v in re.match(r'"\d+;\d+;(\d+);(\d+)', body).groups())
    body = re.sub(r'^"[\d;]+', "", body)
    colours = {int(n): (int(r), int(g), int(b)) for n, r, g, b in re.findall(r"#(\d+);2;(\d+);(\d+);(\d+)", body)}
    body = re.sub(r"#\d+;2;\d+;\d+;\d+", "", body)
    grid = [[None] * width for _ in range(height)]
    x = top = 0
    colour = None
    for token in re.findall(r"#\d+|!\d+[?-~]|[?-~$-]", body):
        if token.startswith("#"):
            colour = colours[int(token[1:])]
        elif token == "$":
            x = 0
        elif token == "-":
            x, top = 0, top + 6
        else:
            count, char = (int(token[1:-1]), token[-1]) if token.startswith("!") else (1, token)
            bits = ord(char) - 63
            for i in range(count):
                for k in range(6):
                    if bits >> k & 1 and top + k < height:
                        grid[top + k][x + i] = colour
            x += count
    return grid, width, height


def percent(colour):
    return tuple(c * 100 // 255 for c in colour)


def test_an_image_decodes_back_to_its_pixels():
    frame = bitmoves.frame("cheer", bitmoves.Context(beat=5, frame=5))
    pixels = bitart.frame_pixels(frame)
    back = (16, 16, 16)
    grid, width, height = decode(sixel.encode(pixels, back))
    assert (width, height) == bitart.size() == (110, 78)
    assert all(grid[y][x] == percent(pixels[y][x] or back) for y in range(height) for x in range(width))


def test_more_colours_than_registers_take_the_nearest():
    rows = [[(i, 255 - i, (i * 7) % 256) for i in range(300)]] * 6
    data = sixel.encode(rows, (0, 0, 0))
    assert len(re.findall(r"#\d+;2;", data)) == sixel.MOST
    grid, _, _ = decode(data)
    assert all(cell is not None for cell in grid[0])


@pytest.mark.parametrize(("answer", "found"), [
    ("\x1b[?61;6;7;22;23;24;28;32;42c", False), ("\x1b[?62;4;9;22c", True), ("garbage", False)])
def test_sixel_is_in_the_device_attributes_or_not(answer, found):
    assert sixel.sixel_in(answer) is found


def test_the_cell_size_comes_from_the_answer():
    assert sixel.cell_in("\x1b[6;20;10t\x1b[?62;4c") == (10, 20)
    assert sixel.cell_in("\x1b[6;2;1t") is None  # nonsense: no
    assert sixel.cell_in("") is None


@pytest.mark.parametrize(("env", "wt", "answer", "window", "cell"), [
    ("0", True, "\x1b[6;20;10t\x1b[?62;4c", None, None),           # turned off
    (None, True, "\x1b[?62;4c", None, (10, 20)),                     # Windows Terminal: its own cell size
    (None, False, "\x1b[?62;4c", None, None),                        # Sixel, but no cell size known: no
    (None, False, "\x1b[?62;4c", (9, 19), (9, 19)),                  # ...known from the window's pixels
    (None, False, "\x1b[6;16;8t\x1b[?62;4c", (9, 19), (8, 16)),      # another terminal, saying its size
    (None, False, "\x1b[6;16;8t\x1b[?62;22c", None, None),           # its size, but no Sixel
    ("1", False, "", None, (10, 20)),                                # turned on by hand
])
def test_the_terminal_is_asked_once(monkeypatch, env, wt, answer, window, cell):
    monkeypatch.setattr(sixel, "window_cell", lambda: window)
    if env is None:
        monkeypatch.delenv("TABMANCER_SIXEL", raising=False)
    else:
        monkeypatch.setenv("TABMANCER_SIXEL", env)
    if wt:
        monkeypatch.setenv("WT_SESSION", "x")
    else:
        monkeypatch.delenv("WT_SESSION", raising=False)
    asked = []
    monkeypatch.setattr(sixel, "_ask", lambda question: asked.append(question) or answer)
    found = sixel.probe()
    assert (found.cell if found else None) == cell
    sixel.probe()
    assert len(asked) <= 1  # once


def test_a_font_made_bigger_or_smaller_measures_the_cell_again(monkeypatch):
    monkeypatch.setattr(sixel, "window_cell", lambda: None)
    monkeypatch.setattr(sixel, "_terminal", None)
    assert not sixel.resized(100, 40, (900, 760))  # no Sixel: nothing to measure
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal((10, 20), sync=True))
    assert sixel.resized(100, 40, (900, 760)) and sixel.terminal() == sixel.Terminal((9, 19), sync=True)
    assert not sixel.resized(100, 40, (905, 770))  # the same cell
    assert not sixel.resized(100, 40) and sixel.terminal().cell == (9, 19)  # nothing said: it stays
    monkeypatch.setattr(sixel, "window_cell", lambda: (12, 24))  # what the system has
    assert sixel.resized(80, 30) and sixel.terminal().cell == (12, 24)
    assert not sixel.resized(80, 30, (80, 30))  # one pixel a cell: nonsense, not taken


def test_the_feet_stand_inside_bits_place_whatever_the_cell():
    assert bitart.scale(bitart.WT_CELL) == (1.0, 1.0)  # Windows Terminal: as drawn
    for cell in [(width, height) for width in range(5, 17) for height in range(10, 33)]:
        assert bitart.GROUND * bitart.scale(cell)[1] <= bitart.size(cell)[1], cell


@pytest.mark.parametrize("cell", [(6, 13), (9, 19), (14, 28)])
def test_bit_is_drawn_at_other_cell_sizes(cell):
    width, height = bitart.size(cell)
    for action in ("walk", "cheer", "wave", "sleep"):
        robot = M.Mascot(seed=3, fine=True)
        robot.start(action, 10 ** 6)
        robot.burst(["#ffffff"], 8)
        for _ in range(20):
            robot.advance(60)
            pixels = bitart.frame_pixels(robot.hires(0, 0, cell), cell)
            assert len(pixels) == height and all(len(row) == width for row in pixels), action
            assert any(px is not None for px in pixels[-3]), action  # down to its feet


class Spans:
    def __init__(self, spans):
        self.spans = spans


class Layout:
    def __init__(self, region):
        self.region = region


def test_an_update_wrote_over_the_cells_or_not():
    cells = (10, 5, 11, 4)
    assert sixel.wrote_over(Spans([(6, 0, 12)]), cells)
    assert not sixel.wrote_over(Spans([(6, 0, 9), (2, 10, 40)]), cells)
    assert sixel.wrote_over(Layout(Region(0, 0, 80, 24)), cells)
    assert not sixel.wrote_over(Layout(Region(0, 0, 80, 4)), cells)
    assert not sixel.wrote_over(None, cells)
    assert sixel.wrote_over(object(), cells)  # an update of another kind: send it again


@pytest.fixture
def pixels_on(monkeypatch):
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))


async def test_bit_in_real_pixels_leaves_its_cells_blank_and_sends_its_picture(fake_home, pixels_on):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.6)
        bit = app.mascot
        assert bit.picture is not None and bit.picture.data.startswith("\x1bP")
        x, y, width, height = bit.picture_cells()
        region = bit.region
        assert (width, height) == (M.WIDTH, M.ROWS) and region.x <= x and x + width <= region.right
        rows = [line.plain for line in bit.lines(region.width)]
        first = x - region.x
        assert all(row[first:first + M.WIDTH].strip() == "" for row in rows)  # no octants under the picture

        written = []
        write = app._driver.write
        app._driver.write = written.append

        def pictures():  # Textual writes through the driver too (the pointer's shape): the pictures only
            return [w for w in written if '\x1bP' in w]

        try:
            bit.picture.fresh = True
            app.send_pictures(None)
            assert len(pictures()) == 1 and pictures()[0].startswith(f"\x1b7\x1b[{y + 1};{x + 1}H\x1bP")
            app.send_pictures(None)  # nothing new, nothing written over: not again
            assert len(pictures()) == 1
            app.send_pictures(Spans([(y + 1, x, x + 2)]))  # Textual wrote over its cells: again
            assert len(pictures()) == 2
            await pilot.press("question_mark")  # a dialog in front: Bit's picture waits
            await pilot.pause(0.2)
            bit.picture.fresh = True
            app.send_pictures(None)
            assert len(pictures()) == 2
        finally:
            app._driver.write = write


def cells_written(update) -> set[tuple[int, int]]:
    """The cells an update of Textual's writes, read off what it writes to the terminal."""
    out = update.render_segments(Console(force_terminal=True, color_system="truecolor"))
    found, row, col = set(), 0, 0
    for move, text in re.findall(r"\x1b\[(\d+;\d+)H|((?:\x1b\[[\d;]*m|[^\x1b\n])+)", out):
        if move:
            row, col = (int(v) - 1 for v in move.split(";"))
            continue
        for _ in re.sub(r"\x1b\[[\d;]*m", "", text):
            found.add((row, col))
            col += 1
    return found


def test_what_a_partial_update_writes_is_what_wrote_over_goes_by():
    # Textual writes a widget's piece of a line from its start, though the span starts further on;
    # another widget's piece on the line is a piece of its own.
    style = Style(bgcolor="#101010")
    chops = [{0: Strip([Segment(" " * 40, style)]), 40: Strip([Segment(" " * 20, style)])}]
    cells = (10, 0, 11, 1)
    found = []
    for spans in ([(0, 30, 31)], [(0, 0, 10)], [(0, 45, 50)], [(0, 2, 3), (0, 50, 51)]):
        update = ChopsUpdate(chops, spans, [[40, 60]])
        assert sixel.wrote_over(update, cells) == any(10 <= x < 21 for _, x in cells_written(update)), spans
        found.append(sixel.wrote_over(update, cells))
    assert found == [True, False, False, False]


async def test_bits_cells_always_show_its_picture_after_textuals_updates(fake_home, pixels_on, monkeypatch):
    # A model of the terminal: which picture each cell shows. Textual's updates wipe the cells
    # they write; a picture fills its own. Bit walks left and right, cell by cell.
    shows: dict[tuple[int, int], tuple[int, int, str]] = {}
    checked = []

    def show(picture):
        x, y, width, height = picture.place()
        for r in range(y, y + height):
            for c in range(x, x + width):
                shows[(r, c)] = (x, y, picture.data)

    def now(picture):
        show(picture)
        picture.fresh = False
        return True

    display = SessionPicker._display

    def update(self, screen, renderable):
        display(self, screen, renderable)  # headless: written nowhere, so the model plays the terminal
        if not isinstance(renderable, ChopsUpdate) or self.mascot.picture is None:
            return
        for cell in cells_written(renderable):
            shows.pop(cell, None)
        written = []
        write, self._driver.write = self._driver.write, written.append
        try:
            self.send_pictures(renderable)
        finally:
            self._driver.write = write
        if any("\x1bP" in w for w in written):
            show(self.mascot.picture)
        cells = self.mascot.picture_cells()
        if cells and self.mascot.picture.data:
            x, y, width, height = cells
            assert all(shows.get((r, c)) == (x, y, self.mascot.picture.data)
                       for r in range(y, y + height) for c in range(x, x + width))
            checked.append(x)

    monkeypatch.setattr(SessionPicker, "_display", update)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        monkeypatch.setattr(app, "send_now", now)
        await pilot.pause(0.6)
        bit = app.mascot
        for direction in (-1, 1):
            bit.bubbles.clear()
            bit.start("walk", 10 ** 6)
            bit.x, bit.direction = 120.0, direction
            bit.tick()  # moved by hand: drawn there at once, as a tick of its own would
            start = len(set(checked))
            for _ in range(50):
                await pilot.pause(0.1)
                if len(set(checked)) >= start + 4:
                    break
        assert len(set(checked)) >= 6  # it walked cells both ways
        bit.x = 20.0  # then near the left, so what it says goes on its right
        bit.tick()
        await pilot.pause(0.3)
        monkeypatch.setattr(bit, "advance", lambda width=None: None)  # standing still: the same picture
        still = len(checked)
        for n in range(4):
            bit.say(f"note {n}")  # written right of it: Textual writes the line from the strip's start
            await pilot.pause(0.2)
        assert len(checked) > still


async def test_after_a_resize_that_changes_the_cell_bit_is_drawn_to_the_new_one(fake_home, pixels_on, monkeypatch):
    monkeypatch.setattr(sixel, "window_cell", lambda: None)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.5)
        bit = app.mascot
        assert '"1;1;110;78' in bit.picture.data
        await pilot.resize_terminal(150, 38)  # the same cell (nothing says otherwise): drawn as it was
        await pilot.pause(0.2)
        assert '"1;1;110;78' in bit.picture.data
        x, y, _, _ = bit.picture_cells()
        written = set()
        display = app._display

        def record(screen, renderable):
            if isinstance(renderable, ChopsUpdate):
                written.update(cells_written(renderable))
            elif isinstance(renderable, LayoutUpdate):  # the whole screen, or a region of it
                r = renderable.region
                written.update((row, col) for row in range(r.y, r.bottom) for col in range(r.x, r.right))
            display(screen, renderable)

        app._display = record
        monkeypatch.setattr(sixel, "window_cell", lambda: (9, 19))  # a smaller font, the same rows and columns (xterm)
        await pilot.resize_terminal(150, 38)
        assert '"1;1;110;78' not in bit.picture.data  # the old size is not sent again
        for _ in range(40):
            await pilot.pause(0.05)
            if bit.picture.data:
                break
        assert '"1;1;99;72' in bit.picture.data
        below = {(row, col) for row in range(y + 4, y + 7) for col in range(x, x + 14)}
        assert below <= written  # where the old, bigger picture reached, text was written again


@pytest.mark.parametrize(("system", "register"), [
    (ColorSystem.TRUECOLOR, "#0;2;4;4;4"),   # the list's own #0b0b0b
    (ColorSystem.EIGHT_BIT, "#0;2;3;3;3"),   # as 256 colours have it: #080808
    (ColorSystem.STANDARD, "#0;2;0;0;0"),    # as 16 do (xterm, saying nothing of true colour): black
])
async def test_bits_picture_takes_the_strips_shade_as_the_terminal_shows_it(fake_home, pixels_on, system, register):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        for _ in range(60):  # the dashboard fades in: until it has
            await pilot.pause(0.05)
            if app.mascot.backdrop().lower() == "#0b0b0b":
                break
        app.console._color_system = system
        bit = app.mascot
        bit.drawn = None
        bit.update_picture(bit.size.width)
        assert bit.backdrop().lower() == "#0b0b0b" and f'"1;1;110;78{register}#' in bit.picture.data


def test_a_new_picture_never_wipes_bits_cells_only_the_ones_it_left(monkeypatch):
    # Written blank again, Bit's cells lose its picture until it is sent again: that blinked.
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    robot = M.Mascot(seed=1, fine=True)
    robot.picture = sixel.Picture(robot, lambda: None)
    refreshed = []
    monkeypatch.setattr(robot, "refresh", lambda *regions: refreshed.append(regions))
    monkeypatch.setattr(robot, "backdrop", lambda: "#101010")
    robot.x = 10.0
    robot.update_picture(60)
    robot.start("wave", 10 ** 6)
    robot.advance(60)
    robot.x = 10.0
    robot.update_picture(60)  # a new picture in the same cells: nothing written over them
    assert refreshed == []
    robot.x = 14.0  # two cells on: the two it left, and only those
    robot.update_picture(60)
    assert refreshed == [(Region(5, 0, 2, M.ROWS),)]
    data = robot.picture.data
    robot.update_picture(60)  # nothing changed: not drawn or encoded again
    assert robot.picture.data is data and len(refreshed) == 1


def test_the_strip_is_repainted_beside_bit_not_under_it(monkeypatch):
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    robot = M.Mascot(seed=1, fine=True)
    robot.picture = sixel.Picture(robot, lambda: None)
    robot.x = 20.0
    assert robot.beside(60) == [Region(0, 0, 10, M.ROWS), Region(10 + M.WIDTH, 0, 60 - 10 - M.WIDTH, M.ROWS)]
    robot.x = 0.0
    assert robot.beside(60) == [Region(M.WIDTH, 0, 60 - M.WIDTH, M.ROWS)]


class Owner:
    is_attached = True

    def __init__(self, screen):
        self.screen = screen


class Screen:
    def __init__(self):
        self.owner = None

    def get_widget_at(self, x, y):
        return self.owner, None


class Driver:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass


class Host(sixel.SixelHost):
    is_headless = False
    _sync_available = False
    console = SimpleNamespace(_color_system=None)  # true colour: Bit's picture keeps the strip's own shade

    def __init__(self):
        self.screen = Screen()
        self._driver = Driver()
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1


def test_a_picture_textual_writes_over_next_goes_with_that_update_not_also_at_once(monkeypatch):
    # Textual writes a line of the strip from its start to the end of what is repainted on it:
    # a cell repainted right of Bit runs over its picture, one left of it does not.
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    host = Host()
    robot = M.Mascot(seed=1, fine=True)
    host.screen.owner = robot
    monkeypatch.setattr(M.Mascot, "is_attached", property(lambda self: True))
    monkeypatch.setattr(M.Mascot, "app", property(lambda self: host))
    monkeypatch.setattr(M.Mascot, "screen", property(lambda self: host.screen))
    monkeypatch.setattr(robot, "refresh", lambda *regions: None)
    monkeypatch.setattr(robot, "backdrop", lambda: "#101010")
    robot.picture = sixel.Picture(robot, lambda: (robot.cell_x(60), 0, M.WIDTH, M.ROWS))
    robot.start("walk", 10 ** 6)

    def sent_at_once(x: float, *repainted: Region) -> bool:
        before = len(host._driver.written)
        robot.advance(60)
        robot.x = x
        robot.update_picture(60, list(repainted))
        return len(host._driver.written) > before

    assert sent_at_once(20.0)                                   # where it was: over the old one
    assert sent_at_once(22.0)                                   # a cell right: the cell it left is left of it
    assert not sent_at_once(20.0) and robot.picture.fresh       # a cell left: the cell it left is right of it
    robot.picture.fresh = False
    assert sent_at_once(20.0, Region(0, 0, 10, M.ROWS))         # what is left of it repainted
    assert not sent_at_once(20.0, Region(30, 0, 30, M.ROWS))    # right of it: with Textual's update


def test_a_picture_drawn_to_the_old_cell_size_is_not_sent_again(monkeypatch):
    # Bigger than its cells after a smaller font, it would stay on beside them until drawn anew.
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    monkeypatch.setattr(sixel, "window_cell", lambda: None)
    host = Host()
    host.screen.owner = Owner(host.screen)
    picture = sixel.Picture(host.screen.owner, lambda: (4, 6, M.WIDTH, M.ROWS))
    picture.data = "\x1bPq-\x1b\\"
    host.add_picture(picture)
    host.on_resize(SimpleNamespace(size=Size(100, 40), pixel_size=Size(1000, 800)))  # the same cell
    assert picture.data and host.refreshed == 0
    host.on_resize(SimpleNamespace(size=Size(100, 40), pixel_size=Size(900, 760)))
    assert picture.data == "" and host.refreshed == 1  # and the screen written again whole
    picture.fresh = True
    host.send_pictures(None)
    assert host._driver.written == []


@pytest.mark.parametrize("sync", [True, False])
def test_a_picture_sent_at_once_is_one_synchronized_frame_where_the_terminal_has_them(monkeypatch, sync):
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL, sync=sync))
    host = Host()
    owner = host.screen.owner = Owner(host.screen)
    picture = sixel.Picture(owner, lambda: (4, 6, M.WIDTH, M.ROWS))
    picture.data = "\x1bPq-\x1b\\"
    host.add_picture(picture)
    assert host.send_now(picture)
    (written,) = host._driver.written
    assert written.startswith(sixel.BEGIN if sync else "\x1b7\x1b[7;5H") and written.endswith(sixel.END if sync else "\x1b8")
    host.screen.owner = object()  # something else in front of it: kept for later
    picture.fresh = True
    assert not host.send_now(picture) and picture.fresh


def test_synchronized_output_comes_from_the_answer():
    assert sixel.sync_in("\x1b[?2026;2$y\x1b[?62;4c")
    assert not sixel.sync_in("\x1b[?2026;0$y\x1b[?62;4c")
    assert not sixel.sync_in("\x1b[?62;4c")


def test_between_cells_bit_moves_a_pixel_at_a_time(monkeypatch):
    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    robot = M.Mascot(seed=1, fine=True)
    robot.picture = sixel.Picture(robot, lambda: None)
    monkeypatch.setattr(robot, "refresh", lambda *regions: None)
    monkeypatch.setattr(robot, "backdrop", lambda: "#101010")
    robot.start("idle", 10 ** 6)
    shown = []
    for step in range(5):
        robot.x = 10.0 + step * 0.4  # two pixels a step: still in the same cell
        robot.update_picture(60)
        shown.append(robot.drawn[3])  # the shift within the cells
    assert shown == [0, 2, 4, 6, 8] and robot.cell_x(60) == 5


def test_every_move_draws_inside_its_place():
    width, height = bitart.size()
    for action in bitmoves.TRAITS:
        robot = M.Mascot(seed=3, fine=True)
        robot.start(action, 10 ** 6)
        if action == "call":
            robot.waiting = 1
        for _ in range(60):
            robot.advance(60)
            pixels = bitart.frame_pixels(robot.hires())
            assert len(pixels) == height and all(len(row) == width for row in pixels), action
            assert any(px is not None for row in pixels for px in row), action


def test_a_walk_is_drawn_once_and_then_kept():
    bitart.drawn.cache_clear()
    robot = M.Mascot(seed=2, fine=True)
    robot.start("walk", 10 ** 6)
    for _ in range(240):
        robot.advance(80)
        bitart.figure(robot.hires().pose)
    info = bitart.drawn.cache_info()
    assert info.hits > info.misses * 3  # the steps come round again


def test_bit_does_many_things_and_calls_three_ways():
    robot = M.Mascot(seed=5, fine=True)
    idle = set()
    for _ in range(200):
        robot.left = 0
        robot.advance(80)
        idle.add(robot.action)
    assert len(idle) >= 10 and {"coffee", "stretch", "whistle"} & idle
    robot.set_mood(working=2, waiting=0)
    working = set()
    for _ in range(200):
        robot.left = 0
        robot.advance(80)
        working.add(robot.action)
    assert {"work", "think", "read"} <= working and "dance" not in working
    robot.set_mood(working=0, waiting=1)
    ways = set()
    for _ in range(M.CALL_EVERY * 3):
        robot.advance(80)
        ways.add(bitmoves.CALLS[robot.calls % len(bitmoves.CALLS)])
    assert ways == set(bitmoves.CALLS)


async def test_b_shows_bit_in_pixels_then_blocks_then_sends_it_away_and_remembers(fake_home, pixels_on):
    from tabmancer.config import read_json, write_json

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.5)
        bit = app.mascot
        assert bit.picture is not None
        await pilot.press("b")
        assert bit.picture is None and not bit.has_class("hidden") and not list(app._sixel_pictures())
        await pilot.pause(0.2)
        assert any(line.plain.strip() for line in bit.lines(bit.size.width))  # in block characters
        await pilot.press("b")
        assert bit.has_class("hidden") and read_json("config.json")["mascot"] == "away"
        await pilot.press("b")
        assert bit.picture is not None and not bit.has_class("hidden")
        await pilot.press("b")
        assert read_json("config.json")["mascot"] == "blocks"
    write_json("config.json", {"mascot": "blocks"})
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.3)
        assert app.mascot.picture is None and not app.mascot.has_class("hidden")  # as chosen last time
