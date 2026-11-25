"""The team's conversation as lanes, under the Orbit (the full Team view and `tabmancer desktop
--monitor`): the terminal team view's sequence diagram (teamtree.sequence) drawn in pixels.

A column a lane: you on the left when you (or tabmancer) said anything, then the lead and the
members, then one for anyone else who wrote to them (viewmodel.lanes_of). A row a message, oldest at
the top: its time, an arrow from the sender's lane to the receiver's, and what it said over the arrow;
a line naming the day before a day's first message, when not today. A message on its way is a dashed
arrow from a hollow dot. With a member picked, its lane and its arrows stay lit and the rest go
quiet. Below a member's last message its lane says how it stands now: red while it needs you, blue
while it works.

It keeps the newest in sight at the bottom unless you scrolled up. In full motion a new message
draws its arrow from the sender to the receiver and types its words; calm and off show it at once.
Nothing runs otherwise.
"""

from __future__ import annotations

import bisect
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip

from .. import teams, teamwords
from .. import viewmodel as VM
from ..palette import mix
from . import look
from .teamgraph import ease, standing, tinted

TIME_W = 56  # "15:04" before the lanes
HEAD_H = 28  # the lanes' names, kept in sight
ROW_H = 36  # a message: its words over its arrow
DAY_H = 24  # "── Tue 24 Nov ──"
WORDS_Y, ARROW_Y = 5, 27  # in a message's row: the top of its words, its arrow
LANE_LEAST = 76  # a lane's least width: more lanes than fit scroll sideways
DRAW_SECONDS = 0.6  # a new message drawing its arrow and typing its words
FRAME_MS = 33
LANE_LINE = "#2a2a2a"
NOTHING = "Nothing said yet: messages between the members show up here."


@dataclass
class Row:
    top: float  # in the whole list, the lanes' names above it
    message: teams.Message | None  # None: a day's line
    words: str  # the message's gist, or the day's line


