"""Bit, the dashboard's own little robot.

An original character (not Anthropic's Clawd): a head with a beacon on top and a dark
visor with two eyes, a small body with a chest light, two arms and two legs, each part
set apart so the shape reads at a glance. Bit is pixel art in a four-row strip.
In Windows Terminal each cell holds 2x4 pixels (Unicode block octants), so the strip
is sixteen pixels tall and Bit is 22x15 pixels in a space eleven cells wide, the outer
cells for its arms when raised. Elsewhere it falls back to half blocks (1x2 per cell),
which every terminal draws.

What Bit does follows the sessions: it works while they work, stays put and calls you
while one needs you, and wanders, sits and dozes off when nothing happens. It cheers with
confetti when a session finishes, greets you, warns about usage limits, follows the mouse
pointer with its eyes and gives a tip when clicked (its head spins when clicked again and
again), in a bubble beside it.
"""

from __future__ import annotations

import math
import os
import random
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache

from rich.style import Style
from rich.text import Text
from textual import events
from textual.geometry import Region
from textual.widget import Widget

from . import bitart, bitmoves, sixel
from .bitmoves import TRAITS
from .blocks import BACKGROUND, octant_cell
from .theme import AMBER, GREEN, MUTED, RED, TEXT, mix

ROWS = 4
PIXELS = ROWS * 4       # pixel rows
FPS = 20
TICK = 1 / FPS
SPRITE_W = 22           # sprite width in pixels: a cell each side for the arms when raised
SPRITE_H = 15           # ... and height, standing on the bottom of the strip
WIDTH = SPRITE_W // 2   # sprite width in cells
SLEEP_AFTER = 90 * FPS  # frames without news before Bit dozes off
JUMP_SPEED = 0.45       # with the gravity below, a hop keeps the feet off the bar for 7 frames
SAYS_ROW = 2            # the strip row Bit's words go on, level with its visor
CALL_EVERY = 120        # while a session needs you: frames between two calls...
CALL_FOR = 48           # ...and how long each call (waving, eyes wide) lasts
POINTER_FRAMES = 60     # how long Bit keeps an eye on where the mouse pointer last was
DIZZY_CLICKS = 3        # clicks this close together (CLICK_FRAMES) make Bit dizzy
CLICK_FRAMES = 30
BUBBLE = "#4a525c"       # the frame of what Bit says: there, but quieter than the words

