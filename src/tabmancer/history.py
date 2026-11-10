"""Every day's work kept, so the stats can look back further than the session files go.

Claude Code deletes a session's transcript some weeks after it was last used (cleanupPeriodDays,
30 by default), and the token counts went with it. So each finished day is kept here as a summary
(history.json in tabmancer's folder): its tokens by model and by project folder, the time at work
and in which hours, the sessions and the tools. A day is written once it is over, and again only
if a later count finds more in it; a few kilobytes a day.

The days before tabmancer kept any come from Claude Code's own record, ~/.claude/stats-cache.json,
which its /stats command keeps (an internal format, read with care: anything unexpected in it is
left out). It has, per day, the tokens by model (not split into cache reads, writes and the rest)
and the sessions and tool calls; that is all such a day shows.

history.attach(report) puts the kept days into a count where its files no longer tell the whole
day, and collect() counts a period with them, or all of it (days=None).
"""

from __future__ import annotations

import json
from datetime import date

from . import tokens
from .config import read_json, write_json
from .sessions import claude_dir
from .tokens import Day, Report, Tokens

FILE = "history.json"
FORMAT = 1
CLAUDE_STATS = "stats-cache.json"
CLAUDE_TAKES_OVER = 0.5  # a day of Claude Code's record stands in when the files left hold less than this of it


# --- kept as JSON -------------------------------------------------------------------------------

def _counts(use: Tokens) -> list[int]:
    return [use.input, use.cache_write, use.cache_read, use.output, use.unsplit]


def _number(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _seconds(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


def _use(counts) -> Tokens:
    values = [_number(v) for v in counts[:5]] if isinstance(counts, list) else []
    return Tokens(*values)


def _to_json(day: Day) -> dict:
    return {"use": _counts(day.use), "models": {m: _counts(u) for m, u in day.models.items()},
            "projects": {f: [_counts(u), n, round(s)] for f, (u, n, s) in day.projects.items()},
            "seconds": round(day.seconds), "hours": [round(h) for h in day.hours], "sessions": day.sessions,
            "tools": day.tools, "calls": day.calls, "source": day.source}


def _from_json(data) -> Day | None:
    if not isinstance(data, dict):
        return None
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    hours = data.get("hours") if isinstance(data.get("hours"), list) else []
    tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    return Day(
        use=_use(data.get("use")),
        models={str(m): _use(c) for m, c in models.items()},
        projects={str(f): (_use(v[0]), _number(v[1]), _seconds(v[2])) for f, v in projects.items()
                  if isinstance(v, list) and len(v) >= 3},
        seconds=_seconds(data.get("seconds")),
        hours=([_seconds(h) for h in hours[:24]] + [0.0] * 24)[:24],
        sessions=_number(data.get("sessions")),
        tools={str(name): _number(n) for name, n in tools.items() if _number(n)},
        calls=_number(data.get("calls")),
        source="claude" if data.get("source") == "claude" else "tabmancer")


def load() -> dict[int, Day]:
    """tabmancer's own days, by day ordinal."""
    saved = read_json(FILE)
    if not isinstance(saved, dict) or saved.get("format") != FORMAT or not isinstance(saved.get("days"), dict):
        return {}
    out = {}
    for stamp, data in saved["days"].items():
        try:
            ordinal = date.fromisoformat(str(stamp)).toordinal()
        except ValueError:
            continue
        day = _from_json(data)
        if day is not None:
            out[ordinal] = day
    return out


def save(days: dict[int, Day]) -> None:
    write_json(FILE, {"format": FORMAT, "days": {date.fromordinal(o).isoformat(): _to_json(d)
                                                 for o, d in sorted(days.items())}}, indent=None)


def record(report: Report) -> bool:
    """The finished days of a count into the history: a day new there, or one the count finds more
    in than was kept (its files only grow, until Claude Code deletes them). True when it changed."""
    today = date.today().toordinal()
    days = load()
    changed = False
    for index, use in enumerate(report.daily()):
        ordinal = report.first.toordinal() + index
        if ordinal >= today or ordinal in report.archive or not use.total:
            continue
        kept = days.get(ordinal)
        if kept is not None and kept.source == "tabmancer" and kept.use.total >= use.total:
            continue
        days[ordinal] = report.day(ordinal)
        changed = True
    if changed:
        save(days)
    return changed


# --- Claude Code's own record ----------------------------------------------------------------

def claude_days() -> dict[int, Day]:
    """The days in Claude Code's stats-cache.json: tokens by model (unsplit), sessions, tool calls."""
    try:
        data = json.loads((claude_dir() / CLAUDE_STATS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[int, Day] = {}

    def day_of(entry) -> Day | None:
        if not isinstance(entry, dict):
            return None
        try:
            ordinal = date.fromisoformat(str(entry.get("date"))).toordinal()
        except ValueError:
            return None
        return out.setdefault(ordinal, Day(source="claude"))

    for entry in data.get("dailyModelTokens") if isinstance(data.get("dailyModelTokens"), list) else []:
        day = day_of(entry)
        models = entry.get("tokensByModel") if day is not None else None
        for model, count in (models.items() if isinstance(models, dict) else ()):
            if _number(count):
                use = Tokens(unsplit=_number(count))
                day.models.setdefault(str(model), Tokens()).add(use)
                day.use.add(use)
    for entry in data.get("dailyActivity") if isinstance(data.get("dailyActivity"), list) else []:
        day = day_of(entry)
        if day is not None:
            day.sessions = _number(entry.get("sessionCount"))
            day.calls = _number(entry.get("toolCallCount"))
    return out


# --- in a count ------------------------------------------------------------------------------

def kept_days() -> dict[int, Day]:
    """Every day known from before: tabmancer's own, and Claude Code's where tabmancer has none."""
    return claude_days() | load()


def first_day() -> date | None:
    known = [o for o, d in kept_days().items() if d.use.total or d.sessions]
    return date.fromordinal(min(known)) if known else None


def attach(report: Report, kept: dict[int, Day] | None = None) -> Report:
    """The kept days into a count, where its files tell less of the day than was kept (they are
    gone, wholly or in part). Today is always the files'."""
    kept = kept_days() if kept is None else kept
    today = date.today().toordinal()
    first, last = report.first.toordinal(), report.last.toordinal()
    daily = report.daily()
    taken: dict[int, Day] = {}
    for ordinal, day in kept.items():
        if not first <= ordinal <= last or ordinal >= today:
            continue
        counted = daily[ordinal - first].total
        if day.source == "claude":
            if day.use.total and counted < day.use.total * CLAUDE_TAKES_OVER or not counted and day.sessions:
                taken[ordinal] = day
        elif day.use.total > counted:
            taken[ordinal] = day
    if not taken:
        return report
    report.cells = {(sid, d): use for (sid, d), use in report.cells.items() if d not in taken}
    report.events = [e for e in report.events if date.fromtimestamp(e.at).toordinal() not in taken]
    report.archive = taken
    models: dict[str, Tokens] = {}
    for event in report.window_events():
        if event.use.total:
            models.setdefault(event.model, Tokens()).add(event.use)
    for day in taken.values():
        for model, use in day.models.items():
            models.setdefault(model, Tokens()).add(use)
    report.models = models
    report.__dict__.pop("activity", None)  # counted again, with the kept days
    return report


def collect(days: int | None) -> Report:
    """A count of the last `days` days with the kept days in it; None: all of it, from the first
    day anything was kept or Claude Code recorded."""
    whole = days is None
    if whole:
        first = first_day()
        days = max((date.today() - first).days + 1 if first else 1, 1)
    report = attach(tokens.collect(days))
    report.whole = whole
    return report

