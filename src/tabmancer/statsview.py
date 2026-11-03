"""The stats: where the tokens went, when you work, which tools and files, how the limits fill.

Shared by the `u` screen in the dashboard and `tabmancer stats` / `tabmancer tokens`.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import ClassVar

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static, TabbedContent, TabPane

from . import report as reports
from . import sixel, stats, summarize, tokens, usage
from .dialogs import frame
from .safework import work
from .sessions import Session
from .theme import AMBER, GREEN, MUTED, RED, TEXT, keys, mix
from .tokenchart import PixelPicture, draw_bars, draw_week
from .tokens import Report, Tokens, duration, human

TOP = 10
BAR = 24
LEVELS = "▁▂▃▄▅▆▇█"
CHART = 84  # columns of a limit's history
TILE_LOW, TILE_NONE = "#1a1a1a", "#3a3a3a"  # an hour of the week: least worked (to the accent), none
CHARTED = ("tokens", "work", "tools")  # the tabs with charts: their parts in widgets of their own
UNLISTED = "(not in the session list)"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TABS = ("tokens", "work", "tools", "limits", "report")
TAB_LABELS = ("Tokens", "When you work", "Tools and files", "Plan limits", "Report")


def bar(value: float, peak: float, width: int = BAR) -> str:
    """A bar of whole cells, seven eighths high: rows of bars under each other keep a sliver apart,
    each its own bar (full height, they ran into one block). Its value is written beside it; any
    value above nothing shows a cell."""
    if peak <= 0 or value <= 0:
        return ""
    return "▇" * max(1, round(value / peak * width))


def _share(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole else "–"


def _grid(*justify: str) -> Table:
    grid = Table.grid(padding=(0, 2))
    for side in justify:
        grid.add_column(justify=side, no_wrap=True)
    return grid


def _heading(text: str) -> Text:
    return Text(f"\n{text}", style=f"bold {MUTED}")


def _out(use: Tokens) -> Text:
    return Text(f"{human(use.output)} out" if use.output else "", style=MUTED)


def _title(sid: str, report: Report, sessions: dict[str, Session]) -> tuple[str, str]:
    known = sessions.get(sid)
    if known:
        return known.project, known.title
    return stats.project_name(report.folders.get(sid, "")), UNLISTED


def projects(report: Report, sessions: dict[str, Session]) -> list[tuple[str, Tokens, int]]:
    """(project name, use, sessions) per folder, most used first."""
    return [(row["project"], row["tokens"], row["sessions"]) for row in stats.per_project(report, sessions)]


# --- the charts ---------------------------------------------------------------------------

@dataclass
class BarChart:
    """Rows of a label, a bar as long as its value against the largest, and figures beside it: BY
    DAY, BY PROJECT, TOOLS. In block characters one grid (`tabmancer stats`); on the screen the
    bars are drawn in real pixels where the terminal draws them (BarsView)."""
    heading: str
    labels: list[RenderableType]
    values: list[float]
    figures: list[tuple[RenderableType, ...]]
    width: int = BAR
    justify: tuple[str, ...] = ("right", "right", "right")  # the labels', then each figure's

    def table(self, accent: str) -> Table:
        peak = max(self.values, default=0)
        grid = _grid(self.justify[0], "left", *self.justify[1:])
        for label, value, figures in zip(self.labels, self.values, self.figures):
            grid.add_row(label, Text(bar(value, peak, self.width), style=accent), *figures)
        return grid


@dataclass
class WeekChart:
    """HOURS OF THE WEEK: the time worked in each hour of each weekday, in seconds."""
    slots: list[list[float]]
    heading: str = "HOURS OF THE WEEK"

    def header(self) -> Text:
        header = Text("     ")
        for hour in range(24):
            header.append(f"{hour:<3}" if hour % 3 == 0 else "   ", style=MUTED)
        return header

    def tiles(self, day: int, accent: str) -> Text:
        """A weekday's hours in block characters, three cells an hour: a tile, or a dot for none."""
        peak = max((s for row in self.slots for s in row), default=0)
        line = Text()
        for value in self.slots[day]:
            if value <= 0:
                line.append("·  ", style=TILE_NONE)
            else:
                line.append("██ ", style=mix(TILE_LOW, accent, 0.25 + 0.75 * value / peak))
        return line

    def lines(self, accent: str) -> list[Text]:
        rows = [self.header()]
        for day in range(7):
            line = Text(f"{WEEKDAYS[day]}  ", style=MUTED)
            line.append_text(self.tiles(day, accent))
            rows.append(line)
        return rows


