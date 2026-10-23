from tabmancer import mascot as M
from tabmancer.blocks import OCTANTS

# Bit is block mosaics (octants, or half blocks as a fallback) plus a line of text.
PIXEL_CHARS = set(OCTANTS) | set(" ▀▄")
TEXT_CHARS = set("◂▸♪z╭╮╰╯─│… ")


def bit(seed=1, fine=True):
    return M.Mascot(seed=seed, fine=fine)


def frames(robot, count, width=80):
    out = []
    for _ in range(count):
        robot.advance(width)
        out.append(robot.lines(width))
    return out


def test_every_frame_fills_the_four_row_strip_exactly():
    for fine in (True, False):
        for rows in frames(bit(fine=fine), 200):
            assert len(rows) == M.ROWS == 4
            assert all(len(row.plain) == 80 for row in rows)


def test_only_block_mosaics_and_text_are_drawn():
    robot = bit()
    robot.celebrate("web-app")
    seen = set()
    for rows in frames(robot, 120):
        for row in rows:
            seen |= set(row.plain)
    allowed = PIXEL_CHARS | TEXT_CHARS | set("web-app is done!")
    assert seen <= allowed, seen - allowed


def test_octant_cells_pick_two_colours_and_the_right_pattern():
    white, dark = "#ffffff", "#123456"
    char, fg, bg = M.octant_cell([white, white] + [M.BACKGROUND] * 6)  # the top row only
    assert (fg, bg) == (white, M.BACKGROUND) and char == OCTANTS[0b11]
    char, fg, bg = M.octant_cell([white] * 4 + [dark] * 4)  # two colours, no background
    assert char == "▀" and {fg, bg} == {white, dark}
    char, fg, bg = M.octant_cell([white] + [M.BACKGROUND] * 7)  # one pixel: its own character
    assert char == "\U0001CEA8" and (fg, bg) == (white, M.BACKGROUND)
    assert len(set(OCTANTS)) == 256


def test_bit_is_small_but_detailed():
    robot = bit()
    grid = robot.pixels(40)
    assert len(grid) == M.PIXELS == 16 and all(len(row) == 80 for row in grid)
    drawn = [(r, c) for r, row in enumerate(grid) for c, colour in enumerate(row) if colour != M.BACKGROUND]
    assert 120 <= len(drawn) <= 180  # a 22x15 sprite, its arms down
    assert max(c for _, c in drawn) - min(c for _, c in drawn) < M.SPRITE_W


def test_bit_walks_and_stays_on_the_strip():
    robot = bit()
    positions = set()
    for _ in range(2000):
        robot.advance(80)
        positions.add(int(robot.x))
        assert 0 <= robot.x <= 80 * 2 - M.SPRITE_W
    assert len(positions) > 20


def shape(grid):
    return [[colour != M.BACKGROUND for colour in row] for row in grid]


def test_a_hop_lifts_bit_a_whole_cell_and_it_lands_where_it_was():
    robot = bit()
    robot.start("idle", 10 ** 6)
    standing = shape(robot.pixels(40))
    robot.jumps_left = 1
    heights, lifted = [], 0
    for _ in range(30):
        robot.advance(40)
        grid = shape(robot.pixels(40))
        heights.append(robot.height)
        if robot.height >= 0.5:  # in the air: everything one cell (4 pixels) up, nothing in between
            lifted += 1
            assert grid[:-4] == standing[4:] and not any(any(row) for row in grid[-4:])
        else:
            assert grid == standing
    assert lifted >= 3 and max(heights) >= 0.9 and heights[-1] == 0


def test_reactions_speak_and_show():
    robot = bit()
    robot.celebrate("web-app")
    assert robot.bubbles[-1].text == "web-app is done!" and robot.particles
    assert robot.eyes == "happy"
    robot.alert("pcb-router")
    assert robot.bubbles[-1].text == "pcb-router needs you!" and robot.alarm_frames
    robot.on_click(None)
    assert robot.bubbles[-1].text in M.TIPS


def test_sayings_and_particles_fade():
    robot = bit()
    robot.say("hello", seconds=0.5)
    robot.burst(["#ffffff"], 5)
    for _ in range(40):
        robot.advance(80)
    assert not robot.bubbles and not robot.particles


def test_bit_falls_asleep_when_nothing_happens_and_wakes_on_news():
    robot = bit()
    robot.quiet = M.SLEEP_AFTER + 1
    robot.left = 0
    robot.advance(80)
    assert robot.action == "sleep"
    robot.set_mood(working=1, waiting=0)
    assert robot.action != "sleep"


def test_fine_blocks_follow_the_terminal(monkeypatch):
    monkeypatch.delenv("TABMANCER_MASCOT_BLOCKS", raising=False)
    monkeypatch.setenv("WT_SESSION", "x")
    assert M.fine_blocks()
    monkeypatch.delenv("WT_SESSION")
    assert not M.fine_blocks()
    monkeypatch.setenv("TABMANCER_MASCOT_BLOCKS", "octants")
    assert M.fine_blocks()


async def test_bit_lives_in_the_dashboard(fake_home):
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.pause(0.2)
        assert app.mascot.display and app.mascot.size.height == M.ROWS
        assert app.mascot.bubbles  # it greets you
        # Bit stands right on the preview, on the list's own background (focus tints it).
        assert app.inspector.region.y == app.mascot.region.bottom
        app.table.focus()
        await pilot.pause(0.1)
        assert app.mascot.backdrop() == app.table.background_colors[1].hex6 != M.BACKGROUND
        await pilot.press("b")  # hide Bit, remembered
        assert app.mascot.has_class("hidden")
        await pilot.pause(0.1)
        assert app.inspector.styles.margin.top == 1  # the blank row is back


