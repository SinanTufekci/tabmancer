"""Antigravity CLI (agy) sessions, from its conversation index.

agy keeps an index of its conversations in
~/.gemini/antigravity-cli/conversation_summaries.db (SQLite): title, folder, times and
run state. The conversations themselves are protobuf blobs, so prompts and replies
cannot be read; a session shows its title, folder and when it was last used.
The database is opened read-only and never written.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from .sessions import Exit, Session, Status, _shorten

INDEX = "conversation_summaries.db"


def home() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def _when(value) -> datetime | None:
    """'2026-10-16 11:57:27.4323262+00:00' -> local time; Go writes 7 digits of fraction."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("T", " ")
    zone = ""
    for sign in ("+", "-"):
        cut = text.rfind(sign)
        if cut > 10:
            text, zone = text[:cut], text[cut:]
            break
    if "." in text:
        whole, fraction = text.split(".", 1)
        text = f"{whole}.{fraction[:6]}"
    try:
        moment = datetime.fromisoformat(text + zone)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment.year < 1980:
        return None  # Go's zero time: never set
    try:
        return moment.astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _folder(uris) -> str:
    try:
        first = (json.loads(uris) if isinstance(uris, str) and uris else [None])[0]
    except (ValueError, IndexError, TypeError):
        return ""
    if not isinstance(first, str) or not first.startswith("file:"):
        return ""
    path = unquote(urlparse(first).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":  # /C:/Users -> C:/Users
        path = path[1:]
    return str(Path(path))


def _rows() -> list[dict]:
    path = home() / INDEX
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM conversation_summaries").fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def find(days: float = 14) -> list[Session]:
    since = datetime.now() - timedelta(days=days)
    sessions = []
    for row in _rows():
        sid = str(row.get("conversation_id") or "")
        last = _when(row.get("last_modified_time"))
        if not sid or last is None or last < since or row.get("nesting_depth"):
            continue  # nested ones are subagents of another conversation
        title = str(row.get("title") or row.get("preview") or "").strip()
        preview = str(row.get("preview") or "").strip()
        status = str(row.get("status") or "")
        session = Session(session_id=sid, path=home() / "conversations" / f"{sid}.db", cli="agy",
                          cwd=_folder(row.get("workspace_uris")), title=_shorten(title or "(untitled)", 80),
                          first_prompt=_shorten(preview or title, 300), entrypoint="cli",
                          last_active=last,
                          exit=Exit.UNKNOWN, size=int(row.get("step_count") or 0))
        if row.get("killed") or row.get("not_fully_idle"):
            session.status = Status.CUT_OFF  # stopped while it was still working
        elif status.endswith("IDLE"):
            session.status = Status.DONE
        sessions.append(session)
    return sessions
