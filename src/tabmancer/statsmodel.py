"""The stats screen's counting and words, with no UI: one copy for the terminal's u screen (statsview.py) and
the desktop window's stats dialog. A period is counted with the kept days (history.collect), and the one
before it as long for the overview's ▲ ▼; one project or all; what each tab with charts shows (statstext's
parts); the written report, its copy and the export, said as Notes. Counting and writing take a while: the
host runs them off its own thread and shows what comes back."""

from __future__ import annotations

from dataclasses import dataclass

from . import history, stats, summarize
from . import report as reports
from .actions import Note
from .palette import MUTED, TEXT
from .sessions import Session
from .statstext import overview_parts, tokens_parts, tools_parts, work_parts
from .tokens import Report

PERIODS = (*stats.PERIODS, stats.ALL_TIME)  # the keys 1 to 5, in order
KEY_PAIRS = (("1-5", "period"), ("p P", "one project"), ("← →", "tabs"), ("x", "export"), ("r", "write a report"),
             ("c", "copy it"), ("Esc", "close"))
NO_REPORT = Note("No report for this period yet: r writes one.", "warning")
COPIED = Note("Report copied: paste it where your team reads it.", seconds=4)

Projects = list[tuple[str, str]]  # the period's projects, most tokens first: (folder key, name)


def head_parts(period: int, name: str, counting: bool, accent: str) -> list[tuple[str, str]]:
    """The line above the tabs as (words, style): the periods with their keys, the one counted marked, the
    project (or all) and whether it is counting."""
    out = [("look back  ", MUTED)]
    for key, days in enumerate(PERIODS, 1):
        on = days == period
        out.append((f" {key} ", f"bold #000000 on {accent}" if on else MUTED))
        out.append((f"{stats.period_words(days)}  ", f"bold {TEXT}" if on else MUTED))
    out.append(("   project ", MUTED))
    out.append((name or "all", f"bold {accent}" if name else MUTED))
    if counting:
        out.append(("   counting…", f"italic {MUTED}"))
    return out


def tab_parts(shown: Report, sessions: dict[str, Session], before: stats.Before | None, name: str,
              accent: str) -> dict[str, list]:
    """What each tab with charts shows. Over many days it takes a second: off the host's thread."""
    return {"overview": overview_parts(shown, sessions, accent, before, name),
            "tokens": tokens_parts(shown, sessions, accent),
            "work": work_parts(shown, sessions, accent),
            "tools": tools_parts(shown, sessions, accent)}


def project_list(report: Report, sessions: dict[str, Session]) -> Projects:
    return [(stats.folder_key(row["folder"]), row["project"]) for row in stats.per_project(report, sessions)]


def project_name(projects: Projects, project: str) -> str:
    """The project's name as the period lists it (its folder's otherwise); "" for all of them."""
    return dict(projects).get(project, stats.project_name(project)) if project else ""


def stepped(projects: Projects, project: str, step: int) -> str:
    """p: the next project of the period's, by tokens, then all of them again ("") ; P (-1) the other way."""
    keys = [""] + [key for key, _ in projects]
    at = keys.index(project) if project in keys else 0
    return keys[(at + step) % len(keys)]


def narrowed(report: Report, longer: Report | None, project: str,
             sessions: dict[str, Session]) -> tuple[Report, stats.Before | None]:
    """The count of the one project (or all), and the period before it (it takes a moment)."""
    if not project:
        return report, None if longer is None else stats.before(longer, report.days, sessions)
    shown = stats.only(report, sessions, project)
    return shown, None if longer is None else stats.before(stats.only(longer, sessions, project), report.days,
                                                           sessions)


@dataclass
class Counted:
    """A period counted: all of it, twice as long (None for all time), the one project's or all, the period
    before, its projects and the tabs' parts."""

    period: int
    report: Report
    longer: Report | None
    shown: Report
    before: stats.Before | None
    projects: Projects
    parts: dict[str, list]


def recount(period: int, project: str, sessions: dict[str, Session], accent: str) -> Counted:
    """The period counted with the kept days, and the one before it as long, for the project picked."""
    all_time = period == stats.ALL_TIME
    report = history.collect(None if all_time else period)
    longer = None if all_time else history.collect(period * 2)
    projects = project_list(report, sessions)
    shown, before = narrowed(report, longer, project, sessions)
    return Counted(period, report, longer, shown, before, projects,
                   tab_parts(shown, sessions, before, dict(projects).get(project, ""), accent))


def latest(report: Report, sessions: dict[str, Session]) -> tuple[dict | None, bool]:
    """The latest report written for the period's days, and whether it is of the sessions as they are now."""
    entry = reports.latest(report.days)
    current = entry is not None and entry.get("key") == reports.key_for(list(sessions.values()), report,
                                                                        summarize.load_cache())
    return entry, current


def write_report(report: Report, sessions: dict[str, Session]) -> Note:
    """r: the report written and saved (a summary by Claude: it takes a while), said as a Note."""
    from .sessions import find_sessions

    try:
        # The sessions of the report's whole period, as tabmancer report reads them: the dashboard lists only
        # its --days (14), and a 90-day report's notes told of the last 14 alone.
        period = {s.session_id: s for s in find_sessions(report.days, quick=True)}
        entry = reports.write(period | sessions, report)
        path = reports.save(entry)
    except (summarize.SummaryError, OSError) as failure:
        return Note(f"The report could not be written: {failure}", "error", 10)
    return Note(f"Report saved as {path}", seconds=8)


def report_text(report: Report) -> str | None:
    """c: the latest report's words for the period's days; None when there is none (NO_REPORT says so)."""
    entry = reports.latest(report.days)
    return None if entry is None else entry["text"]


def export(shown: Report, sessions: dict[str, Session]) -> Note:
    """x: what is shown, as JSON and CSV (stats.write_files), said as a Note."""
    try:
        as_json, as_csv = stats.write_files(shown, sessions)
    except OSError as error:
        return Note(f"Export failed: {error}", "error")
    return Note(f"Saved {as_json.name} and {as_csv.name} in {as_json.parent}", seconds=8)
