"""The stats: an overview, where the tokens went, when you work, which tools and files, how the
limits fill. Over a period or all of the time kept (history.py), for every project or one.

Shared by the `u` screen in the dashboard and `tabmancer stats` / `tabmancer tokens`. The counting, the
words and the notes are statsmodel.py's, one copy with the desktop window's stats dialog.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import ClassVar

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static, TabbedContent, TabPane

from . import sixel, stats, statsmodel, usage
from .actions import Note
from .dialogs import frame
from .pixelcharts import draw_bars, draw_week
from .safework import work
from .sessions import Session

# The parts themselves are drawn in statstext.py, with rich only: named here for the screen and older callers.
from .statstext import (  # noqa: F401
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
    WeekChart,
    _grid,
    _heading,
    bar,
    charts,
    in_text,
    overview_parts,
    plain_text,
    render,
    render_limits,
    render_report,
    render_tokens,
    render_tools,
    render_work,
    tokens_parts,
    tools_parts,
    work_parts,
)
from .theme import MUTED, TEXT, keys
from .tokenchart import PixelPicture
from .tokens import Report
from .widgets import big

# --- the charts on the screen --------------------------------------------------------------

class ChartPicture(PixelPicture, Static):
    """A chart in real pixels where the terminal draws them, in block characters elsewhere and while
    its picture cannot be shown (a tab not shown, a dialog over it). Scrolled partly out of sight,
    the part in sight is shown: the whole chart is drawn once, and that part of it sent."""

    whole: tuple | None = None  # (what it shows, its palette and rows): drawn again only when that changes

    def on_mount(self) -> None:
        self.start_pixels()

    def cells(self) -> tuple[int, int, int, int] | None:
        """The cells of it in sight: all of it, or what a scroll leaves showing."""
        if not self.is_attached:
            return None
        try:
            clip = self.screen.find_widget(self).clip
        except Exception:  # noqa: BLE001 - not laid out (yet), or on a tab not shown
            return None
        seen = self.content_region.intersection(clip)
        if seen.width < 2 or seen.height < 1:
            return None
        return seen.x, seen.y, seen.width, seen.height

    def in_sight(self) -> bool:
        cells = self.cells()
        return (self.picture is not None and bool(self.picture.data) and cells is not None
                and isinstance(self.app, sixel.SixelHost) and sixel.in_view(self.app, self.picture, cells))

    def sight_changed(self) -> None:
        """The host saw the picture's cells in sight change (a scroll): that part of it is sent, or
        the block characters stand in for it, or make way."""
        self.draw_picture()
        self.refresh()

    def draw_picture(self) -> None:
        found = sixel.terminal()
        cells = self.cells()
        if self.picture is None or found is None or cells is None:
            return
        cell_w, cell_h = found.cell
        region = self.content_region
        width, height = region.width * cell_w, region.height * cell_h
        left, top = (cells[0] - region.x) * cell_w, (cells[1] - region.y) * cell_h
        seen_w = cells[2] * cell_w
        seen_h = cells[3] * cell_h if found.kitty else cells[3] * cell_h // 6 * 6  # whole Sixel bands
        ground = self.shown(self.background_colors[1])
        shows = (self.picture_key(), width, height, ground)
        key = (*shows, found.kitty, left, top, seen_w, seen_h)
        if key == self.drawn:
            return
        self.drawn = key
        if self.whole is None or self.whole[0] != shows:
            self.whole = (shows, *self.picture_rows(width, height))
        _, palette, rows = self.whole
        rows = [row[left:left + seen_w] for row in rows[top:top + seen_h]]
        self.picture.data = (sixel.encode_kitty(palette, rows, self.picture.number) if found.kitty
                             else sixel.encode_indexed(palette, rows, ground))
        self.picture.fresh = True
        self.refresh()

    def blocks(self) -> Text:
        raise NotImplementedError

    def render(self):
        return Text("") if self.in_sight() else self.blocks()


class BarsPicture(ChartPicture):
    """A bar chart's bars, a row of cells each."""

    DEFAULT_CSS = "BarsPicture { height: auto; }"

    def __init__(self, chart: BarChart, accent: str) -> None:
        super().__init__("")
        self.chart, self.accent = chart, accent
        self.styles.width = chart.width if any(v > 0 for v in chart.values) else 0  # as the grid's column
        self.styles.height = len(chart.values)

    def blocks(self) -> Text:
        peak = max(self.chart.values, default=0)
        return Text("\n").join(Text(bar(value, peak, self.chart.width), style=self.accent) for value in self.chart.values)

    def picture_key(self) -> tuple:
        return tuple(self.chart.values), self.accent

    def picture_rows(self, width: int, height: int):
        return draw_bars(self.chart.values, sixel.terminal().cell, width, height, self.shown(Color.parse(self.accent)))


