"""Search what was said in every session: your prompts and Claude's replies.

The filter (/) used to look at a session's folder, title and first and last prompts only, so
"which session did we talk about X in?" had no answer. This keeps the words of each
conversation, without tool calls, their output or the requests: of 2.4 GB of transcripts
that is 1.7 million characters here.

They are kept in search.json in the config folder, with how far each transcript has been
read. Transcripts only grow, so after the first time (about 3.5 s for 2.4 GB) only what was
added since is read.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import read_json, write_json
from .sessions import Session, _is_real_prompt, _text

CACHE_FILE = "search.json"
CACHE_FORMAT = 1
SHORTEST = 3  # shorter words are found in nearly every conversation: titles and prompts only
_lock = threading.Lock()


class Index:
    """Session id -> what was said in it. update() reads what is new; find() looks."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self.entries = entries or {}  # id -> {"path", "read": bytes read, "text"}
        self.folded: dict[str, str] = {}  # id -> fold(text), made when first looked in

    @classmethod
    def load(cls) -> Index:
        cached = read_json(CACHE_FILE)
        ok = isinstance(cached, dict) and cached.get("format") == CACHE_FORMAT
        return cls(cached.get("entries", {}) if ok else {})

    def update(self, sessions: list[Session]) -> bool:
        """Read what the Claude Code transcripts gained since last time; True if anything did."""
        changed = False
        with _lock:
            for session in sessions:
                if session.cli != "claude":
                    continue
                entry = self.entries.get(session.session_id)
                if entry is None or entry.get("path") != str(session.path) or entry.get("read", 0) > session.size:
                    entry = {"path": str(session.path), "read": 0, "text": ""}  # new, moved or rewritten
                if entry["read"] == session.size and session.session_id in self.entries:
                    continue
                try:
                    _read(session.path, entry)
                except OSError:
                    continue
                self.entries[session.session_id] = entry
                self.folded.pop(session.session_id, None)
                changed = True
            listed = {s.session_id for s in sessions}
            gone = [sid for sid, entry in self.entries.items()  # deleted (cleanup) or moved away
                    if sid not in listed and not Path(entry.get("path", "")).is_file()]
            for sid in gone:
                del self.entries[sid]
                self.folded.pop(sid, None)
            changed = changed or bool(gone)
            if changed:
                try:
                    write_json(CACHE_FILE, {"format": CACHE_FORMAT, "entries": self.entries}, indent=None)
                except OSError:
                    pass  # a read-only config folder only costs speed
        return changed

    def text(self, session_id: str) -> str:
        return (self.entries.get(session_id) or {}).get("text", "")

    def find(self, needle: str, session_ids) -> set[str]:
        """The sessions among these in which the words were said (case does not matter)."""
        needle = fold(needle.strip())
        if len(needle) < SHORTEST:
            return set()
        found = set()
        for sid in session_ids:
            folded = self.folded.get(sid)
            if folded is None:
                folded = self.folded[sid] = fold(self.text(sid))
            if needle in folded:
                found.add(sid)
        return found

    def snippet(self, session_id: str, needle: str, width: int = 90) -> tuple[str, int, int]:
        """(a stretch of what was said around the first match, where the match starts and ends in it)."""
        text = self.text(session_id)
        at = fold(text).find(fold(needle.strip()))  # fold keeps the length, so places carry over
        if at < 0:
            return "", 0, 0
        size = len(needle.strip())
        start = max(0, at - (width - size) // 3)
        piece = text[start:start + width]
        lead = "…" if start else ""
        tail = "…" if start + width < len(text) else ""
        piece = " ".join(piece.split("\n"))
        return lead + piece + tail, len(lead) + at - start, len(lead) + at - start + size


def fold(text: str) -> str:
    """Lower case of the same length. casefold() and lower() turn the Turkish İ into two
    characters (i and a combining dot), so "istanbul" missed "İstanbul" and every match after
    an İ was placed one off. It is the only letter whose lower case changes length."""
    return text.replace("İ", "i").lower()


def _read(path: Path, entry: dict) -> None:
    """Add the prompts and replies after entry["read"] to entry["text"]; whole lines only."""
    with path.open("rb") as f:
        f.seek(entry["read"])
        data = f.read()
    end = data.rfind(b"\n") + 1  # a line still being written is read next time
    words = []
    for raw in data[:end].split(b"\n"):
        if b'"type":"user"' not in raw and b'"type":"assistant"' not in raw:
            continue  # requests, tool output and the rest: never parsed
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        if record.get("type") == "user" and _is_real_prompt(record):
            words.append(_text(record["message"]["content"]))
        elif record.get("type") == "assistant":
            words.append(_text((record.get("message") or {}).get("content")))
    added = "\n".join(w.strip() for w in words if w.strip())
    if added:
        entry["text"] = f"{entry['text']}\n{added}" if entry["text"] else added
    entry["read"] += end