PALETTE = {
    "H": "#eef0f2",  # head
    "V": "#0b0f14",  # visor
    "B": "#c9cfd6",  # body and legs
    "L": "#c9cfd6",  # legs, in the body's colour so a cell of body and legs needs no third colour
    "a": "#d97757",  # the antenna light (the dashboard makes it glow in the theme's accent)
    "c": "#d97757",  # the chest light, likewise; a cell of its own, between the legs' cells
}
ARM = "#aeb5bd"
# Drawn to the cell grid: every 2x4 block of the sprite holds at most two colours, which is
# all a terminal cell can show, so nothing smears (tests/test_mascot.py checks every pose).
# The eyes stay light like the helmet for the same reason. Rows 1-2 are the beacon on top
# (a stem would be one pixel under a light two wide, off its middle); 3-10 the head; 11-12
# the body with its chest light "c"; the legs follow in rows 13-14. The arms are drawn
# apart from it, each in a column of cells of its own.
SPRITE = [
    "......................",
    "..........aa..........",
    "..........aa..........",
    ".....HHHHHHHHHHHH.....",
    "....HHHHHHHHHHHHHH....",
    "....HHVVVVVVVVVVHH....",
    "....HHVVVVVVVVVVHH....",
    "....HHVVVVVVVVVVHH....",
    "....HHVVVVVVVVVVHH....",
    "....HHHVVVVVVVVHHH....",
    ".....HHHHHHHHHHHH.....",
    ".......BBBccBBB.......",
    ".......BBBccBBB.......",
]
BODY_ROW = 11
# Feet three pixels wide; walking lifts one, then the other, a pixel off the bar.
LEGS = {
    "stand": ["........BB..BB........", ".......BBB..BBB......."],
    "left": [".......BBB..BB........", "............BBB......."],
    "right": ["........BB..BBB.......", ".......BBB............"],
    "sit": [".......BBBBBBBB.......", "......BBBBBBBBBB......"],  # legs out in front
    "tucked": ["........BB..BB........", "......................"],  # in the air (a hop)
}
# Arms, 2 pixels wide and set a pixel apart from the body, as (row, col) of their top-left
# pixel and their length. Down at the sides they fill the cells beside the body; raised,
# they go up beside the head, where nothing else is, so no cell needs a third colour.
ARMS = {
    "down": ((11, 4, 3), (11, 16, 3)),
    "swing": ((11, 4, 2), (11, 16, 3)),   # one hand up a little, the other down
    "swung": ((11, 4, 3), (11, 16, 2)),
    "wave": ((11, 4, 3), (6, 19, 5)),     # the right arm up beside the head, a pixel off it
    "waved": ((11, 4, 3), (8, 19, 3)),
    "up": ((6, 1, 5), (8, 19, 3)),         # both up, one higher, then the other
    "upped": ((8, 1, 3), (6, 19, 5)),
}
STRIDE = ("stand", "left", "stand", "right")
# Eyes: (row, col) pixels inside the visor (rows 5-9, columns 6-15). Rows 7-8 are the top
# half of a cell, so in half blocks too an eye is a whole half-cell, not split across two.
EYES = {
    "normal": [(7, 8), (7, 9), (8, 8), (8, 9), (7, 12), (7, 13), (8, 12), (8, 13)],
    "left": [(7, 7), (7, 8), (8, 7), (8, 8), (7, 11), (7, 12), (8, 11), (8, 12)],
    "right": [(7, 9), (7, 10), (8, 9), (8, 10), (7, 13), (7, 14), (8, 13), (8, 14)],
    "happy": [(8, 7), (7, 8), (7, 9), (8, 10), (8, 11), (7, 12), (7, 13), (8, 14)],  # ^ ^
    "alarm": [(6, 8), (7, 8), (8, 8), (6, 9), (7, 9), (8, 9), (6, 12), (7, 12), (8, 12), (6, 13), (7, 13),
              (8, 13)],  # wide open
    "blink": [(8, 8), (8, 9), (8, 12), (8, 13)],
    "sleep": [(8, 7), (8, 8), (8, 9), (8, 12), (8, 13), (8, 14)],  # closed: a line each
    "down": [(8, 8), (8, 9), (9, 8), (9, 9), (8, 12), (8, 13), (9, 12), (9, 13)],  # at its work
}
TIPS = [
    "p writes to a session", "t switches the theme", "s asks Claude for a summary",
    "e opens a row", "w saves a workspace", "l shows only open sessions",
    "/ filters the list", "b sends me away for a while", "T shows your teams",
]


def _rgb(colour: str) -> bitart.Colour:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


@lru_cache(maxsize=128)
def _picture(pose: bitart.Pose, effects: tuple, shift: int, back: bitart.Colour, cell: tuple[int, int]) -> str:
    """A frame of Bit as a Sixel image; kept, as breathing, sitting and dozing come round again."""
    frame = bitart.Frame(pose, [bitart.Effect(kind, x, y, size, alpha, colour)
                                for kind, x, y, size, alpha, colour in effects])
    palette, rows = bitart.indexed(frame, cell, shift)
    return sixel.encode_indexed(palette, rows, back)


def fine_blocks() -> bool:
    """2x4 pixels per cell where the terminal draws block octants itself (Windows Terminal)."""
    choice = os.environ.get("TABMANCER_MASCOT_BLOCKS", "").lower()
    if choice in ("octants", "half"):
        return choice == "octants"
    return bool(os.environ.get("WT_SESSION"))

@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    colour: str
    life: int


@dataclass
class Saying:
    text: str
    colour: str
    frames: int


