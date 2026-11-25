"""The Board: the main window's sessions as cards in lanes, by what they want from you.

Lanes by the cards' rules (viewmodel.board): NEED YOU, OPEN NOW, TO CONTINUE, and DONE folded to a
strip a click opens. A lane's heading has its card's number and what they are (the projects; the open
ones by what they do). A team is one card, in the lane of its most urgent member, with its members
as chips. The arrows and the mouse move the pick, one card, kept across reads as the list keeps its
row (viewmodel.kept_row). It shows the sessions the window narrowed the list to (viewmodel.visible: a
project, a card, words); a click on a lane's heading asks for only what its card counts (1 2 3), and
that heading is lit while it is so. A card ticked with Space has a tick before its state (a team's,
– while only some of its closed members are). It only shows: the window does what its keys do.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QToolTip, QVBoxLayout, QWidget

from .. import sessionwords
from .. import viewmodel as VM
from ..sessions import Session
from ..store import DashSnapshot
from . import look
from .widgets import tone_colour

GAP = 8  # between cards, and between lanes
PAD_X, PAD_Y = 11, 9  # inside a card
TITLE_LINES = 2  # a card's title wraps to two lines at most
STRIP = 38  # a folded lane's width
GROUND, EDGE = "#121212", "#262626"  # a card's
MODEL_ROOM = 22  # a card's model words ('sonnet 5.5·xhigh', 'Hydrogen (High)')
HEAD = (8.6, True)  # (size, bold) of a card's first line
TITLE = (9.6, True)
META = (8.4, False)
CHIP = (8.2, True)


def metrics(spec: tuple[float, bool]) -> QFontMetricsF:
    return QFontMetricsF(look.font(*spec))


def wrapped(words: str, fm: QFontMetricsF, width: float, most: int) -> list[str]:
    """Words in lines that fit width, at most `most` of them, the last cut short with … when more is left."""
    lines: list[str] = []
    line = ""
    for word in words.split():
        longer = f"{line} {word}" if line else word
        if not line or fm.horizontalAdvance(longer) <= width:
            line = longer
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > most:
        lines = [*lines[:most - 1], " ".join(lines[most - 1:])]
    return [fm.elidedText(one, Qt.TextElideMode.ElideRight, width) for one in lines] or [""]


@dataclass
class Card:
    """A card as drawn: what it says, in which colours."""

    key: str  # the session's id, or viewmodel.TEAM_CARD + the team's name
    head: str  # its state in the list's words
    head_colour: str
    corner: str  # how long ago
    title: str
    meta: list[list[tuple[str, str]]]  # its project, model and the rest: groups of (words, colour) parts
    edge: str  # the stripe down its left: its tone
    tip: str
    marks: str = ""  # git: ± changed, ↑ not pushed
    context: float | None = None
    crowded: bool = False
    pinned: bool = False
    chips: list[tuple[str, str]] = field(default_factory=list)  # a team's members: (words, colour)
    tick: str = ""  # Space's: ✓ ticked, – some of a team's, '' none


def tick_mark(ids: set[str], ticked: Collection[str]) -> str:
    """A card's tick over the sessions Space ticks on it (none for an open one): ✓ all of them, – some."""
    hit = ids & set(ticked)
    return "" if not hit else "✓" if hit == ids else "–"


def session_card(view: VM.SessionView, session: Session | None, accent: str) -> Card:
    colour = tone_colour(view.tone)
    meta = [[(view.project, look.CYAN)]]
    if session is not None:
        model = []  # 'opus 5.5' '↓' '·xhigh', one after the other
        for words, style in sessionwords.model_parts(session, VM.effort_of(session, False), MODEL_ROOM):
            shade, _bold = look.colour_of(style, look.MUTED)
            model.append((words, look.MUTED if shade == look.TEXT else shade))
        if model:
            meta.append(model)
    if view.armed is not None:
        meta.append([(f"▶ at {VM.lifts(view.armed)}", accent)])
    elif view.queued:
        meta.append([("▶ hand over", accent)])
    git = view.git or {}
    marks = ("±" if git.get("changed") else "") + ("↑" if git.get("ahead") else "")
    title = view.title or "(no title yet)"
    if view.hidden:
        title = f"hidden · {title}"
    tip = "\n".join(part for part in (title, f"{view.label} · {view.project}", view.folder) if part)
    return Card(view.id, view.label, colour, VM.ago(view.last_active), title, meta, colour, tip, marks,
                view.context, view.crowded, view.pinned)


