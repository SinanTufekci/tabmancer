"""The desktop monitor's parts, drawn with QPainter: member cards, the hours chart, the limit
rings, the key bar and Bit (the conversation's lanes: conversation.py). Charts are painted by
hand: Qt Charts and Qt Graphs are GPL add-ons, not in the extra."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import bitart, teamwords
from .. import viewmodel as VM
from ..tokens import human
from ..viewmodel import model_words  # the Model column's and the team view's words, for every front end
from . import look

LAST_MOST = 160  # a message on a card, in characters (the Messages panel has all of it)
TONE_COLOUR = {  # the dashboard's tones (app.TONE), as colours
    "your-turn!": look.RED, "your-turn": look.RED, "working": look.BLUE, "open": look.GREEN, "aside": look.CYAN,
    "to-continue!": look.AMBER, "to-continue": look.AMBER, "done": look.MUTED, "plain": look.TEXT,
}


def tone_colour(tone: str) -> str:
    return TONE_COLOUR.get(tone, look.TEXT)


def text_label(words: str = "", colour: str = look.TEXT, size: float = 9.5, bold: bool = False,
               wrap: bool = False) -> QLabel:
    out = QLabel(words)
    out.setFont(look.font(size, bold))
    out.setStyleSheet(f"color:{colour}; background:transparent;")
    out.setWordWrap(wrap)
    return out


class Dot(QWidget):
    def __init__(self, colour: str = look.MUTED, size: int = 9) -> None:
        super().__init__()
        self.colour = colour
        self.setFixedSize(size + 4, size + 4)

    def set_colour(self, colour: str) -> None:
        if colour != self.colour:
            self.colour = colour
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.colour))
        p.drawEllipse(QRectF(2, 2, self.width() - 4, self.height() - 4))


class Pill(QLabel):
    """A few words on a rounded patch: a state, a CLI, a limit."""

    def __init__(self, words: str = "", colour: str = look.TEXT, ground: str = "#1a1a1a", size: float = 8.5) -> None:
        super().__init__()
        self.setFont(look.font(size))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set(words, colour, ground)

    def set(self, words: str, colour: str, ground: str = "#1a1a1a") -> None:
        self.setText(words)
        self.setStyleSheet(f"color:{colour}; background:{ground}; border-radius:9px; padding:2px 9px;")
        if words and self.parent() is None:
            return  # shown by the layout it goes into: shown now, it would be a window of its own
        self.setVisible(bool(words))


class ContextBar(QWidget):
    """How full a member's context is, of its cap or window: amber from 60%, red from 85%."""

    def __init__(self, width: int = 72) -> None:
        super().__init__()
        self.share: float | None = None
        self.bar_w = width
        self.setFixedSize(width + 44, 16)

    def set(self, share: float | None) -> None:
        self.share = share
        self.setVisible(share is not None)
        self.update()

    def paintEvent(self, event) -> None:
        if self.share is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y, h = (self.height() - 6) / 2, 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(look.FAINT))
        p.drawRoundedRect(QRectF(0, y, self.bar_w, h), 3, 3)
        percent = self.share * 100
        colour = look.percent_colour(percent) if percent >= 60 else look.MUTED
        p.setBrush(QColor(colour))
        p.drawRoundedRect(QRectF(0, y, self.bar_w * min(1.0, self.share), h), 3, 3)
        p.setPen(QColor(colour))
        p.setFont(look.font(8.5))
        p.drawText(QRectF(self.bar_w + 6, 0, 40, self.height()), Qt.AlignmentFlag.AlignVCenter, f"{self.share:.0%}")


def facts_html(member: VM.MemberView, leave_out_model: bool = True) -> str:
    """The picked member's facts as the terminal monitor words them (teamwords.facts), in HTML: its
    model first, or not at all (the card's own line), and without the context (its bar)."""
    facts = teamwords.facts(member)
    if leave_out_model:
        facts = facts[1:]  # [0] is the model
    between = f' <span style="color:{look.DIM}">·</span> '
    return between.join(look.span(words, style, look.MUTED) for words, style in facts
                        if not words.startswith("context "))