def in_text(parts: list, accent: str) -> Group:
    """A tab's parts as `tabmancer stats` prints them: the charts in block characters."""
    out: list = []
    for part in parts:
        if isinstance(part, BarChart):
            out += [_heading(part.heading), part.table(accent)]
        elif isinstance(part, WeekChart):
            out += [_heading(part.heading), *part.lines(accent)]
        else:
            out.append(part)
    return Group(*out)


def _sessions(count: int) -> str:
    return f"{count} session{'s' * (count != 1)}"


# --- tokens -----------------------------------------------------------------------------

def render_tokens(report: Report, sessions: dict[str, Session], accent: str) -> Group:
    """sessions: the session list, for titles. Sessions missing from it still count."""
    return in_text(tokens_parts(report, sessions, accent), accent)


def tokens_parts(report: Report, sessions: dict[str, Session], accent: str) -> list:
    """The tokens tab, in order: lines of text, and its charts (BarChart)."""
    window = report.total()
    out: list = [
        Text.assemble(("TOKENS", f"bold {TEXT}"), (f" · last {report.days} days", MUTED)),
        Text.assemble(("today ", MUTED), (human(report.total(last_days=1).total), f"bold {accent}"),
                      *((("    7 days ", MUTED), (human(report.total(last_days=7).total), "bold"))
                        if report.days > 7 else ()),
                      (f"    {report.days} days ", MUTED), (human(window.total), "bold")),
        Text(f"{human(window.cache_read)} cache reads · {human(window.cache_write)} cache writes · "
             f"{human(window.input)} fresh input · {human(window.output)} output", style=MUTED),
        Text("Each turn reads the conversation so far back from the cache, so long sessions add up fast.",
             style=MUTED),
    ]
    weeks = report.week_against_last()
    if weeks:
        now, before = weeks
        line = Text("This week against the last: ", style=MUTED)
        for label, key, show in (("tokens", "tokens", human), ("output", "output", human),
                                 ("at work", "seconds", duration), ("sessions", "sessions", str)):
            line.append(f"{label} {show(now[key])} ", style=TEXT)
            delta = f"{now[key] - before[key]:+d}" if key == "sessions" else stats.change(now[key], before[key])
            line.append(f"({delta})   ", style=MUTED)
        out.append(line)

    newest = list(reversed(report.daily()))
    out.append(BarChart("BY DAY", [f"{date.today() - timedelta(days=offset):%a %d %b}" for offset in range(len(newest))],
                        [use.total for use in newest],
                        [(human(use.total) if use.total else Text("·", style=MUTED), _out(use)) for use in newest]))

    rows = projects(report, sessions)
    if rows:
        shown = rows[:TOP]
        out.append(BarChart("BY PROJECT", [Text(name[:22], style=TEXT) for name, _, _ in shown],
                            [use.total for _, use, _ in shown],
                            [(_share(use.total, window.total), human(use.total), _out(use),
                              Text(_sessions(count), style=MUTED)) for _, use, count in shown],
                            width=16, justify=("left", "right", "right", "right", "right")))
        if len(rows) > TOP:
            out.append(Text(f"and {len(rows) - TOP} more", style=MUTED))

    ranked = sorted(report.per_session().items(), key=lambda item: item[1].total, reverse=True)[:TOP]
    if ranked:
        grid = _grid("left", "left", "right", "right")
        for sid, use in ranked:
            project, title = _title(sid, report, sessions)
            grid.add_row(Text(project[:18], style=TEXT), Text(title[:48], style=MUTED if title == UNLISTED else ""),
                         human(use.total), _out(use))
        out += [_heading("TOP SESSIONS"), grid]

    if report.models:
        grid = _grid("left", "right", "right", "right")
        for model, use in sorted(report.models.items(), key=lambda item: item[1].total, reverse=True):
            grid.add_row(Text(model, style=TEXT), _share(use.total, window.total), human(use.total), _out(use))
        out += [_heading("BY MODEL"), grid]
    return out