def team_card(team: VM.TeamView, views: dict[str, VM.SessionView], lane_tone: str, accent: str) -> Card:
    chips = []
    for member in team.members:
        glyph = VM.TEAM_GLYPHS.get(member.state, ("·", ""))[0]
        chips.append((f"{'★' if member.lead else ''}{member.name} {glyph}", tone_colour(member.tone)))
    known = [views[m.session] for m in team.members if m.session in views]
    newest = max((v.last_active for v in known), default=None)
    pinned = any(v.pinned for v in known)
    meta = [[(team.project, look.CYAN)], [(f"{team.open} of {len(team.members)} open", look.MUTED)]]
    if team.clis and team.clis != ["claude"]:
        meta.append([(" + ".join(team.clis), look.MUTED)])
    goal = team.goal or "no goal written"
    tip = "\n".join([f"team {team.name}: {goal}",
                     *(f"{'★ ' if m.lead else ''}{m.name}: {m.label}" for m in team.members)])
    return Card(VM.TEAM_CARD + team.name, f"team {team.name}", accent, VM.ago(newest) if newest else "", goal, meta,
                tone_colour(lane_tone), tip, pinned=pinned, chips=chips)


@dataclass
class Placed:
    """Where a card is in its column, and its lines as they fit there."""

    card: Card
    rect: QRectF
    title: list[str]
    chips: list[QRectF]


