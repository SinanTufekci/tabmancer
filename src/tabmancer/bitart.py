"""Bit in real pixels, for terminals that draw Sixel images.

Bit takes up eleven cells by four rows, as in octants, but drawn here in pixels: 110x78 in
Windows Terminal, which counts 10x20 of them to a cell (78, not 80, so the image ends on a
whole band of six and never spills into the row below). Other terminals have their own cell
size, and every shape is drawn to it.

Every part is a shape given by its distance (negative inside), in "design" coordinates: the
octant Bit's 22x16 pixels times five, the feet on the bottom. A pose moves, tilts, squashes
and stretches them freely, and they are filled hard-edged, pixel art in two or three tones a
part. Props (a laptop, a mug, a box...) and effects (Z's, notes, confetti...) go with them.

Pure Python, no imaging library. A pose without its effects is drawn once and kept
(figure()); effects are laid over a copy each frame. A new pose costs a few milliseconds: drawn
front to back, each part tried only where nothing nearer is, and a pixel some way outside a part
skips the next ones that cannot be nearer (drawn).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from itertools import pairwise

CELLS = (11, 4)          # Bit's place in the strip, in cells
GROUND = 77.5            # the feet, in design coordinates (the strip's 80 rows, less a band's spare)
HEIGHT = 78              # design rows down Bit's place: in Windows Terminal, its whole bands of six
SCALE = 0.9              # Bit a little smaller than its place: room to hop and raise its arms
WT_CELL = (10, 20)       # Sixel pixels a cell in Windows Terminal
HEAD_DEPTH = 0.8         # the head front to back, its width being 1, and the body's: turned side on,
BODY_DEPTH = 0.65        # Bit is this wide, not a sheet of paper
FAR = 1000.0             # a design coordinate nothing is drawn at: the face, seen from the back

Colour = tuple[int, int, int]

HELMET: Colour = (238, 240, 242)
HELMET_MID: Colour = (214, 219, 225)
HELMET_SHADE: Colour = (184, 191, 200)
RIM: Colour = (252, 253, 255)
VISOR: Colour = (11, 15, 20)
VISOR_EDGE: Colour = (30, 38, 48)
GLINT: Colour = (56, 68, 84)
EYE: Colour = (232, 244, 255)
BLUSH: Colour = (232, 112, 132)
BODY: Colour = (201, 207, 214)
BODY_SHADE: Colour = (162, 170, 180)
PANEL: Colour = (150, 158, 168)
NECK: Colour = (92, 100, 112)
LIMB: Colour = (176, 183, 191)
LIMB_SHADE: Colour = (138, 146, 156)
SOLE: Colour = (104, 112, 124)
SHADOW: Colour = (6, 6, 6)
ACCENT: Colour = (217, 119, 87)
RED: Colour = (232, 72, 64)
YELLOW: Colour = (250, 204, 72)
PINK: Colour = (244, 96, 128)
SKY: Colour = (120, 196, 255)
ZZZ: Colour = (140, 162, 196)
NOTE: Colour = (190, 170, 255)
WATER: Colour = (130, 200, 255)
DUST: Colour = (96, 100, 108)
STEEL: Colour = (150, 160, 172)
CARD: Colour = (196, 152, 98)
CARD_SHADE: Colour = (160, 118, 72)
PAPER: Colour = (236, 236, 228)
LID: Colour = (74, 80, 92)
LID_EDGE: Colour = (104, 112, 126)
# Stand-ins for the lights: drawn in these, coloured each frame (figure), so the shapes are kept
# whatever the glow. No part of Bit is drawn in them otherwise.
BEACON_LIGHT: Colour = (1, 0, 1)
BEACON_SHINE: Colour = (1, 0, 2)
BEACON_RIM: Colour = (1, 0, 3)
CHEST_LIGHT: Colour = (2, 0, 1)
CHEST_SHINE: Colour = (2, 0, 2)
CONFETTI: tuple[Colour, ...] =((250, 204, 72), (120, 196, 255), (244, 96, 128), (130, 220, 150), (217, 119, 87))

EYES = ("open", "half", "blink", "closed", "happy", "down", "wide", "wink", "heart", "star", "x",
        "dizzy", "worried", "focused", "surprised", "left", "right", "up")
PROPS = (None, "laptop", "mug", "clipboard", "wrench", "box", "bulb", "gear", "dots")


@dataclass(frozen=True)
class Arm:
    """An arm: hanging at an angle from straight down (outward positive, inward negative),
    raised beside the head and swung, reaching for a design point (a hand on the laptop), or
    bent with its elbow and hand where they are (on its way from one of those to another)."""
    kind: str = "hang"       # hang, raise, reach, bent
    angle: float = 8.0       # hang: degrees out; raise: the swing
    to: tuple[float, float] = (0.0, 0.0)  # reach, bent: where the hand goes
    front: bool = False      # drawn over the body (reaching in front of it)
    elbow: tuple[float, float] = (0.0, 0.0)  # bent: where the elbow is


@dataclass(frozen=True)
class Pose:
    """Where every part of Bit is this frame. Offsets in design pixels, angles in degrees."""
    lift: float = 0.0          # the whole of Bit off the ground (a hop)
    squash: float = 1.0        # height about the feet: < 1 squashed, > 1 stretched
    turn: float = 0.0          # turned about its middle, degrees: 0 facing you, 180 its back to you
    lean: float = 0.0          # the body leaning, degrees (hurrying)
    tilt: float = 0.0          # the head, degrees, clockwise
    head_y: float = 0.0        # the head bobbing on its own
    antenna: float = 0.0       # the antenna's sway, degrees
    left_foot: float = 0.0     # lifted by
    right_foot: float = 0.0
    tucked: bool = False       # legs pulled up (in the air)
    sitting: bool = False
    left: Arm = Arm()
    right: Arm = Arm()
    eyes: str = "open"
    look: tuple[float, float] = (0.0, 0.0)
    blush: bool = False
    beacon: float = 0.6        # how bright, 0 to 1
    beacon_colour: Colour = ACCENT
    chest: float = 0.6
    chest_colour: Colour = ACCENT
    prop: str | None = None
    prop_at: tuple[float, float] = (0.0, 0.0)  # where the prop is, when it moves (a mug, a box)
    prop_turn: float = 0.0     # a gear's turn, a wrench's swing


@dataclass
class Effect:
    """Something that goes with Bit but is not part of it: a Z, a note, confetti, dust."""
    kind: str                  # z, note, sweat, star, bang, ask, heart, confetti, dust, spark, steam
    x: float                   # design coordinates
    y: float
    size: float = 1.0
    alpha: float = 1.0
    colour: Colour | None = None


@dataclass
class Frame:
    pose: Pose
    effects: list[Effect] = field(default_factory=list)


# --- distances: negative inside -------------------------------------------------------------------

def _rounded(x, y, cx, cy, hx, hy, r):
    qx, qy = abs(x - cx) - hx + r, abs(y - cy) - hy + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _circle(x, y, cx, cy, r):
    return math.hypot(x - cx, y - cy) - r


def _segment(x, y, ax, ay, bx, by, r):
    px, py, vx, vy = x - ax, y - ay, bx - ax, by - ay
    h = max(0.0, min(1.0, (px * vx + py * vy) / (vx * vx + vy * vy or 1.0)))
    return math.hypot(px - vx * h, py - vy * h) - r


def _ellipse(x, y, cx, cy, rx, ry):
    return (math.hypot((x - cx) / rx, (y - cy) / ry) - 1.0) * min(rx, ry)


def _heart(x, y, cx, cy, s):
    """A heart s wide, its point down."""
    u, v = (x - cx) / s, (y - cy) / s
    lobes = min(math.hypot(u + 0.25, v + 0.12), math.hypot(u - 0.25, v + 0.12)) - 0.27
    point = max(abs(u) * 0.95 + v * 0.75 - 0.42, -v - 0.1)
    return min(lobes, point) * s


def _star(x, y, cx, cy, r):
    """A five-pointed star of radius r."""
    a = math.atan2(y - cy, x - cx) + math.pi / 2
    d = math.hypot(x - cx, y - cy)
    spike = 0.55 + 0.45 * abs(math.cos(2.5 * a))
    return d - r * spike


def _gear(x, y, cx, cy, r, turn):
    a = math.atan2(y - cy, x - cx) - math.radians(turn)
    d = math.hypot(x - cx, y - cy)
    teeth = r + (1.4 if math.cos(a * 8) > 0.3 else 0.0)
    return max(d - teeth, 1.6 - d)  # a ring with teeth: the hole in the middle


# --- the figure -----------------------------------------------------------------------------------

class _Space:
    """Screen pixels to design coordinates, for each part of a pose."""

    SIT_DROP = 3.0

    def __init__(self, p: Pose, cell: tuple[int, int]):
        self.p = p
        self.sx, self.sy = scale(cell)  # screen pixels a design pixel
        self.feet = GROUND - p.lift
        self.k = SCALE * p.squash
        tilt, lean, turn = math.radians(-p.tilt), math.radians(-p.lean), math.radians(p.turn)
        self.tilt = (math.cos(tilt), math.sin(tilt))
        self.lean = (math.cos(lean), math.sin(lean))
        self.turn = (math.cos(turn), math.sin(turn))
        # Turned, the head and the body are as wide as they are across and deep there, and what is
        # on their front has gone round by this much (an ellipse turned: its point at the front).
        self.head_across, self.head_round = self._round(HEAD_DEPTH)
        self.body_across, self.body_round = self._round(BODY_DEPTH)
        # Screen pixels to each part's design coordinates, as affine maps worked out once for the pose
        # (they were worked out step by step, legs to body to head, for every pixel).
        self.pixel = 1 / (self.sx * SCALE)  # design pixels a screen pixel across, legs, body and head
        self._ground = (self.pixel, 0.0, 55 - 55 / SCALE, 0.0, 1 / self.sy, 0.0)
        self._legs = (self.pixel, 0.0, 55 - 55 / SCALE, 0.0, 1 / (self.sy * self.k), GROUND - self.feet / self.k)
        body = _moved(self._legs, 0.0, -self.SIT_DROP) if p.sitting else self._legs
        self._body = _turned_about(body, *self.lean, 55, 70) if p.lean else body  # about the hips
        self._head = _moved(_turned_about(self._body, *self.tilt, 55, p.head_y + 40), 0.0, -p.head_y)
        self._helmet = _narrowed(self._head, self.head_across)
        self._torso = _narrowed(self._body, self.body_across)

    def across(self, space: str) -> float | None:
        """How far a screen pixel across goes, at most, in a space's design coordinates (_Space's
        methods): None on a turned front or back, which squeezes towards its sides without end."""
        if space in ("ground", "legs", "body", "head"):
            return self.pixel
        if space == "helmet" or (space == "face" and not self.head_round):
            return self.pixel / self.head_across
        if space == "torso" or (space == "chest" and not self.body_round):
            return self.pixel / self.body_across
        return None

    def _round(self, depth: float) -> tuple[float, float]:
        c, s = self.turn
        return math.hypot(c, depth * s), math.atan2(depth * s, c) if self.p.turn % 360 else 0.0

    def turned(self, x: float, z: float = 0.0) -> tuple[float, float]:
        """A point x across from the middle and z out in front of it, as Bit turns: how far across
        it is now, and how far in front (behind the middle when negative)."""
        c, s = self.turn
        return x * c + z * s, z * c - x * s

    def ground(self, x, y):
        """Where Bit stands: its shadow stays there when it hops."""
        ax, bx, cx, ay, by, cy = self._ground
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def legs(self, x, y):
        ax, bx, cx, ay, by, cy = self._legs
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def body(self, x, y):
        """The legs' coordinates, sitting lower, leaning about the hips."""
        ax, bx, cx, ay, by, cy = self._body
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def head(self, x, y):
        """The body's, the head tilted and bobbing."""
        ax, bx, cx, ay, by, cy = self._head
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def helmet(self, x, y):
        """The head's coordinates for its outline, as wide as it is turned."""
        ax, bx, cx, ay, by, cy = self._helmet
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def face(self, x, y):
        """The head's coordinates on its front, where the visor and the eyes are: turned, they go
        round to the side and out of sight, narrower as they go."""
        return _surface(*self.helmet(x, y), 35.0, self.head_round, True)

    def nape(self, x, y):
        """The head's coordinates on its back, seen as Bit turns away."""
        return _surface(*self.helmet(x, y), 35.0, self.head_round, False)

    def torso(self, x, y):
        """The body's coordinates for its outline, as wide as it is turned."""
        ax, bx, cx, ay, by, cy = self._torso
        return ax * x + bx * y + cx, ay * x + by * y + cy

    def chest(self, x, y):
        """The body's coordinates on its front (the panel, its light, the vents)."""
        return _surface(*self.torso(x, y), 17.5, self.body_round, True)

    def back(self, x, y):
        """The body's coordinates on its back."""
        return _surface(*self.torso(x, y), 17.5, self.body_round, False)

    def seen(self, left: float, right: float, part: str = "head", front: bool = True) -> tuple[float, float] | None:
        """Where across what lies from left to right on the front (or the back) of the head or the
        body shows as Bit turns, for its box: None when it has gone round out of sight."""
        half, gone, across = (35.0, self.head_round, self.head_across) if part == "head" else \
            (17.5, self.body_round, self.body_across)
        if not gone:
            return (left, right) if front else None
        xs = []
        for k in range(9):
            at = math.asin(max(-1.0, min(1.0, (left + (right - left) * k / 8 - 55) / half))) + (0.0 if front else math.pi)
            if math.cos(at + gone) >= 0:
                xs.append(55 + half * math.sin(at + gone) * across)
        return (min(xs) - 3, max(xs) + 3) if xs else None

    def to_screen(self, x, y, part="body"):
        """Where a design point lands on the screen, roughly (lean and tilt aside): for boxes."""
        drop = self.SIT_DROP if self.p.sitting and part != "legs" else 0.0
        head = self.p.head_y if part == "head" else 0.0
        sx = 55 + (x - 55) * SCALE
        sy = self.feet - (GROUND - y - drop - head) * self.k
        return sx * self.sx, sy * self.sy