POSES = [("idle", {}), ("look", {"eyes": "left"}), ("work", {"eyes": "down", "working": 1}),
         ("wave", {"eyes": "happy"}), ("call", {"eyes": "alarm", "waiting": 1}),
         ("cheer", {"eyes": "happy"}), ("alarm", {"eyes": "alarm"}), ("sit", {}),
         ("sleep", {"eyes": "sleep"}), ("dizzy", {}), ("jump", {"height": 2.0}),
         *(("walk", {"distance": float(d)}) for d in range(0, 8, 2))]


def test_every_cell_of_every_pose_needs_two_colours_at_most():
    # A terminal cell shows two colours; a third would be dropped and the drawing smear.
    for action, state in POSES:
        for frame in range(0, 24, 2):  # the arms and lights move with the frames
            robot = bit()
            robot.start(action, 50)
            robot.frame, robot.since = frame, 0
            for key, value in state.items():
                setattr(robot, key, value)
            grid = robot.pixels(30)
            for row in range(M.ROWS):
                for col in range(30):
                    block = {grid[row * 4 + r][col * 2 + c] for r in range(4) for c in range(2)}
                    assert len(block) <= 2, (action, frame, row, col, block)


def shown(robot, width):
    """The strip as a terminal shows it: each cell's octant back to its 2x4 pixels."""
    from rich.console import Console

    index = {char: mask for mask, char in enumerate(OCTANTS)}
    console = Console(width=width, color_system="truecolor", force_terminal=True)
    grid = [[None] * (width * 2) for _ in range(M.PIXELS)]
    for row, line in enumerate(robot.lines(width)):
        col = 0
        for seg in line.render(console):
            for char in seg.text:
                fg = seg.style.color.triplet.hex if seg.style and seg.style.color else None
                bg = seg.style.bgcolor.triplet.hex if seg.style and seg.style.bgcolor else None
                mask = index.get(char, 0)
                for bit_ in range(8):
                    r, c = divmod(bit_, 2)
                    grid[row * 4 + r][col * 2 + c] = fg if mask >> bit_ & 1 else bg
                col += 1
    return grid


def test_closed_eyes_show_when_bit_sleeps():
    robot = bit()
    robot.start("sleep", 10 ** 6)
    robot.look("sleep", 10 ** 6)
    robot.x = 0.0
    grid = shown(robot, 30)
    top = M.PIXELS - M.SPRITE_H
    for r, c in M.EYES["sleep"]:  # every pixel of its closed eyes is drawn, light on the visor
        assert grid[top + r][c] == robot.eye_colour(), (r, c)


def test_while_a_session_needs_you_bit_stays_put_and_calls_you():
    robot = bit()
    robot.set_mood(working=0, waiting=1)
    assert robot.action == "call"
    where, poses, eyes = robot.x, set(), set()
    for _ in range(M.CALL_EVERY * 2):
        robot.advance(80)
        poses.add(robot.arms())
        eyes.add(robot.eyes)
        assert robot.action == "call" and robot.x == where  # it keeps calling, right there
    assert {"wave", "waved", "down"} <= poses and "alarm" in eyes
    robot.set_mood(working=0, waiting=0)
    robot.advance(80)
    assert robot.action != "call"


def test_bit_works_while_sessions_work_and_never_dances():
    robot = bit()
    robot.set_mood(working=2, waiting=0)
    actions = set()
    for _ in range(300):
        robot.left = 0
        robot.advance(80)
        actions.add(robot.action)
    assert "work" in actions and "dance" not in actions
    calm, busy = bit(), bit()
    busy.set_mood(working=1, waiting=0)
    lights = {name: set() for name in ("calm", "busy")}
    for _ in range(40):
        for name, robot in (("calm", calm), ("busy", busy)):
            robot.advance(80)
            lights[name].add(robot.chest())
    assert len(lights["busy"]) > len(lights["calm"])  # the chest light pulses quicker at work


def test_words_come_in_a_bubble_with_its_tail_toward_bit():
    robot = bit()
    robot.say("hello")
    rows = [line.plain for line in robot.lines(60)]
    assert "╭───────╮" in rows[1] and "◂│ hello │" in rows[2] and "╰───────╯" in rows[3]
    robot.x = 2 * 60 - M.SPRITE_W  # at the strip's right end: the bubble goes to its left
    rows = [line.plain for line in robot.lines(60)]
    assert "│ hello │▸" in rows[2]
    robot.say("a long thing to say, far too long for the room there is beside Bit")
    robot.x = 0.0
    rows = [line.plain for line in robot.lines(40)]
    assert "…" in rows[2] and all(len(row) == 40 for row in rows)


def test_the_eyes_follow_the_mouse_pointer_for_a_while():
    robot = bit()
    robot.start("idle", 10 ** 6)
    robot.x = 40.0
    robot.point_at(2.0)  # well to Bit's left
    robot.advance(80)
    assert robot.eyes == "left"
    robot.point_at(150.0)
    robot.advance(80)
    assert robot.eyes == "right"
    for _ in range(M.POINTER_FRAMES + 1):
        robot.advance(80)
    assert robot.eyes in ("normal", "blink")  # the pointer went still: eyes front again


def test_clicked_again_and_again_bit_gets_dizzy():
    robot = bit()
    for _ in range(M.DIZZY_CLICKS):
        robot.on_click(None)
    assert robot.action == "dizzy" and robot.bubbles[-1].text == "Whoa, easy!"


async def test_the_dashboard_tells_bit_where_the_pointer_is(fake_home):
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        before = app.mascot.pointer_seen
        await pilot.hover("#table", offset=(10, 2))
        await pilot.pause(0.1)
        assert app.mascot.pointer_seen > before