class Conversation(QAbstractScrollArea):
    """set() gives it the team, what was said, the team's view (how each member stands), the colours
    and the pick, each poll."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.now = now
        self.team: teams.Team | None = None
        self.view: VM.TeamView | None = None
        self.said: list[teams.Message] = []
        self.colours: dict[str, str] = {}
        self.picked: str | None = None
        self.mode = "full"
        self.lanes: list[str] = []
        self.rows: list[Row] = []
        self.tops: list[float] = []
        self.last_top: dict[str, float] = {}  # each lane's last message
        self.length = 0.0
        self.keys: set | None = None  # the messages seen: None before the first look
        self.drawing: tuple[teams.Message, float] | None = None  # the new one drawing itself, and since when
        self.frames = QTimer(self)
        self.frames.setInterval(FRAME_MS)
        self.frames.timeout.connect(self.on_frame)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(ROW_H // 2)
        self.setMinimumHeight(120)

    # --- what it is given ---------------------------------------------------------------------------

    def set(self, team: teams.Team, said: list[teams.Message], view: VM.TeamView | None,
            colours: dict[str, str], picked: str | None = None) -> None:
        keys = {(m.sender, m.receiver, m.text) for m in said}
        newest = said[-1] if said else None
        if (self.keys is not None and newest is not None and self.mode == "full"
                and (newest.sender, newest.receiver, newest.text) not in self.keys):
            self.drawing = (newest, self.now())
        self.keys = keys
        self.team, self.said, self.view, self.colours, self.picked = team, list(said), view, colours, picked
        self.lanes = VM.lanes_of(team, said)
        self.lay_out()
        self.wake()
        self.viewport().update()

    def clear(self) -> None:
        """Another team: nothing read yet (its messages so far will not draw themselves)."""
        self.team, self.view, self.said, self.keys, self.drawing = None, None, [], None, None
        self.lanes, self.rows, self.tops, self.last_top = [], [], [], {}
        self.lay_out()
        self.wake()
        self.viewport().update()

    def set_motion(self, mode: str) -> None:
        self.mode = mode
        if mode != "full" and self.drawing is not None:
            self.drawing = None
            self.wake()
            self.viewport().update()

    def lay_out(self) -> None:
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        rows, top, day, last = [], HEAD_H + 6.0, date.today(), {}
        for message in self.said:
            if message.at.date() != day:  # as the terminal's: a day's line when not today
                day = message.at.date()
                rows.append(Row(top, None, f"── {message.at:%a %d %b} ──"))
                top += DAY_H
            rows.append(Row(top, message, teamwords.gist(message, message.receiver)))
            for name in (message.sender, message.receiver):
                last[name] = top
            top += ROW_H
        self.rows, self.tops, self.last_top, self.length = rows, [r.top for r in rows], last, top + 8
        self.fit_bars(at_end)

    def fit_bars(self, at_end: bool) -> None:
        page = self.viewport().height()
        bar = self.verticalScrollBar()
        bar.setRange(0, max(0, int(self.length - page)))
        bar.setPageStep(page)
        if at_end:
            bar.setValue(bar.maximum())
        room = self.viewport().width()
        across = self.horizontalScrollBar()
        across.setRange(0, max(0, int(TIME_W + 12 + LANE_LEAST * len(self.lanes) - room)))
        across.setPageStep(room)

    # --- the clock: only while a new message draws itself ----------------------------------------------

    def wake(self) -> None:
        if self.drawing is not None and self.now() - self.drawing[1] >= DRAW_SECONDS:
            self.drawing = None
        # the viewport's region: its own leaves out the children (the viewport covers it all)
        seen = self.isVisible() and not self.window().isMinimized() and not self.viewport().visibleRegion().isEmpty()
        if self.drawing is not None and seen:
            if not self.frames.isActive():
                self.frames.start()
        else:
            self.frames.stop()

    def on_frame(self) -> None:
        self.viewport().update()
        self.wake()

    def hideEvent(self, event) -> None:
        self.drawing = None  # shown again: whole
        self.frames.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 2
        super().resizeEvent(event)
        self.fit_bars(at_end)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self.viewport().update()

    # --- where things are -------------------------------------------------------------------------

    def xs(self) -> dict[str, float]:
        room = self.viewport().width() - TIME_W - 12
        step = max(LANE_LEAST, room / max(1, len(self.lanes)))
        shift = self.horizontalScrollBar().value()
        return {name: TIME_W + step * (i + 0.5) - shift for i, name in enumerate(self.lanes)}

    def lane_x(self, name: str, xs: dict[str, float]) -> float:
        """The terminal's rule: tabmancer's prompts on your lane, anyone else on the last one."""
        if name in xs:
            return xs[name]
        return xs[self.lanes[0]] if name == teams.TABMANCER else xs[self.lanes[-1]]

    def colour_of(self, message: teams.Message) -> str:
        """The sender's colour; quiet when another member is picked; half way there while on its way."""
        lit = self.picked is None or self.picked in (message.sender, message.receiver)
        colour = self.colours.get(message.sender, look.TEXT) if lit else look.MUTED
        return colour if message.delivered else mix(look.MUTED, colour, 0.5)

    def share(self, message: teams.Message, now: float) -> float:
        """How much of a message is drawn: all of it, but for a new one drawing itself."""
        if self.drawing is None or self.drawing[0] is not message:
            return 1.0
        return ease((now - self.drawing[1]) / DRAW_SECONDS)

    def row_at(self, y: float) -> Row | None:
        if y < HEAD_H:
            return None
        at = y + self.verticalScrollBar().value()
        k = bisect.bisect_right(self.tops, at) - 1
        if k < 0:
            return None
        row = self.rows[k]
        return row if at < row.top + (ROW_H if row.message is not None else DAY_H) else None

    def words_at(self, y: float) -> str:
        """What a row says in full, for its tooltip."""
        row = self.row_at(y)
        if row is None or row.message is None:
            return ""
        m = row.message
        head = (look.span(f"{m.at:%H:%M}  ", look.DIM) + look.span(m.sender, f"bold {self.colours.get(m.sender, look.TEXT)}")
                + look.span(" → ", look.MUTED) + look.span(m.receiver, f"bold {self.colours.get(m.receiver, look.TEXT)}"))
        if not m.delivered:
            head += look.span("  on its way", look.MUTED)
        return head + "<br>" + look.span(teamwords.one_line(m.text)[:600], look.TEXT)

    # --- drawing ----------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.viewport().width(), self.viewport().height()
        p.fillRect(event.rect(), QColor(look.SUNKEN))
        if self.team is None or not self.said:
            p.setPen(QColor(look.DIM))
            p.setFont(look.font(9))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "Reading the team…" if self.team is None else NOTHING)
            return
        now, xs, shift = self.now(), self.xs(), self.verticalScrollBar().value()
        p.setClipRect(QRectF(0, HEAD_H, w, h - HEAD_H))
        self.draw_lifelines(p, xs, shift, h)
        first = max(0, bisect.bisect_left(self.tops, shift + HEAD_H - ROW_H))
        for row in self.rows[first:]:
            y = row.top - shift
            if y > h:
                break
            if row.message is None:
                p.setFont(look.font(8.4))
                p.setPen(QColor(look.MUTED))
                p.drawText(QRectF(10, y, w - 20, DAY_H), Qt.AlignmentFlag.AlignVCenter, row.words)
            else:
                self.draw_row(p, row, y, xs, now, w)
        p.setClipping(False)
        self.draw_heads(p, xs, w)

    def draw_lifelines(self, p: QPainter, xs: dict[str, float], shift: float, h: float) -> None:
        standing_of = {m.name: standing(m) for m in self.view.members} if self.view is not None else {}
        for name, x in xs.items():
            colour = QColor(LANE_LINE) if name != self.picked else tinted(self.colours.get(name, look.TEXT), 120)
            p.setPen(QPen(colour, 1.2))
            p.drawLine(QPointF(x, HEAD_H), QPointF(x, h))
            now_is = standing_of.get(name)
            if now_is in ("needs", "working"):  # how it stands since its last message
                since = self.last_top[name] - shift + ARROW_Y if name in self.last_top else HEAD_H
                pen = QPen(tinted(look.RED, 200), 2.6) if now_is == "needs" else QPen(tinted(look.BLUE, 110), 2.0)
                p.setPen(pen)
                p.drawLine(QPointF(x, max(HEAD_H, since)), QPointF(x, h))

    def draw_row(self, p: QPainter, row: Row, y: float, xs: dict[str, float], now: float, w: float) -> None:
        m = row.message
        assert m is not None
        colour = QColor(self.colour_of(m))
        share = self.share(m, now)
        font = look.font(8.8)
        metrics = QFontMetricsF(font)
        p.setFont(font)
        p.setPen(QColor(look.DIM))
        p.drawText(QRectF(10, y + WORDS_Y, TIME_W - 10, 18), Qt.AlignmentFlag.AlignVCenter, f"{m.at:%H:%M}")
        a, b = self.lane_x(m.sender, xs), self.lane_x(m.receiver, xs)
        line = y + ARROW_Y
        if a != b:
            end = a + (b - a) * share
            style = Qt.PenStyle.SolidLine if m.delivered else Qt.PenStyle.DashLine
            p.setPen(QPen(colour, 1.8, style, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(a, line), QPointF(end, line))
            way = 1 if b > a else -1
            tip = QPointF(end, line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawPolygon(QPolygonF([tip, tip - QPointF(way * 8, 4.5), tip - QPointF(way * 8, -4.5)]))
        if m.delivered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
        else:
            p.setPen(QPen(colour, 1.4))
            p.setBrush(QColor(look.SUNKEN))
        p.drawEllipse(QPointF(a, line), 3.4, 3.4)
        left = min(a, b) + 8
        room = max(w - left - 10, 20.0)  # on past a short arrow: the words have a ground of their own
        words = row.words[: max(1, round(len(row.words) * share))] if share < 1 else row.words
        tail = "  on its way" if not m.delivered else ""
        shown = metrics.elidedText(words + tail, Qt.TextElideMode.ElideRight, room)
        if not shown:
            return
        box = QRectF(left - 3, y + WORDS_Y, metrics.horizontalAdvance(shown) + 6, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tinted(look.SUNKEN, 225))  # over the lanes it crosses
        p.drawRect(box)
        lit = (self.picked is None or self.picked in (m.sender, m.receiver)) and m.delivered
        p.setPen(QColor(look.TEXT if lit else look.MUTED))
        p.drawText(box.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, shown)

    def draw_heads(self, p: QPainter, xs: dict[str, float], w: float) -> None:
        p.fillRect(QRectF(0, 0, w, HEAD_H), QColor(look.SURFACE))
        p.setPen(QPen(QColor(look.BORDER), 1))
        p.drawLine(QPointF(0, HEAD_H - 0.5), QPointF(w, HEAD_H - 0.5))
        font = look.font(9, True)
        metrics = QFontMetricsF(font)
        p.setFont(font)
        lead = next((m.name for m in self.team.members if m.lead), None) if self.team is not None else None
        step = (xs[self.lanes[1]] - xs[self.lanes[0]]) if len(self.lanes) > 1 else w
        for name, x in xs.items():
            label = ("★ " if name == lead else "") + name
            colour = look.DIM if name == VM.OTHER else self.colours.get(name, look.TEXT)
            if self.picked is not None and name != self.picked:
                colour = look.MUTED
            label = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(20.0, step - 8))
            p.setPen(QColor(colour))
            p.drawText(QRectF(x - step / 2, 0, step, HEAD_H), Qt.AlignmentFlag.AlignCenter, label)
        p.fillRect(QRectF(0, 0, TIME_W - 4, HEAD_H - 1), QColor(look.SURFACE))  # the time column's corner

    # --- the mouse ----------------------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        words = self.words_at(event.position().y())
        if words:
            QToolTip.showText(event.globalPosition().toPoint(), words, self.viewport())
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)