# An affine map of screen pixels (x, y) to design coordinates (u, v): (ax, bx, cx, ay, by, cy) for
# u = ax*x + bx*y + cx and v = ay*x + by*y + cy.

def _moved(m: tuple, du: float, dv: float) -> tuple:
    ax, bx, cx, ay, by, cy = m
    return ax, bx, cx + du, ay, by, cy + dv


def _turned_about(m: tuple, c: float, s: float, ox: float, oy: float) -> tuple:
    """The map, then turned by the angle of cosine c and sine s about the design point (ox, oy)."""
    ax, bx, cx, ay, by, cy = m
    return (c * ax - s * ay, c * bx - s * by, c * (cx - ox) - s * (cy - oy) + ox,
            s * ax + c * ay, s * bx + c * by, s * (cx - ox) + c * (cy - oy) + oy)


def _narrowed(m: tuple, across: float) -> tuple:
    """The map, then squeezed to a width of across about the middle."""
    ax, bx, cx, ay, by, cy = m
    return ax / across, bx / across, (cx - 55) / across + 55, ay, by, cy


def _surface(x: float, y: float, half: float, gone: float, front: bool) -> tuple[float, float]:
    """A point of an outline half wide, turned: where on the front (or the back) of the part it is,
    across as if it faced you; far off where that side is turned away. Untouched facing you."""
    if not gone:
        return (x, y) if front else (FAR, y)
    if abs(x - 55) >= half:  # off the part's side: nothing of its front or back there
        return FAR, y
    seen = math.asin((x - 55) / half) - gone
    if (math.cos(seen) > 0) != front:
        return FAR, y
    return 55 + half * math.sin(seen) * (1 if front else -1), y


