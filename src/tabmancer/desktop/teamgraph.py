"""The team as a picture that moves (the Orbit): the team widget of `tabmancer desktop`.

The lead in the middle, the members around it, a wire for each pair that has talked, thicker the
more it carried. A new message flies along its wire from the sender to the receiver with its first
words riding along, the receiver ripples when it lands, and the wire glows in the sender's colour,
then fades. Working members breathe, a member that needs you pulses red, one at rest is quiet, a
closed one dim. A member that joins grows in where it belongs while the others make room; one that
leaves shrinks away. Hover says the words; a click asks for the member's tab and a right-click for
its actions, which the window holding the widget answers (signals).

Motion (config.json "motion", the windows' View menu): full; calm, fewer moves (a message still
travels, as a plain dot; nothing breathes, ripples or grows), also whenever Bit is calm (b); off,
nothing moves (a message lights its wire for a moment). The same facts in each.

Nothing runs while nothing moves: one clock ticks 30 times a second while a message flies, a wire
fades or a member comes or goes, and stops after. Breathing, the one lasting move, ticks ten times a
second and draws only the moving rings over a still picture, and not at all while the window is hidden
or minimized. A wire lit in calm or off goes out with one more paint.
Which messages fly is the dashboard's rule (viewmodel.new_flights): none on the first look, then each
new one.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap
from PySide6.QtWidgets import QToolTip, QWidget

from .. import teams
from .. import viewmodel as VM
from ..config import merge_json, read_json
from . import look
from .widgets import tone_colour

MOTIONS = {"full": "Full motion", "calm": "Calm: fewer moves", "off": "No motion"}
FLY_SECONDS = 1.2  # a message on its way (the terminal's cells take VM.FLIGHT_SECONDS; pixels move faster)
STAGGER = 0.35  # messages read in one poll leave one after another
MOST_FLYING = 6  # a burst read at once (after a sleep): its newest fly, the rest only count on their wires
GLOW_SECONDS = 1.6  # a wire's glow fading after its message lands
LIT_SECONDS = 2.0  # calm and off: a wire lit, then not
RIPPLE_SECONDS = 0.7
MOVE_SECONDS = 0.5  # members making room, growing in, shrinking away
FRAME_MS = 33
BREATH_MS = 100
BREATH_PERIOD = 2.4
NEEDS_PERIOD = 1.3
YOU = "you"  # the node your messages (and tabmancer's prompts) come from
WIRE = "#2e2e2e"
NODE = "#151515"
QUIET = "#0e0e0e"


def saved_motion() -> str:
    """The motion picked in the View menu (config.json), full when none was."""
    saved = read_json("config.json")
    mode = saved.get("motion") if isinstance(saved, dict) else None
    return mode if mode in MOTIONS else "full"


def motion() -> str:
    """How much moves: the picked motion, calm at most while Bit is calm (b in the dashboard)."""
    saved = read_json("config.json")
    mode = saved_motion()
    if mode == "full" and isinstance(saved, dict) and saved.get("mascot") == "calm":
        return "calm"
    return mode


def save_motion(mode: str) -> bool:
    return merge_json("config.json", motion=mode)


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ease(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out(x: float) -> float:
    return 1 - (1 - clamp(x)) ** 3


def tinted(colour: str, alpha: float) -> QColor:
    out = QColor(colour)
    out.setAlpha(max(0, min(255, int(alpha))))
    return out


def one_line(text: str) -> str:
    return " ".join(text.split())


def node_of(name: str, members: set[str]) -> str | None:
    if name in members:
        return name
    return YOU if name in (teams.YOU, teams.TABMANCER) else None


RESTING = ("idle", "open", "shell")  # open, waiting for its lead or a command (VM.state_id)


def standing(member: VM.MemberView) -> str:
    """How a member is drawn: needs (red, pulsing), working (blue, breathing), stopped (amber: at a limit,
    to continue), resting (open, done for now) or closed (its session ended, or never started: dim)."""
    if member.needs_you or member.tone.startswith("your-turn"):
        return "needs"
    if member.tone == "working":
        return "working"
    if member.tone.startswith("to-continue"):
        return "stopped"
    return "resting" if member.state in RESTING or member.tone in ("open", "aside") else "closed"


def facts_line(member: VM.MemberView) -> str:
    """'sonnet 5.5 · 51%': its model as the team view says it, and how full its context is."""
    context = f"{round(member.context * 100)}%" if member.context is not None else ""
    return " · ".join(part for part in (VM.model_words(member), context) if part)


@dataclass
class Flight:
    sender: str  # nodes: member names or YOU
    receiver: str
    words: str
    colour: str
    start: float

    @property
    def lands(self) -> float:
        return self.start + FLY_SECONDS


def wire_path(a: QPointF, b: QPointF, centre: QPointF) -> QPainterPath:
    """A gentle curve from a to b: nearly straight to and from the middle, bowed away from it between
    two members."""
    mid = (a + b) * 0.5
    length = math.hypot((b - a).x(), (b - a).y()) or 1.0
    d = QPointF((b - a).x() / length, (b - a).y() / length)
    side = QPointF(-d.y(), d.x())
    if centre in (a, b):
        ctrl = mid + side * (length * 0.06)
    else:
        away = mid - centre
        if side.x() * away.x() + side.y() * away.y() < 0 or (abs(away.x()) + abs(away.y()) < 1 and side.y() > 0):
            side = side * -1
        ctrl = mid + side * (length * 0.42)
    path = QPainterPath(a)
    path.quadTo(ctrl, b)
    return path


class TeamGraph(QWidget):
    """The Orbit. set() gives it the team (a TeamView, what was said, the members' colours, the pick)
    each poll; compact draws it for a narrow panel (dots and names)."""

    member_clicked = Signal(str)
    member_menu = Signal(str, QPoint)

    def __init__(self, compact: bool = False, now: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.compact, self.now = compact, now
        self.view: VM.TeamView | None = None
        self.colours: dict[str, str] = {}
        self.picked: str | None = None
        self.mode = motion()
        self.seen: set | None = None
        self.flights: list[Flight] = []
        self.you = False  # you said something to the team: your corner is drawn
        self.names: list[str] = []
        self.moved_at = -math.inf  # when members last came or went
        self.came: set[str] = set()
        self.went: dict[str, QPointF] = {}  # leaving: where each was
        self.was: dict[str, QPointF] = {}  # where each stood before the last change
        self.drawn: dict[str, QPointF] = {}  # where each was drawn last
        self.radius: dict[str, float] = {}
        self.watched: QObject | None = None
        self.version = 0  # each set() and motion change: the still picture is drawn again
        self.still: QPixmap | None = None  # everything but the breathing members, while only they move
        self.still_key: tuple = ()
        self.frames = QTimer(self)
        self.frames.setInterval(FRAME_MS)
        self.frames.timeout.connect(self.on_frame)
        self.breaths = QTimer(self)
        self.breaths.setInterval(BREATH_MS)
        self.breaths.timeout.connect(self.on_breath)
        self.lights = QTimer(self)  # calm and off: the paint that puts a lit wire out
        self.lights.setSingleShot(True)
        self.lights.timeout.connect(self.lights_out)
        self.setMouseTracking(True)
        self.setMinimumSize(180, 140) if compact else self.setMinimumSize(360, 260)

    # --- what it is given ---------------------------------------------------------------------------

    def set(self, view: VM.TeamView, said: list[teams.Message], colours: dict[str, str],
            picked: str | None = None) -> None:
        now = self.now()
        members = {m.name for m in view.members}
        self.seen, new = VM.new_flights(self.seen, said, members, you=True)
        names = [m.name for m in view.members]
        if names != self.names:
            self.members_changed(names, now)
        self.view, self.colours, self.picked = view, colours, picked
        self.version += 1
        self.you = any(YOU in (node_of(m.sender, members), node_of(m.receiver, members)) for m in said)
        for i, message in enumerate(new[-MOST_FLYING:]):
            a, b = node_of(message.sender, members), node_of(message.receiver, members)
            if a is None or b is None or a == b:
                continue
            start = now + (i * STAGGER if self.mode == "full" else 0.0)
            colour = str(colours.get(message.sender) or look.TEXT) if a != YOU else look.TEXT
            self.flights.append(Flight(a, b, one_line(message.summary or message.text), colour, start))
        self.wake()
        self.update()

    def set_motion(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.version += 1
            self.wake()
            self.update()

    def members_changed(self, names: list[str], now: float) -> None:
        """Called before the new view is taken: where each stands at this moment is still the old view's."""
        if self.names and self.mode == "full":
            where = self.placed(now)[0]
            self.was = where
            self.came = set(names) - set(self.names)
            self.went = {name: where[name] for name in self.names if name not in names and name in where}
            self.moved_at = now
        self.names = names

    # --- the clock: running only while something moves ------------------------------------------

    def busy(self, now: float) -> bool:
        if self.mode == "off":
            return False
        tail = GLOW_SECONDS if self.mode == "full" else 0.0
        if any(f.lands + tail > now for f in self.flights):
            return True
        return self.mode == "full" and now - self.moved_at < MOVE_SECONDS

    def breathing(self) -> bool:
        if self.mode != "full" or self.view is None:
            return False
        return any(standing(m) in ("working", "needs") for m in self.view.members)

    def can_move(self) -> bool:
        window = self.window()
        return self.isVisible() and not window.isMinimized()

    def wake(self) -> None:
        """Run the frames while something moves, the breaths while only members breathe, nothing else."""
        now = self.now()
        self.flights = [f for f in self.flights if f.lands + max(GLOW_SECONDS, LIT_SECONDS) > now]
        if not self.can_move():
            self.frames.stop()
            self.breaths.stop()
            return
        if self.busy(now):
            self.breaths.stop()
            if not self.frames.isActive():
                self.frames.start()
        else:
            self.frames.stop()
            if self.breathing():
                if not self.breaths.isActive():
                    self.breaths.start()
            else:
                self.breaths.stop()
        left = [f.lands + LIT_SECONDS - now for f in self.flights if f.lands + LIT_SECONDS > now]
        if self.mode != "full" and left and not self.frames.isActive():
            self.lights.start(int(min(left) * 1000) + 30)

    def lights_out(self) -> None:
        self.update()
        self.wake()

    def on_frame(self) -> None:
        self.update()
        if not self.busy(self.now()):
            self.wake()

    def on_breath(self) -> None:
        if self.view is None or not self.can_move():
            self.wake()
            return
        for member in self.view.members:
            if standing(member) in ("working", "needs") and member.name in self.drawn:
                spot, reach = self.drawn[member.name], self.radius.get(member.name, 20) + 34
                self.update(QRect(int(spot.x() - reach), int(spot.y() - reach), int(reach * 2), int(reach * 2)))

    def showEvent(self, event) -> None:
        window = self.window()
        if window is not self and window is not self.watched:
            window.installEventFilter(self)
            self.watched = window
        self.wake()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.frames.stop()
        self.breaths.stop()
        self.lights.stop()
        super().hideEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self.wake)
        return False

    # --- where things are -------------------------------------------------------------------------

    def stands(self) -> tuple[dict[str, QPointF], QPointF]:
        """Where each member stands now (and you, when you wrote), and the middle."""
        view = self.view
        rect = QRectF(self.rect())
        if view is None:
            return {}, rect.center()
        others = [m.name for m in view.members if not (m.lead and view.lead == m.name)]
        lead = view.lead if view.lead in {m.name for m in view.members} else None
        n = len(others)
        r = 12.0 if self.compact else max(18.0, min(32.0, min(rect.width(), rect.height()) * 0.07))
        if n > 6:
            r = max(10.0 if self.compact else 14.0, r * 6 / n)
        under = 26 if self.compact else 42  # the words under a member
        centre = QPointF(rect.center().x(), rect.top() + (rect.height() - under) / 2 + 6)
        rx = max(20.0, min(rect.width() * 0.36, rect.width() / 2 - r - (34 if self.compact else 70)))
        ry = max(16.0, min(rect.height() * 0.36, (rect.height() - under) / 2 - r - 8))
        out = {lead: centre} if lead else {}
        angles = [0.0] if n == 1 else [180.0, 0.0] if n == 2 else [90 + 360 * (i + 1) / n for i in range(n)]
        for name, angle in zip(others, angles):
            a = math.radians(angle)
            out[name] = QPointF(centre.x() + rx * math.cos(a), centre.y() + ry * math.sin(a))
        self.radius = {name: r * (1.22 if name == lead else 1.0) for name in out}
        if self.you:
            corner = 22 if self.compact else 34
            out[YOU] = QPointF(rect.left() + corner, rect.top() + corner)
            self.radius[YOU] = 9 if self.compact else 15
        return out, centre

    def placed(self, now: float) -> tuple[dict[str, QPointF], QPointF, dict[str, float]]:
        """Where each is drawn at this moment: making room, growing in (scale), as the last change goes."""
        spots, centre = self.stands()
        grow = {name: 1.0 for name in spots}
        moving = self.mode == "full" and now - self.moved_at < MOVE_SECONDS
        if moving:
            share = ease((now - self.moved_at) / MOVE_SECONDS)
            for name, spot in spots.items():
                if name in self.came:
                    grow[name] = ease_out((now - self.moved_at) / MOVE_SECONDS)
                elif name in self.was:
                    start = self.was[name]
                    spots[name] = start + (spot - start) * share
        return spots, centre, grow

    def pairs(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for wire in self.view.wires if self.view is not None else []:
            key = tuple(sorted((wire["from"], wire["to"])))
            out[key] = out.get(key, 0) + int(wire.get("count", 0))
        return out

    # --- drawing ----------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.view is None or not self.view.members:
            p.fillRect(event.rect(), QColor(look.SUNKEN))
            p.setPen(QColor(look.DIM))
            p.setFont(look.font(9))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter,
                       "Reading the team…" if self.view is None else "No members yet.")
            return
        now = self.now()
        spots, centre, grow = self.placed(now)
        self.drawn = dict(spots)
        if self.mode == "full" and not self.busy(now):  # only breathing: a still picture, the rings over it
            p.drawPixmap(0, 0, self.still_picture(spots, centre, now))
        else:
            p.fillRect(event.rect(), QColor(look.SUNKEN))
            self.draw_scene(p, spots, centre, grow, now)
        for member in self.view.members:
            if member.name in spots:
                self.draw_aura(p, member, spots[member.name], grow.get(member.name, 1.0), now)
        self.draw_riders(p, spots, centre, now)

    def still_picture(self, spots: dict[str, QPointF], centre: QPointF, now: float) -> QPixmap:
        """The picture while only members breathe: drawn once for each set(), size and motion."""
        ratio = self.devicePixelRatioF()
        key = (self.version, self.width(), self.height(), ratio)
        if self.still is None or self.still_key != key:
            picture = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            picture.setDevicePixelRatio(ratio)
            picture.fill(QColor(look.SUNKEN))
            q = QPainter(picture)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            self.draw_scene(q, spots, centre, {name: 1.0 for name in spots}, now)
            q.end()
            self.still, self.still_key = picture, key
        return self.still

    def draw_scene(self, p: QPainter, spots: dict[str, QPointF], centre: QPointF, grow: dict[str, float],
                   now: float) -> None:
        """Everything but what moves round the members (their auras) and the riders."""
        assert self.view is not None
        if self.view.lead is None and not self.compact:
            p.setPen(QColor(look.DIM))
            p.setFont(look.font(8))
            p.drawText(QRectF(centre.x() - 60, centre.y() - 8, 120, 16), Qt.AlignmentFlag.AlignCenter, "peers")
        self.draw_wires(p, spots, centre, now)
        self.draw_flights(p, spots, centre, now)
        for name, spot in self.went.items():
            share = (now - self.moved_at) / MOVE_SECONDS
            if share < 1 and self.mode == "full":
                p.setOpacity(1 - ease(share))
                p.setPen(QPen(QColor("#3a3a3a"), 1.6))
                p.setBrush(QColor(QUIET))
                r = self.radius.get(name, 18) * (1 - ease(share))
                p.drawEllipse(spot, r, r)
                p.setOpacity(1.0)
        for member in self.view.members:
            if member.name in spots:
                self.draw_member(p, member, spots[member.name], grow.get(member.name, 1.0), self.view.peers)
        if YOU in spots:
            self.draw_you(p, spots[YOU])

    def flight_path(self, flight: Flight, spots: dict[str, QPointF], centre: QPointF) -> tuple[QPainterPath, bool] | None:
        a, b = spots.get(flight.sender), spots.get(flight.receiver)
        if a is None or b is None:
            return None
        if YOU in (flight.sender, flight.receiver):
            path = QPainterPath(a)
            path.quadTo(QPointF(a.x(), b.y()) if flight.sender == YOU else QPointF(b.x(), a.y()), b)
            return path, False
        first, second = sorted((flight.sender, flight.receiver))
        return wire_path(spots[first], spots[second], centre), flight.sender != first

    def lit(self, flight: Flight, now: float) -> float:
        if self.mode != "full":
            return 1.0 if flight.start <= now < flight.lands + LIT_SECONDS else 0.0
        if now < flight.start:
            return 0.0
        if now < flight.lands:
            return 0.35 + 0.65 * ease((now - flight.start) / FLY_SECONDS)
        return max(0.0, 1 - (now - flight.lands) / GLOW_SECONDS)

    def draw_wires(self, p: QPainter, spots: dict[str, QPointF], centre: QPointF, now: float) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        for (a, b), count in self.pairs().items():
            if a in spots and b in spots:
                width = (0.8 + 0.4 * min(count, 6)) if self.compact else (1.0 + 0.8 * min(count, 6))
                p.setPen(QPen(QColor(WIRE), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(wire_path(spots[a], spots[b], centre))
        for flight in self.flights:
            glow = self.lit(flight, now)
            found = self.flight_path(flight, spots, centre) if glow > 0 else None
            if found is not None:
                width = (1.5 if self.compact else 2.4) + 2.4 * glow
                p.setPen(QPen(tinted(flight.colour, 150 * glow), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(found[0])

    def draw_flights(self, p: QPainter, spots: dict[str, QPointF], centre: QPointF, now: float) -> None:
        if self.mode == "off":
            return
        for flight in self.flights:
            if not flight.start <= now < flight.lands:
                continue
            found = self.flight_path(flight, spots, centre)
            if found is None:
                continue
            path, backwards = found
            at = ease((now - flight.start) / FLY_SECONDS)
            size = 3.6 if self.compact else 6.0
            tail = 12 if self.mode == "full" else 0
            p.setPen(Qt.PenStyle.NoPen)
            for k in range(tail, -1, -1):
                u = at - k * 0.022
                if u < 0:
                    continue
                spot = path.pointAtPercent(clamp(1 - u if backwards else u))
                p.setBrush(tinted(flight.colour, 230 * (1 - k / (tail + 1)) ** 1.6))
                r = size * (1 - k / 15)
                p.drawEllipse(spot, r, r)
            head = path.pointAtPercent(clamp(1 - at if backwards else at))
            p.setBrush(tinted("#ffffff", 220))
            p.drawEllipse(head, size * 0.42, size * 0.42)

    def draw_riders(self, p: QPainter, spots: dict[str, QPointF], centre: QPointF, now: float) -> None:
        """The first words of each message in flight, riding over its head (full motion, wide only)."""
        if self.mode != "full" or self.compact:
            return
        font = look.font(8.2)
        metrics = QFontMetricsF(font)
        for flight in self.flights:
            if not flight.start <= now < flight.lands:
                continue
            found = self.flight_path(flight, spots, centre)
            if found is None:
                continue
            path, backwards = found
            share = (now - flight.start) / FLY_SECONDS
            head = path.pointAtPercent(clamp(1 - ease(share) if backwards else ease(share)))
            fade = min(ease(share / 0.15), ease((1 - share) / 0.2))
            text = metrics.elidedText(flight.words, Qt.TextElideMode.ElideRight, 250)
            box = QRectF(head.x() - 60, head.y() - 34, metrics.horizontalAdvance(text) + 16, 20)
            box.moveLeft(max(4.0, min(box.left(), self.width() - box.width() - 4)))
            box.moveTop(max(4.0, box.top()))
            p.setPen(QPen(tinted(flight.colour, 150 * fade), 1))
            p.setBrush(tinted("#161616", 235 * fade))
            p.drawRoundedRect(box, 10, 10)
            p.setFont(font)
            p.setPen(tinted(look.TEXT, 255 * fade))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def draw_you(self, p: QPainter, spot: QPointF) -> None:
        r = self.radius.get(YOU, 12)
        p.setPen(QPen(QColor("#3a3a3a"), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(QColor("#0d0d0d"))
        p.drawEllipse(spot, r, r)
        p.setFont(look.font(7 if self.compact else 8.5, True))
        p.setPen(QColor(look.TEXT))
        p.drawText(QRectF(spot.x() - 30, spot.y() - 10, 60, 20), Qt.AlignmentFlag.AlignCenter, "you")

    def draw_aura(self, p: QPainter, member: VM.MemberView, spot: QPointF, grow: float, now: float) -> None:
        """What moves round a member, over it and outside its ring: the red halo of one that needs you, the
        breath of one at work, the ripple of a message landing (full motion; calm: the halo stands still)."""
        name, way = member.name, standing(member)
        r = self.radius.get(name, 18) * grow
        if r <= 0.5:
            return
        full = self.mode == "full"
        p.setBrush(Qt.BrushStyle.NoBrush)
        if way == "needs":
            for k in (0.0, 0.5) if full else (0.0,):
                share = ((now / NEEDS_PERIOD) + k) % 1 if full else 0.35
                reach = r + 4 + share * (12 if self.compact else 24)
                p.setPen(QPen(tinted(look.RED, 170 * (1 - share)), 2.2))
                p.drawEllipse(spot, reach, reach)
        elif way == "working" and full:
            b = self.breath(name, now)
            width = 4 + 4 * b
            p.setPen(QPen(tinted(look.BLUE, 40 + 80 * b), width))
            p.drawEllipse(spot, r + 2.4 + width / 2, r + 2.4 + width / 2)
        if full:
            for flight in self.flights:  # a message arriving: a ripple
                since = now - flight.lands
                if flight.receiver == name and 0 <= since < RIPPLE_SECONDS:
                    e = ease_out(since / RIPPLE_SECONDS)
                    reach = r + 4 + e * (16 if self.compact else 30)
                    p.setPen(QPen(tinted(flight.colour, 200 * (1 - e)), 2))
                    p.drawEllipse(spot, reach, reach)

    def draw_member(self, p: QPainter, member: VM.MemberView, spot: QPointF, grow: float, peers: bool) -> None:
        """A member as it stands: its ring and name, its state and model under it (nothing here moves)."""
        name, way = member.name, standing(member)
        r = self.radius.get(name, 18) * grow
        if r <= 0.5:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == self.picked:
            p.setPen(QPen(QColor(self.colours.get(name, look.TEXT)), 1.4, Qt.PenStyle.DotLine))
            p.drawEllipse(spot, r + 7, r + 7)
        ring = {"working": QColor(look.BLUE), "needs": QColor(look.RED), "stopped": QColor(look.AMBER),
                "resting": tinted(tone_colour(member.tone), 150), "closed": QColor("#3a3a3a")}[way]
        p.setPen(QPen(ring, 1.8 if self.compact else 2.2, Qt.PenStyle.DashLine if way == "closed" else Qt.PenStyle.SolidLine))
        p.setBrush(QColor(QUIET if way in ("resting", "closed") else NODE))
        p.drawEllipse(spot, r, r)
        own = self.colours.get(name, look.TEXT) if way != "closed" else look.DIM
        if not self.compact:  # its name inside; in a narrow panel under it, whole
            font = look.font(9.6, True)
            text = QFontMetricsF(font).elidedText(name, Qt.TextElideMode.ElideRight, r * 2 - 4)
            p.setFont(font)
            p.setPen(QColor(own))
            p.drawText(QRectF(spot.x() - r, spot.y() - r, r * 2, r * 2), Qt.AlignmentFlag.AlignCenter, text)
        if member.lead and not peers:
            p.setFont(look.font(7 if self.compact else 9))
            p.setPen(QColor(own))
            p.drawText(QRectF(spot.x() - 10, spot.y() - r - (14 if self.compact else 20), 20, 14),
                       Qt.AlignmentFlag.AlignCenter, "★")
        if member.unread:
            badge = QPointF(spot.x() + r * 0.75, spot.y() - r * 0.75)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(look.ORANGE))
            p.drawEllipse(badge, 7, 7)
            p.setFont(look.font(6.8, True))
            p.setPen(QColor(look.BLACK))
            p.drawText(QRectF(badge.x() - 7, badge.y() - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, str(member.unread))
        if grow < 1:
            return
        tone = tone_colour(member.tone) if way != "closed" else look.DIM
        if self.compact:
            p.setFont(look.font(7.4, True))
            p.setPen(QColor(own))
            p.drawText(QRectF(spot.x() - 50, spot.y() + r + 1, 100, 12), Qt.AlignmentFlag.AlignCenter, name)
            p.setFont(look.font(6.8))
            p.setPen(QColor(tone))
            p.drawText(QRectF(spot.x() - 50, spot.y() + r + 12, 100, 12), Qt.AlignmentFlag.AlignCenter, member.label)
            return
        p.setFont(look.font(8.4, True))
        p.setPen(QColor(tone))
        p.drawText(QRectF(spot.x() - 90, spot.y() + r + 6, 180, 16), Qt.AlignmentFlag.AlignCenter, member.label)
        facts = facts_line(member)
        if facts:
            p.setFont(look.font(8))
            p.setPen(QColor(look.MUTED if way not in ("closed", "resting") else look.DIM))
            p.drawText(QRectF(spot.x() - 90, spot.y() + r + 22, 180, 16), Qt.AlignmentFlag.AlignCenter, facts)

    def breath(self, name: str, now: float) -> float:
        phase = (sum(map(ord, name)) % 10) / 10
        return 0.5 + 0.5 * math.sin(2 * math.pi * (now / BREATH_PERIOD + phase))

    # --- the mouse ----------------------------------------------------------------------------------

    def member_at(self, point: QPointF) -> str | None:
        for name, spot in self.drawn.items():
            if name != YOU and math.hypot((point - spot).x(), (point - spot).y()) <= self.radius.get(name, 18) + 4:
                return name
        return None

    def words_at(self, point: QPointF) -> str:
        """What the pointer is over, in the words a tooltip says: a member, a message flying, a wire."""
        view = self.view
        if view is None:
            return ""
        name = self.member_at(point)
        if name is not None:
            member = next(m for m in view.members if m.name == name)
            colour = self.colours.get(name, look.TEXT)
            facts = facts_line(member)
            lines = [look.span(name, f"bold {colour}") + look.span("  " + member.label, tone_colour(member.tone))]
            if facts:
                lines.append(look.span(facts, look.MUTED))
            if member.last is not None:
                mine = member.last.sender == name
                other = member.last.receiver if mine else member.last.sender
                lines.append(look.span(("→ " if mine else "← ") + other + "  ", f"bold {self.colours.get(other, look.TEXT)}")
                             + look.span(member.last.text[:200], look.TEXT))
            lines.append(look.span("click: its tab · right-click: its actions", look.DIM))
            return "<br>".join(lines)
        now = self.now()
        spots, centre, _ = self.placed(now)
        for flight in self.flights:
            found = self.flight_path(flight, spots, centre) if flight.start <= now < flight.lands else None
            if found is not None:
                share = ease((now - flight.start) / FLY_SECONDS)
                head = found[0].pointAtPercent(clamp(1 - share if found[1] else share))
                if math.hypot((point - head).x(), (point - head).y()) < 14:
                    return (look.span(f"{flight.sender} → {flight.receiver}  ", f"bold {flight.colour}")
                            + look.span(flight.words[:240], look.TEXT))
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        for (a, b), count in self.pairs().items():
            if a in spots and b in spots and stroker.createStroke(wire_path(spots[a], spots[b], centre)).contains(point):
                return look.span(f"{a} ↔ {b}", f"bold {look.TEXT}") + look.span(
                    f"  {count} message{'s' * (count != 1)}", look.MUTED)
        return ""

    def mouseMoveEvent(self, event) -> None:
        words = self.words_at(event.position())
        if words:
            QToolTip.showText(event.globalPosition().toPoint(), words, self)
        else:
            QToolTip.hideText()
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.member_at(event.position())
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        name = self.member_at(event.position())
        if name is not None and event.button() == Qt.MouseButton.LeftButton:
            self.member_clicked.emit(name)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        name = self.member_at(QPointF(event.pos()))
        if name is not None:
            self.member_menu.emit(name, event.globalPos())

