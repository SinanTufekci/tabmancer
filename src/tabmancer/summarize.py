"""On-demand AI summaries of a session, written by Claude Code itself.

Runs `claude -p` with a small model, no tools and no session persistence, so it uses
whatever plan or API key Claude Code is logged in with and leaves no transcript
behind. Summaries are cached until the transcript changes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime

from .config import read_json, write_json
from .sessions import Session, _is_real_prompt, _shorten, _text, sanitize

FILE = "summaries.json"

MODEL = os.environ.get("TABMANCER_SUMMARY_MODEL", "haiku")
MAX_PROMPTS = 40
INSTRUCTIONS = """Summarise this coding-agent session for someone deciding whether to reopen it.
At most three short sentences and 60 words, in the same language as the user's messages:
what the goal was, what got done, and what was left open or where it stopped. Plain text
only: no headings, no lists, no preamble. Do not use any tools.
"""

_lock = threading.Lock()


class SummaryError(Exception):
    pass


def digest(session: Session, max_prompts: int = MAX_PROMPTS, keep_replies: int = 3) -> str:
    """The user's prompts, Claude's last replies and the edited files, trimmed to size."""
    prompts: list[str] = []
    replies: list[str] = []
    with session.path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            # Conversation records start with parentUuid; request blobs and
            # bookkeeping records (the bulk of a big transcript) do not.
            if not line.startswith('{"parentUuid"'):
                continue
            is_user = '"type":"user"' in line
            if not is_user and '"type":"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            record = sanitize(record)
            content = (record.get("message") or {}).get("content")
            if record.get("type") == "user" and _is_real_prompt(record):
                prompts.append(_shorten(_text(content), 300))
            elif record.get("type") == "assistant":
                reply = _text(content).strip()
                if reply:
                    replies = [*replies[-(keep_replies - 1):], _shorten(reply, 600)] if keep_replies > 1 else \
                        [_shorten(reply, 600)]
    if len(prompts) > max_prompts:
        prompts = [*prompts[:5], "(…)", *prompts[-(max_prompts - 5):]]
    parts = [f"Title: {session.title}", f"Folder: {session.cwd}",
             f"How it ended: {session.status.value}", "", "User messages, oldest first:"]
    parts += [f"- {p}" for p in prompts]
    if replies:
        parts += ["", "Claude's last replies:", *[f"- {r}" for r in replies]]
    if session.files:
        parts += ["", "Files edited near the end: " + ", ".join(session.files)]
    return "\n".join(parts)


def load_cache() -> dict[str, dict]:
    data = read_json(FILE)
    return data if isinstance(data, dict) else {}


def _store(session_id: str, entry: dict | None) -> None:
    with _lock:  # summaries can finish on several worker threads at once
        data = load_cache()
        if entry is None:
            data.pop(session_id, None)
        else:
            data[session_id] = entry
        write_json(FILE, data)


def forget(session_ids: list[str]) -> None:
    """Drop cached summaries, for sessions that were deleted."""
    for session_id in session_ids:
        _store(session_id, None)


def session_digest(session: Session) -> str:
    """What an AI is shown of a session. Antigravity's conversations cannot be read."""
    if session.cli == "agy":
        raise SummaryError("Antigravity stores its conversations in a binary format, so there is "
                           "nothing to read.")
    if session.cli == "codex":
        from .codex import digest as codex_digest

        return codex_digest(session)
    return digest(session)


def ask(prompt: str, model: str, timeout: float = 180) -> str:
    """One answer from `claude -p`: no tools, no transcript left behind, run from a neutral
    folder so no project CLAUDE.md is loaded into the request."""
    claude = shutil.which("claude")
    if not claude:
        raise SummaryError("The claude CLI is not on PATH.")
    try:
        result = subprocess.run(
            [claude, "-p", "--model", model, "--tools", "", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as error:
        raise SummaryError(str(error)) from error
    answer = result.stdout.strip()
    if result.returncode != 0 or not answer:
        raise SummaryError(result.stderr.strip()[:300] or "claude returned nothing.")
    return answer


def summarize(session: Session) -> dict:
    """Ask Claude for a summary, cache it and return the cache entry."""
    text = session_digest(session)
    size = session.path.stat().st_size
    summary = " ".join(ask(INSTRUCTIONS + "\n" + text, MODEL).split())
    entry = {"summary": summary, "size": size, "model": MODEL,
             "at": datetime.now().isoformat(timespec="seconds")}
    _store(session.session_id, entry)
    return entry