render = render_tokens  # the name tabmancer tokens and older callers use


# --- when you work ----------------------------------------------------------------------

def render_work(report: Report, sessions: dict[str, Session], accent: str) -> Group:
    return in_text(work_parts(report, sessions, accent), accent)


def work_parts(report: Report, sessions: dict[str, Session], accent: str) -> list:
    """The tab of when you work, in order: lines of text, the hours of the week (WeekChart, without
    any time at work none) and BY DAY (BarChart)."""
    activity = report.activity
    worked = sum(activity.day_seconds)
    active_days = sum(1 for s in activity.day_seconds if s > 0)
    lengths = [s for s in activity.session_seconds.values() if s > 0]
    out: list = [
        Text.assemble(("WHEN YOU WORK", f"bold {TEXT}"), (f" · last {report.days} days", MUTED)),
        Text.assemble((duration(worked), f"bold {accent}"), (" at work", MUTED),
                      (f" over {active_days} day{'s' * (active_days != 1)}", MUTED),
                      (f" ({duration(worked / active_days)} a day)" if active_days else "", MUTED),
                      (f" · {len(lengths)} sessions", MUTED),
                      (f" · a session runs {duration(sum(lengths) / len(lengths))} on average" if lengths else "",
                       MUTED)),
        Text("Time at work counts the minutes in which the agent replied, with breaks over five minutes "
             "left out; sessions side by side count once.", style=MUTED),
    ]
    slots = activity.slot_seconds
    peak = max((s for row in slots for s in row), default=0)
    if peak:
        weekday, hour = max(((d, h) for d in range(7) for h in range(24)), key=lambda dh: slots[dh[0]][dh[1]])
        out.append(Text(f"Busiest: {WEEKDAYS[weekday]} {hour:02d}:00 to {(hour + 1) % 24:02d}:00, "
                        f"{duration(slots[weekday][hour])} in all.", style=MUTED))
        out.append(WeekChart(slots))

    dates, values, figures = [], [], []
    for offset in range(report.days):
        index = report.days - 1 - offset
        seconds, count = activity.day_seconds[index], activity.sessions_per_day[index]
        dates.append(f"{date.today() - timedelta(days=offset):%a %d %b}")
        values.append(seconds)
        figures.append((duration(seconds) if seconds else Text("·", style=MUTED),
                        Text(_sessions(count) if count else "", style=MUTED)))
    out.append(BarChart("BY DAY", dates, values, figures))

    longest = sorted(activity.session_seconds.items(), key=lambda kv: kv[1], reverse=True)[:TOP]
    if longest:
        grid = _grid("left", "left", "right")
        for sid, seconds in longest:
            project, title = _title(sid, report, sessions)
            grid.add_row(Text(project[:18], style=TEXT), Text(title[:52], style=MUTED if title == UNLISTED else ""),
                         duration(seconds))
        out += [_heading("LONGEST SESSIONS"), grid]
    return out


# --- tools and files --------------------------------------------------------------------

def render_tools(report: Report, sessions: dict[str, Session], accent: str) -> Group:
    return in_text(tools_parts(report, sessions, accent), accent)


