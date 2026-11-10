"""The numbers behind the stats screen and `tabmancer stats`, and their export to JSON and CSV."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import usage
from .sessions import Session, folder_key
from .tokens import Day, Report, Tokens, duration, human

PERIODS = (7, 14, 30, 90)


def folder_of(sid: str, report: Report, sessions: dict[str, Session]) -> str:
    """Where the session list knows a session, its folder decides, as in the list."""
    return sessions[sid].cwd if sid in sessions else report.folders.get(sid, "")


def project_name(folder: str) -> str:
    return Path(folder).name or folder or "?"


def per_project(report: Report, sessions: dict[str, Session]) -> list[dict]:
    """Per folder: project name, folder, tokens, sessions and time at work (its sessions side by
    side counted once, as for the whole period), most tokens first."""
    groups: dict[str, dict] = {}
    for sid, use in report.per_session().items():
        folder = folder_of(sid, report, sessions)
        entry = groups.setdefault(folder_key(folder), {"project": project_name(folder), "folder": folder,
                                                       "tokens": Tokens(), "sessions": 0, "ids": set()})
        entry["tokens"].add(use)
        entry["sessions"] += 1
        entry["ids"].add(sid)
    walls = report.wall_seconds_by({key: entry["ids"] for key, entry in groups.items()})
    for key, entry in groups.items():
        del entry["ids"]
        entry["seconds"] = walls[key]
    for kept in report.archive.values():  # the days the history tells (their files are gone)
        for folder, (use, count, seconds) in kept.projects.items():
            entry = groups.setdefault(folder_key(folder), {"project": project_name(folder), "folder": folder,
                                                           "tokens": Tokens(), "sessions": 0, "seconds": 0.0})
            entry["tokens"].add(use)
            entry["sessions"] += count  # a session on two kept days counts twice: only days are kept
            entry["seconds"] += seconds
    return sorted(groups.values(), key=lambda e: e["tokens"].total, reverse=True)


def tool_group(name: str) -> str:
    """MCP tools count per server: mcp__kicad__run_drc is "kicad (MCP)"."""
    if name.startswith("mcp__"):
        server = name[5:].split("__", 1)[0]
        return f"{server} (MCP)"
    return name


def tools(report: Report) -> list[tuple[str, int]]:
    grouped: Counter[str] = Counter()
    for name, count in report.tools().items():
        grouped[tool_group(name)] += count
    return grouped.most_common()


def edited_files(report: Report, sessions: dict[str, Session]) -> list[tuple[str, str, int]]:
    """(project, path inside it, edits), most edited first. Edits outside the project folder
    keep their full path."""
    counts: Counter[tuple[str, str]] = Counter()
    for (sid, path), edits in report.edited().items():
        folder = folder_of(sid, report, sessions)
        shown = path
        try:
            shown = str(Path(path).relative_to(folder)) if folder else path
        except ValueError:
            pass
        counts[(project_name(folder), shown)] += edits
    return [(project, path, edits) for (project, path), edits in counts.most_common()]


def lines_by_project(report: Report, sessions: dict[str, Session]) -> list[tuple[str, int, int]]:
    """Lines added and removed per project, by Claude Code's own count, for the sessions that
    were active in the window. Claude Code records each session's total when it exits."""
    active = {e.session for e in report.window_events()}
    totals: dict[str, list] = {}
    for sid in active:
        session = sessions.get(sid)
        if session is None or session.lines_added is None:
            continue
        entry = totals.setdefault(folder_key(session.cwd), [session.project, 0, 0])
        entry[1] += session.lines_added
        entry[2] += session.lines_removed or 0
    rows = [(name, added, removed) for name, added, removed in totals.values()]
    return sorted(rows, key=lambda row: row[1] + row[2], reverse=True)


def _use(use: Tokens) -> dict:
    return {"input": use.input, "cache_write": use.cache_write, "cache_read": use.cache_read,
            "output": use.output, "unsplit": use.unsplit, "total": use.total}


def daily_rows(report: Report) -> list[dict]:
    activity = report.activity
    rows = []
    for index, use in enumerate(report.daily()):
        rows.append({"date": (report.first + timedelta(days=index)).isoformat(),
                     "sessions": activity.sessions_per_day[index],
                     "minutes": round(activity.day_seconds[index] / 60), **_use(use)})
    return rows


