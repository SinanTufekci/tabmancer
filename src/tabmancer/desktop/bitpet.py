"""Bit in the window, alive: the terminal's mascot (bitart draws it, bitmoves moves it) as a pet on the floor of
its panel, its line beside it.

Its life (Life) is the terminal's (mascot.Mascot), timed the window's way: still between moves, a move now and
then (a stroll, a sit, a stretch; at the laptop while sessions work), a call while a session needs you, and in the
end a doze when nothing happens. A click: a hop (three quick ones make it dizzy), or awake again.

Cheap when nothing happens: a still Bit is one picture, made once (Sprites keeps it). The 20 fps timer runs only
while Bit moves; a blink, the next move, a call or a doze is a single-shot timer, and asleep it breathes twice a
second. Nothing runs while its panel or the window is out of sight.

b picks how it lives, as in the terminal (config.json "mascot", one choice for both): lively ("pixels"; the
terminal's "blocks" reads so too), calm (bitmoves.CALM: no hops or spins, fewer moves) or away (no Bit; its line
stays). View ▸ Reduce motion on top: calm, or off (a still Bit, its pose changing with the news alone)."""

from __future__ import annotations

import random
import time
from collections.abc import Iterable

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QHideEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    qRgba,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from .. import bitart, bitmoves
from ..config import merge_json, read_json
from ..rowwords import SPINNING
from ..sessions import Exit, Session
from ..viewmodel import state_of
from . import look

FPS = 20
FRAME_MS = 1000 // FPS
CELL = (6, 12)  # the window's Bit, 66x48 (bitart.size), as big as the still one before it
SPRITE_W, SPRITE_H = bitart.size(CELL)
OCTANT = CELL[0] / 2  # screen pixels a terminal octant pixel, which bitmoves' speeds go in
ROAM = 120  # pixels right of its home (the panel's left) Bit strolls, its line going along beside it
GAP = 10  # between Bit and its line
LEAST_LINE = 160  # the line's narrowest: Bit strolls less in a narrow panel
BLINK_MS = (3500, 8000)
MOVE_MS = (25_000, 70_000)  # a move this often, standing about...
WORK_MOVE_MS = (8_000, 20_000)  # ...and at the laptop this often while sessions work
CALL_FOR = 48  # frames of a call (bitmoves' call: the arms up, waved and down again)
CALL_GAP_MS = 8_000  # between two calls while a session needs you
CALL_HELD = 20  # motion off: a call held still at this frame, its arms up
LEAST_ROOM = 10  # octant pixels of floor a stroll needs: less, and Bit looks about instead
DOZE_MS = 90_000  # this long without news, nothing at work and nobody waiting: Bit dozes off (the terminal's)
SLEEP_MS = 500  # asleep: a breath this often (bitmoves' sleep SLEEP_STEP frames on)
SLEEP_STEP = 8
BLEND = 6  # frames, at least, a new move takes to come out of the last one's pose (mascot's)...
BLEND_PACE = 6.0  # ...and design pixels a hand goes a frame on its way
JUMP_SPEED = 0.45
DIZZY_CLICKS = 3  # clicks this close together (CLICK_S) make Bit dizzy
CLICK_S = 1.5
REST = "rest"  # standing still: the idle pose at its first frame, the same picture until something happens
STILL = (REST, "sleep")
DROWSY = ("yawn", "nod", "sleep")
WAYS = ("pixels", "calm", "away")  # b, in turn: lively, calm, away
SAID = {"pixels": "Bit, lively.", "calm": "Bit, calm: fewer moves, no hops.", "away": "Bit is away: b brings it back."}


def saved_way() -> str:
    """b's choice (config.json "mascot", bitart.way_of): lively ("pixels", the terminal's blocks too), calm or
    away."""
    saved = read_json("config.json")
    way = bitart.way_of(saved.get("mascot") if isinstance(saved, dict) else None)
    return "pixels" if way == "blocks" else way


def next_way(way: str) -> str:
    return WAYS[(WAYS.index(way) + 1) % len(WAYS)] if way in WAYS else WAYS[0]


def save_way(way: str) -> bool:
    return merge_json("config.json", mascot=way)


def mood(sessions: Iterable[Session], hidden: Iterable[str] = ()) -> tuple[int, int]:
    """(at work, needing you) among the open sessions in sight, as the terminal's Bit counts them."""
    out_of_sight = set(hidden)
    open_now = [s for s in sessions if s.exit is Exit.RUNNING and s.session_id not in out_of_sight]
    return (sum(s.live_state.lower() in SPINNING for s in open_now),
            sum(state_of(s)[2] == 0 for s in open_now))