class WeekPicture(ChartPicture):
    """The hours of the week, a row of cells a day, three cells an hour."""

    DEFAULT_CSS = "WeekPicture { width: 72; height: 7; }"

    def __init__(self, week: WeekChart, accent: str) -> None:
        super().__init__("")
        self.week, self.accent = week, accent

    def blocks(self) -> Text:
        return Text("\n").join(self.week.tiles(day, self.accent) for day in range(7))

    def picture_key(self) -> tuple:
        return tuple(tuple(hours) for hours in self.week.slots), self.accent

    def picture_rows(self, width: int, height: int):
        return draw_week(self.week.slots, sixel.terminal().cell, width, height, self.shown(Color.parse(TILE_LOW)),
                         self.shown(Color.parse(self.accent)), self.shown(Color.parse(TILE_NONE)))


class BarsView(Vertical):
    """A bar chart on the screen: its heading, the labels, the bars, the figures, laid out as the
    grid `tabmancer stats` prints."""

    DEFAULT_CSS = """
    BarsView { height: auto; }
    BarsView > Horizontal { height: auto; }
    BarsView .labels { width: auto; margin-right: 2; }
    BarsView BarsPicture { margin-right: 2; }
    BarsView .figures { width: auto; }
    """

    def __init__(self, chart: BarChart, accent: str) -> None:
        super().__init__()
        self.chart, self.accent = chart, accent

    def compose(self) -> ComposeResult:
        chart = self.chart
        if chart.heading:
            yield Static(_heading(chart.heading))
        with Horizontal():
            labels = (label if isinstance(label, Text) else Text(str(label)) for label in chart.labels)
            yield Static(Text("\n", justify=chart.justify[0]).join(labels), classes="labels")
            yield BarsPicture(chart, self.accent)
            figures = _grid(*chart.justify[1:])
            for row in chart.figures:
                figures.add_row(*row)
            yield Static(figures, classes="figures")


class WeekView(Vertical):
    """HOURS OF THE WEEK on the screen: the hours written above, the weekdays beside, the tiles."""

    DEFAULT_CSS = """
    WeekView { height: auto; }
    WeekView > Horizontal { height: auto; }
    WeekView .days { width: 5; }
    """

    def __init__(self, week: WeekChart, accent: str, titled: bool = True) -> None:
        super().__init__()
        self.week, self.accent, self.titled = week, accent, titled

    def compose(self) -> ComposeResult:
        if self.titled:
            yield Static(_heading(self.week.heading))
        yield Static(self.week.header())
        with Horizontal():
            yield Static(Text("\n").join(Text(f"{day}  ", style=MUTED) for day in WEEKDAYS), classes="days")
            yield WeekPicture(self.week, self.accent)


