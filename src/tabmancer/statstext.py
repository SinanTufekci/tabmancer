"""The stats as text, in rich only: an overview, where the tokens went, when you work, which tools and
files, how the limits fill. Over a period or all of the time kept (history.py), for every project or one.

What `tabmancer stats` and `tabmancer tokens` print, with neither Textual nor Qt; the `u` screen in the
dashboard (statsview.py) draws the same parts, and a desktop window can too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from . import report as reports
from . import stats, tokens, usage
from .palette import AMBER, GREEN, MUTED, RED, TEXT, mix
from .sessions import Session
from .tokens import Report, Tokens, duration, human

TOP = 10
BAR = 24
LEVELS = "▁▂▃▄▅▆▇█"
CHART = 84  # columns of a limit's history
TILE_LOW, TILE_NONE = "#1a1a1a", "#3a3a3a"  # an hour of the week: least worked (to the accent), none
CHARTED = ("overview", "tokens", "work", "tools")  # the tabs with charts and cards: their parts in widgets
UNLISTED = "(not in the session list)"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TABS = ("overview", "tokens", "work", "tools", "limits", "report")
TAB_LABELS = ("Overview", "Tokens", "When you work", "Tools and files", "Plan limits", "Report")
BARS = " " + LEVELS


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


@dataclass
class Card:
    """A part of a tab in a frame of its own, its title in the frame: lines of text, grids and charts.
    Half cards go two side by side on the screen; `tabmancer stats` prints them one after another."""
    title: str
    body: list
    half: bool = False


@dataclass
class Kpi:
    """One of the overview's big numbers: what it counts, the number (digits and a point, for the
    pixel font), its unit, the change against the period before (▲ 23%, or nothing), a line under it."""
    title: str
    number: str
    unit: str
    change: str
    caption: str

    def line(self, accent: str) -> Text:
        return Text.assemble((f"{self.title:<10}", f"bold {MUTED}"), (self.number, f"bold {accent}"),
                             (f" {self.unit}" if self.unit else "", MUTED), (f"  {self.change}" if self.change else "", TEXT),
                             (f"  {self.caption}" if self.caption else "", MUTED))


@dataclass
class Kpis:
    """The overview's row of big numbers."""
    cards: list[Kpi]


