"""The numbers behind the stats screen and `tabmancer stats`, and their export to JSON and CSV."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from . import usage
from .sessions import Session, folder_key
from .tokens import Report, Tokens

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
    for entry in groups.values():
        entry["seconds"] = report.wall_seconds(entry.pop("ids"))
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
            "output": use.output, "total": use.total}


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
    projects: dict[str, dict] = {}
    for sid, use in per_session.items():
        folder = folder_of(sid, report, sessions)
        entry = projects.setdefault(folder_key(folder), {"project": project_name(folder), "folder": folder,
                                                         "sessions": 0, **_use(Tokens())})
        entry["sessions"] += 1
        for key, value in _use(use).items():
            entry[key] += value
    readings = usage.history(report.days)
    return {
        "first_day": report.first.isoformat(), "days": report.days,
        "daily": daily_rows(report),
        "projects": sorted(projects.values(), key=lambda p: p["total"], reverse=True),
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