def rgb(colour: str) -> bitart.Colour:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


class Life:
    """Bit's life, a frame at a time while it moves (advance), still between: the terminal's Mascot without its
    strip. x is in the terminal's octant pixels from home (bitmoves' speeds go in them)."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.action = REST
        self.left = 0  # frames the move has still to go
        self.beat = 0  # frames into it
        self.frame = 0  # frames lived
        self.x = 0.0
        self.direction = 1
        self.distance = 0.0
        self.gait = 0.0
        self.height = 0.0
        self.velocity = 0.0
        self.jumps_left = 0
        self.eyes = "normal"
        self.eyes_left = 0
        self.antenna = 0.0
        self.antenna_speed = 0.0
        self.calm = False
        self.working = 0
        self.waiting = 0
        self.calls = 0
        self.clicks: list[float] = []
        self.posed: bitart.Pose | None = None
        self.blend_from: bitart.Pose | None = None
        self.blend_for: int | None = None

    # --- what it is doing ------------------------------------------------------------------------

    def resting(self) -> bool:
        return self.action == REST

    def drowsy(self) -> bool:
        return self.action in DROWSY

    def blending(self) -> bool:
        return self.blend_from is not None and (self.blend_for is None or self.beat < self.blend_for)

    def moving(self) -> bool:
        """Whether the next frame can differ: a move under way, on its way out of the last one's pose, off the
        ground, a blink, or the antenna still swinging."""
        return (self.action not in STILL or self.blending() or self.height > 0 or self.velocity != 0
                or self.eyes_left > 0 or abs(self.antenna) > 0.5 or abs(self.antenna_speed) > 0.5)

    def speed(self) -> float:
        if self.action == "walk" and self.working:
            return 0.6
        trait = bitmoves.TRAITS.get(self.action)
        return trait.speed if trait else 0.0

    # --- what starts a move ----------------------------------------------------------------------

    def start(self, action: str, frames: int) -> None:
        if self.calm:
            action = bitmoves.CALM.get(action, action)
        self.action, self.left, self.beat = action, frames, 0
        self.blend_from, self.blend_for = self.posed, None

    def rest(self) -> None:
        """Back to standing still, out of the move's pose."""
        self.start(REST, 0)

    def look(self, eyes: str, frames: int) -> None:
        self.eyes, self.eyes_left = eyes, frames

    def blink(self) -> None:
        self.look("blink", 3)

    def set_mood(self, working: int, waiting: int) -> bool:
        """working: sessions at work; waiting: sessions that need you. A first one waiting: a call at once; none
        any more: the call ends. Whether anything changed (news: it wakes Bit)."""
        news = (working, waiting) != (self.working, self.waiting)
        first = waiting and not self.waiting
        self.working, self.waiting = working, waiting
        if first:
            self.call()
        elif not waiting and self.action == "call":
            self.rest()
        return news

    def call(self) -> None:
        """A call: its arms up, waved (pointing, bouncing: each call another way) and down, eyes wide."""
        self.start("call", CALL_FOR)
        self.look("alarm", CALL_FOR)

    def next_move(self, span: float) -> None:
        """A move now and then: the terminal's choices (fewer while calm), at the laptop while sessions work."""
        if self.calm:
            choices = bitmoves.CALM_WORK_CHOICES if self.working else bitmoves.CALM_IDLE_CHOICES
        else:
            choices = bitmoves.WORK_CHOICES if self.working else bitmoves.IDLE_CHOICES
        action = self.rng.choice(choices)
        if bitmoves.TRAITS[action].speed and span < LEAST_ROOM:
            action = "look"  # no room to stroll
        self.start(action, self.rng.randint(*bitmoves.TRAITS[action].frames))
        if self.speed():  # off towards the farther end of its floor
            self.direction = 1 if self.x < span / 2 else -1
        if self.action == "look":
            self.look(self.rng.choice(("left", "right")), 20)
        elif self.action == "wave":
            self.look("happy", 36)
        elif self.action == "jump":
            self.jumps_left = 1

    def doze(self) -> None:
        self.start("yawn", bitmoves.TRAITS["yawn"].frames[0])

    def rouse(self) -> None:
        """Woken from a doze: a start, and up."""
        self.start("wake", bitmoves.TRAITS["wake"].frames[0])
        self.jumps_left = 1
        self.look("alarm", 8)

    def click(self, now: float) -> None:
        """Clicked: awake if drowsy, else a hop, or dizzy after a few quick clicks."""
        if self.drowsy():
            self.rouse()
            return
        self.clicks = [t for t in self.clicks if now - t < CLICK_S] + [now]
        if len(self.clicks) >= DIZZY_CLICKS:
            self.clicks = []
            self.start("dizzy", 40)
            return
        self.start("jump", 20)
        self.jumps_left = 1
        self.look("happy", 20)

    # --- a frame ---------------------------------------------------------------------------------

    def ended(self) -> None:
        """A move done: a yawn nods off, a nod falls asleep; anything else stands still again."""
        if self.action == "yawn":
            self.start("nod", bitmoves.TRAITS["nod"].frames[0])
        elif self.action == "nod":
            self.start("sleep", 0)
            self.eyes, self.eyes_left = "sleep", 0
        else:
            if self.action == "call":
                self.calls += 1  # the next call goes another way (bitmoves.CALLS)
            self.rest()

    def advance(self, span: float) -> None:
        """One frame, span octant pixels of floor to stroll in."""
        self.frame += 1
        self.beat += 1
        if self.action not in STILL:
            self.left -= 1
            if self.left <= 0:
                self.ended()
        speed = self.speed()
        if speed:
            self.x += speed * self.direction
            self.distance += speed
            self.gait += speed / bitmoves.stride_length(speed)
            if (self.x <= 0 and self.direction < 0) or (self.x >= span and self.direction > 0):
                self.direction *= -1
        self.x = max(0.0, min(self.x, max(0.0, span)))
        if self.calm:
            self.jumps_left = 0
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
        lean = -self.direction * min(12.0, speed * 18)  # the antenna on a spring: it trails, and settles
        self.antenna_speed += (lean - self.antenna) * 0.25 - self.antenna_speed * 0.3
        self.antenna = max(-30.0, min(30.0, self.antenna + self.antenna_speed))
        if abs(self.antenna) <= 0.5 and abs(self.antenna_speed) <= 0.5 and not speed:
            self.antenna = self.antenna_speed = 0.0  # settled: the still picture again
        if self.eyes_left:
            self.eyes_left -= 1
            if not self.eyes_left:
                self.eyes = "normal"

    def picture(self, accent: bitart.Colour) -> bitart.Frame:
        """Bit this frame: the action's pose with what the life knows, out of the last move's pose."""
        red = bool(self.waiting)
        still = self.resting()
        context = bitmoves.Context(
            beat=0 if still else self.beat, frame=0 if still else self.frame, direction=self.direction,
            distance=self.distance, gait=self.gait, height=self.height,
            eyes="normal" if self.eyes == "down" and self.action != "work" else self.eyes, antenna=self.antenna,
            beacon=(1.0 if self.frame % 10 < 5 or still else 0.15) if red else 0.6, beacon_red=red,
            working=bool(self.working),
            call="wave" if self.calm else bitmoves.CALLS[self.calls % len(bitmoves.CALLS)])
        made = bitmoves.frame("idle" if still else self.action, context)
        pose = bitart.themed(made.pose, accent)
        if self.blend_from is not None:
            if self.blend_for is None:
                way = bitart.travel(self.blend_from, pose)
                self.blend_for = max(BLEND, min(BLEND * 3, round(way / BLEND_PACE)))
            if self.beat < self.blend_for:
                pose = bitmoves.steady(bitart.between(self.blend_from, pose, bitmoves.ease((self.beat + 1)
                                                                                          / (self.blend_for + 1))))
            else:
                self.blend_from = None
        self.posed = pose
        return bitart.Frame(pose, made.effects)


