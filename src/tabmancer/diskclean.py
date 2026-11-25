"""Sessions on disk, with no UI at all: what each takes (its transcript, its subagents, its file
snapshots for rewind), biggest first, and moving the ones chosen to the Recycle Bin (Trash on macOS
and Linux). `tabmancer cleanup` (cleanup.py) and a desktop window use these alike.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import summarize
from .sessions import (
    Exit,
    Session,
    _glob,
    _read_cached,
    claude_dir,
    live_sessions,
    mark_live,
)

DEFAULT_CLEANUP_DAYS = 30  # Claude Code's own cleanupPeriodDays default


@dataclass
class Item:
    session: Session
    files: list[Path]
    size: int

    @property
    def is_open(self) -> bool:
        return self.session.exit is Exit.RUNNING


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def disk_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return 0


def session_files(session_id: str, transcript: Path) -> list[Path]:
    """Everything Claude Code keeps for one session."""
    root = claude_dir()
    candidates = [transcript, transcript.with_suffix(""),  # <project>/<id>/ holds subagents
                  root / "file-history" / session_id, root / "session-env" / session_id]
    return [p for p in candidates if p.exists()]


def claude_cleanup_days() -> int:
    """How long Claude Code itself keeps transcripts (cleanupPeriodDays)."""
    try:
        settings = json.loads((claude_dir() / "settings.json").read_text(encoding="utf-8"))
        return int(settings.get("cleanupPeriodDays", DEFAULT_CLEANUP_DAYS))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_CLEANUP_DAYS


def collect() -> list[Item]:
    """Every session on disk, biggest first, including ones with nothing to resume."""
    paths = _glob(claude_dir() / "projects", "*/*.jsonl")
    parsed = {s.path: s for s in _read_cached(paths)}
    live = live_sessions()
    items = []
    for path in paths:
        session = parsed.get(path)
        if session is None:
            try:
                stat = path.stat()
            except OSError:
                continue
            session = Session(session_id=path.stem, path=path, cwd=path.parent.name,
                              title="(no prompt: nothing to resume)", size=stat.st_size,
                              last_active=datetime.fromtimestamp(stat.st_mtime))
        mark_live(session, live.get(session.session_id))
        files = session_files(session.session_id, path)
        items.append(Item(session, files, sum(disk_size(f) for f in files)))
    items.sort(key=lambda item: item.size, reverse=True)
    return items


BIN = "Recycle Bin" if sys.platform == "win32" else "Trash"


def older_than(items: list[Item], days: float, now: datetime | None = None) -> list[Item]:
    """The closed sessions last active more than `days` ago: what the clean-up screen's o picks
    (7 days) and `tabmancer cleanup --older DAYS` moves. Open ones are never picked."""
    cutoff = (now or datetime.now()).timestamp() - days * 86400
    return [item for item in items if not item.is_open and item.session.last_active.timestamp() < cutoff]


def as_json(item: Item) -> dict:
    s = item.session
    return {"session_id": s.session_id, "project": s.project, "title": s.title, "cwd": s.cwd,
            "last_active": s.last_active.isoformat(timespec="seconds"), "size": item.size, "open": item.is_open,
            "files": [str(path) for path in item.files]}


def _trash_one(path: Path) -> None:
    from send2trash import send2trash  # imported late: only deleting needs it

    send2trash(str(path))


def trash(items: list[Item]) -> tuple[list[Item], list[str]]:
    """Move sessions to the Recycle Bin. Returns the ones moved and any problems. Which are open is
    asked again here, not taken from the list: one resumed while the clean-up screen stood open went
    to the Recycle Bin from under the Claude Code still writing it."""
    root = claude_dir().resolve()
    moved, problems = [], []
    open_now = set(live_sessions())
    for item in items:
        if item.is_open or item.session.session_id in open_now:
            problems.append(f"{item.session.project}: open right now, skipped")
            continue
        ok = True
        for path in item.files:
            if not path.resolve().is_relative_to(root):
                problems.append(f"{path}: outside {root}, skipped")
                ok = False
                continue
            try:
                _trash_one(path)
            except OSError as error:
                problems.append(f"{path}: {error}")
                ok = False
        if ok:
            moved.append(item)
    summarize.forget([item.session.session_id for item in moved])
    return moved, problems
