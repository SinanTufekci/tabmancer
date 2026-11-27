"""u in the window: the stats, as the terminal's stats screen shows them (StatsDialog): an overview, where the
tokens went, when you work, which tools and files, how the limits fill, and the written report. Over a period
or all of the time kept (history.py), for every project or one. What it counts and says is statsmodel.py's
and statstext.py's, one copy with the terminal's; here they are drawn. The charts are painted (QPainter: bars,
the hours of the week, the days' trend); the words and grids are rich's, shown as HTML in the window's
monospace. Counting, writing the report and the export run off the window's thread (Worker)."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from html import escape
from typing import Any

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from rich.console import Console, Group, RenderableType
from rich.style import Style

from .. import palette, statsmodel, usage
from ..actions import Note
from ..sessions import Session
from ..statstext import (
    CHARTED,
    TAB_LABELS,
    TABS,
    TILE_LOW,
    TILE_NONE,
    WEEKDAYS,
    BarChart,
    Card,
    Kpi,
    Kpis,
    Trend,
    WeekChart,
    render_limits,
    render_report,
)
from ..tokens import Report
from . import look
from .formparts import keys_html
from .handover import label

NOTE_COLOURS = {"information": look.TEXT, "warning": look.AMBER, "error": look.RED}
COLUMNS = 104  # the words' width in characters, as the terminal's large frame has it
HALF = 50  # a half card's
ROW = 20  # a bar chart's row, in pixels
CHARTS = (BarChart, WeekChart, Trend)


def css(style: Style | None) -> str:
    """A rich style as the CSS a QLabel reads: the colour, the ground, bold, italic; dim toward black, reverse
    the colour behind black words (as look.Ink draws them)."""
    if style is None:
        return ""
    colour = style.color.get_truecolor().hex if style.color is not None and not style.color.is_default else None
    ground = (style.bgcolor.get_truecolor().hex if style.bgcolor is not None and not style.bgcolor.is_default
              else None)
    if style.dim:
        colour = palette.mix(colour, look.BLACK, 0.4) if colour else look.DIM
    if style.reverse:
        colour, ground = look.BLACK, colour or look.TEXT
    return ((f"color:{colour};" if colour else "") + (f"background:{ground};" if ground else "")
            + ("font-weight:600;" if style.bold else "") + ("font-style:italic;" if style.italic else ""))


def html_of(thing: RenderableType, columns: int = COLUMNS) -> str:
    """Anything rich draws (words, grids, a report's Markdown) as HTML in its colours, laid out as the terminal
    lays it out in so many columns: every space kept (in the window's monospace the columns line up), a line
    a line."""
    console = Console(width=columns, color_system="truecolor", force_terminal=True, file=io.StringIO(), highlight=False,
                      legacy_windows=False)
    out = []
    for line in console.render_lines(thing, console.options.update(width=columns), pad=False):
        cells = []
        for segment in line:
            if segment.control or not segment.text:
                continue
            text = escape(segment.text).replace(" ", "&nbsp;")
            style = css(segment.style)
            cells.append(f'<span style="{style}">{text}</span>' if style else text)
        out.append("".join(cells))
    while out and not out[-1].replace("&nbsp;", ""):
        out.pop()  # the blank lines rich ends on
    return "<br>".join(out)


def words(thing: RenderableType, columns: int = COLUMNS) -> QLabel:
    made = label(html_of(thing, columns))
    made.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return made


# --- the charts, painted ---------------------------------------------------------------------------

class Bar(QWidget):
    """One row's bar: as long as its value against the largest, its ends round; nothing for none."""

    def __init__(self, value: float, peak: float, colour: str) -> None:
        super().__init__()
        self.value, self.peak, self.colour = value, peak, colour
        self.setMinimumWidth(120)
        self.setFixedHeight(ROW)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(220, ROW)

    def paintEvent(self, event: QPaintEvent) -> None:
        if self.value <= 0 or self.peak <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        thick = round(ROW * 0.55)
        long = max(3.0, self.value / self.peak * self.width())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(look.qcolor(self.colour))
        painter.drawRoundedRect(QRectF(0, (ROW - thick) / 2, long, thick), thick / 3, thick / 3)
        painter.end()


class BarsChart(QWidget):
    """A bar chart (BY DAY, BY PROJECT, TOOLS): the labels, the bars, the figures beside them, a row each."""

    def __init__(self, chart: BarChart, accent: str) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)
        top = 0
        if chart.heading:
            grid.addWidget(label(look.span(chart.heading, f"bold {look.MUTED}")), 0, 0, 1, 2 + len(chart.justify))
            top = 1
        peak = max(chart.values, default=0)
        sides = {"right": Qt.AlignmentFlag.AlignRight, "left": Qt.AlignmentFlag.AlignLeft,
                 "center": Qt.AlignmentFlag.AlignHCenter}
        for at, (name, value, figures) in enumerate(zip(chart.labels, chart.values, chart.figures, strict=False)):
            row = top + at
            cell = words(name, 40)
            cell.setAlignment(sides.get(chart.justify[0], Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(cell, row, 0)
            grid.addWidget(Bar(value, peak, accent), row, 1)
            for column, figure in enumerate(figures):
                justify = chart.justify[column + 1] if column + 1 < len(chart.justify) else "right"
                shown = words(figure, 40)
                shown.setAlignment(sides.get(justify, Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(shown, row, 2 + column)
        grid.setColumnStretch(1, 1)
        self.chart = chart


class WeekGrid(QWidget):
    """HOURS OF THE WEEK: a row a weekday, a tile an hour, from the quiet colour to the accent as more time
    was worked then; a dot where none was. The hours written above every third."""

    LEFT, TOP, PITCH, SIDE = 40, 18, 19, 14

    def __init__(self, week: WeekChart, accent: str) -> None:
        super().__init__()
        self.week, self.accent = week, accent
        self.setFixedSize(self.LEFT + 24 * self.PITCH, self.TOP + 7 * self.PITCH)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(look.font(8))
        painter.setPen(look.qcolor(look.MUTED))
        for hour in range(0, 24, 3):
            painter.drawText(QRectF(self.LEFT + hour * self.PITCH, 0, 3 * self.PITCH, self.TOP - 4),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, str(hour))
        peak = max((s for row in self.week.slots for s in row), default=0)
        for day in range(7):
            top = self.TOP + day * self.PITCH
            painter.setPen(look.qcolor(look.MUTED))
            painter.drawText(QRectF(0, top, self.LEFT - 6, self.PITCH), Qt.AlignmentFlag.AlignLeft |
                             Qt.AlignmentFlag.AlignVCenter, WEEKDAYS[day])
            painter.setPen(Qt.PenStyle.NoPen)
            for hour, value in enumerate(self.week.slots[day][:24]):
                left = self.LEFT + hour * self.PITCH
                if value <= 0 or peak <= 0:
                    painter.setBrush(look.qcolor(TILE_NONE))
                    painter.drawEllipse(QRectF(left + self.SIDE / 2 - 1.5, top + self.SIDE / 2 - 1.5, 3, 3))
                    continue
                painter.setBrush(look.qcolor(palette.mix(TILE_LOW, self.accent, 0.25 + 0.75 * value / peak)))
                painter.drawRoundedRect(QRectF(left, top, self.SIDE, self.SIDE), 3, 3)
        painter.end()


class TrendChart(QWidget):
    """Values over the days as bars across the width (EVERY DAY's tokens, the time at work), a thin line for a
    day with none; the first and the last day under it."""

    DATES = 16

    def __init__(self, trend: Trend) -> None:
        super().__init__()
        self.trend = trend
        self.tall = 14 * trend.rows
        self.setMinimumHeight(self.tall + (self.DATES if trend.dates else 0))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(600, self.minimumHeight())

    def paintEvent(self, event: QPaintEvent) -> None:
        values, trend = self.trend.values, self.trend
        if not values:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        each = width / len(values)
        gap = 2.0 if each >= 6 else 0.0
        peak = max(values) or 1
        painter.setPen(Qt.PenStyle.NoPen)
        for at, value in enumerate(values):
            left = at * each
            if value <= 0:
                painter.setBrush(look.qcolor(TILE_NONE))
                painter.drawRect(QRectF(left, self.tall - 1, max(1.0, each - gap), 1))
                continue
            tall = max(2.0, value / peak * self.tall)
            painter.setBrush(look.qcolor(trend.accent))
            painter.drawRoundedRect(QRectF(left, self.tall - tall, max(1.0, each - gap), tall), 1.5, 1.5)
        if trend.dates:
            shape = "%d %b %Y" if trend.first.year != trend.last.year or (trend.last - trend.first).days > 180 \
                else "%a %d %b"
            painter.setFont(look.font(8))
            painter.setPen(look.qcolor(look.MUTED))
            under = QRectF(0, self.tall + 2, width, self.DATES - 2)
            painter.drawText(under, Qt.AlignmentFlag.AlignLeft, f"{trend.first:{shape}}")
            painter.drawText(under, Qt.AlignmentFlag.AlignRight, f"{trend.last:{shape}}")
        painter.end()


# --- the cards ---------------------------------------------------------------------------------------

def framed(title: str, name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    box = QVBoxLayout(frame)
    box.setContentsMargins(12, 8, 12, 10)
    box.setSpacing(4)
    if title:
        box.addWidget(label(look.span(title, f"bold {look.MUTED}")))
    return frame, box


class KpiCard(QFrame):
    """One of the overview's big numbers: what it counts, the number large in the accent, its unit and the
    change beside it, a line under it."""

    def __init__(self, kpi: Kpi, accent: str) -> None:
        super().__init__()
        self.setObjectName("card")
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 8, 12, 10)
        box.setSpacing(2)
        box.addWidget(label(look.span(kpi.title, f"bold {look.MUTED}")))
        line = QHBoxLayout()
        line.setSpacing(8)
        number = QLabel(kpi.number)
        number.setFont(look.font(22, bold=True))
        number.setStyleSheet(f"color:{accent}; background:transparent;")
        line.addWidget(number)
        beside = label(look.span(kpi.unit, look.MUTED) + "<br>" + look.span(kpi.change, f"bold {look.TEXT}"))
        line.addWidget(beside, 1)
        box.addLayout(line)
        caption = label(look.span(kpi.caption, look.MUTED))
        caption.setToolTip(kpi.caption)
        box.addWidget(caption)
        self.kpi = kpi


def flowed(items: list, accent: str, columns: int = COLUMNS) -> list[QWidget]:
    """A card's (or a tab's) items as widgets: the charts painted, the words between them together."""
    out: list[QWidget] = []
    text: list = []

    def settle() -> None:
        if text:
            out.append(words(Group(*text), columns))
            text.clear()

    for item in items:
        if isinstance(item, CHARTS):
            settle()
            out.append(BarsChart(item, accent) if isinstance(item, BarChart) else
                       WeekGrid(item, accent) if isinstance(item, WeekChart) else TrendChart(item))
        else:
            text.append(item)
    settle()
    return out


def card(part: Card, accent: str, columns: int = COLUMNS) -> QFrame:
    frame, box = framed(part.title)
    for widget in flowed(part.body, accent, columns):
        box.addWidget(widget)
    box.addStretch(1)  # beside a taller half card: its rows at the top, not spread down it
    return frame


def widgets_for(parts: list, accent: str) -> list[QWidget]:
    """A tab's parts as widgets, as the terminal's screen lays them out: each card a frame, half cards two
    side by side, the big numbers a row of cards, the words between them."""
    out: list[QWidget] = []
    loose: list = []
    halves: list[Card] = []

    def settle() -> None:
        if loose:
            out.extend(flowed(loose, accent))
            loose.clear()
        while halves:
            pair, halves[:] = halves[:2], halves[2:]
            if len(pair) == 1:
                out.append(card(pair[0], accent))
                continue
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            for half in pair:
                line.addWidget(card(half, accent, HALF), 1)
            out.append(row)

    for part in parts:
        if isinstance(part, Card) and part.half:
            if loose:
                settle()
            halves.append(part)
            continue
        settle()
        if isinstance(part, Card):
            out.append(card(part, accent))
        elif isinstance(part, Kpis):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            for kpi in part.cards:
                line.addWidget(KpiCard(kpi, accent), 1)
            out.append(row)
        elif isinstance(part, WeekChart):
            frame, box = framed(part.heading)
            box.addWidget(WeekGrid(part, accent))
            out.append(frame)
        else:
            loose.append(part)
    settle()
    return out


# --- off the window's thread -----------------------------------------------------------------------------

class Worker(QObject):
    """Work on threads of its own (counting, the report, the export); what it came to handed back on the
    window's thread, to the callable given with it."""

    came = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.workers: list[threading.Thread] = []
        self.came.connect(lambda then, value: then(value))

    def run(self, work: Callable[[], Any], then: Callable[[Any], None]) -> None:
        def body() -> None:
            value = work()
            try:
                self.came.emit(then, value)
            except RuntimeError:
                pass  # the window has gone meanwhile

        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=body, daemon=True, name="tabmancer-stats")
        self.workers.append(thread)
        thread.start()

    def settle(self, seconds: float = 20) -> None:
        for thread in list(self.workers):
            thread.join(seconds)


# --- the dialog --------------------------------------------------------------------------------------------

class StatsDialog(QDialog):
    """1-5 a period, p P one project or all, ← → the tabs, x export, r write a report, c copy it, Esc (or u)
    closes it. What it was given shows at once; the period is then counted with the kept days, and the one
    before it as long for the overview's ▲ ▼. Window-modal, never waited on (open(), not exec())."""

    def __init__(self, parent: QWidget | None, report: Report, sessions: dict[str, Session], accent: str,
                 worker: Worker | None = None) -> None:
        super().__init__(parent)
        self.report, self.sessions, self.accent = report, dict(sessions), accent
        self.period = report.days
        self.longer: Report | None = None
        self.before = None
        self.project = ""
        self.shown = report
        self.projects: statsmodel.Projects = []
        self.counting = True
        self.writing = False
        self.turn = 0  # the latest count or narrowing asked: an older one that comes back late is dropped
        self.worker = worker or Worker(self)
        self.setWindowTitle("tabmancer: stats")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(1040, 760)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.head = label("")
        box.addWidget(self.head)
        self.tabs = QTabWidget()
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scrolls: dict[str, QScrollArea] = {}
        for key, name in zip(TABS, TAB_LABELS, strict=True):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scrolls[key] = scroll
            self.tabs.addTab(scroll, name)
            self.fill(key, [])
        box.addWidget(self.tabs, 1)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        box.addWidget(label(keys_html(statsmodel.KEY_PAIRS)))
        self.setStyleSheet(
            f"QDialog {{ background:{look.SURFACE}; }} QLabel {{ color:{look.TEXT}; }}"
            f"QTabWidget::pane {{ border:none; border-top:1px solid {look.BORDER}; }}"
            f"QTabBar::tab {{ background:{look.SURFACE}; color:{look.MUTED}; padding:5px 14px; border:none;"
            " border-bottom:2px solid transparent; }"
            f"QTabBar::tab:selected {{ color:{look.TEXT}; border-bottom:2px solid {accent}; }}"
            f"QTabBar::tab:hover {{ color:{look.TEXT}; }}"
            f"QScrollArea, QScrollArea > QWidget > QWidget {{ background:{look.SURFACE}; border:none; }}"
            f"QFrame#card {{ background:{look.PANEL}; border:1px solid {look.BORDER}; border-radius:8px; }}"
            f"QFrame#card QLabel {{ border:none; }}"
            f"QScrollBar:vertical {{ background:{look.SURFACE}; width:10px; border:none; }}"
            f"QScrollBar::handle:vertical {{ background:{look.FAINT}; border-radius:4px; min-height:24px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line { height:0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background:none; }")
        self.finished.connect(self.deleteLater)
        self.show_head()
        self.show_side()
        self.turn += 1
        turn = self.turn
        self.worker.run(lambda: (statsmodel.project_list(report, self.sessions),
                                 statsmodel.tab_parts(report, self.sessions, None, "", accent)),
                        lambda first: self.shown_first(turn, *first))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.setFocus()

    # --- what it shows -------------------------------------------------------------------------------

    def show_head(self) -> None:
        name = statsmodel.project_name(self.projects, self.project)
        self.head.setText("".join(look.styled(words_, style) for words_, style in
                                  statsmodel.head_parts(self.period, name, self.counting, self.accent)))

    def fill(self, key: str, widgets: list[QWidget]) -> None:
        """A tab's widgets, in place of what it showed; where it was scrolled to kept."""
        scroll = self.scrolls[key]
        at = scroll.verticalScrollBar().value()
        inside = QWidget()
        column = QVBoxLayout(inside)
        column.setContentsMargins(6, 10, 6, 10)
        column.setSpacing(10)
        for widget in widgets:
            column.addWidget(widget)
        column.addStretch(1)
        scroll.setWidget(inside)  # the old one deleted with it
        scroll.verticalScrollBar().setValue(at)

    def show_parts(self, parts: dict[str, list]) -> None:
        for key, tab in parts.items():
            if key in CHARTED:
                self.fill(key, widgets_for(tab, self.accent))

    def show_side(self) -> None:
        """The limits and the report: read again each time (they change on their own)."""
        self.fill("limits", [words(render_limits(usage.chart_history(7), self.accent))])
        entry, current = statsmodel.latest(self.report, self.sessions)
        self.fill("report", [words(render_report(entry, current, self.report.days, self.writing, self.accent))])

    def say(self, note: Note) -> None:
        self.note.setText(look.span(note.words, NOTE_COLOURS.get(note.severity, look.TEXT)))

    def shown_first(self, turn: int, projects: statsmodel.Projects, parts: dict[str, list]) -> None:
        """What it was given, shown while the period counts with the kept days."""
        if turn == self.turn:
            self.projects = projects
            self.show_head()
            self.show_parts(parts)
            self.recount(self.period)

    # --- counting -----------------------------------------------------------------------------------

    def recount(self, period: int) -> None:
        self.turn += 1
        turn, project = self.turn, self.project
        self.counting = True
        self.show_head()
        self.worker.run(lambda: statsmodel.recount(period, project, self.sessions, self.accent),
                        lambda counted: self.recounted(turn, counted))

    def recounted(self, turn: int, counted: statsmodel.Counted) -> None:
        if turn != self.turn:
            return
        self.period, self.report, self.longer = counted.period, counted.report, counted.longer
        self.shown, self.before, self.projects = counted.shown, counted.before, counted.projects
        self.counting = False
        self.show_head()
        self.show_parts(counted.parts)
        self.show_side()

    def narrow(self, project: str) -> None:
        self.turn += 1
        turn = self.turn
        self.project, self.counting = project, True
        self.show_head()
        report, longer, projects = self.report, self.longer, self.projects

        def work():
            shown, before = statsmodel.narrowed(report, longer, project, self.sessions)
            name = statsmodel.project_name(projects, project)
            return shown, before, statsmodel.tab_parts(shown, self.sessions, before, name, self.accent)

        self.worker.run(work, lambda done: self.narrowed(turn, *done))

    def narrowed(self, turn: int, shown: Report, before, parts: dict[str, list]) -> None:
        if turn != self.turn:
            return
        self.shown, self.before, self.counting = shown, before, False
        self.show_head()
        self.show_parts(parts)

    # --- the keys -----------------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key, text = event.key(), event.text()
        if text and text in "12345":
            days = statsmodel.PERIODS[int(text) - 1]
            if days != self.period and not self.counting:
                self.recount(days)
        elif text in ("p", "P"):
            if not self.counting:
                self.narrow(statsmodel.stepped(self.projects, self.project, 1 if text == "p" else -1))
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 1 if key == Qt.Key.Key_Right else -1
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % self.tabs.count())
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Home,
                     Qt.Key.Key_End):
            self.scroll_by(key)
        elif text == "x":
            shown = self.shown
            self.worker.run(lambda: statsmodel.export(shown, self.sessions), self.say)
        elif text == "r":
            self.write_report()
        elif text == "c":
            self.copy_report()
        elif text in ("u", "q"):
            self.reject()
        else:
            super().keyPressEvent(event)  # Esc: closed

    def scroll_by(self, key: Qt.Key) -> None:
        bar = self.scrolls[TABS[self.tabs.currentIndex()]].verticalScrollBar()
        step = {Qt.Key.Key_Up: -bar.singleStep() * 3, Qt.Key.Key_Down: bar.singleStep() * 3,
                Qt.Key.Key_PageUp: -bar.pageStep(), Qt.Key.Key_PageDown: bar.pageStep()}
        if key == Qt.Key.Key_Home:
            bar.setValue(bar.minimum())
        elif key == Qt.Key.Key_End:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(bar.value() + step[key])

    def write_report(self) -> None:
        if self.writing:
            return
        self.writing = True
        self.tabs.setCurrentIndex(TABS.index("report"))
        self.show_side()
        report = self.report
        self.worker.run(lambda: statsmodel.write_report(report, self.sessions), self.report_done)

    def report_done(self, note: Note) -> None:
        self.writing = False
        self.show_side()
        self.say(note)

    def copy_report(self) -> None:
        text = statsmodel.report_text(self.report)
        if text is None:
            self.say(statsmodel.NO_REPORT)
            return
        QGuiApplication.clipboard().setText(text)
        self.say(statsmodel.COPIED)