Paint = "callable[[float, float, float], Colour]"


def _shapes(p: Pose, s: _Space):
    """Back to front: (part, box in design coordinates, distance(x, y), paint(x, y, d))."""
    shapes = []

    def add(part, box, distance, paint):
        if box is not None:  # None: round the other side, out of sight
            shapes.append((part, box, distance, paint))

    flat = _flat

    spread = max(0.35, 1 - p.lift / 14)
    add("ground", (25, 74, 85, 80), lambda x, y: _ellipse(*s.ground(x, y), 55, GROUND - 0.3, 20 * spread, 1.8),
        flat(SHADOW))

    def on(left, top, right, bottom, part, front=True):
        """The box of what lies on the front (or the back) of the head or the body, where it shows
        as Bit turns (_Space.seen); None out of sight."""
        seen = s.seen(left, right, part, front)
        return None if seen is None else (seen[0], top, seen[1], bottom)

    arms = {-1: p.left, 1: p.right}
    # An arm is in front of the body when it reaches over it, or when Bit has turned it towards you.
    front = {side: s.turned(side * 20.5, 6.0 if arms[side].front else 0.0)[1] > 0 for side in (-1, 1)}
    for side in (-1, 1):  # arms behind the body first
        if not front[side]:
            shapes += _arm(side, arms[side], p, s)

    # The legs, the far one first; turned, they go round the middle, the feet pointing ahead.
    for side, lifted in sorted(((-1, p.left_foot), (1, p.right_foot)), key=lambda leg: s.turned(leg[0] * 9)[1]):
        hip = 55 + s.turned(side * 9)[0]
        toe = 55 + s.turned(side * 11, 1.5)[0]
        long = 6.0 * abs(s.turn[0]) + 4.5 * abs(s.turn[1])
        if p.sitting:
            continue
        top, sole = (68.0, 72.0) if p.tucked else (68.0, 75.6 - lifted)
        add(f"leg{side}", (hip - 4, top - 4, hip + 4, sole + 2),
            lambda x, y, h=hip, t=top, so=sole: _segment(*s.legs(x, y), h, t, h, so - 1.5, 2.8), flat(LIMB_SHADE))
        add(f"foot{side}", (toe - 9, sole - 3, toe + 9, sole + 3),
            lambda x, y, f=toe, so=sole, w=long: _rounded(*s.legs(x, y), f, so, w, 2.2, 2.0),
            lambda x, y, d, so=sole: SOLE if s.legs(x, y)[1] > so + 1.0 else BODY)

    add("neck", (45, 57, 65, 64), lambda x, y: _rounded(*s.body(x, y), 55, 60.5, 8, 2.6, 1.5), flat(NECK))
    add("body", (35, 58, 75, 74), lambda x, y: _rounded(*s.torso(x, y), 55, 66, 17.5, 6.5, 4.0),
        lambda x, y, d: BODY if s.body(x, y)[1] < 67.5 else BODY_SHADE)
    add("panel", on(46, 61, 64, 71, "body"), lambda x, y: _rounded(*s.chest(x, y), 55, 66, 7.5, 4.3, 2.0), flat(PANEL))
    add("chest", on(50, 61, 60, 71, "body"), lambda x, y: _circle(*s.chest(x, y), 55, 66, 3.0),
        lambda x, y, d: CHEST_SHINE if d < -1.6 and s.body(x, y)[1] < 65.2 else CHEST_LIGHT)
    for side in (-1, 1):  # vents on either side of the panel
        vx = 55 + side * 12.5
        add(f"vent{side}", on(vx - 2, 62, vx + 2, 70, "body"),
            lambda x, y, v=vx: _segment(*s.chest(x, y), v, 63.5, v, 68.5, 0.55), flat(BODY_SHADE))
    # Its back, turned away: a hatch where the panel is in front.
    add("hatch", on(48, 61, 62, 71, "body", front=False), lambda x, y: _rounded(*s.back(x, y), 55, 66, 6.0, 3.6, 1.6),
        flat(PANEL))

    if p.sitting:  # legs out in front, under the lowered body: wider than it, shoes at their ends
        add("lap", (33, 72, 77, 79), lambda x, y: _rounded(*s.legs(x, y), 55, 75.6, 19.5, 2.0, 2.0), flat(LIMB_SHADE))
        for side in (-1, 1):
            fx = 55 + side * 20
            lifted = p.left_foot if side < 0 else p.right_foot
            add(f"shoe{side}", (fx - 5, 68, fx + 5, 79),
                lambda x, y, f=fx, up=lifted: _rounded(*s.legs(x, y), f, 74.6 - up, 3.2, 2.9, 2.6),
                lambda x, y, d: SOLE if d > -1.0 else LIMB)

    # The head: ears, helmet, visor and eyes, the antenna. Turned, an ear goes round to the middle,
    # over the helmet on the side that comes to you and behind it on the other.
    ears = []
    for side in (-1, 1):
        ex, ahead = s.turned(side * 35.5)
        ex += 55
        ears.append((ahead > 0.5, f"ear{side}", (ex - 6, 35, ex + 6, 47),
                     lambda x, y, e=ex: _circle(*s.head(x, y), e, 41, 4.6),
                     lambda x, y, d, e=ex: HELMET_SHADE if _circle(*s.head(x, y), e, 41, 1.7) < 0 else HELMET_MID))
    for near, *ear in ears:
        if not near:
            add(*ear)
    add("helmet", (19, 19, 91, 61), lambda x, y: _rounded(*s.helmet(x, y), 55, 40, 35, 20, 9), _helmet(s))
    for near, *ear in ears:
        if near:
            add(*ear)
    # The back of the head, turned away: a grille where the visor is in front.
    add("grille", on(45, 34, 65, 50, "head", front=False),
        lambda x, y: min(_segment(*s.nape(x, y), 47, gy, 63, gy, 1.1) for gy in (37, 42, 47)), flat(HELMET_SHADE))
    add("visor", on(29, 28, 81, 55, "head"), lambda x, y: _rounded(*s.face(x, y), 55, 41.5, 25, 12.5, 6.5), _visor(s))
    shapes += _eyes(p, s)
    if p.blush:
        for side in (-1, 1):
            bx = 55 + side * 11
            add(f"blush{side}", on(bx - 4, 48, bx + 4, 52, "head"),
                lambda x, y, b=bx: _rounded(*s.face(x, y), b, 49.6, 2.6, 0.9, 0.9), flat(BLUSH))
    tip = _antenna_tip(p)
    tip = (55 + s.turned(tip[0] - 55)[0], tip[1])  # it sways across Bit, which turns
    add("stem", (45, 4, 65, 22), lambda x, y: _segment(*s.head(x, y), 55, 21, tip[0], tip[1] + 3, 1.3), flat(HELMET_SHADE))
    add("beacon", (tip[0] - 6, tip[1] - 6, tip[0] + 6, tip[1] + 6),
        lambda x, y: _circle(*s.head(x, y), *tip, 4.4),
        lambda x, y, d: BEACON_RIM if d > -0.9 else (
            BEACON_SHINE if _circle(*s.head(x, y), tip[0] - 1.4, tip[1] - 1.4, 1.2) < 0 else BEACON_LIGHT))

    for side in (-1, 1):  # arms in front of the body: on the laptop, holding the box, or turned to you
        if front[side]:
            shapes[len(shapes):] = _arm(side, arms[side], p, s)
    shapes += _prop(p, s)
    return shapes


