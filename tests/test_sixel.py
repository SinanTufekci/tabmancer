"""Bit in real pixels: asking the terminal, the Sixel images themselves, and sending them right
after Textual's updates, never over a dialog."""

import re

import pytest
from textual.geometry import Region

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


@pytest.mark.parametrize(("env", "wt", "answer", "cell"), [
    ("0", True, "\x1b[6;20;10t\x1b[?62;4c", None),           # turned off
    (None, True, "\x1b[?62;4c", (10, 20)),                     # Windows Terminal: its own cell size
    (None, False, "\x1b[?62;4c", None),                        # Sixel, but no cell size said: no
    (None, False, "\x1b[6;16;8t\x1b[?62;4c", (8, 16)),         # another terminal, saying its size
    (None, False, "\x1b[6;16;8t\x1b[?62;22c", None),           # its size, but no Sixel
    ("1", False, "", (10, 20)),                                # turned on by hand
])
def test_the_terminal_is_asked_once(monkeypatch, env, wt, answer, cell):
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

    def __init__(self):
        self.screen = Screen()
        self._driver = Driver()


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