def cut(words: str, most: int) -> str:
    words = " ".join(words.split())
    return words if len(words) <= most else words[: most - 1].rstrip() + "…"


def last_words(member: VM.MemberView, colours: dict[str, str], most: int = LAST_MOST) -> str:
    """The newest message to or from a member, its time and who to whom, cut to `most` characters."""
    said = member.last
    if said is None:
        return look.span(f"Nothing said to or by {member.name} yet.", look.DIM)
    return (look.span(f"{said.at:%H:%M}  ", look.DIM) + look.span(said.sender, f"bold {colours.get(said.sender, look.TEXT)}")
            + look.span(" → ", look.MUTED) + look.span(said.receiver, f"bold {colours.get(said.receiver, look.TEXT)}")
            + look.span(f"  {cut(said.text, most)}", look.TEXT))


class MemberCard(QFrame):
    """A member: its state, what it does, on which model and how full, the facts and its last word."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.name = ""
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 9, 12, 10)
        box.setSpacing(5)
        head = QHBoxLayout()
        self.dot = Dot()
        self.title = text_label(size=10.5, bold=True)
        self.cli = Pill()
        self.state = Pill()
        head.addWidget(self.dot)
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.cli)
        head.addWidget(self.state)
        box.addLayout(head)
        self.focus = text_label(colour=look.MUTED, size=9, wrap=True)
        box.addWidget(self.focus)
        row = QHBoxLayout()
        self.model = text_label(size=9)
        self.bar = ContextBar()
        row.addWidget(self.model)
        row.addStretch()
        row.addWidget(self.bar)
        box.addLayout(row)
        self.facts = text_label(size=8.5, wrap=True)
        self.facts.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.facts)
        self.last = text_label(size=8.5, wrap=True)  # wrapped: a long message never widens the card
        self.last.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.last)
        box.addStretch()

    def set(self, member: VM.MemberView, colours: dict[str, str], picked: bool, accent: str) -> None:
        self.name = member.name
        colour = tone_colour(member.tone)
        edge = accent if picked else "#5a2a2a" if member.needs_you else look.BORDER
        width = 2 if picked else 1
        self.setStyleSheet(f"#card {{ background:{look.SURFACE}; border:{width}px solid {edge}; border-radius:6px; }}")
        self.dot.set_colour(colour)
        self.title.setText(("★ " if member.lead else "") + member.name)
        self.title.setStyleSheet(f"color:{colours.get(member.name, look.TEXT)}; background:transparent;")
        self.cli.set(f"@{member.cli}" if member.cli != "claude" else "", look.CYAN, "#0f2326")
        self.state.set(teamwords.member_status_words(member), colour)  # as the terminal's card says it
        self.focus.setText(member.focus)
        self.model.setText(model_words(member))
        self.model.setStyleSheet(f"color:{look.CYAN if member.cli != 'claude' else look.TEXT}; background:transparent;")
        self.bar.set(member.context)
        facts = facts_html(member)
        self.facts.setText(facts)
        self.facts.setVisible(bool(facts))
        self.last.setText(last_words(member, colours))
        self.last.setToolTip(member.last.text if member.last else "")


class HourChart(QWidget):
    """The team's tokens an hour today: a bar an hour, the hour now in the accent."""

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.hours: list[int] = []
        self.setMinimumHeight(140)

    def set(self, hours: list[int]) -> None:
        self.hours = list(hours)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QColor(look.MUTED))
        p.setFont(look.font(9))
        total = sum(self.hours)
        p.drawText(QRectF(8, 2, w, 18), Qt.AlignmentFlag.AlignLeft,
                   f"Tokens an hour · today · {human(total)}" if total else "Tokens an hour · today")
        top, bottom, left = 26, h - 20, 44
        most = max(self.hours, default=0)
        if most:
            p.setPen(QPen(QColor("#202020"), 1))
            for share in (1.0, 0.5):
                y = bottom - (bottom - top) * share
                p.drawLine(QPointF(left, y), QPointF(w - 8, y))
                p.setPen(QColor(look.DIM))
                p.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           human(most * share))
                p.setPen(QPen(QColor("#202020"), 1))
        step = (w - left - 8) / 24
        now = len(self.hours) - 1
        p.setPen(Qt.PenStyle.NoPen)
        for hour, used in enumerate(self.hours):
            if not used or not most:
                continue
            high = (bottom - top) * used / most
            p.setBrush(QColor(self.accent if hour == now else "#4a4a4a"))
            p.drawRoundedRect(QRectF(left + hour * step + 2, bottom - high, max(1.0, step - 4), high), 2, 2)
        p.setPen(QColor(look.DIM))
        p.setFont(look.font(8))
        for hour in (0, 6, 12, 18):
            p.drawText(QRectF(left + hour * step, bottom + 3, 44, 14), Qt.AlignmentFlag.AlignLeft, f"{hour:02d}:00")