def export(report: Report, sessions: dict[str, Session]) -> dict:
    """Everything the stats screen shows, as plain data."""
    per_session = report.per_session()
    activity = report.activity
    projects = [{"project": row["project"], "folder": row["folder"], "sessions": row["sessions"],
                 "minutes": round(row["seconds"] / 60), **_use(row["tokens"])}
                for row in per_project(report, sessions)]
    readings = usage.history(report.days)
    return {
        "first_day": report.first.isoformat(), "days": report.days,
        "daily": daily_rows(report),
        "projects": projects,
        "sessions": [{"session_id": sid, "cli": report.clis.get(sid, "claude"),
                      "folder": folder_of(sid, report, sessions),
                      "title": sessions[sid].title if sid in sessions else "",
                      "minutes": round(activity.session_seconds.get(sid, 0) / 60), **_use(use)}
                     for sid, use in sorted(per_session.items(), key=lambda kv: kv[1].total, reverse=True)],
        "models": {model: _use(use) for model, use in report.models.items()},
        "tools": dict(tools(report)),
        "files": [{"project": p, "path": f, "edits": n} for p, f, n in edited_files(report, sessions)],
        "hours": {day: [round(s / 60) for s in hours]
                  for day, hours in zip(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), activity.slot_seconds)},
        "limits": [{"at": r.at.isoformat(timespec="seconds"), "label": r.label, "percent": r.percent,
                    "resets": r.resets.isoformat(timespec="minutes") if r.resets else None} for r in readings],
    }