def effects_key(frame: bitart.Frame) -> tuple:
    return tuple((e.kind, round(e.x * 2) / 2, round(e.y * 2) / 2, e.size, round(e.alpha, 1), e.colour)
                 for e in frame.effects)


class Sprites:
    """Bit's pictures, each made once: a still Bit, a blink and the poses a move comes round to are drawn again
    from here (bitmoves.steady makes poses repeat)."""

    def __init__(self, most: int = 600) -> None:
        self.most = most
        self.kept: dict[tuple, QPixmap] = {}

    def get(self, frame: bitart.Frame) -> tuple[tuple, QPixmap]:
        key = (frame.pose, effects_key(frame))
        pixmap = self.kept.get(key)
        if pixmap is None:
            if len(self.kept) >= self.most:
                self.kept.clear()
            pixmap = self.kept[key] = sprite(frame)
        return key, pixmap


def sprite(frame: bitart.Frame) -> QPixmap:
    """A frame as a picture, clear where nothing is drawn (bitart's palette and rows of indices: one byte a
    pixel, an 8-bit image with that palette)."""
    palette, rows = bitart.indexed(frame, CELL)
    data = b"".join(rows)
    image = QImage(data, SPRITE_W, SPRITE_H, SPRITE_W, QImage.Format.Format_Indexed8)
    image.setColorTable([qRgba(0, 0, 0, 0) if colour is None else qRgba(*colour, 255) for colour in palette])
    return QPixmap.fromImage(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))