class LimitRing(QWidget):
    """A ring: how much of a limit is used (or of a model's quota left), and when it lifts."""

    def __init__(self, size: int = 104) -> None:
        super().__init__()
        self.title, self.share, self.middle, self.under, self.colour = "", 0.0, "", "", look.TEXT
        self.setFixedSize(size, size + 30)

    def set(self, title: str, share: float, middle: str, under: str, colour: str) -> None:
        self.title, self.share, self.middle, self.under, self.colour = title, share, middle, under, colour
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        ring = QRectF(12, 6, s - 24, s - 24)
        p.setPen(QPen(QColor(look.FAINT), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(ring, 0, 360 * 16)
        if self.share > 0:
            p.setPen(QPen(QColor(self.colour), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, int(-360 * 16 * min(1.0, self.share)))
        p.setPen(QColor(look.TEXT))
        p.setFont(look.font(12, True))
        p.drawText(ring, Qt.AlignmentFlag.AlignCenter, self.middle)
        p.setFont(look.font(8.5, True))
        p.drawText(QRectF(0, s - 14, s, 16), Qt.AlignmentFlag.AlignHCenter, self.title)
        p.setPen(QColor(look.MUTED))
        p.setFont(look.font(8))
        metrics = QFontMetrics(p.font())
        p.drawText(QRectF(0, s + 4, s, 16), Qt.AlignmentFlag.AlignHCenter,
                   metrics.elidedText(self.under, Qt.TextElideMode.ElideRight, s))


class KeyBar(QWidget):
    """The keys, always shown under the window: 'Enter its tab  j needs you ...'. The dim ones are
    not done in the window (yet), and drawn greyed."""

    def __init__(self, pairs: list[tuple[str, str]], note: str = "", dim: set[str] | frozenset = frozenset()) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 8, 3)
        row.setSpacing(14)
        self.keys: list[str] = []
        self.dim = set(dim)
        for key, words in pairs:
            pair = QHBoxLayout()
            pair.setSpacing(5)
            cap = QLabel(key)
            cap.setFont(look.font(9, True))
            cap.setStyleSheet(f"color:{look.DIM if key in dim else look.TEXT}; background:{look.FAINT}; "
                              "border-radius:4px; padding:1px 6px;")
            pair.addWidget(cap)
            pair.addWidget(text_label(words, look.DIM if key in dim else look.MUTED, 9))
            row.addLayout(pair)
            self.keys.append(key)
        row.addStretch()
        if note:
            row.addWidget(text_label(note, look.DIM, 8.5))


def bit_pixmap(pose: bitart.Pose, scale: float = 1.0) -> QPixmap:
    """Bit in a pose, from bitart's rows of colours (the dashboard's own drawing)."""
    rows = bitart.figure(pose, cell=bitart.WT_CELL)
    high, wide = len(rows), len(rows[0]) if rows else 0
    image = QImage(max(1, wide), max(1, high), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for y, row in enumerate(rows):
        for x, colour in enumerate(row):
            if colour is not None:
                image.setPixelColor(x, y, QColor(*colour))
    size = QSize(int(wide * scale), int(high * scale))
    return QPixmap.fromImage(image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