def to_csv(report: Report) -> str:
    """One row per day: the date, sessions, minutes of work and the four kinds of tokens."""
    rows = daily_rows(report)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]) if rows else ["date"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def write_files(report: Report, sessions: dict[str, Session], folder: Path | None = None) -> tuple[Path, Path]:
    """Save the export next to your downloads; returns the JSON and CSV paths."""
    if folder is None:
        downloads = Path.home() / "Downloads"
        folder = downloads if downloads.is_dir() else Path.home()
    stem = f"tabmancer-stats-{date.today().isoformat()}-{report.days}d"
    as_json, as_csv = folder / f"{stem}.json", folder / f"{stem}.csv"
    as_json.write_text(json.dumps(export(report, sessions), indent=2, ensure_ascii=False), encoding="utf-8")
    as_csv.write_text(to_csv(report), encoding="utf-8")
    return as_json, as_csv


def change(now: float, before: float) -> str:
    """+23 %, −5 %, new, or the same."""
    if not before:
        return "new" if now else "–"
    delta = (now - before) / before
    if abs(delta) < 0.005:
        return "same"
    return f"{'+' if delta > 0 else '−'}{abs(delta):.0%}"


# --- periods, the one before, records, one project -----------------------------------------------

ALL_TIME = 0  # the period of everything kept (history.collect(None))


def period_words(days: int) -> str:
    """'7 days', 'all time'."""
    return "all time" if days == ALL_TIME else f"{days} days"


def span(report: Report) -> str:
    """'last 30 days', or 'all time, since 13 Feb 2026'."""
    return f"all time, since {report.first:%d %b %Y}" if report.whole else f"last {report.days} days"


def session_count(report: Report) -> int:
    """Sessions in the window: the ones counted from their files, and the kept days' (history)."""
    return len(report.per_session()) + sum(day.sessions for day in report.archive.values())


def project_count(report: Report, sessions: dict[str, Session]) -> int:
    return len({row["folder"] and folder_key(row["folder"]) for row in per_project(report, sessions)})


@dataclass
class Before:
    """The period before this one, as long: what the overview's ▲ ▼ are against."""
    tokens: Tokens
    seconds: float
    sessions: int
    projects: dict[str, int]  # folder key -> tokens
    models: dict[str, int]
    days: int = 0  # days worked
    # False when some of its days come from Claude Code's own record, which keeps neither the time at
    # work nor the projects: those are then not set against it.
    timed: bool = True


def before(longer: Report, days: int, sessions: dict[str, Session]) -> Before:
    """The first `days` days of a count twice as long: the period before the last `days`."""
    first, last = longer.first.toordinal(), longer.first.toordinal() + days - 1
    use = longer.between(longer.first, date.fromordinal(last))
    seconds = sum(longer.activity.day_seconds[:days])
    daily = longer.daily()
    worked = sum(1 for i in range(min(days, longer.days)) if longer.activity.day_seconds[i] > 0 or daily[i].total)
    ids: set[str] = set()
    projects: Counter[str] = Counter()
    for (sid, day), counted in longer.cells.items():
        if first <= day <= last:
            ids.add(sid)
            projects[folder_key(folder_of(sid, longer, sessions))] += counted.total
    models: Counter[str] = Counter()
    for event in longer.events:
        if first <= date.fromtimestamp(event.at).toordinal() <= last and event.use.total:
            models[event.model] += event.use.total
    archived = 0
    timed = True
    for day, kept in longer.archive.items():
        if first <= day <= last:
            timed = timed and kept.source != "claude"
            archived += kept.sessions
            for folder, (counted, _, _) in kept.projects.items():
                projects[folder_key(folder)] += counted.total
            for model, counted in kept.models.items():
                models[model] += counted.total
    return Before(use, seconds, len(ids) + archived, dict(projects), dict(models), worked, timed)


def grouped(report: Report) -> tuple[str, list[tuple[str, Tokens, float, int]]]:
    """The window's days for a chart, newest first: day by day for a month or less, by week (from
    Monday) up to four months, by month beyond. (heading, [(label, tokens, seconds at work, sessions)])."""
    daily = report.daily()
    activity = report.activity
    days = [(report.first + timedelta(days=i), daily[i], activity.day_seconds[i], activity.sessions_per_day[i])
            for i in range(report.days)]
    if report.days <= 31:
        return "BY DAY", [(f"{d:%a %d %b}", use, s, n) for d, use, s, n in reversed(days)]
    by_week = report.days <= 120
    groups: dict[date, list] = {}
    for day, use, seconds, count in days:
        key = day - timedelta(days=day.weekday()) if by_week else day.replace(day=1)
        entry = groups.setdefault(key, [Tokens(), 0.0, 0])
        entry[0].add(use)
        entry[1] += seconds
        entry[2] += count  # sessions on each day, added: a session worked on two days counts twice
    label = (lambda d: f"wk {d:%d %b}") if by_week else (lambda d: f"{d:%b %Y}")
    return ("BY WEEK" if by_week else "BY MONTH",
            [(label(key), use, seconds, count) for key, (use, seconds, count) in sorted(groups.items(), reverse=True)])


def streak(day_seconds: list[float], tokens_by_day: list[Tokens]) -> tuple[int, int]:
    """The longest run of days worked one after another: (days, index of its last day)."""
    best, best_end, run = 0, -1, 0
    for index, (seconds, use) in enumerate(zip(day_seconds, tokens_by_day)):
        run = run + 1 if seconds > 0 or use.total > 0 else 0
        if run > best:
            best, best_end = run, index
    return best, best_end


def records(report: Report, sessions: dict[str, Session]) -> list[tuple[str, str, str]]:
    """The period's high points: (what, how much, when or which)."""
    out: list[tuple[str, str, str]] = []
    daily = report.daily()
    activity = report.activity
    shape = "%d %b %Y" if report.first.year != report.last.year else "%a %d %b"

    def day(index: int) -> str:
        return f"{report.first + timedelta(days=index):{shape}}"

    if any(use.total for use in daily):
        top = max(range(len(daily)), key=lambda i: daily[i].total)
        out.append(("Busiest day", human(daily[top].total), day(top)))
    if any(activity.day_seconds):
        top = max(range(report.days), key=lambda i: activity.day_seconds[i])
        out.append(("Longest day", duration(activity.day_seconds[top]), day(top)))
    run, end = streak(activity.day_seconds, daily)
    if run > 1:
        out.append(("Days in a row", f"{run}", f"to {day(end)}"))
    if activity.session_seconds:
        sid, seconds = max(activity.session_seconds.items(), key=lambda kv: kv[1])
        known = sessions.get(sid)
        what = f"{known.project}: {known.title}" if known else project_name(report.folders.get(sid, ""))
        out.append(("Longest session", duration(seconds), what))
    slots = activity.slot_seconds
    if any(any(row) for row in slots):
        weekday, hour = max(((d, h) for d in range(7) for h in range(24)), key=lambda dh: slots[dh[0]][dh[1]])
        out.append(("Busiest hour", f"{('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[weekday]} {hour:02d}:00",
                    f"{duration(slots[weekday][hour])} in all"))
    used = tools(report)
    if used:
        name, count = used[0]
        out.append(("Top tool", name, f"{count:,} calls"))
    return out


def only(report: Report, sessions: dict[str, Session], key: str) -> Report:
    """The count of one project folder (by folder_key): its sessions, and its share of the kept days."""
    ids = {sid for sid in {s for s, _ in report.cells} | {e.session for e in report.events}
           if folder_key(folder_of(sid, report, sessions)) == key}
    out = Report(first=report.first, days=report.days, whole=report.whole,
                 cells={cell: use for cell, use in report.cells.items() if cell[0] in ids},
                 folders={sid: folder for sid, folder in report.folders.items() if sid in ids},
                 clis={sid: cli for sid, cli in report.clis.items() if sid in ids},
                 events=[e for e in report.events if e.session in ids])
    for event in out.window_events():
        if event.use.total:
            out.models.setdefault(event.model, Tokens()).add(event.use)
    for ordinal, kept in report.archive.items():
        for folder, (use, count, seconds) in kept.projects.items():
            if folder_key(folder) == key:
                out.archive[ordinal] = Day(use=use, projects={folder: (use, count, seconds)}, seconds=seconds,
                                           sessions=count, source=kept.source)
    return out
