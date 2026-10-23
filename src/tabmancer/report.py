"""A report of the work over a period, to share: written by Claude from the session list.

It reads only what the dashboard already knows about each session (title, prompts, last
reply or AI summary, files, lines changed), so it is one model call however many sessions
there were. The numbers at the end (sessions, time at work, tokens per project) come from
the stats, not from the model. Reports are cached until the sessions behind them change.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import stats, summarize
from .config import read_json, write_json
from .sessions import Session, _shorten, folder_key
from .tokens import Report, duration, human

FILE = "reports.json"
MODEL = os.environ.get("TABMANCER_REPORT_MODEL", "sonnet")
MAX_DIGEST = 60_000
PER_PROJECT = 15  # sessions shown to the model per project, newest first
INSTRUCTIONS = """Write a work report from the notes below on coding-agent sessions, for the person
who ran them to share with their team. Markdown, in the same language as the user's
messages in the notes. Begin with a level-1 heading for the period, with its dates. Then
two or three sentences on the period as a whole. Then a "## <project>" section for each
project with real work, most work first: short bullets on what got done, then one bullet
starting with "Open:" (in the report's language) for what is unfinished, if anything. Be
concrete (features, fixes, decisions, files). Leave out chit-chat, tests of the tools
themselves and anything that is not in the notes; do not mention tokens, costs or session
ids. At most 400 words before the table. End with a section, headed in the report's
language, that holds the numbers table given below: translate only its header row and the
note under it, and copy every other row exactly as it is, numbers and units included. Do
not use any tools.
"""


def in_period(sessions: list[Session], first: date) -> list[Session]:
    return [s for s in sessions if s.last_active.date() >= first]


def digest(sessions: list[Session], first: date, last: date, summaries: dict[str, dict]) -> str:
    """What the model is shown: the period, then each project's sessions, newest first."""
    groups: dict[str, list[Session]] = {}
    for session in sorted(in_period(sessions, first), key=lambda s: s.last_active, reverse=True):
        groups.setdefault(folder_key(session.cwd), []).append(session)
    lines = [f"Period: {first:%d %b %Y} to {last:%d %b %Y}", ""]
    for members in sorted(groups.values(), key=len, reverse=True):
        lines.append(f"# Project {members[0].project} ({members[0].cwd})")
        for session in members[:PER_PROJECT]:
            when = f"{session.started:%d %b}–{session.last_active:%d %b}" if session.started else \
                f"{session.last_active:%d %b}"
            lines.append(f"- {session.title} [{when}, {session.status.value}"
                         f"{'' if session.cli == 'claude' else ', ' + session.cli}]")
            lines.append(f"  asked first: {_shorten(session.first_prompt, 240)}")
            if session.last_prompt and session.last_prompt != session.first_prompt:
                lines.append(f"  asked last: {_shorten(session.last_prompt, 240)}")
            summary = summaries.get(session.session_id, {}).get("summary")
            if summary:
                lines.append(f"  summary: {_shorten(summary, 400)}")
            elif session.last_reply:
                lines.append(f"  last reply: {_shorten(session.last_reply, 400)}")
            if session.files:
                lines.append("  files: " + ", ".join(session.files[:8]))
            if session.lines_added is not None:
                lines.append(f"  lines: +{session.lines_added} -{session.lines_removed or 0}")
        if len(members) > PER_PROJECT:
            lines.append(f"- and {len(members) - PER_PROJECT} older sessions")
        lines.append("")
    text = "\n".join(lines)
    return text if len(text) <= MAX_DIGEST else text[:MAX_DIGEST] + "\n(cut short)"


def numbers(stats_report: Report, sessions: dict[str, Session]) -> str:
    """The numbers table, from the stats."""
    rows = stats.per_project(stats_report, sessions)
    table = ["| Project | Sessions | Time at work | Tokens | Output |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        table.append(f"| {row['project']} | {row['sessions']} | {duration(row['seconds'])} | "
                     f"{human(row['tokens'].total)} | {human(row['tokens'].output)} |")
    worked = sum(stats_report.activity.day_seconds)
    total = stats_report.total()
    table.append(f"| **All** | **{sum(r['sessions'] for r in rows)}** | **{duration(worked)}** | "
                 f"**{human(total.total)}** | **{human(total.output)}** |")
    table += ["", "_Projects worked on side by side overlap, so their times add up to more than the total._"]
    return "\n".join(table)


def has_numbers(body: str, table: str) -> bool:
    """Every row of the table made it into the body unchanged, bar spacing and bold."""
    def flat(text: str) -> str:
        return re.sub(r"[\s*]", "", text)

    written = flat(body)
    rows = [line for line in table.splitlines()[2:] if line.startswith("|")]  # past the header and |---|
    return bool(rows) and all(flat("|".join(row.strip().strip("|").split("|")[1:])) in written for row in rows)


def _load() -> dict:
    data = read_json(FILE)
    return data if isinstance(data, dict) else {}


def latest(days: int) -> dict | None:
    """The last report written over that many days, if any."""
    entry = _load().get(str(days))
    return entry if isinstance(entry, dict) and entry.get("text") else None


def key_for(sessions: list[Session], stats_report: Report, summaries: dict[str, dict]) -> str:
    first = stats_report.first
    last = first + timedelta(days=stats_report.days - 1)
    return hashlib.sha256(digest(sessions, first, last, summaries).encode("utf-8")).hexdigest()[:16]


def write(sessions: dict[str, Session], stats_report: Report, summaries: dict[str, dict] | None = None) -> dict:
    """Have Claude write the report for the stats report's period; cached until it would change."""
    summaries = summarize.load_cache() if summaries is None else summaries
    listed = list(sessions.values())
    first = stats_report.first
    last = first + timedelta(days=stats_report.days - 1)
    notes = digest(listed, first, last, summaries)
    key = hashlib.sha256(notes.encode("utf-8")).hexdigest()[:16]
    cached = latest(stats_report.days)
    if cached and cached.get("key") == key:
        return cached
    if not in_period(listed, first):
        raise summarize.SummaryError("No sessions in this period to report on.")
    table = numbers(stats_report, sessions)
    body = summarize.ask(f"{INSTRUCTIONS}\nNumbers table:\n{table}\n\nNotes:\n{notes}", MODEL, timeout=300).strip()
    if not body.startswith("#"):
        body = f"# {first:%d %b} – {last:%d %b %Y}\n\n{body}"
    if not has_numbers(body, table):  # the numbers never depend on the model copying them right
        body += f"\n\n## Numbers\n\n{table}"
    text = body + "\n"
    entry = {"text": text, "key": key, "days": stats_report.days, "first": first.isoformat(),
             "last": last.isoformat(), "model": MODEL, "at": datetime.now().isoformat(timespec="seconds")}
    data = _load()
    data[str(stats_report.days)] = entry
    write_json(FILE, data)
    return entry


def save(entry: dict, folder: Path | None = None) -> Path:
    """Write the report as Markdown next to your downloads."""
    if folder is None:
        downloads = Path.home() / "Downloads"
        folder = downloads if downloads.is_dir() else Path.home()
    path = folder / f"tabmancer-report-{entry['first']}-to-{entry['last']}.md"
    path.write_text(entry["text"], encoding="utf-8")
    return path