def _helmet(s: _Space):
    def paint(x, y, d):
        hx, hy = s.helmet(x, y)  # the light stays where it is as Bit turns
        if d > -1.6 and hy < 31:
            return RIM
        t = max(0.0, min(1.0, (hy - 22) / 38)) * 0.7 + max(0.0, min(1.0, (hx - 26) / 60)) * 0.3
        return HELMET if t < 0.42 else HELMET_MID if t < 0.7 else HELMET_SHADE
    return paint


def _visor(s: _Space):
    def paint(x, y, d):
        hx, hy = s.face(x, y)
        across = hx - 34 + (hy - 31) * 0.9  # a glint running down to the right, top left
        if 0 <= across <= 3 and hy < 37:
            return GLINT
        if d > -1.2 and hy < 41:
            return VISOR_EDGE
        return VISOR
    return paint


def _antenna_tip(p: Pose) -> tuple[float, float]:
    a = math.radians(p.antenna)
    return 55 + math.sin(a) * 10, 21 - math.cos(a) * 10 - 0.5


def _eyes(p: Pose, s: _Space):
    shapes = []
    lx, ly = p.look
    shift = {"left": (-3, 0), "right": (3, 0), "up": (0, -3), "down": (0, 4)}.get(p.eyes, (0, 0))
    kind = {"left": "open", "right": "open", "up": "open", "down": "open"}.get(p.eyes, p.eyes)
    for side, ex in ((-1, 45.0), (1, 65.0)):
        cx, cy = ex + lx + shift[0], 41 + ly + shift[1]
        own = "happy" if kind == "wink" and side < 0 else "open" if kind == "wink" else kind

        def distance(x, y, own=own, side=side, cx=cx, cy=cy):
            return _eye(own, side, *s.face(x, y), cx, cy)
        seen = s.seen(cx - 8, cx + 8)
        if seen is None:  # round the side, out of sight
            continue
        shapes.append((f"eye{side}:{own}", (seen[0], cy - 8, seen[1], cy + 8), distance,
                       _flat(PINK if own == "heart" else YELLOW if own == "star" else EYE)))
    return shapes