class KpiCard(Vertical):
    """One of the overview's big numbers in a card of its own: the number in the pixel font, beside
    it its unit and the change, under it a line."""

    DEFAULT_CSS = """
    KpiCard { width: 1fr; height: 6; border: round $panel-lighten-2; border-title-color: $text-muted;
              border-title-style: bold; padding: 0 1; margin-right: 1; }
    KpiCard.last { margin-right: 0; }
    KpiCard > Horizontal { height: 3; }
    KpiCard .number { width: auto; }
    KpiCard .beside { width: 1fr; height: 3; padding-left: 1; }
    KpiCard .caption { height: 1; }
    """

    def __init__(self, kpi: Kpi, accent: str, last: bool = False) -> None:
        super().__init__(classes="last" if last else "")
        self.kpi, self.accent = kpi, accent
        self.border_title = kpi.title

    def compose(self) -> ComposeResult:
        kpi = self.kpi
        with Horizontal():
            yield Static(Text(big(kpi.number), style=f"bold {self.accent}"), classes="number")
            yield Static(Text.assemble((kpi.unit, MUTED), "\n", (kpi.change, f"bold {TEXT}"), no_wrap=True,
                                       overflow="ellipsis"), classes="beside")
        yield Static(Text(kpi.caption, style=MUTED, no_wrap=True, overflow="ellipsis"), classes="caption")


def widgets_for(parts: list, accent: str) -> list:
    """A tab's parts as widgets: text in Statics, each chart a widget of its own (in real pixels where
    the terminal draws them), each card a frame round its own, half cards two side by side, the big
    numbers a row of cards."""

    def chart(item):
        if isinstance(item, BarChart):
            return BarsView(item, accent)
        return WeekView(item, accent, titled=False)

    def flowed(items: list) -> list:
        widgets, text = [], []
        for item in items:
            if isinstance(item, (BarChart, WeekChart)):
                if text:
                    widgets.append(Static(Group(*text)))
                    text = []
                widgets.append(chart(item))
            else:
                text.append(item)
        if text:
            widgets.append(Static(Group(*text)))
        return widgets

    def card(part: Card) -> Vertical:
        box = Vertical(*flowed(part.body), classes="card")
        box.border_title = part.title
        return box

    out: list = []
    loose: list = []
    halves: list[Card] = []

    def settle() -> None:
        if loose:
            out.extend(flowed(loose))
            loose.clear()
        while halves:
            pair = halves[:2]
            del halves[:2]
            if len(pair) == 1:
                out.append(card(pair[0]))
            else:
                left, right = card(pair[0]), card(pair[1])
                left.add_class("left")
                out.append(Horizontal(left, right, classes="pair"))

    for part in parts:
        if isinstance(part, Card) and part.half:
            if loose:
                settle()
            halves.append(part)
            continue
        settle()
        if isinstance(part, Card):
            out.append(card(part))
        elif isinstance(part, Kpis):
            out.append(Horizontal(*(KpiCard(kpi, accent, last=i == len(part.cards) - 1)
                                    for i, kpi in enumerate(part.cards)), classes="kpis"))
        elif isinstance(part, WeekChart):
            out.append(WeekView(part, accent))
        else:
            loose.append(part)
    settle()
    return out


# --- the screen ---------------------------------------------------------------------------