class Trend:
    """Values over time as bars across the width there is, `rows` high: a value to a column or more,
    or, when they do not fit, several to a column (added up; the newest column whole). With dates,
    the first and the last day under it."""

    def __init__(self, values: list[float], first: date, last: date, accent: str, rows: int = 3,
                 dates: bool = True) -> None:
        self.values, self.first, self.last, self.accent = values, first, last, accent
        self.rows, self.dates = rows, dates

    def columns(self, width: int) -> list[float | None]:
        """Each column's value; None for the gap between days given a few columns each."""
        values = self.values
        if not values or width < 1:
            return []
        if len(values) <= width:
            each = width // len(values)
            gap = 1 if each >= 3 else 0
            out: list[float | None] = []
            for value in values:
                out += [value] * (each - gap) + [None] * gap
            return out
        per = -(-len(values) // width)
        return [sum(values[max(0, end - per):end]) for end in range(len(values), 0, -per)][::-1]

    def __rich_console__(self, console, options):
        columns = self.columns(options.max_width)
        peak = max((c for c in columns if c), default=0) or 1
        steps = 8 * self.rows
        levels = [None if c is None else max(1, round(c / peak * steps)) if c else 0 for c in columns]
        for row in reversed(range(self.rows)):
            line = Text(no_wrap=True)
            for level in levels:
                if level is None:
                    line.append(" ")
                elif level > row * 8:
                    line.append(BARS[min(8, level - row * 8)], style=self.accent)
                else:
                    line.append("▁" if row == 0 else " ", style=TILE_NONE)  # a day with none
            yield line
        if self.dates:
            shape = "%d %b %Y" if self.first.year != self.last.year or (self.last - self.first).days > 180 else "%a %d %b"
            left, right = f"{self.first:{shape}}", f"{self.last:{shape}}"
            yield Text(left + " " * max(1, len(columns) - len(left) - len(right)) + right, style=MUTED, no_wrap=True)


def _body_text(item, accent: str) -> list:
    if isinstance(item, BarChart):
        return ([_heading(item.heading)] if item.heading else []) + [item.table(accent)]
    if isinstance(item, WeekChart):
        return item.lines(accent)
    return [item]


def in_text(parts: list, accent: str) -> Group:
    """A tab's parts as `tabmancer stats` prints them: the charts in block characters, a card as its
    title and what is in it."""
    out: list = []
    for part in parts:
        if isinstance(part, Card):
            out.append(_heading(part.title))
            for item in part.body:
                out += _body_text(item, accent)
        elif isinstance(part, Kpis):
            out += [Text("")] + [kpi.line(accent) for kpi in part.cards]
        elif isinstance(part, WeekChart):
            out += [_heading(part.heading), *part.lines(accent)]
        else:
            out += _body_text(part, accent)
    return Group(*out)


def charts(parts: list) -> list:
    """The charts among a tab's parts, those in its cards too, in order."""
    out: list = []
    for part in parts:
        out += [item for item in part.body if isinstance(item, (BarChart, WeekChart))] if isinstance(part, Card) \
            else [part] if isinstance(part, (BarChart, WeekChart)) else []
    return out


def _arrow(change: str) -> str:
    """stats.change as the overview shows it: ▲ 23%, ▼ 5%, new, same; nothing for no figures."""
    if change.startswith("+"):
        return "▲ " + change[1:]
    if change.startswith("−"):
        return "▼ " + change[1:]
    return "" if change == "–" else change


def _model_name(model: str) -> str:
    return model.removeprefix("claude-")[:18]


def history_notes(report: Report) -> list[Text]:
    """What the days told by the history (history.py) do not show."""
    if not report.archive:
        return []
    out = [Text(f"{len(report.archive)} day{'s' * (len(report.archive) != 1)} of these come from the history "
                "tabmancer keeps: their sessions' files are gone, so they count in the totals, by day and by "
                "project, but not in the lists of sessions.", style=MUTED)]
    if any(day.source == "claude" for day in report.archive.values()):
        out.append(Text("The days before tabmancer kept any come from Claude Code's own record (stats-cache.json): "
                        "their tokens by model, unsplit, their sessions and tool calls; not the time at work, "
                        "the hours or the projects.", style=MUTED))
    return out


# --- the overview -----------------------------------------------------------------------------

def overview_parts(report: Report, sessions: dict[str, Session], accent: str, before: stats.Before | None = None,
                   project: str = "") -> list:
    """The overview, in order: the period, the big numbers (Kpis, against the period before when it is
    given), every day's tokens and time at work (Trend), then cards: the top projects (not for one
    project), the models, where the tokens go, the records; and what the kept days leave out."""
    window = report.total()
    activity = report.activity
    daily = report.daily()
    worked = sum(activity.day_seconds)
    active_days = sum(1 for seconds, use in zip(activity.day_seconds, daily) if seconds > 0 or use.total)
    count = stats.session_count(report)
    rows = stats.per_project(report, sessions)
    lengths = [s for s in activity.session_seconds.values() if s > 0]
    out: list = [Text.assemble(("OVERVIEW", f"bold {TEXT}"), (f" · {stats.span(report)}", MUTED),
                               (f" · {project}" if project else "", f"bold {accent}"))]
    empty_before = before is not None and not (before.tokens.total or before.seconds or before.sessions)
    if empty_before:
        before = None  # nothing to set it against: every figure would say "new"

    def against(now: float, then: float, timed: bool = False) -> str:
        """▲ 23% against the period before; for time at work and projects only where it knows them."""
        if before is None or timed and not before.timed:
            return ""
        return _arrow(stats.change(now, then))

    value, word, decimals = tokens.scaled(window.total)
    hours = worked / 3600
    kpis = [Kpi("TOKENS", f"{value:.{decimals}f}", word, against(window.total, before.tokens.total if before else 0),
                f"{_share(window.cache_read, window.total - window.unsplit)} read from cache"),
            Kpi("AT WORK", f"{hours:.1f}" if hours < 10 else f"{hours:.0f}", "hours",
                against(worked, before.seconds if before else 0, timed=True),
                f"{active_days} day{'s' * (active_days != 1)}" + (f", {duration(worked / active_days)} a day"
                                                                 if active_days and worked else "")),
            Kpi("SESSIONS", f"{count}", "", against(count, before.sessions if before else 0),
                f"{duration(sum(lengths) / len(lengths))} on average" if lengths else "")]
    if project:
        kpis.append(Kpi("DAYS", f"{active_days}", "", against(active_days, before.days if before else 0, timed=True),
                        f"of {report.days}"))
    else:
        kpis.append(Kpi("PROJECTS", f"{len(rows)}", "",
                        against(len(rows), sum(1 for v in (before.projects if before else {}).values() if v),
                                timed=True),
                        f"most: {rows[0]['project']}" if rows else ""))
    out.append(Kpis(kpis))
    if before is not None:
        out.append(Text(f"▲ ▼ against the {report.days} days before."
                        + ("" if before.timed else " Some of those come from Claude Code's own record, which keeps "
                                                   "no time at work and no projects: only the tokens, sessions and "
                                                   "models are set against them."), style=MUTED))
    elif empty_before:
        out.append(Text(f"Nothing in the {report.days} days before to set these against.", style=MUTED))

    out.append(Card("EVERY DAY", [
        Text("tokens", style=MUTED), Trend([use.total for use in daily], report.first, report.last, accent, dates=False),
        Text("at work", style=MUTED), Trend(list(activity.day_seconds), report.first, report.last, accent, rows=2)]))

    total = window.total
    if not project and rows:
        shown = rows[:6]
        out.append(Card("TOP PROJECTS", [BarChart(
            "", [Text(row["project"][:16], style=TEXT) for row in shown], [row["tokens"].total for row in shown],
            [(_share(row["tokens"].total, total), human(row["tokens"].total),
              Text(against(row["tokens"].total, before.projects.get(stats.folder_key(row["folder"]), 0)
                           if before else 0, timed=True), style=MUTED)) for row in shown],
            width=8, justify=("left", "right", "right", "left"))]
            + ([Text(f"and {len(rows) - 6} more: the Tokens tab has them", style=MUTED)] if len(rows) > 6 else []),
            half=True))
    if report.models:
        ranked = sorted(report.models.items(), key=lambda item: item[1].total, reverse=True)[:6]
        out.append(Card("MODELS", [BarChart(
            "", [Text(_model_name(model), style=TEXT) for model, _ in ranked], [use.total for _, use in ranked],
            [(_share(use.total, total), human(use.total),
              Text(against(use.total, before.models.get(model, 0) if before else 0), style=MUTED))
             for model, use in ranked],
            width=8, justify=("left", "right", "right", "left"))], half=True))
    if total:
        kinds = [("cache reads", window.cache_read), ("cache writes", window.cache_write),
                 ("fresh input", window.input), ("output", window.output)]
        if window.unsplit:
            kinds.append(("not split", window.unsplit))
        body: list = [BarChart("", [Text(name, style=TEXT) for name, _ in kinds], [n for _, n in kinds],
                               [(_share(n, total), human(n)) for _, n in kinds], width=8,
                               justify=("left", "right", "right"))]
        body.append(Text("Each reply reads the whole conversation again.", style=MUTED))
        if window.unsplit:
            body.append(Text("Not split: the days from Claude Code's own record.", style=MUTED))
        out.append(Card("WHERE THE TOKENS GO", body, half=True))
    found = stats.records(report, sessions)
    if found:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True, min_width=max(len(what) for what, _, _ in found))
        grid.add_column(justify="right", no_wrap=True, min_width=max(len(much) for _, much, _ in found))
        grid.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        for what, much, when in found:
            grid.add_row(Text(what, style=MUTED), Text(much, style=f"bold {TEXT}"), Text(when, style=MUTED))
        out.append(Card("RECORDS", [grid], half=True))
    out += history_notes(report)
    return out


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
        Text.assemble(("TOKENS", f"bold {TEXT}"), (f" · {stats.span(report)}", MUTED)),
        Text.assemble(("today ", MUTED), (human(report.total(last_days=1).total), f"bold {accent}"),
                      *((("    7 days ", MUTED), (human(report.total(last_days=7).total), "bold"))
                        if report.days > 7 else ()),
                      (f"    {report.days} days ", MUTED), (human(window.total), "bold")),
        Text(f"{human(window.cache_read)} cache reads · {human(window.cache_write)} cache writes · "
             f"{human(window.input)} fresh input · {human(window.output)} output"
             + (f" · {human(window.unsplit)} not split (Claude Code's own record)" if window.unsplit else ""),
             style=MUTED),
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

    heading, groups = stats.grouped(report)
    out.append(Card(heading, [BarChart("", [label for label, _, _, _ in groups], [use.total for _, use, _, _ in groups],
                                       [(human(use.total) if use.total else Text("·", style=MUTED), _out(use))
                                        for _, use, _, _ in groups])]))

    rows = projects(report, sessions)
    if rows:
        shown = rows[:TOP]
        body: list = [BarChart("", [Text(name[:22], style=TEXT) for name, _, _ in shown],
                               [use.total for _, use, _ in shown],
                               [(_share(use.total, window.total), human(use.total), _out(use),
                                 Text(_sessions(count), style=MUTED)) for _, use, count in shown],
                               width=16, justify=("left", "right", "right", "right", "right"))]
        if len(rows) > TOP:
            body.append(Text(f"and {len(rows) - TOP} more", style=MUTED))
        out.append(Card("BY PROJECT", body))

    ranked = sorted(report.per_session().items(), key=lambda item: item[1].total, reverse=True)[:TOP]
    if ranked:
        grid = _grid("left", "left", "right", "right")
        for sid, use in ranked:
            project, title = _title(sid, report, sessions)
            grid.add_row(Text(project[:18], style=TEXT), Text(title[:48], style=MUTED if title == UNLISTED else ""),
                         human(use.total), _out(use))
        out.append(Card("TOP SESSIONS", [grid]))

    if report.models:
        grid = _grid("left", "right", "right", "right")
        for model, use in sorted(report.models.items(), key=lambda item: item[1].total, reverse=True):
            grid.add_row(Text(model, style=TEXT), _share(use.total, window.total), human(use.total), _out(use))
        out.append(Card("BY MODEL", [grid]))
    return out + history_notes(report)


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
        Text.assemble(("WHEN YOU WORK", f"bold {TEXT}"), (f" · {stats.span(report)}", MUTED)),
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
        out.append(Card("HOURS OF THE WEEK", [
            Text(f"Busiest: {WEEKDAYS[weekday]} {hour:02d}:00 to {(hour + 1) % 24:02d}:00, "
                 f"{duration(slots[weekday][hour])} in all.", style=MUTED), WeekChart(slots)]))

    heading, groups = stats.grouped(report)
    out.append(Card(heading, [BarChart(
        "", [label for label, _, _, _ in groups], [seconds for _, _, seconds, _ in groups],
        [(duration(seconds) if seconds else Text("·", style=MUTED), Text(_sessions(count) if count else "", style=MUTED))
         for _, _, seconds, count in groups])]))

    longest = sorted(activity.session_seconds.items(), key=lambda kv: kv[1], reverse=True)[:TOP]
    if longest:
        grid = _grid("left", "left", "right")
        for sid, seconds in longest:
            project, title = _title(sid, report, sessions)
            grid.add_row(Text(project[:18], style=TEXT), Text(title[:52], style=MUTED if title == UNLISTED else ""),
                         duration(seconds))
        out.append(Card("LONGEST SESSIONS", [grid]))
    return out + history_notes(report)