def _eye(own: str, side: int, x: float, y: float, cx: float, cy: float) -> float:
    """One eye's distance, in the head's coordinates, as the kind of eye it is."""
    if own in ("open", "wide", "focused", "worried"):
        hw, hh = (4.8, 5.8) if own == "wide" else (4.0, 4.8)
        lid = _rounded(x, y, cx, cy, hw, hh, 2.2)
        if own in ("focused", "worried"):  # the top cut at a slant: inward for worried, outward for focused
            slope = 0.55 * (side if own == "worried" else -side)
            return max(lid, (cy - 1.5 + (x - cx) * slope) - y)
        return lid
    if own == "half":
        return _rounded(x, y, cx, cy + 2.2, 4.0, 2.4, 1.6)
    if own == "blink":
        return _rounded(x, y, cx, cy + 2.5, 4.3, 1.1, 1.0)
    if own == "closed":  # a little downturned line
        return min(_segment(x, y, cx - 4, cy + 2, cx, cy + 3.5, 1.1), _segment(x, y, cx, cy + 3.5, cx + 4, cy + 2, 1.1))
    if own == "happy":  # ^
        return min(_segment(x, y, cx - 4.2, cy + 3, cx, cy - 1.8, 1.5), _segment(x, y, cx, cy - 1.8, cx + 4.2, cy + 3, 1.5))
    if own == "heart":
        return _heart(x, y, cx, cy + 0.5, 11.0)
    if own == "star":
        return _star(x, y, cx, cy, 5.6)
    if own == "x":
        return min(_segment(x, y, cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, 1.2),
                   _segment(x, y, cx - 3.5, cy + 3.5, cx + 3.5, cy - 3.5, 1.2))
    if own == "dizzy":  # a ring with a dot: round and round
        return min(abs(_circle(x, y, cx, cy, 3.6)) - 1.1, _circle(x, y, cx, cy, 1.1))
    return abs(_circle(x, y, cx, cy, 3.8)) - 1.2  # surprised: a ring


def _flat(colour: Colour):
    def paint(x, y, d):
        return colour
    return paint


def _arm_points(side: int, arm: Arm):
    """Shoulder, elbow and hand of an arm, in design coordinates of the body."""
    sx, sy = 55 + side * 20.5, 63.0
    if arm.kind == "bent":
        return (sx, sy), arm.elbow, arm.to
    if arm.kind == "raise":
        ex, ey = sx + side * 22, sy - 5
        a = math.radians(arm.angle)
        return (sx, sy), (ex, ey), (ex + side * math.sin(a) * 17, ey - math.cos(a) * 17)
    if arm.kind == "reach":
        hx, hy = arm.to
        mx, my = (sx + hx) / 2, (sy + hy) / 2
        dx, dy = hx - sx, hy - sy
        n = math.hypot(dx, dy) or 1.0
        bulge = max(0.0, 14 - n) * 0.5  # a short reach bends the elbow out...
        ox, oy = dy / n * side, -dx / n * side  # ...and down, always that side: it never flips over
        return (sx, sy), (mx + ox * bulge, my + oy * bulge), (hx, hy)
    a = math.radians(arm.angle)
    hx, hy = sx + side * math.sin(a) * 13, sy + math.cos(a) * 13
    return (sx, sy), ((sx + hx) / 2, (sy + hy) / 2), (hx, hy)


def _arm(side: int, arm: Arm, p: Pose, s: _Space):
    ahead = 6.0 if arm.front else 0.0  # turned, an arm goes round with Bit, as thick as it is
    (sx, sy), (ex, ey), (hx, hy) = ((55 + s.turned(x - 55, ahead)[0], y) for x, y in _arm_points(side, arm))
    box = (min(sx, ex, hx) - 5, min(sy, ey, hy) - 5, max(sx, ex, hx) + 5, max(sy, ey, hy) + 5)
    return [
        (f"upper{side}", box, lambda x, y: _segment(*s.body(x, y), sx, sy, ex, ey, 2.6), lambda x, y, d: LIMB_SHADE),
        (f"fore{side}", box, lambda x, y: _segment(*s.body(x, y), ex, ey, hx, hy, 2.6),
         lambda x, y, d: LIMB if d < -0.9 else LIMB_SHADE),
        (f"hand{side}", (hx - 5, hy - 5, hx + 5, hy + 5), lambda x, y: _circle(*s.body(x, y), hx, hy, 3.5),
         lambda x, y, d: _mix(LIMB, RIM, 0.6) if _circle(*s.body(x, y), hx - 1.2, hy - 1.2, 1.1) < 0 else LIMB),
    ]