class Line(QLabel):
    """Bit's line: words told it change the floor's height for them (a long note wraps)."""

    def setText(self, text: str) -> None:
        super().setText(text)
        floor = self.parentWidget()
        if floor is not None:
            floor.updateGeometry()


def single(owner: QObject, then) -> QTimer:
    made = QTimer(owner)
    made.setSingleShot(True)
    made.timeout.connect(then)
    return made


class Pet(QWidget):
    """Bit's floor in its panel: Bit where it stands (home at the left, strolling up to ROAM pixels right), its
    line beside it, as the terminal's words go beside its Bit."""

    def __init__(self, accent: str, seed: int | None = None) -> None:
        super().__init__()
        self.accent = rgb(accent)
        self.life = Life(random.Random(seed))
        self.sprites = Sprites()
        self.way = "pixels"
        self.motion = "full"
        self.at = QRect()  # where Bit is drawn: none away
        self.pixmap: QPixmap | None = None
        self.shown: tuple | None = None
        self.line = Line(self)
        self.line.setFont(look.font(9.5))
        self.line.setStyleSheet(f"color:{look.MUTED}; background:transparent;")
        self.line.setWordWrap(True)
        self.line.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(SPRITE_H)
        self.ticker = QTimer(self)
        self.ticker.setInterval(FRAME_MS)
        self.ticker.timeout.connect(self.step)
        self.blinker = single(self, self.blink)
        self.mover = single(self, self.stroll)
        self.caller = single(self, self.call)
        self.dozer = single(self, self.doze)
        self.breather = single(self, self.breathe)
        self.timers = (self.ticker, self.blinker, self.mover, self.caller, self.dozer, self.breather)
        self.draw()

    # --- what the window tells it ----------------------------------------------------------------

    def set_way(self, way: str) -> None:
        """b's choice: lively, calm or away."""
        self.way = way if way in WAYS else "pixels"
        self.changed()

    def set_motion(self, mode: str) -> None:
        """View ▸ Reduce motion (teamgraph.motion: calm too while Bit is calm)."""
        self.motion = mode
        self.changed()

    def changed(self) -> None:
        self.life.calm = self.way == "calm" or self.motion == "calm"
        self.hold_still()
        self.pause()
        self.draw()
        self.resume()

    def set_mood(self, working: int, waiting: int) -> None:
        """What the sessions do (mood): news wakes Bit; one needing you, a call."""
        if not self.life.set_mood(working, waiting):
            return
        if self.lively() and self.life.drowsy() and not waiting:
            self.life.rouse()
        self.hold_still()
        self.dozer.stop()
        self.draw()
        self.go()

    def hold_still(self) -> None:
        """Motion off: no move plays. A call holds its arms up, eyes wide; anything else stands, eyes open."""
        if self.motion != "off":
            return
        life = self.life
        if life.action == "call":
            life.beat = CALL_HELD
        else:
            if not life.resting():
                life.rest()
            life.eyes = "normal"
        life.eyes_left, life.blend_from = 0, None
        life.height = life.velocity = life.antenna = life.antenna_speed = 0.0

    # --- its timers ------------------------------------------------------------------------------

    def lively(self) -> bool:
        """Whether Bit moves at all now: it is here, may move, and is in sight."""
        window = self.window()
        return (self.way != "away" and self.motion != "off" and self.isVisible()
                and not (window is not None and window.isMinimized()))

    def go(self) -> None:
        """The frame timer while Bit moves; still, the single-shot timers for what comes next."""
        if not self.lively():
            self.ticker.stop()
            return
        if self.life.moving():
            if not self.ticker.isActive():
                self.ticker.start()
            return
        self.ticker.stop()
        self.arm()

    def arm(self) -> None:
        """Still: the next blink, move, call, doze or breath, each a single shot (one running is left be)."""
        life = self.life
        asleep = life.action == "sleep"

        def once(timer: QTimer, ms: int) -> None:
            if not timer.isActive():
                timer.start(ms)

        if not asleep:
            once(self.blinker, life.rng.randint(*BLINK_MS))
        if life.waiting:
            self.mover.stop()
            once(self.caller, CALL_GAP_MS)
        else:
            self.caller.stop()
            if not asleep:
                once(self.mover, life.rng.randint(*(WORK_MOVE_MS if life.working else MOVE_MS)))
        if not (life.working or life.waiting or life.drowsy()):
            once(self.dozer, DOZE_MS)
        if asleep and not life.calm:
            once(self.breather, SLEEP_MS)

    def pause(self) -> None:
        for timer in self.timers:
            timer.stop()

    def resume(self) -> None:
        if self.lively():
            self.go()

    def step(self) -> None:
        self.life.advance(self.span())
        self.draw()
        self.go()

    def blink(self) -> None:
        if self.life.resting():
            self.life.blink()
        self.go()

    def stroll(self) -> None:
        if self.life.resting() and not self.life.waiting:
            self.life.next_move(self.span())
        self.go()

    def call(self) -> None:
        if self.life.waiting and self.life.action in STILL:
            self.life.call()
        self.go()

    def doze(self) -> None:
        if self.life.resting() and not (self.life.working or self.life.waiting):
            self.life.doze()
        else:
            self.dozer.start(DOZE_MS)
        self.go()

    def breathe(self) -> None:
        if self.life.action == "sleep":
            self.life.frame += SLEEP_STEP
            self.draw()
        self.go()

    # --- drawing ---------------------------------------------------------------------------------

    def roam(self) -> int:
        """Pixels of floor right of home Bit strolls in: ROAM, less where the line would get too narrow."""
        return max(0, min(ROAM, self.width() - SPRITE_W - GAP - LEAST_LINE))

    def span(self) -> float:
        return self.roam() / OCTANT

    def draw(self) -> None:
        """Bit as the life has it now, drawn again only where it was and is, and only when it changed."""
        if self.way == "away":
            key, pixmap, at = None, None, QRect()
        else:
            key, pixmap = self.sprites.get(self.life.picture(self.accent))
            x = round(min(self.life.x, self.span()) * OCTANT)
            at = QRect(x, self.height() - SPRITE_H, SPRITE_W, SPRITE_H)
        if key == self.shown and at == self.at:
            return
        moved = at.topLeft() != self.at.topLeft() or at.isNull() != self.at.isNull()
        old, self.at, self.pixmap, self.shown = self.at, at, pixmap, key
        self.update(old.united(at))
        if moved:
            self.place_line()

    def place_line(self) -> None:
        """The line beside Bit (at the left, Bit away), as wide wherever Bit strolls."""
        left = 0 if self.at.isNull() else self.at.x() + SPRITE_W + GAP
        wide = self.width() - (0 if self.at.isNull() else SPRITE_W + GAP + self.roam())
        self.line.setGeometry(left, 0, max(1, wide), self.height())

    def line_width(self, width: int) -> int:
        return width - (0 if self.way == "away" else SPRITE_W + GAP + max(0, min(ROAM, width - SPRITE_W - GAP
                                                                                   - LEAST_LINE)))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(SPRITE_H, self.line.heightForWidth(max(1, self.line_width(width))))

    def sizeHint(self) -> QSize:
        return QSize(SPRITE_W + GAP + LEAST_LINE, SPRITE_H)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.pixmap is not None and not self.at.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.at.topLeft(), self.pixmap)
            painter.end()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.life.x = min(self.life.x, self.span())
        self.shown = None  # its place changes with the floor's height
        self.draw()
        self.place_line()

    # --- in sight or not -------------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        window = self.window()
        if window is not self:  # its minimising stops Bit; installed again, it is still installed once. Never
            window.installEventFilter(self)  # kept here: a window held by its own Bit would outlive its close
        self.resume()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self.pause()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowStateChange and watched is self.window():
            if self.lively():
                self.resume()
            else:
                self.pause()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.way == "away" or not self.at.contains(event.position().toPoint()) or self.motion == "off":
            event.ignore()
            return
        self.life.click(time.monotonic())
        self.dozer.stop()
        self.go()