class StatsScreen(ModalScreen[None]):
    """Six tabs, a period to look back over (or all the time kept), one project or all, an export and
    a written report. The period is counted with the kept days (history.collect), and the one before
    it as long for the overview's ▲ ▼."""

    DEFAULT_CSS = frame("StatsScreen", "large", height="92%") + """
    StatsScreen > Vertical { padding: 0 1; }  /* its tabs and panes have padding of their own */
    StatsScreen #stats-head { padding: 1 1 0 1; }
    StatsScreen TabbedContent { height: 1fr; }
    StatsScreen TabPane { padding: 0; }
    StatsScreen VerticalScroll { height: 1fr; padding: 1 1 0 1; }
    StatsScreen .hint { color: $text-muted; padding: 0 1; margin-top: 1; }
    StatsScreen .card { height: auto; border: round $panel-lighten-2; border-title-color: $text-muted;
                        border-title-style: bold; padding: 0 1; margin-top: 1; }
    StatsScreen .pair { height: auto; margin-top: 1; }
    StatsScreen .pair > .card { width: 1fr; margin-top: 0; }
    StatsScreen .pair > .card.left { margin-right: 1; }
    StatsScreen .kpis { height: auto; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,u,q", "close", "Close"),
        Binding("1", "period(7)", "7 days"),
        Binding("2", "period(14)", "14 days"),
        Binding("3", "period(30)", "30 days"),
        Binding("4", "period(90)", "90 days"),
        Binding("5", "period(0)", "All time"),
        Binding("p", "project(1)", "Next project"),
        Binding("P", "project(-1)", "Previous project"),
        Binding("left", "tab(-1)", "Previous tab", priority=True),
        Binding("right", "tab(1)", "Next tab", priority=True),
        Binding("x", "export", "Export"),
        Binding("r", "write_report", "Write the report"),
        Binding("c", "copy_report", "Copy the report"),
    ]

    def __init__(self, report: Report, sessions: dict[str, Session], accent: str) -> None:
        super().__init__()
        self.report, self.sessions, self.accent = report, sessions, accent
        self.period = report.days  # or stats.ALL_TIME
        self.longer: Report | None = None  # twice the period: the one before it is its first half
        self.before: stats.Before | None = None
        self.project = ""  # a folder_key: the tabs count that project alone; "" all of them
        self.shown = report  # self.report, or its one project
        self.projects: list[tuple[str, str]] = []  # the period's projects, most tokens first: (folder key, name)
        self.counting = False
        self.writing = False
        self.fills: dict[str, int] = {}  # each tab's latest fill
        self.fill_locks: dict[str, asyncio.Lock] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("", id="stats-head")
            with TabbedContent(initial="overview"):
                for key, label in zip(TABS, TAB_LABELS):
                    with TabPane(label, id=key), VerticalScroll(id=f"{key}-scroll"):
                        if key not in CHARTED:  # those are filled with their parts (fill)
                            yield Static("", id=f"{key}-body")
            yield Label(keys(*statsmodel.KEY_PAIRS), classes="hint")

    def on_mount(self) -> None:
        self.counting = True
        self.show_head()
        self.query_one("#overview-scroll").focus()
        self.query_one("#limits-body", Static).update(render_limits(usage.chart_history(7), self.accent))
        self.show_report()
        self.recount(self.period, first=True)  # what it was given at once, then with the kept days and ▲ ▼

    def project_name(self, project: str | None = None) -> str:
        return statsmodel.project_name(self.projects, self.project if project is None else project)

    def show_head(self) -> str:
        """The periods, the project and whether it is counting, above the tabs; the project's name."""
        name = self.project_name()
        self.query_one("#stats-head", Label).update(Text.assemble(*statsmodel.head_parts(
            self.period, name, self.counting, self.accent)))
        return name

    def show(self, parts: dict[str, list]) -> None:
        """The tabs' parts (made in a worker: tab_parts) on them, and the limits and the report."""
        self.show_head()
        for key, tab in parts.items():
            self.run_worker(self.fill(key, tab), group="fill")
        self.query_one("#limits-body", Static).update(render_limits(usage.chart_history(7), self.accent))
        self.show_report()

    def tab_parts(self, shown: Report, before: stats.Before | None, name: str) -> dict[str, list]:
        """What each tab with charts shows. Made in a worker: over many days it takes a second."""
        return statsmodel.tab_parts(shown, self.sessions, before, name, self.accent)

    def project_list(self, report: Report) -> list[tuple[str, str]]:
        return statsmodel.project_list(report, self.sessions)

    async def fill(self, key: str, parts: list) -> None:
        """A tab's parts on it (widgets_for), in place of what it showed. A tab's fills wait for each
        other, the newest only put on: one cancelled while it mounted left the screen waiting for good."""
        self.fills[key] = turn = self.fills.get(key, 0) + 1
        async with self.fill_locks.setdefault(key, asyncio.Lock()):
            if turn != self.fills[key]:
                return  # a newer one is waiting
            scroll = self.query_one(f"#{key}-scroll", VerticalScroll)
            await scroll.remove_children()
            await scroll.mount_all(widgets_for(parts, self.accent))

    def show_report(self) -> None:
        entry, current = statsmodel.latest(self.report, self.sessions)
        self.query_one("#report-body", Static).update(
            render_report(entry, current, self.report.days, self.writing, self.accent))

    def action_write_report(self) -> None:
        if self.writing:
            return
        self.writing = True
        self.query_one(TabbedContent).active = "report"
        self.show_report()
        self.write_report()

    @work(thread=True, exclusive=True, group="report")
    def write_report(self) -> None:
        self.app.call_from_thread(self.report_done, statsmodel.write_report(self.report, self.sessions))

    def report_done(self, note: Note) -> None:
        self.writing = False
        self.show_report()
        self.say(note)

    def say(self, note: Note) -> None:
        self.notify(note.words, severity=note.severity, timeout=note.seconds)

    def action_copy_report(self) -> None:
        text = statsmodel.report_text(self.report)
        if text is None:
            self.say(statsmodel.NO_REPORT)
            return
        copy_text(self.app, text)
        self.say(statsmodel.COPIED)

    def action_tab(self, step: int) -> None:
        tabs = self.query_one(TabbedContent)
        current = TABS.index(tabs.active) if tabs.active in TABS else 0
        tabs.active = TABS[(current + step) % len(TABS)]
        self.query_one(f"#{tabs.active}-scroll").focus()

    def action_period(self, days: int) -> None:
        if days == self.period or self.counting:
            return
        self.counting = True
        self.show_head()
        self.recount(days)

    @work(thread=True, exclusive=True)
    def recount(self, period: int, first: bool = False) -> None:
        """The period counted with the kept days, and the one before it as long; first, what the
        screen was given, shown while that counts."""
        if first:
            self.app.call_from_thread(self.shown_first, self.project_list(self.report),
                                      self.tab_parts(self.report, None, ""))
        counted = statsmodel.recount(period, self.project, self.sessions, self.accent)
        self.app.call_from_thread(self.recounted, counted.period, counted.report, counted.longer, counted.shown,
                                  counted.before, counted.projects, counted.parts)

    def shown_first(self, projects: list[tuple[str, str]], parts: dict[str, list]) -> None:
        self.projects = projects
        self.show(parts)

    def narrowed(self, report: Report, longer: Report | None, project: str) -> tuple[Report, stats.Before | None]:
        """The count of the one project (or all), and the period before it (in a worker: it takes a moment)."""
        return statsmodel.narrowed(report, longer, project, self.sessions)

    def recounted(self, period: int, report: Report, longer: Report | None, shown: Report,
                  before: stats.Before | None, projects: list[tuple[str, str]], parts: dict[str, list]) -> None:
        self.period, self.report, self.longer, self.shown, self.before = period, report, longer, shown, before
        self.projects, self.counting = projects, False
        self.show(parts)

    def action_project(self, step: int) -> None:
        """p: the next project of the period's, by tokens, then all of them again; P the other way."""
        if self.counting:
            return
        self.project = statsmodel.stepped(self.projects, self.project, step)
        self.counting = True
        self.show_head()
        self.refilter(self.project)

    @work(thread=True, exclusive=True, group="refilter")
    def refilter(self, project: str) -> None:
        shown, before = self.narrowed(self.report, self.longer, project)
        parts = self.tab_parts(shown, before, self.project_name(project))
        self.app.call_from_thread(self.refiltered, project, shown, before, parts)

    def refiltered(self, project: str, shown: Report, before: stats.Before | None, parts: dict[str, list]) -> None:
        if project != self.project:
            return
        self.shown, self.before, self.counting = shown, before, False
        self.show(parts)

    @work(thread=True, exclusive=True, group="export")
    def action_export(self) -> None:
        self.app.call_from_thread(self.say, statsmodel.export(self.shown, self.sessions))

    def action_close(self) -> None:
        self.dismiss(None)


def copy_text(app, text: str) -> None:
    """To the clipboard: through the terminal (OSC 52), and on Windows through clip.exe too,
    in case the terminal does not pass it on."""
    app.copy_to_clipboard(text)
    if sys.platform == "win32":
        try:
            subprocess.run(["clip"], input="﻿".encode("utf-16-le") + text.encode("utf-16-le"), check=False,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