def _prop(p: Pose, s: _Space):
    if p.prop is None:
        return []
    px, py = p.prop_at
    if p.prop == "laptop":  # its lid's back to you, a light on it; the screen lights the bottom of the visor
        return [
            ("lid", (40, 63, 70, 77), lambda x, y: _rounded(*s.body(x, y), 55, 70.5, 12.5, 5.0, 1.6),
             lambda x, y, d: LID_EDGE if d > -1.0 else LID),
            ("logo", (52, 67, 58, 73), lambda x, y: _circle(*s.body(x, y), 55, 70.2, 1.3), lambda x, y, d: SKY),
            ("base", (38, 74, 72, 79), lambda x, y: _rounded(*s.body(x, y), 55, 76.0, 15.5, 1.3, 1.0),
             lambda x, y, d: LID_EDGE),
        ]
    if p.prop == "mug":
        return [
            ("mug", (px - 6, py - 6, px + 8, py + 6), lambda x, y: min(_rounded(*s.body(x, y), px, py, 3.2, 3.8, 1.0),
                                                                    abs(_circle(*s.body(x, y), px + 3.6, py, 2.0)) - 0.7),
             lambda x, y, d: RIM if s.body(x, y)[1] < py - 2.4 else ACCENT),
        ]
    if p.prop == "clipboard":
        return [
            ("board", (px - 9, py - 11, px + 9, py + 11), lambda x, y: _rounded(*s.body(x, y), px, py, 7.5, 9.5, 1.2),
             lambda x, y, d: CARD_SHADE if d > -1.2 else PAPER),
            ("lines", (px - 6, py - 6, px + 6, py + 8),
             lambda x, y: (abs(((s.body(x, y)[1] - py + 6) % 3.4) - 1.7) - 0.5
                           if abs(s.body(x, y)[0] - px) < 5 and -6 < s.body(x, y)[1] - py < 7 else 9.0),
             lambda x, y, d: (150, 156, 168)),
            ("clip", (px - 4, py - 12, px + 4, py - 7), lambda x, y: _rounded(*s.body(x, y), px, py - 9.5, 3.0, 1.4, 0.8),
             lambda x, y, d: STEEL),
        ]
    if p.prop == "wrench":
        a = math.radians(p.prop_turn)
        tx, ty = px + math.sin(a) * 12, py - math.cos(a) * 12
        return [
            ("handle", (min(px, tx) - 4, min(py, ty) - 4, max(px, tx) + 4, max(py, ty) + 4),
             lambda x, y: _segment(*s.body(x, y), px, py, tx, ty, 1.5), lambda x, y, d: STEEL),
            ("jaw", (tx - 6, ty - 6, tx + 6, ty + 6),
             lambda x, y: max(_circle(*s.body(x, y), tx, ty, 3.6), -_circle(*s.body(x, y), tx + math.sin(a) * 2.8,
                                                                          ty - math.cos(a) * 2.8, 1.8)),
             lambda x, y, d: STEEL),
        ]
    if p.prop == "box":
        return [
            ("box", (px - 13, py - 10, px + 13, py + 10), lambda x, y: _rounded(*s.body(x, y), px, py, 12.0, 8.5, 1.0),
             lambda x, y, d: CARD_SHADE if d > -1.2 or s.body(x, y)[1] > py + 5 else CARD),
            ("tape", (px - 2, py - 10, px + 2, py + 10), lambda x, y: _rounded(*s.body(x, y), px, py, 1.4, 8.5, 0.2),
             lambda x, y, d: (222, 196, 150)),
        ]
    if p.prop == "bulb":  # an idea, above the head
        return [
            ("glass", (px - 7, py - 8, px + 7, py + 6), lambda x, y: _circle(*s.head(x, y), px, py - 1.5, 5.0),
             lambda x, y, d: (255, 244, 190) if _circle(*s.head(x, y), px - 1.6, py - 3.2, 1.4) < 0 else YELLOW),
            ("screw", (px - 4, py + 2, px + 4, py + 8), lambda x, y: _rounded(*s.head(x, y), px, py + 5.0, 2.4, 1.8, 0.6),
             lambda x, y, d: STEEL),
        ]
    if p.prop == "gear":  # thinking it over: a gear turning above the head
        return [("gear", (px - 8, py - 8, px + 8, py + 8), lambda x, y: _gear(*s.head(x, y), px, py, 4.6, p.prop_turn),
                 lambda x, y, d: STEEL)]
    if p.prop == "dots":  # "..." in a little cloud
        n = int(p.prop_turn) % 4
        return [(f"dot{k}", (px - 9 + k * 6, py - 3, px - 3 + k * 6, py + 3),
                 lambda x, y, k=k: _circle(*s.head(x, y), px - 6 + k * 6, py, 1.6), lambda x, y, d: EYE)
                for k in range(n)]
    return []


def _mix(a: Colour, b: Colour, t: float) -> Colour:
    t = max(0.0, min(1.0, t))
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def size(cell: tuple[int, int] = WT_CELL) -> tuple[int, int]:
    """Pixels across and down of Bit's place: whole cells across, whole bands of six down."""
    return CELLS[0] * cell[0], (CELLS[1] * cell[1]) // 6 * 6


def scale(cell: tuple[int, int] = WT_CELL) -> tuple[float, float]:
    """Screen pixels a design pixel, across and down: ten across a cell, and the design's rows
    down the place's whole bands, so the feet stand inside it whatever the cell (13 pixels high,
    four rows of cells are 52, and the bands 48)."""
    return cell[0] / 10, size(cell)[1] / HEIGHT


def figure(pose: Pose, cell: tuple[int, int] = WT_CELL) -> list[list[Colour | None]]:
    """Bit in a pose: rows of colours, None where nothing is drawn (the strip shows through)."""
    palette, rows = indexed(Frame(pose), cell)
    return [[palette[n] for n in row] for row in rows]


def indexed(frame: Frame, cell: tuple[int, int] = WT_CELL, shift: int = 0) -> tuple[list[Colour | None], list[bytes]]:
    """A frame as a palette and rows of indices into it (0: nothing drawn), moved shift pixels
    right. The shapes are drawn once for a pose (drawn); the lights take their colour here, so a
    glow or a blink does not draw Bit again, and the effects are laid over a copy."""
    shapes, rows = drawn(replace(frame.pose, beacon=0.0, beacon_colour=ACCENT, chest=0.0, chest_colour=ACCENT), cell)
    lit = lights(frame.pose)
    palette = [lit.get(colour, colour) if colour is not None else None for colour in shapes]
    out: list = list(rows)
    if frame.effects:
        out = [bytearray(row) for row in out]
        _effects(palette, out, frame.effects, cell)
    if shift:
        blank = bytes(shift)
        out = [blank + bytes(row[:-shift]) for row in out]
    return palette, [bytes(row) for row in out]


def lights(pose: Pose) -> dict[Colour, Colour]:
    """The lights' stand-in colours, in this pose's colour and brightness: dim, they darken."""
    def light(colour: Colour, level: float) -> tuple[Colour, Colour]:
        body = _mix(_mix(colour, (28, 24, 22), 0.55), _mix(colour, (255, 236, 224), 0.3), level)
        return body, _mix(body, (255, 255, 255), 0.65)
    beacon, beacon_shine = light(pose.beacon_colour, pose.beacon)
    chest, chest_shine = light(pose.chest_colour, pose.chest)
    return {BEACON_LIGHT: beacon, BEACON_SHINE: beacon_shine, BEACON_RIM: _mix(beacon, (0, 0, 0), 0.4),
            CHEST_LIGHT: chest, CHEST_SHINE: chest_shine}


