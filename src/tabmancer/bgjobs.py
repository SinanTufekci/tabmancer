"""Background sessions (`claude --bg`), with no UI at all: list them, stop them, remove them.

A background session's process stays alive after Claude finishes, waiting for more,
and its entry stays after the process ends, so both pile up. `claude stop` ends the
process and keeps the conversation. `claude rm` deletes the entry and its worktree;
the transcript stays on disk, so the session can still be resumed from the list.

The B screen (background.py), a team's close (actions.py) and a desktop window use these alike.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import sessions
from .launch import clean_env
from .palette import AMBER, GREEN, MUTED, RED

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


def state_of(entry: dict) -> str:
    """How a background job stands, as sessions.mark_live reads it: a job says how it stands (working,
    blocked) and how its turn does (busy, idle, waiting). A working job whose turn is over is idle;
    B showed it at work while the list said idle, and c left it where it sat."""
    state, turn = str(entry.get("state") or ""), str(entry.get("status") or "")
    return turn if state in ("", "working") and turn else state


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
                         state=state_of(entry),
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