class Mascot(Widget):
    DEFAULT_CSS = f"""
    Mascot {{ height: {ROWS}; }}
    """

    def __init__(self, seed: int | None = None, accent: str = AMBER, fine: bool | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.rng = random.Random(seed)
        self.accent = accent
        self.fine = fine_blocks() if fine is None else fine
        self.x = 8.0            # in pixels
        self.direction = 1
        self.action = "idle"
        self.left = 40
        self.distance = 0.0     # walked so far, drives the steps
        self.height = 0.0       # pixels above the floor
        self.velocity = 0.0
        self.jumps_left = 0
        self.eyes = "normal"
        self.eyes_left = 0
        self.particles: list[Particle] = []
        self.notes: list[list[float]] = []  # floating ♪ / z: [cell x, frames, kind]
        self.bubbles: list[Saying] = []
        self.working = 0
        self.waiting = 0
        self.quiet = 0
        self.frame = 0
        self.alarm_frames = 0
        self.important: frozenset[str] = frozenset()
        self.painted: set[tuple[int, int]] = set()
        self.shown: Text | None = None  # the frame on screen
        self.shown_width = 0
        self.ground: Widget | None = None  # the widget whose background Bit takes on
        self.since = 0          # the frame the current action started on
        self.pointer = 0.0      # where the mouse pointer was, in pixels along the strip
        self.pointer_seen = -10 ** 6  # ...and on which frame
        self.following = False  # the eyes are on the pointer
        self.clicks: list[int] = []  # frames of the recent clicks on Bit
        self.calls = 0          # calls made: each goes another way (bitmoves.CALLS)
        self.antenna = 0.0      # its sway, degrees, and how fast it swings (a spring)
        self.antenna_speed = 0.0
        self.picture: sixel.Picture | None = None  # Bit in real pixels, where the terminal draws them
        self.drawn = None       # what the picture shows: no need to send it again

    def on_mount(self) -> None:
        self.set_interval(TICK, self.tick)
        found = sixel.terminal()
        if found is not None and isinstance(self.app, sixel.SixelHost):
            self.picture = sixel.Picture(self, self.picture_cells)
            self.app.add_picture(self.picture)

    def base(self) -> str:
        """The octant pose that stands in for the action (bitmoves.TRAITS)."""
        trait = TRAITS.get(self.action)
        return trait.octant if trait else self.action

    # --- what the dashboard tells Bit ---------------------------------------------------

    def set_mood(self, working: int, waiting: int) -> None:
        """`working`: sessions at work; `waiting`: sessions that need you (a question to answer)."""
        if (working, waiting) != (self.working, self.waiting):
            self.quiet = 0
            if self.drowsy():
                self.rouse()
        if waiting and not self.waiting:
            self.start("call", CALL_EVERY)  # at once: it stays put and calls you while it lasts
        elif not waiting and self.action == "call":
            self.left = 1
        self.working, self.waiting = working, waiting

    def say(self, text: str, colour: str = TEXT, seconds: float = 4) -> None:
        if self.bubbles and self.bubbles[-1].text == text:
            return
        self.bubbles.append(Saying(text, colour, int(seconds * FPS)))
        self.bubbles = self.bubbles[-3:]

    def celebrate(self, project: str = "") -> None:
        """A session finished: a cheer (two hops), a dance or a spin, confetti and a word about it."""
        self.wake()
        how = self.rng.choice(bitmoves.CHEERS)
        self.start(how, TRAITS[how].frames[0])
        self.jumps_left = 2 if how == "cheer" else 0
        self.look("happy", TRAITS[how].frames[0])
        self.burst([GREEN, self.accent, "#ffffff", "#7ee0a1"], 14)
        self.say(f"{project} is done!" if project else "Done!", GREEN)

    def worry(self, text: str, colour: str = RED) -> None:
        """A usage limit nearly full: a worried look, a drop of sweat, and the word."""
        self.wake()
        self.start("worried", TRAITS["worried"].frames[0])
        self.say(text, colour, seconds=6)

    def alert(self, project: str = "") -> None:
        """A session needs you: a startled hop, red sparks and a call."""
        self.wake()
        self.start("alarm", 50)
        self.jumps_left = 1
        self.look("alarm", 50)
        self.alarm_frames = 60
        self.burst([RED, "#ff8a80"], 10)
        self.say(f"{project} needs you!" if project else "Someone needs you!", RED)

    def on_click(self, event: events.Click) -> None:
        if self.drowsy():
            self.rouse()
            return
        self.wake()
        self.clicks = [f for f in self.clicks if self.frame - f < CLICK_FRAMES] + [self.frame]
        if len(self.clicks) >= DIZZY_CLICKS:  # clicked again and again: its head spins
            self.clicks = []
            self.start("dizzy", 40)
            self.say("Whoa, easy!", self.accent)
            return
        self.jumps_left = 1
        self.start("jump", 20)
        self.look("happy", 20)
        self.say(self.rng.choice(TIPS), self.accent)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self.drowsy():  # the pointer over Bit wakes it
            self.rouse()

    def point_at(self, pixel: float) -> None:
        """Where the mouse pointer is, in pixels along the strip: Bit's eyes follow it a while."""
        self.pointer, self.pointer_seen = pixel, self.frame

    # --- behaviour ------------------------------------------------------------------------

    def wake(self) -> None:
        self.quiet = 0
        if self.action == "sleep":
            self.notes.clear()

    def drowsy(self) -> bool:
        """Dozing off or asleep: news, a click or the pointer startles it awake."""
        return self.action in ("yawn", "nod", "sleep")

    def rouse(self) -> None:
        self.wake()
        self.start("wake", TRAITS["wake"].frames[0])
        self.jumps_left = 1
        self.look("alarm", 8)

    def start(self, action: str, frames: int) -> None:
        self.action, self.left, self.since = action, frames, self.frame

    def look(self, eyes: str, frames: int) -> None:
        self.eyes, self.eyes_left = eyes, frames

    def walk_room(self, width: int) -> int:
        """Pixels from the strip's left edge that Bit may walk and talk in: all of it."""
        return width * 2

    def burst(self, colours: list[str], count: int, cx: float | None = None) -> None:
        cx = self.x + SPRITE_W / 2 if cx is None else cx
        for _ in range(count):
            self.particles.append(Particle(cx, 4.0, self.rng.uniform(-1.1, 1.1), self.rng.uniform(-0.6, -0.15),
                                           self.rng.choice(colours), self.rng.randint(12, 22)))

    def next_action(self) -> None:
        """What Bit does next follows what the sessions do: it calls you while one needs you,
        works and hurries while they work, and wanders, sits and in the end dozes off when
        nothing happens."""
        if self.waiting:
            self.start("call", CALL_EVERY)
            return
        if self.quiet > SLEEP_AFTER and not self.working:  # it yawns, nods off, and sleeps
            following = {"yawn": "nod", "nod": "sleep", "sleep": "sleep"}.get(self.action, "yawn")
            self.start(following, self.rng.randint(*TRAITS[following].frames))
            if following == "sleep":
                self.look("sleep", 10 ** 9)
            return
        if self.action == "think" and self.rng.random() < 0.35:  # and then it has it
            action = "idea"
        else:
            action = self.rng.choice(bitmoves.WORK_CHOICES if self.working else bitmoves.IDLE_CHOICES)
        self.start(action, self.rng.randint(*TRAITS[action].frames))
        if TRAITS[action].speed and self.rng.random() < 0.35:
            self.direction *= -1
        if action == "look":
            self.look(self.rng.choice(("left", "right")), 20)
        elif action == "wave":
            self.look("happy", 36)
        elif action == "jump":
            self.jumps_left = 1

    def tick(self) -> None:
        self.advance()
        # Repaint only when the picture changed: sitting, dozing or standing still, most
        # frames are identical, and redrawing them would cost CPU for nothing.
        width = self.size.width
        frame = Text("\n").join(self.lines(width))
        repainted: list[Region] = []
        if frame != self.shown or self.shown_width != width:
            whole = self.picture is None or self.shown_width != width
            self.shown, self.shown_width = frame, width
            # In real pixels Bit's own cells are blank and stay so: only what is beside them is repainted.
            repainted = [Region(0, 0, width, ROWS)] if whole else self.beside(width)
            self.refresh(*(() if whole else repainted))
        self.update_picture(width, repainted)

    def beside(self, width: int) -> list[Region]:
        """The strip left and right of Bit's cells."""
        cell = self.cell_x(width)
        return [r for r in (Region(0, 0, cell, ROWS), Region(cell + WIDTH, 0, width - cell - WIDTH, ROWS))
                if r.width > 0]

    def speed(self) -> float:
        """Pixels along the strip a frame: brisker walking while sessions work."""
        if self.action == "walk" and self.working:
            return 0.6
        trait = TRAITS.get(self.action)
        return trait.speed if trait else 0.0

    def advance(self, width: int | None = None) -> None:
        """One frame of Bit's life (separate from tick, so tests can pass a width in cells)."""
        self.frame += 1
        self.quiet += 1
        cols = max(width or self.size.width, WIDTH + 2)
        span = max(4, self.walk_room(cols) - SPRITE_W)  # room to walk, in pixels
        self.x = min(self.x, float(span))
        self.left -= 1
        if self.left <= 0:
            self.next_action()

        speed = self.speed()
        if speed:
            self.x += speed * self.direction
            self.distance += speed
            if self.x <= 0 or self.x >= span:
                self.direction *= -1
                self.x = max(0.0, min(self.x, float(span)))

        if self.jumps_left and self.height == 0 and self.velocity == 0:
            self.velocity = JUMP_SPEED
            self.jumps_left -= 1
            self.antenna_speed -= 3  # it lags behind as Bit takes off...
        if self.velocity or self.height:
            self.height += self.velocity
            self.velocity -= 0.1
            if self.height <= 0:
                self.height, self.velocity = 0.0, 0.0
                self.antenna_speed += 6 * self.rng.choice((-1, 1))  # ...and wobbles on landing
        # The antenna is on a spring: it trails behind as Bit walks, and settles.
        lean = -self.direction * min(12.0, speed * 18)
        self.antenna_speed += (lean - self.antenna) * 0.25 - self.antenna_speed * 0.3
        self.antenna = max(-30.0, min(30.0, self.antenna + self.antenna_speed))

        if self.action == "call" and (self.frame - self.since) % CALL_EVERY == 0:
            self.calls += 1  # each call another way: waving, pointing at the list, bouncing
            self.look("alarm", CALL_FOR)  # each call: eyes wide while it waves
        elif self.base() == "work" and self.frame == self.since + 1:
            self.look("down", self.left)
        elif self.base() == "dizzy":
            self.eyes, self.eyes_left = ("left", "right")[(self.frame // 3) % 2], 1
        if self.eyes_left:
            self.eyes_left -= 1
            if not self.eyes_left:
                self.eyes = "normal"
        elif self.frame - self.pointer_seen < POINTER_FRAMES and self.action != "sleep":
            centre = self.x + SPRITE_W / 2
            self.eyes = "left" if self.pointer < centre - 8 else "right" if self.pointer > centre + 8 else "normal"
            self.following = True
        elif self.following:  # the pointer has gone still: eyes front again
            self.eyes, self.following = "normal", False
        elif self.rng.random() < 0.012:
            self.look("blink", 3)

        if self.action == "sleep" and self.frame % 30 == 0:
            self.notes.append([(self.x + SPRITE_W) / 2, 40, "z"])
        if self.action in ("cheer", "dance") and self.frame % 12 == 0:
            self.notes.append([self.x / 2 + self.rng.choice((-2, WIDTH + 1)), 30, "♪"])
        for note in self.notes:
            note[0] += 0.04
            note[1] -= 1
        self.notes = [n for n in self.notes if n[1] > 0]

        for p in self.particles:
            p.x += p.vx
            p.y += p.vy
            p.vy += 0.07
            p.life -= 1
        self.particles = [p for p in self.particles if p.life > 0 and 0 <= p.y < PIXELS]

        for saying in self.bubbles:
            saying.frames -= 1
        self.bubbles = [b for b in self.bubbles if b.frames > 0]
        self.alarm_frames = max(0, self.alarm_frames - 1)

    # --- drawing -----------------------------------------------------------------------------

    def light(self) -> str:
        """The antenna: red blinks when needed, otherwise a slow glow in the accent colour."""
        if self.waiting or self.alarm_frames:
            return RED if self.frame % 10 < 5 else "#3d0f0d"
        if self.action in ("cheer", "dance", "spin"):
            return GREEN
        return mix(mix(self.accent, BACKGROUND, 0.6), self.accent, self.glow())

    def glow(self) -> float:
        """How bright the antenna glows, 0 to 1: slowly, quicker while sessions work."""
        glow = (math.sin(self.frame / FPS * (5 if self.working else 2)) + 1) / 2
        return round(glow * 8) / 8  # in steps, so a still Bit repeats frames and costs nothing

    def chest(self) -> str:
        """The chest light: the accent, pulsing quickly while sessions work, dim asleep."""
        if self.action in ("sleep", "nod"):
            return mix(self.accent, BACKGROUND, 0.7)
        return mix(mix(self.accent, BACKGROUND, 0.55), self.accent, self.chest_glow())

    def chest_glow(self) -> float:
        glow = (math.sin(self.frame / FPS * (9 if self.working else 1.5)) + 1) / 2
        return round(glow * 6) / 6  # in steps, so a still Bit repeats frames and costs nothing

    # --- in real pixels (Sixel) -------------------------------------------------------------

    def cell_x(self, width: int) -> int:
        """The first of Bit's cells along the strip. In octants it moves a cell at a time; in
        real pixels it is drawn between cells too, its picture starting at this one."""
        x = max(0.0, min(self.x, float(width * 2 - SPRITE_W)))
        return int(x // 2) if self.picture is not None else round(x / 2)

    def picture_cells(self) -> tuple[int, int, int, int] | None:
        """Where Bit's picture goes on the screen, in cells: x, y, width, height."""
        region = self.region
        if not self.is_attached or region.width < WIDTH or region.height < ROWS:
            return None
        return region.x + self.cell_x(region.width), region.y, WIDTH, ROWS

    def hires(self, cell: int = 0, shift: int = 0, size: tuple[int, int] = bitart.WT_CELL) -> bitart.Frame:
        """Bit this frame in real pixels: the action's pose, with what the behaviour knows."""
        eyes = "normal" if self.eyes == "down" and self.action != "work" else self.eyes  # the moves look their own way
        red = bool(self.waiting or self.alarm_frames)
        context = bitmoves.Context(
            beat=self.frame - self.since, frame=self.frame, direction=self.direction, distance=self.distance,
            height=self.height, eyes=eyes, antenna=self.antenna,
            beacon=(1.0 if self.frame % 10 < 5 else 0.15) if red else self.glow(), beacon_red=red,
            chest=self.chest_glow(), working=bool(self.working), call=bitmoves.CALLS[self.calls % len(bitmoves.CALLS)],
            effects=self.confetti(cell, shift, size))
        frame = bitmoves.frame(self.action, context)
        pose = bitart.themed(frame.pose, _rgb(self.accent))
        if self.action in ("cheer", "dance", "spin"):
            pose = replace(pose, beacon_colour=_rgb(GREEN))
        return bitart.Frame(pose, frame.effects)

    def confetti(self, cell: int, shift: int, size: tuple[int, int]) -> list[bitart.Effect]:
        """The particles flying about Bit, in its picture's own coordinates."""
        found = []
        width = WIDTH * size[0]
        for p in self.particles:
            x = p.x * size[0] / 2 - cell * size[0] - shift  # pixels from the picture's left, before its shift
            y = p.y * size[1] / 4
            if -2 <= x < width:
                found.append(bitart.Effect("confetti", 55 + (x / (size[0] / 10) - 55) / bitart.SCALE,
                                           bitart.GROUND - (bitart.GROUND - y / (size[1] / 20)) / bitart.SCALE,
                                           colour=_rgb(p.colour)))
        return found

    def update_picture(self, width: int, repainted: list[Region] | None = None) -> None:
        """Bit in real pixels, drawn again only when it changed, and sent over the old one at once;
        the cells it left are written blank again, so no old picture stays behind there.

        Textual writes a line of the strip from its start to the end of what is repainted on it
        (sixel.wrote_over), so anything repainted right of Bit's first cell runs over its picture.
        Then the picture goes right after that update instead, in the same frame (sixel.SixelHost):
        sent now as well, it would go twice."""
        found = sixel.terminal()
        if self.picture is None or found is None or width < WIDTH:
            return
        cell = self.cell_x(width)
        x = max(0.0, min(self.x, float(width * 2 - SPRITE_W)))
        shift = round((x / 2 - cell) * found.cell[0])  # between cells, in pixels
        frame = self.hires(cell, shift, found.cell)
        back = self.backdrop()
        effects = tuple((e.kind, round(e.x), round(e.y), e.size, round(e.alpha, 1), e.colour) for e in frame.effects)
        key = (frame.pose, effects, cell, shift, back, found.cell)
        if key == self.drawn:
            return
        before = self.drawn[2] if self.drawn else cell
        self.drawn = key
        self.picture.data = _picture(frame.pose, effects, shift, _rgb(back), found.cell)
        self.picture.fresh = True
        repainted = list(repainted or ())
        if before != cell:  # the cells it left: written blank again, which wipes the old picture there
            left = [c for c in range(before, before + WIDTH) if not cell <= c < cell + WIDTH]
            repainted.append(Region(min(left), 0, len(left), ROWS))
            self.refresh(repainted[-1])
        if any(region.right > cell for region in repainted):
            return  # Textual writes over it next: it goes with that update
        if self.is_attached and isinstance(self.app, sixel.SixelHost):
            self.app.send_now(self.picture)  # over the old one, opaque: replaced, not wiped

    def eye_colour(self) -> str:
        # In octants the helmet's own colour, so a cell holding eye, visor and helmet needs
        # only two (light and dark). Asleep too: closed eyes are light lines (a dim colour
        # there was a third one, and the cell dropped it: the visor looked empty). In half
        # blocks a shade of its own: there the eyes' colour wins a quarter of a cell, and
        # the helmet's would then take the visor's edges too.
        return PALETTE["H"] if self.fine else "#e6f4ff"

    def pixels(self, width: int, sprite: bool = True) -> list[list[str]]:
        """The strip as a grid of colours, two pixels per cell across and four down; without the
        sprite where Bit is drawn in real pixels (its cells left blank for the picture)."""
        cols = width * 2
        grid = [[BACKGROUND] * cols for _ in range(PIXELS)]
        self.painted = set()  # cells with something in them
        # Whole cells across, so the sprite always lines up with the cell grid.
        x0 = 2 * round(max(0.0, min(self.x, float(cols - SPRITE_W))) / 2)
        base = self.base()
        if base == "dizzy":  # its head spins: a cell this way and that
            x0 = max(0, min(cols - SPRITE_W, x0 + (2 if (self.frame // 2) % 2 else -2)))
        airborne = self.height >= 0.5
        sitting = base in ("sit", "sleep")
        # Bit moves up or down a whole cell at a time, never a pixel: across the cell grid,
        # cells would need more colours than they can show and the face would smear. A hop
        # lifts it one cell (its antenna goes above the strip for that moment).
        top = PIXELS - SPRITE_H - (4 if airborne else 0)
        body_top = top + BODY_ROW

        def put(row: int, col: int, colour: str) -> None:
            if 0 <= row < PIXELS and 0 <= col < cols:
                grid[row][col] = colour
                self.painted.add((row // 4, col // 2))

        if sprite:
            colours = {**PALETTE, "a": self.light(), "c": self.chest()}
            for r, line in enumerate(SPRITE):
                for c, key in enumerate(line):
                    if key in colours:
                        put(top + r, x0 + c, colours[key])
            for r, c in EYES.get(self.eyes, EYES["normal"]):
                put(top + r, x0 + c, self.eye_colour())

            for row, col, length in ARMS[self.arms()]:
                if row < BODY_ROW:  # raised: out from the shoulder along the body's top, then up
                    span = range(col, 6) if col < SPRITE_W // 2 else range(16, col + 2)
                    for c in span:
                        put(body_top, x0 + c, ARM)
                for r in range(row, min(row + length, BODY_ROW + 3)):
                    put(top + r, x0 + col, ARM)
                    put(top + r, x0 + col + 1, ARM)

            step = int(self.distance / 2)  # one per cell walked
            if sitting:
                legs = LEGS["sit"]
            elif base in ("walk", "alarm") and not airborne:
                legs = LEGS[STRIDE[step % 4]]
            else:
                legs = LEGS["stand"]
            for r, line in enumerate(legs):
                for c, key in enumerate(line):
                    if key in PALETTE:
                        put(body_top + 2 + r, x0 + c, PALETTE[key])

        for p in self.particles:
            row, col = round(p.y), round(p.x)
            if 0 <= row < PIXELS and 0 <= col < cols and grid[row][col] == BACKGROUND:
                put(row, col, p.colour)  # behind Bit, never over its face
        return grid

    def arms(self) -> str:
        """Which of ARMS the arms are in, this frame."""
        beat = self.frame - self.since
        action = self.base()
        if action == "walk":
            return ("swing", "swung")[int(self.distance / 2) % 2]
        if action == "work":  # quick, like typing
            return ("swing", "swung")[(self.frame // 3) % 2]
        if action == "wave":
            return ("wave", "waved")[(self.frame // 4) % 2]
        if action == "call":
            return ("wave", "waved")[(self.frame // 4) % 2] if beat % CALL_EVERY < CALL_FOR else "down"
        if action in ("cheer", "alarm"):
            return ("up", "upped")[(self.frame // 5) % 2]
        if action == "dizzy":
            return ("up", "upped")[(self.frame // 2) % 2]
        return "down"

    def lines(self, width: int) -> list[Text]:
        """The strip as ROWS lines of text, each exactly `width` cells wide."""
        width = max(width, WIDTH)
        grid = self.pixels(width, sprite=self.picture is None)
        # The face and the lights win when a cell has more colours than it can show.
        self.important = frozenset({self.eye_colour(), self.light(), self.chest(),
                                    *(p.colour for p in self.particles)})
        overlay: dict[tuple[int, int], tuple[str, str]] = {}
        for x, _, kind in self.notes:
            overlay[(0, int(x))] = (kind, MUTED if kind == "z" else self.accent)
        self._saying(overlay, self.walk_room(width) // 2)

        back = self.backdrop()
        blank = Style(bgcolor=back)
        lines = []
        for row in range(ROWS):
            text = Text()
            run_text, run_style = "", None
            for col in range(width):
                if (row, col) in overlay:
                    char, colour = overlay[(row, col)]
                    style = Style(color=colour, bgcolor=back, bold=True)
                elif (row, col) not in self.painted:
                    char, style = " ", blank  # most of the strip: nothing to work out
                else:
                    char, style = self._cell(grid, row, col, back)
                if style == run_style:
                    run_text += char
                else:
                    if run_text:
                        text.append(run_text, run_style)
                    run_text, run_style = char, style
            text.append(run_text, run_style)
            lines.append(text)
        return lines

    def _cell(self, grid: list[list[str]], row: int, col: int, back: str) -> tuple[str, Style]:
        rows = grid[row * 4:row * 4 + 4]
        if self.fine:
            block = [rows[r][col * 2 + c] for r in range(4) for c in range(2)]
            char, fg, bg = octant_cell(block, self.important)
            return char, Style(color=fg, bgcolor=back if bg == BACKGROUND else bg)
        # Half blocks: the upper and lower 2x2 quarters, each by its most common colour,
        # except that the eyes and lights win in the upper quarter. Standing, that is
        # where the eyes sit, so the visor shows two eyes above a dark band.
        def common(rs: list[list[str]], upper: bool) -> str:
            counts = Counter(r[col * 2 + c] for r in rs for c in range(2))
            features = [c for c in counts if c in self.important]
            if features and upper:
                return features[0]
            non_background = [(n, c) for c, n in counts.items() if c != BACKGROUND]
            return max(non_background)[1] if non_background and max(non_background)[0] >= 2 else BACKGROUND
        upper, lower = common(rows[:2], True), common(rows[2:], False)
        upper, lower = (back if c == BACKGROUND else c for c in (upper, lower))
        if upper == lower:
            return " ", Style(bgcolor=upper)
        return "▀", Style(color=upper, bgcolor=lower)

    def _saying(self, overlay: dict[tuple[int, int], tuple[str, str]], width: int) -> None:
        """The newest thing Bit says, in a bubble beside it (rows 1-3), its tail toward Bit;
        on its other side when there is more room there. Too long, the words end in "…"."""
        if not self.bubbles:
            return
        saying = self.bubbles[-1]
        x0 = self.cell_x(width)
        right, left = width - (x0 + WIDTH), x0  # cells free beside Bit
        room = max(right, left) - 5  # the tail, the frame and a space each side
        if room < 3:
            return
        words = saying.text if len(saying.text) <= room else saying.text[:room - 1] + "…"
        inner = len(words) + 2
        if right >= inner + 3:
            tail_col, col, tail = x0 + WIDTH, x0 + WIDTH + 1, "◂"
        else:
            col = max(0, x0 - inner - 3)
            tail_col, tail = col + inner + 2, "▸"
        lines = {SAYS_ROW - 1: "╭" + "─" * inner + "╮", SAYS_ROW: f"│ {words} │",
                 SAYS_ROW + 1: "╰" + "─" * inner + "╯"}
        for row, line in lines.items():
            for c, char in enumerate(line):
                if 0 <= col + c < width:
                    words_here = row == SAYS_ROW and 1 < c < len(line) - 2
                    overlay[(row, col + c)] = (char, saying.colour if words_here else BUBBLE)
        if 0 <= tail_col < width:
            overlay[(SAYS_ROW, tail_col)] = (tail, BUBBLE)

    def backdrop(self) -> str:
        """The background around Bit, so it stands on the dashboard itself, not in a box.

        That is the background of `ground` when set (the session list, whose shade changes
        with focus), otherwise whatever is behind the strip.
        """
        colour = (self.ground or self).background_colors[1]
        return colour.hex6 if colour.a else BACKGROUND

    def render(self) -> Text:
        if self.shown is None or self.shown_width != self.size.width:
            self.shown = Text("\n").join(self.lines(self.size.width))
            self.shown_width = self.size.width
        return self.shown