# --- tools and files --------------------------------------------------------------------

def render_tools(report: Report, sessions: dict[str, Session], accent: str) -> Group:
    return in_text(tools_parts(report, sessions, accent), accent)


def tools_parts(report: Report, sessions: dict[str, Session], accent: str) -> list:
    """The tab of tools and files, in order: lines of text and TOOLS (BarChart)."""
    used = stats.tools(report)
    calls = sum(count for _, count in used)
    unnamed = report.calls() - calls  # the days of Claude Code's record: their calls counted, not named
    out: list = [Text.assemble(("TOOLS AND FILES", f"bold {TEXT}"), (f" · {stats.span(report)}", MUTED)),
                 Text(f"{calls + unnamed:,} tool calls" + (f" ({unnamed:,} of them not named: the days of Claude "
                                                            "Code's own record)" if unnamed else "")
                      + " · MCP tools are counted per server.", style=MUTED)]
    if used:
        shown = used[:15]
        body: list = [BarChart("", [Text(name[:28], style=TEXT) for name, _ in shown], [count for _, count in shown],
                               [(f"{count:,}", Text(_share(count, calls), style=MUTED)) for _, count in shown],
                               width=20, justify=("left", "right", "right"))]
        if len(used) > 15:
            body.append(Text(f"and {len(used) - 15} more", style=MUTED))
        out.append(Card("TOOLS", body))
    files = stats.edited_files(report, sessions)
    if files:
        grid = _grid("left", "left", "right")
        for project, path, edits in files[:15]:
            grid.add_row(Text(project[:18], style=TEXT), Text(_clip_left(path, 60), style=MUTED), f"{edits}")
        out.append(Card("FILES EDITED MOST", [grid]))
    lines = stats.lines_by_project(report, sessions)
    if lines:
        grid = _grid("left", "right", "right")
        for name, added, removed in lines[:TOP]:
            grid.add_row(Text(name[:22], style=TEXT), Text(f"+{added:,}", style=GREEN), Text(f"−{removed:,}", style=RED))
        out.append(Card("LINES CHANGED", [grid, Text("By Claude Code's own count, which it records for a session "
                                                      "when the session exits.", style=MUTED)]))
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


def plain_text(report: Report, sessions: dict[str, Session], accent: str, before: stats.Before | None = None) -> list:
    """Every tab one after another, for `tabmancer stats`."""
    return [in_text(overview_parts(report, sessions, accent, before), accent), Text(""),
            render_tokens(report, sessions, accent), Text(""), render_work(report, sessions, accent), Text(""),
            render_tools(report, sessions, accent), Text(""), render_limits(usage.chart_history(7), accent)]