class Column(QWidget):
    """A lane's cards, one under the other, drawn at once: it is as tall as they are (in a scroll area)."""

    clicked = Signal(str)

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.cards: list[Card] = []
        self.placed: list[Placed] = []
        self.picked: str | None = None
        self.laid_for = -1
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the board keeps the keys

    def set(self, cards: list[Card], picked: str | None) -> None:
        self.cards, self.picked = cards, picked
        self.lay_out()
        self.update()

    def set_picked(self, picked: str | None) -> None:
        if picked != self.picked:
            self.picked = picked
            self.update()

    def lay_out(self) -> None:
        width = self.width()
        self.laid_for = width
        head, title, meta, chip = metrics(HEAD), metrics(TITLE), metrics(META), metrics(CHIP)
        inner = max(40.0, width - 2 * PAD_X - 3)
        y = 0.0
        self.placed = []
        for card in self.cards:
            lines = wrapped(card.title, title, inner, TITLE_LINES)
            top = y + PAD_Y + head.height() + 5
            bottom = top + len(lines) * title.lineSpacing() + 5 + meta.height()
            chips: list[QRectF] = []
            if card.chips:
                x, row_y, high = 0.0, bottom + 7, chip.height() + 4
                for words, _colour in card.chips:
                    wide = chip.horizontalAdvance(words) + 12
                    if x and x + wide > inner:
                        x, row_y = 0.0, row_y + high + 4
                    chips.append(QRectF(PAD_X + 3 + x, row_y, wide, high))
                    x += wide + 5
                bottom = row_y + high
            height = bottom + PAD_Y - y
            self.placed.append(Placed(card, QRectF(0.5, y + 0.5, width - 1, height), lines, chips))
            y += height + GAP
        self.setMinimumHeight(int(max(0.0, y - GAP)) + 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.width() != self.laid_for:
            self.lay_out()

    def rect_of(self, key: str) -> QRectF | None:
        return next((one.rect for one in self.placed if one.card.key == key), None)

    def at(self, point: QPointF) -> Card | None:
        return next((one.card for one in self.placed if one.rect.contains(point)), None)

    def mousePressEvent(self, event) -> None:
        card = self.at(event.position())
        if card is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(card.key)
        super().mousePressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            card = self.at(QPointF(event.pos()))
            if card is not None:
                QToolTip.showText(event.globalPos(), card.tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        head, title, meta, chip = (look.font(*HEAD), look.font(*TITLE), look.font(*META), look.font(*CHIP))
        fm_head, fm_title, fm_meta, fm_chip = (QFontMetricsF(f) for f in (head, title, meta, chip))
        seen = event.rect()
        for one in self.placed:
            box, card = one.rect, one.card
            if not box.intersects(QRectF(seen)):
                continue
            picked = card.key == self.picked
            shape = QPainterPath()
            shape.addRoundedRect(box, 7, 7)
            p.fillPath(shape, QColor(GROUND))
            p.save()
            p.setClipPath(shape)
            p.fillRect(QRectF(box.left(), box.top(), 3, box.height()), QColor(card.edge))
            p.restore()
            p.setPen(QPen(QColor(self.accent if picked else EDGE), 1.6 if picked else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(shape)
            left, right = box.left() + PAD_X + 3, box.right() - PAD_X
            y = box.top() + PAD_Y
            # its state, and on the right how long ago (a pin before it)
            corner = card.corner
            p.setFont(meta)
            corner_w = fm_meta.horizontalAdvance(corner)
            p.setPen(QColor(look.MUTED))
            p.drawText(QRectF(right - corner_w, y, corner_w + 1, fm_head.height()),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, corner)
            room = right - left - corner_w - 8
            head_x = left
            if card.tick:
                p.setFont(head)
                p.setPen(QColor(self.accent))
                tick_w = fm_head.horizontalAdvance(f"{card.tick} ")
                p.drawText(QRectF(left, y, tick_w, fm_head.height()), Qt.AlignmentFlag.AlignVCenter, card.tick)
                head_x, room = left + tick_w, room - tick_w
            if card.pinned:
                p.setFont(head)
                p.setPen(QColor(self.accent))
                star_w = fm_head.horizontalAdvance("★ ")
                p.drawText(QRectF(right - corner_w - 6 - star_w, y, star_w, fm_head.height()),
                           Qt.AlignmentFlag.AlignVCenter, "★")
                room -= star_w
            p.setFont(head)
            p.setPen(QColor(card.head_colour))
            p.drawText(QRectF(head_x, y, room, fm_head.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm_head.elidedText(card.head, Qt.TextElideMode.ElideRight, room))
            y += fm_head.height() + 5
            p.setFont(title)
            p.setPen(QColor(look.TEXT))
            for line in one.title:
                p.drawText(QPointF(left, y + fm_title.ascent()), line)
                y += fm_title.lineSpacing()
            y += 5
            # its project and model on the left; git, and how full its context is, on the right
            p.setFont(meta)
            end = right
            if card.context is not None:
                share = min(1.0, card.context)
                words = f"{card.context:.0%}"
                words_w = fm_meta.horizontalAdvance(words)
                bar_w = 42 if box.width() >= 280 else 24  # a narrow card keeps room for its model
                bar = QRectF(end - words_w - 6 - bar_w, y + fm_meta.height() / 2 - 2, bar_w, 4)
                colour = (look.RED if share >= 0.85 else look.AMBER if share >= 0.6 or card.crowded else look.GREEN)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(look.FAINT))
                p.drawRoundedRect(bar, 2, 2)
                p.setBrush(QColor(colour))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * share, bar.height()), 2, 2)
                p.setPen(QColor(look.MUTED))
                p.drawText(QRectF(end - words_w, y, words_w + 1, fm_meta.height()), Qt.AlignmentFlag.AlignVCenter, words)
                end = bar.left() - 8
            if card.marks:
                marks_w = fm_meta.horizontalAdvance(card.marks)
                p.setPen(QColor(look.AMBER))
                p.drawText(QRectF(end - marks_w, y, marks_w + 1, fm_meta.height()), Qt.AlignmentFlag.AlignVCenter,
                           card.marks)
                end -= marks_w + 8
            x, gap = left, fm_meta.horizontalAdvance("  ")
            for group in card.meta:
                if x > left:
                    x += gap
                for words, colour in group:
                    room = end - x
                    shown = fm_meta.elidedText(words, Qt.TextElideMode.ElideRight, room) if room > 0 else ""
                    if shown in ("", "…"):
                        break  # no room left for it
                    p.setPen(QColor(colour))
                    p.drawText(QRectF(x, y, room, fm_meta.height()), Qt.AlignmentFlag.AlignVCenter, shown)
                    x += fm_meta.horizontalAdvance(shown)
            # a team's members
            p.setFont(chip)
            for (words, colour), spot in zip(card.chips, one.chips, strict=True):
                p.setPen(QPen(QColor(colour).darker(170), 1))
                p.setBrush(QColor("#1b1b1b"))
                p.drawRoundedRect(spot, spot.height() / 2, spot.height() / 2)
                p.setPen(QColor(colour))
                p.drawText(spot, Qt.AlignmentFlag.AlignCenter, fm_chip.elidedText(words, Qt.TextElideMode.ElideRight,
                                                                                  spot.width() - 8))
        p.end()


class Heading(QWidget):
    """A lane's title and number in its tone, what they are under it, and those of them on a team's
    card in another lane. A click on a lane that folds (DONE) folds it again; on a card's lane, it asks
    for only what the card counts, or for every lane again while it is lit (on)."""

    clicked = Signal()

    def __init__(self, accent: str = look.ORANGE) -> None:
        super().__init__()
        self.accent = accent
        self.lane: VM.Lane | None = None
        self.folds = False
        self.on = False  # the list is narrowed to its card
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set(self, lane: VM.Lane, folds: bool, on: bool = False) -> None:
        self.lane, self.folds, self.on = lane, folds, on
        lines = 2 + bool(lane.away)
        self.setFixedHeight(int(metrics((9, True)).height() + (lines - 1) * metrics(META).height() + 14))
        card = VM.CARDS.get(lane.key)
        if folds:
            tip = "a click folds it"
        elif card is None:
            tip = ""
        elif on:
            tip = f"only what {card[1]} counts: a click (or {card[0]}) shows every lane again"
        else:
            tip = f"a click (or {card[0]}): only what {card[1]} counts"
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor if tip else Qt.CursorShape.ArrowCursor)
        self.update()

    def lines(self) -> list[str]:
        """What it says under the title (for the tests, as drawn)."""
        lane = self.lane
        if lane is None:
            return []
        out = [" · ".join(lane.words) if lane.words else lane.caption]
        if lane.away:
            out.append(" · ".join(f"{count} on {team}'s card" for team, count in lane.away.items()))
        return out

    def mousePressEvent(self, event) -> None:
        if self.folds or (self.lane is not None and self.lane.key in VM.CARDS):
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        lane = self.lane
        if lane is None:
            return
        p = QPainter(self)
        if self.on:  # the list is narrowed to its card: a bar in the accent under the heading
            p.fillRect(QRectF(4, self.height() - 2, self.width() - 8, 2), QColor(self.accent))
        colour = tone_colour(lane.tone) if lane.count else look.MUTED
        big, small = look.font(9, True), look.font(*META)
        fm_big, fm_small = QFontMetricsF(big), QFontMetricsF(small)
        box = QRectF(self.rect()).adjusted(4, 6, -4, 0)
        p.setFont(big)
        p.setPen(QColor(colour))
        count = str(lane.count) + ("  ‹" if self.folds else "")
        count_w = fm_big.horizontalAdvance(count)
        p.drawText(QRectF(box.left(), box.top(), box.width() - count_w - 8, fm_big.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm_big.elidedText(lane.title, Qt.TextElideMode.ElideRight, box.width() - count_w - 8))
        p.drawText(QRectF(box.right() - count_w, box.top(), count_w + 1, fm_big.height()), Qt.AlignmentFlag.AlignVCenter,
                   count)
        y = box.top() + fm_big.height() + 2
        p.setFont(small)
        for index, words in enumerate(self.lines()):
            p.setPen(QColor(look.MUTED if index == 0 else look.DIM))
            p.drawText(QRectF(box.left(), y, box.width(), fm_small.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm_small.elidedText(words, Qt.TextElideMode.ElideRight, box.width()))
            y += fm_small.height()
        p.end()


class Strip(QWidget):
    """A folded lane: its title down a narrow strip, its number, and › to open it (a click)."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.lane: VM.Lane | None = None
        self.setFixedWidth(STRIP)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set(self, lane: VM.Lane) -> None:
        self.lane = lane
        self.setToolTip(f"{lane.title}: {lane.count} · {lane.caption}. A click opens it.")
        self.update()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        lane = self.lane
        if lane is None:
            return
        p = QPainter(self)
        p.setFont(look.font(8.8, True))
        fm = QFontMetricsF(p.font())
        p.setPen(QColor(look.MUTED))
        y = 12.0
        for letter in [*lane.title, "", str(lane.count), "›"]:
            if letter:
                p.drawText(QRectF(0, y, self.width(), fm.height()), Qt.AlignmentFlag.AlignHCenter, letter)
            y += fm.height()
        p.end()


class LaneView(QFrame):
    """One lane: its heading over its cards (scrolled), or the strip while it is folded."""

    clicked = Signal(str)  # a card's key
    fold = Signal(str, bool)  # the lane's key, folded
    card = Signal(str)  # its heading clicked: the lane's key, a card's (VM.CARDS)

    def __init__(self, key: str, accent: str) -> None:
        super().__init__()
        self.key = key
        self.folds = False
        self.setObjectName("lane")
        self.heading = Heading(accent)
        self.column = Column(accent)
        self.scroller = QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setWidget(self.column)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroller.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the arrows move the pick, not the scroll
        self.scroller.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                                  " { background: transparent; }")
        self.strip = Strip()
        column = QVBoxLayout(self)
        column.setContentsMargins(6, 0, 6, 6)
        column.setSpacing(4)
        column.addWidget(self.heading)
        column.addWidget(self.scroller, 1)
        column.addWidget(self.strip, 1)
        self.column.clicked.connect(self.clicked)
        self.strip.clicked.connect(self.unfold)
        self.heading.clicked.connect(self.heading_clicked)

    def unfold(self) -> None:
        self.fold.emit(self.key, False)

    def heading_clicked(self) -> None:
        if self.folds:
            self.fold.emit(self.key, True)
        else:
            self.card.emit(self.key)

    def set(self, lane: VM.Lane, cards: list[Card], picked: str | None, folds: bool, on: bool = False) -> None:
        self.folds = folds
        folded = lane.folded
        self.heading.setVisible(not folded)
        self.scroller.setVisible(not folded)
        self.strip.setVisible(folded)
        self.layout().setContentsMargins(0 if folded else 6, 0, 0 if folded else 6, 6)
        if folded:
            self.setFixedWidth(STRIP)
            self.strip.set(lane)
            self.column.set([], None)
            return
        self.setMinimumWidth(150)
        self.setMaximumWidth(16777215)
        self.heading.set(lane, folds, on)
        self.column.set(cards, picked)

    def show_card(self, key: str) -> None:
        """Scrolled so the card is in sight."""
        rect = self.column.rect_of(key)
        if rect is not None:
            self.scroller.ensureVisible(int(rect.center().x()), int(rect.center().y()), 0, int(rect.height() / 2) + GAP)


class Board(QWidget):
    """The lanes side by side, a card picked in them. picked: the key of the card picked now."""

    picked = Signal(str)
    card = Signal(str)  # a lane's heading clicked: its card (VM.CARDS), to narrow the list to or back

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.snap: DashSnapshot | None = None
        self.on: str | None = None  # the card the list is narrowed to: its heading is lit
        self.lanes: list[VM.Lane] = []
        self.cards: dict[str, Card] = {}
        self.pick: str | None = None
        self.row = 0  # where the pick is among keys(): kept_row puts it back near there
        self.opened: set[str] = set()  # lanes that fold, opened with a click
        self.ticked: frozenset[str] = frozenset()  # the sessions ticked (Space), by id
        self.views: dict[str, LaneView] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(GAP)
        for key, *_rest in VM.LANES:
            lane = LaneView(key, accent)
            lane.clicked.connect(self.click)
            lane.fold.connect(self.fold)
            lane.card.connect(self.card)
            self.views[key] = lane
            row.addWidget(lane, 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set(self, snap: DashSnapshot, shown: list[Session] | None = None, on: str | None = None,
            ticked: Collection[str] | None = None) -> None:
        """The board as the store read it, over the sessions shown (VM.visible: the window's filters);
        without them, every one in sight (the hidden ones are not). on: the card they are narrowed to;
        ticked: the sessions ticked now (None: as they were)."""
        self.snap, self.on = snap, on
        if ticked is not None:
            self.ticked = frozenset(ticked)
        if shown is None:
            shown = VM.visible(snap.sessions.values(), lambda s: s.session_id in snap.hidden)
        self.lanes = VM.board(shown, snap.team_states, snap.pinned)
        self.show_lanes()

    def set_ticked(self, ids: Collection[str]) -> None:
        if frozenset(ids) != self.ticked:
            self.ticked = frozenset(ids)
            self.show_lanes()

    def show_lanes(self) -> None:
        snap = self.snap
        if snap is None:
            return
        views = {view.id: view for view in snap.view.sessions}
        teams = {team.name: team for team in snap.view.teams}
        self.cards = {}
        by_lane: dict[str, list[Card]] = {}
        for lane in self.lanes:
            lane.folded = lane.folded and lane.key not in self.opened
            made = by_lane.setdefault(lane.key, [])
            for item in lane.items:
                if item.startswith(VM.TEAM_CARD):
                    name = item[len(VM.TEAM_CARD):]
                    team, state = teams.get(name), snap.team_states.get(name)
                    card = team_card(team, views, lane.tone, self.accent) if team is not None else None
                    ids = VM.members_closed(state, snap.sessions) if state is not None else set()
                else:
                    view, session = views.get(item), snap.sessions.get(item)
                    card = session_card(view, session, self.accent) if view is not None else None
                    ids = {item} if session is not None and VM.tickable(session) else set()
                if card is not None:
                    card.tick = tick_mark(ids, self.ticked)
                    made.append(card)
                    self.cards[card.key] = card
        keys = self.keys()
        place = VM.kept_row(keys, self.pick, self.row, True)
        before = self.pick
        self.pick = keys[place] if place is not None else (keys[0] if keys else None)
        self.row = keys.index(self.pick) if self.pick is not None else 0
        for lane in self.lanes:
            self.views[lane.key].set(lane, by_lane[lane.key], self.pick, lane.key in self.opened, lane.key == self.on)
        if self.pick != before:
            self.picked.emit(self.pick or "")

    def keys(self) -> list[str]:
        """The cards that can be picked, lane by lane (a folded lane's cannot)."""
        return [key for lane in self.lanes if not lane.folded for key in self.lane_keys(lane)]

    def lane_keys(self, lane: VM.Lane) -> list[str]:
        return [key for key in lane.items if key in self.cards]

    def where(self) -> tuple[int, int]:
        """The lane (of the open ones) and the row the pick is in."""
        shown = [lane for lane in self.lanes if not lane.folded]
        for index, lane in enumerate(shown):
            keys = self.lane_keys(lane)
            if self.pick in keys:
                return index, keys.index(self.pick)
        return 0, 0

    def move(self, key: str | None) -> None:
        if key is None or key == self.pick:
            return
        self.pick = key
        self.row = self.keys().index(key) if key in self.keys() else 0
        for lane in self.lanes:
            self.views[lane.key].column.set_picked(key)
            if key in lane.items:
                self.views[lane.key].show_card(key)
        self.picked.emit(key)

    def step(self, by: int) -> None:
        """The pick on down its lane (by 1) or up it (-1), as the arrows move it; it stays at either end."""
        shown = [lane for lane in self.lanes if not lane.folded and self.lane_keys(lane)]
        if not shown:
            return
        column, row = self.where()
        open_lanes = [lane for lane in self.lanes if not lane.folded]
        keys = self.lane_keys(open_lanes[column] if column < len(open_lanes) else shown[0])
        if keys:
            self.move(keys[max(0, min(row + by, len(keys) - 1))])

    def card_of(self, key: str) -> str | None:
        """The card a session is on: its own, or its team's; or a team's card (TEAM_CARD + its name)."""
        if any(key in lane.items for lane in self.lanes):
            return key
        states = self.snap.team_states if self.snap is not None else {}
        for name, state in states.items():
            if any(session is not None and session.session_id == key for session in state.sessions.values()):
                team = VM.TEAM_CARD + name
                return team if any(team in lane.items for lane in self.lanes) else None
        return None

    def reveal(self, key: str) -> bool:
        """A session's card (or its team's) picked and in view, its lane opened if it was folded."""
        card = self.card_of(key)
        if card is None:
            return False
        for lane in self.lanes:
            if card in lane.items and lane.folded:
                self.fold(lane.key, False)
        self.move(card)
        return True

    def click(self, key: str) -> None:
        self.setFocus()
        self.move(key)

    def fold(self, key: str, folded: bool) -> None:
        if folded:
            self.opened.discard(key)
        else:
            self.opened.add(key)
        for lane in self.lanes:
            if lane.key == key:
                lane.folded = folded
        self.show_lanes()

    def keyPressEvent(self, event) -> None:
        shown = [lane for lane in self.lanes if not lane.folded and self.lane_keys(lane)]
        if not shown:
            super().keyPressEvent(event)
            return
        column, row = self.where()
        open_lanes = [lane for lane in self.lanes if not lane.folded]
        lane = open_lanes[column] if column < len(open_lanes) else shown[0]
        keys = self.lane_keys(lane)
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.step(1 if key == Qt.Key.Key_Down else -1)
        elif key == Qt.Key.Key_Home and keys:
            self.move(keys[0])
        elif key == Qt.Key.Key_End and keys:
            self.move(keys[-1])
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if key == Qt.Key.Key_Left else 1
            index = column + step
            while 0 <= index < len(open_lanes) and not self.lane_keys(open_lanes[index]):
                index += step  # an empty lane is passed over
            if 0 <= index < len(open_lanes):
                beside = self.lane_keys(open_lanes[index])
                self.move(beside[min(row, len(beside) - 1)])
        else:
            super().keyPressEvent(event)
