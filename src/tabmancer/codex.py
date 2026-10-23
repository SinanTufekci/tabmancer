"""Codex sessions, read from its rollout files.

Codex writes each session to ~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl
(or under CODEX_HOME): a session_meta record first, then the conversation as
event_msg records (user_message, agent_message, task_started, task_complete ...).
Like Claude Code's transcripts the format is internal, so reading is defensive.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from .sessions import Exit, Session, Status, _glob, _parse_lines, _read_cached, _relative, _shorten

HEAD_BYTES = 256 * 1024
MAX_HEAD_BYTES = 8 * 1024 * 1024
TAIL_BYTES = 2 * 1024 * 1024
PATCHED = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE)
WORK = {"agent_message", "agent_reasoning", "exec_command_begin", "exec_command_end", "patch_apply_begin",
        "patch_apply_end", "mcp_tool_call_begin", "web_search_end", "item_completed"}


def home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _clean_path(path: str) -> str:
    return path.removeprefix("\\\\?\\")  # Windows long-path prefix


def _stamp(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def read(path: Path) -> Session | None:
    """One rollout, or None if it holds no prompt of yours."""
    try:
        stat = path.stat()
        with path.open("rb") as f:
            span = HEAD_BYTES
            while True:  # read further while the first prompt is not in view yet
                f.seek(0)
                head = _parse_lines(f.read(span), drop_first=False, drop_last=stat.st_size > span)
                if span >= min(stat.st_size, MAX_HEAD_BYTES) or any(_said(r, "user") for r in head):
                    break
                span *= 4
            if stat.st_size <= span:
                tail = head  # the head was the whole file
            else:
                start = max(0, stat.st_size - TAIL_BYTES)
                f.seek(start)
                tail = _parse_lines(f.read(), drop_first=start > 0, drop_last=False)
    except OSError:
        return None
    meta = next((r.get("payload") for r in head if r.get("type") == "session_meta"), None)
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    session = Session(session_id=str(meta["id"]), path=path, cli="codex", size=stat.st_size,
                      cwd=_clean_path(str(meta.get("cwd") or "")), exit=Exit.UNKNOWN,
                      started=_stamp(meta.get("timestamp")),
                      last_active=datetime.fromtimestamp(stat.st_mtime))
    # "cli" for the interactive TUI; "exec" for `codex exec`, which is what claude -p is to Claude.
    source = str(meta.get("source") or "")
    session.entrypoint = source or ("cli" if meta.get("originator") == "codex-tui" else "?")
    git = meta.get("git")
    session.git_branch = str(git.get("branch") or "") if isinstance(git, dict) else ""
    first = next((text for text in (_said(r, "user") for r in head) if text), None)
    if not first:
        return None
    session.first_prompt = _shorten(first, 300)
    session.title = _shorten(first, 80)
    _read_tail(tail, session)
    return session


def _message(record: dict) -> tuple[str, str] | None:
    """("user" or "assistant", text) for a message event, else None.

    Older Codex writes user_message and agent_message events; newer Codex writes
    item_completed events holding a UserMessage or AgentMessage item. Both come
    alongside response_item copies, which also carry injected context, so only the
    events are read.
    """
    payload = record.get("payload")
    if record.get("type") != "event_msg" or not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind in ("user_message", "agent_message"):
        message = payload.get("message")
        return ("user" if kind == "user_message" else "assistant", message if isinstance(message, str) else "")
    item = payload.get("item")
    if kind == "item_completed" and isinstance(item, dict) and item.get("type") in ("UserMessage", "AgentMessage"):
        parts = item.get("content") if isinstance(item.get("content"), list) else []
        text = "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        return ("user" if item["type"] == "UserMessage" else "assistant", text)
    return None


def _said(record: dict, role: str) -> str | None:
    """The text of a message event from that role, else None."""
    message = _message(record)
    return message[1] if message and message[0] == role else None


def _read_tail(records: list[dict], session: Session) -> None:
    status = Status.UNKNOWN
    worked = False  # anything happened since the last prompt
    files: dict[str, None] = {}
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if record.get("type") == "response_item" and payload.get("name") == "apply_patch":
            text = payload.get("input") or payload.get("arguments") or ""
            for target in PATCHED.findall(text if isinstance(text, str) else ""):
                files.pop(target, None)
                files[target] = None
            continue
        if record.get("type") != "event_msg":
            continue
        kind = payload.get("type")
        message = _message(record)
        if message and message[0] == "user":
            session.last_prompt = _shorten(message[1], 300)
            status, worked = Status.UNANSWERED, False
        elif kind == "task_started":
            status = Status.UNANSWERED if not worked else Status.CUT_OFF
        elif message or kind in WORK:
            worked = True
            if message and message[1]:
                session.last_reply = _shorten(message[1], 500)
            if status is Status.UNANSWERED:
                status = Status.CUT_OFF  # working on it, and nothing says it finished
        elif kind == "task_complete":
            status = Status.DONE
            if payload.get("last_agent_message"):
                session.last_reply = _shorten(str(payload["last_agent_message"]), 500)
        elif kind == "turn_aborted":
            status = Status.STOPPED
        elif kind in ("error", "stream_error"):
            status = Status.ERROR
    session.status = status
    absolute = [f if Path(f).is_absolute() else str(Path(session.cwd) / f) for f in reversed(files)]
    session.files = _relative(absolute, session.cwd)[:8]


def rollouts(days: float) -> list[Path]:
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    found = []
    for path in _glob(home() / "sessions", "*/*/*/rollout-*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                found.append(path)
        except OSError:
            continue
    return found


def find(days: float = 14) -> list[Session]:
    return _read_cached(rollouts(days), read)


def digest(session: Session) -> str:
    """Your prompts and Codex's last replies, for an AI summary."""
    prompts: list[str] = []
    replies: list[str] = []
    with session.path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"event_msg"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            message = _message(record)
            if not message or not message[1]:
                continue
            role, text = message
            if role == "user":
                prompts.append(_shorten(text, 300))
            else:
                replies.append(_shorten(text, 600))
    parts = [f"Folder: {session.cwd}", "Your messages:", *(f"- {p}" for p in prompts[-40:])]
    parts += ["Codex's last replies:", *(f"- {r}" for r in replies[-3:])]
    if session.files:
        parts.append("Files edited: " + ", ".join(session.files))
    return "\n".join(parts)