def tools_parts(report: Report, sessions: dict[str, Session], accent: str) -> list:
    """The tab of tools and files, in order: lines of text and TOOLS (BarChart)."""
    used = stats.tools(report)
    calls = sum(count for _, count in used)
    out: list = [Text.assemble(("TOOLS AND FILES", f"bold {TEXT}"), (f" · last {report.days} days", MUTED)),
                 Text(f"{calls:,} tool calls · MCP tools are counted per server.", style=MUTED)]
    if used:
        shown = used[:15]
        out.append(BarChart("TOOLS", [Text(name[:28], style=TEXT) for name, _ in shown], [count for _, count in shown],
                            [(f"{count:,}", Text(_share(count, calls), style=MUTED)) for _, count in shown],
                            width=20, justify=("left", "right", "right")))
        if len(used) > 15:
            out.append(Text(f"and {len(used) - 15} more", style=MUTED))
    files = stats.edited_files(report, sessions)
    if files:
        grid = _grid("left", "left", "right")
        for project, path, edits in files[:15]:
            grid.add_row(Text(project[:18], style=TEXT), Text(_clip_left(path, 60), style=MUTED), f"{edits}")
        out += [_heading("FILES EDITED MOST"), grid]
    lines = stats.lines_by_project(report, sessions)
    if lines:
        grid = _grid("left", "right", "right")
        for name, added, removed in lines[:TOP]:
            grid.add_row(Text(name[:22], style=TEXT), Text(f"+{added:,}", style=GREEN), Text(f"−{removed:,}", style=RED))
        out += [_heading("LINES CHANGED"), grid,
                Text("By Claude Code's own count, which it records for a session when the session exits.",
                     style=MUTED)]
    return out