# How much each part's distance changes at most as its point moves a design pixel, and the
# coordinates it is measured in (_Space.across). Boxes, circles, segments and ellipses give true
# distances, which change no more than that: a pixel d outside one is d from it at least, and the
# pixels before that far need not be tried. Not here, tried pixel by pixel: the gear's teeth and the
# clipboard's lines (no true distance), and the back's hatch and grille (on a turned back, always).
REACH = {
    "ground": (1.0, "ground"), "leg": (1.0, "legs"), "foot": (1.0, "legs"), "lap": (1.0, "legs"),
    "shoe": (1.0, "legs"), "neck": (1.0, "body"), "body": (1.0, "torso"), "panel": (1.0, "chest"),
    "chest": (1.0, "chest"), "vent": (1.0, "chest"), "ear": (1.0, "head"), "helmet": (1.0, "helmet"),
    "visor": (1.0, "face"), "blush": (1.0, "face"), "stem": (1.0, "head"), "beacon": (1.0, "head"),
    "upper": (1.0, "body"), "fore": (1.0, "body"), "hand": (1.0, "body"), "lid": (1.0, "body"),
    "logo": (1.0, "body"), "base": (1.0, "body"), "mug": (1.0, "body"), "board": (1.0, "body"),
    "clip": (1.0, "body"), "handle": (1.0, "body"), "jaw": (1.0, "body"), "box": (1.0, "body"),
    "tape": (1.0, "body"), "glass": (1.0, "head"), "screw": (1.0, "head"), "dot": (1.0, "head"),
}
EYE_REACH = {"focused": 1.15, "worried": 1.15, "heart": 1.25, "star": None}  # the rest: 1 (_eye)


def _reach(part: str, s: _Space) -> float | None:
    """How far a part's distance changes at most from one screen pixel to the next across: None
    where that is not known."""
    name, _, eye = part.partition(":")
    name = name.rstrip("-0123456789")
    bound, space = (EYE_REACH.get(eye, 1.0), "face") if name == "eye" else REACH.get(name, (None, ""))
    across = s.across(space) if bound is not None else None
    return None if across is None else bound * across * 1.01  # a little over: rounding never skips a pixel


@lru_cache(maxsize=1024)
def drawn(pose: Pose, cell: tuple[int, int] = WT_CELL) -> tuple[tuple[Colour | None, ...], tuple[bytes, ...]]:
    """The shapes of a pose, the lights in their stand-in colours: a palette and rows of indices
    into it, a byte a pixel, so that hundreds of poses kept take a few megabytes.

    Front to back: a pixel a nearer part took is not tried again for the ones behind it. And along
    a row, a pixel outside a part skips the next pixels its distance says cannot be inside (_reach)."""
    width, height = size(cell)
    s = _Space(pose, cell)
    palette: list[Colour | None] = [None]
    index: dict[Colour, int] = {}
    out = [bytearray(width) for _ in range(height)]
    for part, box, distance, paint in reversed(_shapes(pose, s)):
        x0, y0, x1, y1 = _screen_box(box, part, s, width, height)
        reach = _reach(part, s)
        for y in range(y0, y1):
            row = out[y]
            cy = y + 0.5
            x = x0
            while x < x1:
                if row[x]:
                    x += 1
                    continue
                cx = x + 0.5
                d = distance(cx, cy)
                if d <= 0:
                    colour = paint(cx, cy, d)
                    n = index.get(colour)
                    if n is None:
                        n = index[colour] = len(palette)
                        palette.append(colour)
                    row[x] = n
                    x += 1
                elif reach is None:
                    x += 1
                else:
                    x += max(1, int(d / reach))
    return tuple(palette), tuple(bytes(row) for row in out)


def _screen_box(box, part, s: _Space, width: int, height: int) -> tuple[int, int, int, int]:
    """The screen pixels a part's design box can reach, tilted or leaning, plus a margin."""
    which = "head" if part in ("helmet", "visor", "grille", "stem", "beacon", "glass", "screw", "gear") or part.startswith(
        ("ear", "eye", "blush", "dot")) else "legs" if part.startswith(("leg", "foot", "shoe")) or part in ("ground", "lap") else "body"
    corners = [s.to_screen(x, y, which) for x in (box[0], box[2]) for y in (box[1], box[3])]
    spin = 4 + abs(s.p.tilt) * 0.6 + abs(s.p.lean) * 0.6  # rotation can carry a corner this far
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (max(0, int(min(xs) - spin * s.sx)), max(0, int(min(ys) - spin * s.sy)),
            min(width, int(max(xs) + spin * s.sx) + 1), min(height, int(max(ys) + spin * s.sy) + 1))


# --- effects -------------------------------------------------------------------------------------

GLYPHS = {
    "z": ["####", "...#", "..#.", ".#..", "####"],
    "note": ["..##", "..#.", "..#.", "###.", "##.."],
    "bang": ["#", "#", "#", ".", "#"],
    "ask": [".##.", "#..#", "..#.", "....", "..#."],
    "sweat": [".#.", "###", "###", ".#."],
    "heart": [".#.#.", "#####", ".###.", "..#.."],
    "star": ["..#..", "#####", ".###.", "#.#.#"],
}
EFFECT_COLOUR = {"z": ZZZ, "note": NOTE, "bang": YELLOW, "ask": EYE, "sweat": WATER, "heart": PINK,
                 "star": YELLOW, "dust": DUST, "spark": YELLOW, "steam": (170, 176, 186)}


