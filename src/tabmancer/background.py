"""Background sessions (`claude --bg`): see them all, stop them, remove them.

A background session's process stays alive after Claude finishes, waiting for more,
and its entry stays after the process ends, so both pile up. `claude stop` ends the
process and keeps the conversation. `claude rm` deletes the entry and its worktree;
the transcript stays on disk, so the session can still be resumed from the list.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label

from . import sessions
from .dialogs import frame, title
from .launch import clean_env
from .safework import work
from .theme import AMBER, GREEN, MUTED, RED, TEXT, keys

CONFIRM_SECONDS = 4  # a second press within this long confirms
REFRESH_SECONDS = 5
NEEDS_YOU = {"blocked", "waiting"}
WORKING = {"working", "busy"}
IDLE = {"done", "idle"}


@dataclass
class Job:
    id: str          # the short id that `claude attach`, `stop` and `rm` take
    session_id: str
    name: str
    cwd: str
    state: str       # as Claude Code reports it: working, blocked, done ...
    running: bool    # its process is alive
    started: datetime | None = None

    @property
    def project(self) -> str:
        return Path(self.cwd).name or self.cwd or "?"

    @property
    def idle(self) -> bool:
        """Running but finished: it only waits for a next message nobody may send."""
        return self.running and self.state.lower() in IDLE

    @property
    def tidy(self) -> bool:
        """Safe to clear away: nothing is working and nothing waits for an answer. A session still
        starting has no pid yet but works: it was cleared away as stopped (c twice), worktree and all."""
        return (not self.running and self.state.lower() not in WORKING) or self.idle


def jobs() -> list[Job] | None:
    """Every background session Claude Code knows, running or not; None without the CLI."""
    entries = sessions._agents_json(include_finished=True)
    if entries is None:
        return None
    found = []
    for entry in entries:
        if entry.get("kind") != "background" or not entry.get("id"):
            continue
        started = entry.get("startedAt")
        try:
            when = datetime.fromtimestamp(started / 1000) if isinstance(started, (int, float)) else None
        except (OverflowError, OSError, ValueError):
            when = None
        found.append(Job(id=str(entry["id"]), session_id=str(entry.get("sessionId") or ""),
                         name=str(entry.get("name") or ""), cwd=str(entry.get("cwd") or ""),
                         state=str(entry.get("state") or entry.get("status") or ""),
                         running=bool(entry.get("pid")), started=when))
    order = {"needs": 0, "working": 1, "idle": 2, "stopped": 3}
    found.sort(key=lambda job: (order[kind(job)], -(job.started.timestamp() if job.started else 0)))
    return found


def kind(job: Job) -> str:
    state = job.state.lower()
    if not job.running:
        return "working" if state in WORKING else "stopped"  # a session still starting has no pid yet
    if state in NEEDS_YOU:
        return "needs"
    return "working" if state in WORKING else "idle"


LABELS = {
    "needs": ("! needs you", f"bold {RED}"),
    "working": ("● working", AMBER),
    "idle": ("● idle", GREEN),
    "stopped": ("○ stopped", MUTED),
}


def _run(*args: str) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise OSError("The claude CLI is not on PATH.")
    try:
        result = subprocess.run(
            [claude, *args], env=clean_env(), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.SubprocessError as error:
        raise OSError(str(error)) from error
    message = (result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        raise OSError(message[:500] or f"claude {args[0]} failed")
    return message or f"{args[0]}: done"


def stop(job_id: str) -> str:
    """End the process; the conversation is kept and `claude attach` brings it back."""
    return _run("stop", job_id)


def remove(job_id: str) -> str:
    """Delete the entry and its worktree. Claude Code refuses if that would lose work."""
    return _run("rm", job_id)


def ago(moment: datetime | None) -> str:
    if moment is None:
        return ""
    seconds = max(0.0, (datetime.now() - moment).total_seconds())
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


class BackgroundDialog(ModalScreen[Job | None]):
    """The list of background sessions. Dismisses with a job to attach to, or None."""

    DEFAULT_CSS = frame("BackgroundDialog", "large") + """
    BackgroundDialog DataTable { height: auto; max-height: 20; background: $surface; }
    BackgroundDialog #note { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("x", "stop", "Stop"),
        Binding("d,delete", "remove", "Remove"),
        Binding("c", "clean_up", "Clean up"),
        Binding("enter", "attach", "Attach", priority=True),
    ]

    def __init__(self, found: list[Job]) -> None:
        super().__init__()
        self.jobs = found
        self.armed: tuple[str, str] | None = None  # (action, job id) waiting for a second press
        self.armed_at = 0.0
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("", id="title")
            yield DataTable(cursor_type="row", id="jobs")
            yield Label("", id="note")
            yield Label(keys(("Enter", "attach"), ("x", "stop"), ("d", "remove"), ("c", "clean up idle and stopped"),
                             ("Esc", "close")), classes="hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, label in (("state", "State"), ("project", "Project"), ("name", "Name"),
                           ("started", "Started"), ("id", "Id")):
            table.add_column(label, key=key)
        self.show_jobs(self.jobs)
        table.focus()
        self.set_interval(REFRESH_SECONDS, self.refresh_jobs)

    @work(thread=True, exclusive=True, group="refresh")
    def refresh_jobs(self) -> None:
        if self.busy or self.armed:
            return  # never move rows under a pending confirmation
        found = jobs()
        if found is not None and not self.busy:
            self.app.call_from_thread(self.show_jobs, found)

    def show_jobs(self, found: list[Job]) -> None:
        self.jobs = found
        table = self.query_one(DataTable)
        keep = self.current()
        table.clear()
        for job in found:
            label, style = LABELS[kind(job)]
            table.add_row(Text(label, style=style), Text(job.project[:18], style=TEXT),
                          Text(job.name[:44] or job.session_id[:8]), Text(ago(job.started), style=MUTED),
                          Text(job.id, style=MUTED), key=job.id)
        if keep is not None and keep.id in table.rows:
            table.move_cursor(row=table.get_row_index(keep.id))
        tidy = sum(job.tidy for job in found)
        self.query_one("#title", Label).update(title("Background sessions", str(len(found)),
                                                     *([f"{tidy} idle or stopped"] if tidy else [])))
        if not found:
            self.note("No background sessions. Ctrl+B in a prompt starts one.")

    def current(self) -> Job | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return next((job for job in self.jobs if job.id == key), None)

    def note(self, message: str, style: str = MUTED) -> None:
        self.query_one("#note", Label).update(Text(message, style=style))

    def confirmed(self, action: str, target: str, warning: str) -> bool:
        """True on the second press of the same key for the same target; arms it on the first."""
        if self.armed == (action, target) and time.monotonic() - self.armed_at < CONFIRM_SECONDS:
            self.armed = None
            return True
        self.armed, self.armed_at = (action, target), time.monotonic()
        self.note(warning, AMBER)
        return False

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.armed:
            self.armed = None
            self.note("")

    def action_stop(self) -> None:
        job = self.current()
        if job is None or self.busy:
            return
        if not job.running:
            self.note(f"{job.id} is not running.")
            return
        if kind(job) != "idle" and not self.confirmed(
                "stop", job.id, f"{job.project} is {kind(job).replace('needs', 'waiting for you')}: "
                                "press x again to stop it. Its conversation is kept."):
            return
        self.run_commands([("stop", job)])

    def action_remove(self) -> None:
        job = self.current()
        if job is None or self.busy:
            return
        stopping = "stop and remove" if job.running else "remove"
        if self.confirmed("remove", job.id, f"Press d again to {stopping} {job.id}. Its transcript stays, "
                                            "so the session can still be resumed from the list."):
            self.run_commands([("remove", job)])

    def action_clean_up(self) -> None:
        tidy = [job for job in self.jobs if job.tidy]
        if self.busy:
            return
        if not tidy:
            self.note("Nothing to clean up: every background session is working or waiting for you.")
            return
        if self.confirmed("clean", "*", f"Press c again to remove {len(tidy)} idle or stopped background "
                                        "sessions. Their transcripts stay."):
            self.run_commands([("remove", job) for job in tidy])

    def action_attach(self) -> None:
        job = self.current()
        if job is not None:
            self.dismiss(job)

    def action_close(self) -> None:
        self.dismiss(None)

    @work(thread=True, exclusive=True)
    def run_commands(self, commands: list[tuple[str, Job]]) -> None:
        self.busy = True
        self.app.call_from_thread(self.note, "Working…")
        done, failures = 0, []
        for action, job in commands:
            try:
                if job.running:
                    stop(job.id)  # a session is stopped before it is removed
                if action == "remove":
                    remove(job.id)
                done += 1
            except OSError as error:
                failures.append(f"{job.id}: {error}")
        found = jobs()
        self.busy = False
        self.app.call_from_thread(self.finished, commands, done, failures, found)

    def finished(self, commands: list[tuple[str, Job]], done: int, failures: list[str],
                 found: list[Job] | None) -> None:
        if found is not None:
            self.show_jobs(found)
        verb = "Stopped" if commands[0][0] == "stop" else "Removed"
        if failures:
            self.note(f"{verb} {done} of {len(commands)}. " + " ".join(failures), RED)
        else:
            what = commands[0][1].project if len(commands) == 1 else f"{done} background sessions"
            self.note(f"{verb} {what}.", GREEN)
        refresh = getattr(self.app, "poll_live", None)
        if refresh is not None:
            refresh(full=True)  # the dashboard shows the change straight away