def _clip_left(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


# --- plan limits ------------------------------------------------------------------------

def render_limits(readings: list[usage.Reading], accent: str, now: datetime | None = None) -> Group:
    now = now or datetime.now()
    out: list = [Text.assemble(("PLAN LIMITS", f"bold {TEXT}"), (" · how they filled, and where they head", MUTED))]
    if not readings:
        out.append(Text("\nNo readings yet. While the dashboard is open it reads the limits every five minutes "
                        "and keeps them here.", style=MUTED))
        return Group(*out)
    latest_at = readings[-1].at
    current = [r for r in readings if r.at == latest_at]
    out.append(Text(f"Last read {latest_at:%a %H:%M}. Readings come every five minutes while the dashboard is "
                    "open; gaps are when it was closed.", style=MUTED))
    for reading in current:
        colour = GREEN if reading.percent < 60 else AMBER if reading.percent < 85 else RED
        left = usage.until(reading.resets, now)
        line = Text.assemble((f"\n{reading.label:<14}", f"bold {TEXT}"),
                             ("▰" * round(reading.percent / 10), colour),
                             ("▱" * (10 - round(reading.percent / 10)), "#2a2a2a"),
                             (f" {reading.percent}%", f"bold {colour}"),
                             (f"   resets {reading.resets:%a %H:%M} ({left})" if reading.resets else "", MUTED))
        out.append(line)
        guess = usage.forecast(readings, reading.label, now)
        out.append(_forecast_line(guess))
        span = timedelta(hours=24) if usage.short_window(reading.label) else timedelta(days=7)
        out.append(_history_chart([r for r in readings if r.label == reading.label], now - span, now, CHART))
        since = f"{(now - span):%a %H:%M}"
        out.append(Text(f"{'':14}{since}{'':>{CHART - len(since) - 3}}now", style=MUTED))
    return Group(*out)


def _pace(per_hour: float) -> str:
    return f"{per_hour:.0f}%" if per_hour >= 10 else f"{per_hour:.1f}%"


def _forecast_line(guess: usage.Forecast | None) -> Text:
    if guess is None:
        return Text(f"{'':14}Not enough readings since the last reset to tell how fast it fills.", style=MUTED)
    if guess.per_hour <= 0.01:
        return Text(f"{'':14}Not filling right now.", style=MUTED)
    pace = f"{_pace(guess.per_hour)} an hour"
    if guess.fills_first and guess.full_at:
        return Text(f"{'':14}At {pace} it is full around {guess.full_at:%a %H:%M}, before it resets.",
                    style=f"bold {RED}")
    end = guess.at_reset
    return Text(f"{'':14}At {pace} it reaches about {end}% by the reset." if end is not None
                else f"{'':14}Filling at {pace}.", style=AMBER if end is not None and end >= 85 else GREEN)


def _history_chart(readings: list[usage.Reading], start: datetime, end: datetime, width: int) -> Text:
    """One column per slice of time: the highest reading in it, or a gap where there was none."""
    slice_seconds = (end - start).total_seconds() / width
    columns: list[int | None] = [None] * width
    for reading in readings:
        index = int((reading.at - start).total_seconds() / slice_seconds)
        if 0 <= index < width:
            columns[index] = max(columns[index] or 0, reading.percent)
    line = Text(" " * 14)
    for value in columns:
        if value is None:
            line.append(" ")
            continue
        colour = GREEN if value < 60 else AMBER if value < 85 else RED
        line.append(LEVELS[min(7, round(value / 100 * 7))], style=colour)
    return line


# --- the report ---------------------------------------------------------------------------

def render_report(entry: dict | None, current: bool, days: int, writing: bool, accent: str) -> Group:
    """The last report written for this period, or how to get one."""
    out: list = [Text.assemble(("REPORT", f"bold {TEXT}"), (f" · last {days} days", MUTED))]
    if writing:
        out.append(Text(f"\nClaude ({reports.MODEL}) is writing the report… this takes about a minute.",
                        style=f"italic {accent}"))
    if entry is None:
        if not writing:
            out += [Text(f"\nPress r and Claude ({reports.MODEL}) writes a report of these {days} days from your "
                         "session list: a few sentences on the period, then what got done and what is open "
                         "in each project. The numbers (sessions, time at work, tokens) are added from the "
                         "stats.", style=TEXT),
                    Text("\nIt is one claude -p call on your plan, like an AI summary, and only what the "
                         "session list shows is sent: titles, prompts, last replies, summaries, file names. "
                         "The report is saved as Markdown in your Downloads folder; c copies it.", style=MUTED)]
        return Group(*out)
    written = datetime.fromisoformat(entry["at"])
    note = (f"Written {written:%a %d %b %H:%M}. " + ("" if current else "Sessions have changed since: r writes "
                                                     "it again. ") + "c copies it.")
    out += [Text(note, style=MUTED if current else AMBER), Text(""), Markdown(entry["text"])]
    return Group(*out)


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

    def __init__(self, week: WeekChart, accent: str) -> None:
        super().__init__()
        self.week, self.accent = week, accent

    def compose(self) -> ComposeResult:
        yield Static(_heading(self.week.heading))
        yield Static(self.week.header())
        with Horizontal():
            yield Static(Text("\n").join(Text(f"{day}  ", style=MUTED) for day in WEEKDAYS), classes="days")
            yield WeekPicture(self.week, self.accent)


# --- the screen ---------------------------------------------------------------------------

class StatsScreen(ModalScreen[None]):
    """Five tabs, a period to look back over, an export and a written report."""

    DEFAULT_CSS = frame("StatsScreen", "large", height="92%") + """
    StatsScreen > Vertical { padding: 0 1; }  /* its tabs and panes have padding of their own */
    StatsScreen #stats-head { padding: 1 1 0 1; }
    StatsScreen TabbedContent { height: 1fr; }
    StatsScreen TabPane { padding: 0; }
    StatsScreen VerticalScroll { height: 1fr; padding: 1 1 0 1; }
    StatsScreen .hint { color: $text-muted; padding: 0 1; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape,u,q", "close", "Close"),
        Binding("1", "period(7)", "7 days"),
        Binding("2", "period(14)", "14 days"),
        Binding("3", "period(30)", "30 days"),
        Binding("4", "period(90)", "90 days"),
        Binding("left", "tab(-1)", "Previous tab", priority=True),
        Binding("right", "tab(1)", "Next tab", priority=True),
        Binding("x", "export", "Export"),
        Binding("r", "write_report", "Write the report"),
        Binding("c", "copy_report", "Copy the report"),
    ]

    def __init__(self, report: Report, sessions: dict[str, Session], accent: str) -> None:
        super().__init__()
        self.report, self.sessions, self.accent = report, sessions, accent
        self.counting = False
        self.writing = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("", id="stats-head")
            with TabbedContent(initial="tokens"):
                for key, label in zip(TABS, TAB_LABELS):
                    with TabPane(label, id=key), VerticalScroll(id=f"{key}-scroll"):
                        if key not in CHARTED:  # those are filled with their parts (fill)
                            yield Static("", id=f"{key}-body")
            yield Label(keys(("1-4", "7, 14, 30 or 90 days"), ("← →", "tabs"), ("x", "export"),
                             ("r", "write the report"), ("c", "copy it"), ("Esc", "close")), classes="hint")

    def on_mount(self) -> None:
        self.show()
        self.query_one("#tokens-scroll").focus()

    def show(self) -> None:
        periods = Text("look back  ", style=MUTED)
        for key, days in enumerate(stats.PERIODS, 1):
            on = days == self.report.days
            periods.append(f" {key} ", style=f"bold #000000 on {self.accent}" if on else MUTED)
            periods.append(f"{days} days  ", style=f"bold {TEXT}" if on else MUTED)
        if self.counting:
            periods.append("counting…", style=f"italic {MUTED}")
        self.query_one("#stats-head", Label).update(periods)
        for key, parts in (("tokens", tokens_parts), ("work", work_parts), ("tools", tools_parts)):
            self.run_worker(self.fill(key, parts(self.report, self.sessions, self.accent)), group=f"fill-{key}",
                            exclusive=True)
        self.query_one("#limits-body", Static).update(render_limits(usage.history(7), self.accent))
        self.show_report()

    async def fill(self, key: str, parts: list) -> None:
        """A tab's parts on it: its text in Statics, each chart a widget of its own, in real pixels
        where the terminal draws them."""
        widgets: list = []
        text: list = []
        for part in parts:
            if isinstance(part, (BarChart, WeekChart)):
                if text:
                    widgets.append(Static(Group(*text)))
                    text = []
                widgets.append(BarsView(part, self.accent) if isinstance(part, BarChart) else WeekView(part, self.accent))
            else:
                text.append(part)
        if text:
            widgets.append(Static(Group(*text)))
        scroll = self.query_one(f"#{key}-scroll", VerticalScroll)
        await scroll.remove_children()
        await scroll.mount_all(widgets)

    def show_report(self) -> None:
        entry = reports.latest(self.report.days)
        current = entry is not None and entry.get("key") == reports.key_for(
            list(self.sessions.values()), self.report, summarize.load_cache())
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
        try:
            entry = reports.write(self.sessions, self.report)
            path, error = reports.save(entry), None
        except (summarize.SummaryError, OSError) as failure:
            path, error = None, str(failure)
        self.app.call_from_thread(self.report_done, path, error)

    def report_done(self, path, error: str | None) -> None:
        self.writing = False
        self.show_report()
        if error:
            self.notify(f"The report could not be written: {error}", severity="error", timeout=10)
        else:
            self.notify(f"Report saved as {path}", timeout=8)

    def action_copy_report(self) -> None:
        entry = reports.latest(self.report.days)
        if entry is None:
            self.notify("No report for this period yet: r writes one.", severity="warning")
            return
        copy_text(self.app, entry["text"])
        self.notify("Report copied: paste it where your team reads it.", timeout=4)

    def action_tab(self, step: int) -> None:
        tabs = self.query_one(TabbedContent)
        current = TABS.index(tabs.active) if tabs.active in TABS else 0
        tabs.active = TABS[(current + step) % len(TABS)]
        self.query_one(f"#{tabs.active}-scroll").focus()

    def action_period(self, days: int) -> None:
        if days == self.report.days or self.counting:
            return
        self.counting = True
        self.show()
        self.recount(days)

    @work(thread=True, exclusive=True)
    def recount(self, days: int) -> None:
        report = tokens.collect(days)
        self.app.call_from_thread(self.recounted, report)

    def recounted(self, report: Report) -> None:
        self.report, self.counting = report, False
        self.show()

    @work(thread=True, exclusive=True, group="export")
    def action_export(self) -> None:
        try:
            as_json, as_csv = stats.write_files(self.report, self.sessions)
        except OSError as error:
            self.app.call_from_thread(self.notify, f"Export failed: {error}", severity="error")
            return
        self.app.call_from_thread(self.notify, f"Saved {as_json.name} and {as_csv.name} in {as_json.parent}",
                                  timeout=8)

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


def plain_text(report: Report, sessions: dict[str, Session], accent: str) -> list:
    """Every tab one after another, for `tabmancer stats`."""
    return [render_tokens(report, sessions, accent), Text(""), render_work(report, sessions, accent), Text(""),
            render_tools(report, sessions, accent), Text(""), render_limits(usage.history(7), accent)]