def _effects(palette: list[Colour | None], rows: list[bytearray], effects: list[Effect],
             cell: tuple[int, int] = WT_CELL) -> None:
    """The effects laid over Bit's pixels: glyphs of a few pixels, dots and puffs."""
    height, width = len(rows), len(rows[0]) if rows else 0
    sx, sy = scale(cell)
    unit = max(1, round(sx))  # a glyph's pixel: one screen pixel in Windows Terminal
    index = {colour: n for n, colour in enumerate(palette)}

    def put(x: int, y: int, colour: Colour, alpha: float) -> None:
        if 0 <= x < width and 0 <= y < height and alpha > 0.5:
            n = index.get(colour)
            if n is None and len(palette) < 255:
                n = index[colour] = len(palette)
                palette.append(colour)
            if n is not None:
                rows[y][x] = n

    for e in effects:
        colour = e.colour or EFFECT_COLOUR.get(e.kind, EYE)
        x, y = round((55 + (e.x - 55) * SCALE) * sx), round((GROUND - (GROUND - e.y) * SCALE) * sy)
        if e.kind in GLYPHS:
            dot = max(1, round(e.size)) * unit
            for gy, line in enumerate(GLYPHS[e.kind]):
                for gx, c in enumerate(line):
                    if c == "#":
                        for yy in range(dot):
                            for xx in range(dot):
                                put(x + gx * dot + xx, y + gy * dot + yy, colour, e.alpha)
        elif e.kind in ("dust", "steam"):
            r = max(1.0, e.size * 2 * sx)
            for yy in range(int(y - r), int(y + r) + 1):
                for xx in range(int(x - r), int(x + r) + 1):
                    if (xx - x) ** 2 + (yy - y) ** 2 <= r * r:
                        put(xx, yy, colour, e.alpha)
        else:  # confetti, sparks: a dot or a short dash
            n = max(1, round(e.size * unit))
            for yy in range(n):
                for xx in range(n + (1 if e.kind == "confetti" else 0)):
                    put(x + xx, y + yy, colour, e.alpha)


def frame_pixels(frame: Frame, cell: tuple[int, int] = WT_CELL) -> list[list[Colour | None]]:
    """A frame as rows of colours, None where nothing is drawn."""
    palette, rows = indexed(frame, cell)
    return [[palette[n] for n in row] for row in rows]


def themed(pose: Pose, accent: Colour) -> Pose:
    """The pose with the lights in the dashboard's accent, unless they are red for a call."""
    return replace(pose, beacon_colour=accent if pose.beacon_colour == ACCENT else pose.beacon_colour,
                   chest_colour=accent if pose.chest_colour == ACCENT else pose.chest_colour)


def _towards(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _round_towards(a: float, b: float, t: float, whole: float) -> float:
    """An angle t of the way to another, the short way round."""
    return a + ((b - a + whole / 2) % whole - whole / 2) * t


def arm_between(side: int, a: Arm, b: Arm, t: float) -> Arm:
    """An arm t of the way from one place to another: the upper arm turning about the shoulder and
    the elbow bending, so it keeps its length and swings, never shrinks, on the way (an arm coming
    down from over the head unfolds outwards, not across the face)."""
    if t <= 0:
        return a
    if t >= 1 or a == b:
        return b
    whole = 2 * math.pi
    (shoulder, elbow_a, hand_a), (_, elbow_b, hand_b) = _arm_points(side, a), _arm_points(side, b)

    # Angles as either arm sees them: 0 out from the body, a quarter turn down, a half in. Counted
    # from where an arm never points (up and in, over its own head) and from a fold it never makes,
    # so that an arm on its way, while the other pose moves too, never flips to the other way round.
    up_in, fold = -2 * math.pi / 3, -3 * math.pi / 4

    def angle(p, q):
        return math.atan2(q[1] - p[1], side * (q[0] - p[0]))

    def counted(a, never):
        return (a - never) % whole + never

    upper_a, upper_b = counted(angle(shoulder, elbow_a), up_in), counted(angle(shoulder, elbow_b), up_in)
    bend_a = counted(angle(elbow_a, hand_a) - upper_a, fold)
    bend_b = counted(angle(elbow_b, hand_b) - upper_b, fold)
    upper = _towards(upper_a, upper_b, t)
    fore = upper + _towards(bend_a, bend_b, t)
    reach = _towards(math.dist(shoulder, elbow_a), math.dist(shoulder, elbow_b), t)
    elbow = shoulder[0] + side * math.cos(upper) * reach, shoulder[1] + math.sin(upper) * reach
    reach = _towards(math.dist(elbow_a, hand_a), math.dist(elbow_b, hand_b), t)
    hand = elbow[0] + side * math.cos(fore) * reach, elbow[1] + math.sin(fore) * reach
    return Arm("bent", to=hand, elbow=elbow, front=b.front if t >= 0.5 else a.front)


def travel(a: Pose, b: Pose) -> float:
    """How far the hands and elbows go from one pose to another, along the way between() takes
    them, in design pixels: the farther, the longer a move takes to come out of the last one."""
    longest = 0.0
    for side, arm_a, arm_b in ((-1, a.left, b.left), (1, a.right, b.right)):
        points = [_arm_points(side, arm_between(side, arm_a, arm_b, k / 8))[1:] for k in range(9)]
        for joint in (0, 1):
            longest = max(longest, sum(math.dist(p[joint], q[joint]) for p, q in pairwise(points)))
    return longest


def between(a: Pose, b: Pose, t: float) -> Pose:
    """A pose t of the way from one to another, for a move taking over from the last: what moves
    goes there, what does not (the eyes, a prop, the lights' colour) is the new one's, and sitting
    or tucked up halfway."""
    if t >= 1 or a == b:
        return b
    half = b if t >= 0.5 else a
    return replace(
        b, lift=_towards(a.lift, b.lift, t), squash=_towards(a.squash, b.squash, t),
        turn=_round_towards(a.turn, b.turn, t, 360) % 360, lean=_towards(a.lean, b.lean, t),
        tilt=_towards(a.tilt, b.tilt, t), head_y=_towards(a.head_y, b.head_y, t),
        antenna=_towards(a.antenna, b.antenna, t), left_foot=_towards(a.left_foot, b.left_foot, t),
        right_foot=_towards(a.right_foot, b.right_foot, t), tucked=half.tucked, sitting=half.sitting,
        left=arm_between(-1, a.left, b.left, t), right=arm_between(1, a.right, b.right, t),
        look=(_towards(a.look[0], b.look[0], t), _towards(a.look[1], b.look[1], t)),
        beacon=_towards(a.beacon, b.beacon, t), chest=_towards(a.chest, b.chest, t))
