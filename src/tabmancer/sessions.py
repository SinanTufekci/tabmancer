"""Find Claude Code sessions on disk and work out where each one left off.

Transcripts are JSONL files under ~/.claude/projects/<project>/<session-id>.jsonl.
Their format is internal to Claude Code, so everything here is defensive: an unknown
record shape degrades a field to empty, it never raises.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .config import read_json, write_json

CACHE_FILE = "index.json"
CACHE_FORMAT = 7  # bump when Session or the parser changes shape (7: the effort of the last reply)
HEAD_BYTES = 512 * 1024
TAIL_BYTES = 2 * 1024 * 1024
MAX_HEAD_BYTES = 8 * 1024 * 1024
MAX_TAIL_BYTES = 32 * 1024 * 1024
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
LOCAL_PREFIXES = ("<command-name>", "<command-message>", "<local-command", "<bash-input>",
                  "<bash-stdout>", "<bash-stderr>")
# "resets 4pm (Europe/Istanbul)", "resets 4:10pm (...)", "resets Oct 6, 6am (...)"
RESETS = re.compile(r"resets\s+(?:(?P<month>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2}),?\s+)?(?P<hour>\d{1,2})"
                    r"(?::(?P<minute>\d{2}))?\s*(?P<half>[ap]m)(?:\s*\((?P<zone>[^)]+)\))?", re.IGNORECASE)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class Status(str, Enum):
    RUNNING = "running"        # open in a terminal right now
    BACKGROUND = "background"  # running as a background session (claude --bg)
    DONE = "done"              # Claude had finished its reply; the session was idle
    STOPPED = "stopped"        # you pressed Esc in the middle of a reply
    CUT_OFF = "cut-off"        # closed while Claude was still working
    UNANSWERED = "unanswered"  # your last message never got a reply
    ERROR = "error"            # the last reply was an API error
    LIMIT = "limit"            # stopped at a usage limit; Session.resets says when it lifts
    DROPPED = "dropped"        # the connection to the API dropped
    UNKNOWN = "unknown"


class Exit(str, Enum):
    RUNNING = "running"
    CLEAN = "clean"    # process shut down normally: /exit, closed window, OS shutdown
    KILLED = "killed"  # process died without cleaning up: crash, power loss, task kill
    UNKNOWN = "unknown"  # a CLI that leaves no record of how its process ended


INTERRUPTED = (Status.CUT_OFF, Status.UNANSWERED, Status.ERROR, Status.LIMIT, Status.DROPPED)  # to continue


def folder_key(path: str) -> str:
    """A folder as a key: the same folder written two ways is one project."""
    return os.path.normcase(os.path.normpath(path)) if path else ""


@dataclass
class Session:
    session_id: str
    path: Path
    cwd: str = ""
    title: str = ""
    first_prompt: str = ""
    last_prompt: str = ""
    last_reply: str = ""
    git_branch: str = ""
    entrypoint: str = ""
    started: datetime | None = None
    last_active: datetime = field(default_factory=datetime.now)
    status: Status = Status.UNKNOWN
    exit: Exit = Exit.CLEAN
    files: list[str] = field(default_factory=list)
    lines_added: int | None = None
    lines_removed: int | None = None
    cost_usd: float | None = None
    live_state: str = ""  # busy / idle / blocked ... while a process has it open
    size: int = 0  # transcript bytes; a change means new activity
    cli: str = "claude"  # which CLI it belongs to, see clis.CLIS
    context: int = 0  # tokens the last reply was sent with: how full the context window is
    model: str = ""   # the model of the last reply
    effort: str = ""  # ...and its effort, which Claude Code writes on each reply (2.1.319); empty before that
    resets: datetime | None = None  # stopped at a usage limit (open or not): when the limit lifts
    signed_in_again: bool = False  # ...and /login in it since: another account, so it can go on now

    @property
    def project(self) -> str:
        return Path(self.cwd).name or self.cwd or "?"


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def nothing_found(days: float, include_all: bool, dashboard: bool = False) -> str:
    """Why the list is empty and what to do: a first run should not just show nothing."""
    where = claude_dir() / "projects"
    if not where.is_dir():
        start = "press n" if dashboard else "run `tabmancer --demo` to see made-up ones"
        return (f"No Claude Code sessions yet: there is no {where}. Start one with `claude` in a project "
                f"folder, or {start}.")
    more = [f"--days {max(60, round(days * 2))} looks further back"]
    if not include_all:
        more.append("--all adds claude -p, desktop app and IDE sessions")
    if dashboard:
        return f"No sessions in the last {days:g} days. n starts fresh ones; tabmancer " + " and ".join(more) + "."
    return f"No sessions in the last {days:g} days in {where}. " + "; ".join(more) + "."


def _glob(folder: Path, pattern: str) -> list[Path]:
    """Path.glob that tolerates a missing folder (before Python 3.13 it raises)."""
    try:
        return list(folder.glob(pattern)) if folder.is_dir() else []
    except OSError:
        return []


# The fields tabmancer reads from a record, by the type they must have. A field of another type, a
# shape some later Claude Code may write, is dropped, so the record reads as if it were not there.
TEXT_FIELDS = ("type", "cwd", "gitBranch", "timestamp", "entrypoint", "aiTitle", "customTitle", "lastPrompt",
               "sessionId", "error", "requestId", "effort")
DICT_FIELDS = ("origin", "attachment")
BLOCK_TEXT_FIELDS = ("type", "text", "name", "id")
# Transcripts that could not be read at all, by what went wrong: the list goes on without them,
# the dashboard says so, and doctor reports it.
UNREADABLE: Counter[str] = Counter()


def sanitize(record: dict) -> dict:
    """The record with every field tabmancer reads of the type it expects, or gone."""
    for key in TEXT_FIELDS:
        if key in record and not isinstance(record[key], str):
            del record[key]
    for key in DICT_FIELDS:
        if key in record and not isinstance(record[key], dict):
            del record[key]
    message = record.get("message")
    if message is None:
        return record
    if not isinstance(message, dict):
        del record["message"]
        return record
    for key in ("model", "id", "stop_reason"):
        if message.get(key) is not None and not isinstance(message[key], str):
            del message[key]
    if "usage" in message and not isinstance(message["usage"], dict):
        del message["usage"]
    content = message.get("content")
    if isinstance(content, list):
        blocks = [block for block in content if isinstance(block, dict)]
        for block in blocks:
            for key in BLOCK_TEXT_FIELDS:
                if key in block and not isinstance(block[key], str):
                    del block[key]
            if "input" in block and not isinstance(block["input"], dict):
                del block["input"]
        message["content"] = blocks
    elif content is not None and not isinstance(content, str):
        del message["content"]
    return record


def unreadable(error: Exception) -> None:
    UNREADABLE[f"{type(error).__name__}: {error}"[:160]] += 1


def _parse_lines(chunk: bytes, drop_first: bool, drop_last: bool) -> list[dict]:
    lines = chunk.split(b"\n")
    if drop_first:
        lines = lines[1:]
    if drop_last:
        lines = lines[:-1]
    records = []
    for raw in lines:
        # Request blobs are the bulk of a transcript and never needed here.
        if not raw.strip() or raw.startswith(b'{"type":"api-request'):
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(sanitize(record))
    return records


def _rfind_record(f, size: int, needle: bytes) -> dict | None:
    """Last whole JSON line containing needle, reading the file backwards in blocks."""
    block = 4 * 1024 * 1024
    end = size
    carry = b""
    while end > 0:
        start = max(0, end - block)
        f.seek(start)
        data = f.read(end - start) + carry
        pos = data.rfind(needle)
        while pos != -1:
            line_start = data.rfind(b"\n", 0, pos) + 1
            line_end = data.find(b"\n", pos)
            if line_start == 0 and start > 0:
                break  # line begins in the previous block
            raw = data[line_start:] if line_end == -1 else data[line_start:line_end]
            try:
                record = json.loads(raw)
            except ValueError:
                pos = data.rfind(needle, 0, line_start)
                continue
            return sanitize(record) if isinstance(record, dict) else None
        first_nl = data.find(b"\n")
        carry = data[:first_nl] if first_nl != -1 and start > 0 else b""
        if start == 0:
            return None
        end = start
    return None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


# What Claude Code itself puts in a user record: <command-name>, <local-command-stdout>, <bash-input>,
# <system-reminder>, <task-notification>, <ide_opened_file>... a tag with a dash or an underscore in its
# name. A prompt of yours that begins with markup (<div class="card"> why is this not centred?) was
# taken for one, and a session with no other prompt was left out of the list.
CLAUDE_TAG = re.compile(r"<[a-z][a-z0-9]*[-_][a-z0-9_-]*[\s>/]")


def _is_real_prompt(record: dict) -> bool:
    if record.get("type") != "user" or record.get("isSidechain") or record.get("isMeta"):
        return False
    if record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                         for b in content):
        return False
    text = _text(content).strip()
    return bool(text) and not CLAUDE_TAG.match(text) and not text.startswith("[Request interrupted")


def _conversation_status(record: dict) -> Status:
    message = record.get("message") or {}
    content = message.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if record.get("type") == "assistant":
        if record.get("isApiErrorMessage"):
            return Status.ERROR
        if any(b.get("type") == "tool_use" for b in blocks):
            return Status.CUT_OFF
        stop = message.get("stop_reason")
        if stop in ("end_turn", "stop_sequence"):
            return Status.DONE
        if stop is None and blocks and blocks[-1].get("type") == "text":
            return Status.DONE
        return Status.CUT_OFF  # thinking only, max_tokens, or tool_use stop
    text = _text(content).lstrip()
    if "[Request interrupted by user" in text:
        return Status.STOPPED
    if any(b.get("type") == "tool_result" for b in blocks):
        return Status.CUT_OFF
    if record.get("isCompactSummary") or text.startswith(LOCAL_PREFIXES):
        return Status.DONE
    if text.startswith("<task-notification>"):
        return Status.CUT_OFF  # a background task reported back; Claude never got to it
    return Status.UNANSWERED


def _limit_resets(record: dict) -> datetime:
    """When the usage limit a reply ran into lifts, in local time.

    The reply says it: "You've hit your session limit · resets 4:10pm (Europe/Istanbul)", or
    "... weekly limit · resets Oct 6, 6am (...)". The time is the first such moment after the
    reply. If it cannot be read, five hours on, the length of a session limit's window.
    """
    try:
        stamp = datetime.fromisoformat(str(record.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        stamp = datetime.now().astimezone()
    match = RESETS.search(_text((record.get("message") or {}).get("content")))
    if match is None:
        return (stamp + timedelta(hours=5)).astimezone().replace(tzinfo=None)
    try:
        local = stamp.astimezone(ZoneInfo(match["zone"])) if match["zone"] else stamp.astimezone()
    except (ValueError, ZoneInfoNotFoundError):  # no time zone data on this machine: the clock's own
        local = stamp.astimezone()
    hour = int(match["hour"]) % 12 + (12 if match["half"].lower() == "pm" else 0)
    try:
        when = local.replace(hour=hour, minute=int(match["minute"] or 0), second=0, microsecond=0)
        if match["month"]:
            when = when.replace(month=MONTHS.index(match["month"][:3].title()) + 1, day=int(match["day"]))
            if when < local:
                when = when.replace(year=when.year + 1)
        elif when <= local:
            when += timedelta(days=1)
    except ValueError:
        return (stamp + timedelta(hours=5)).astimezone().replace(tzinfo=None)
    return when.astimezone().replace(tzinfo=None)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def read_session(path: Path, strict: bool = False) -> Session | None:
    """Summarise one transcript. None if it holds no real prompt (nothing to resume), or (unless
    strict, when the error is raised) when it cannot be read."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size == 0:
        return None
    session = Session(session_id=path.stem, path=path, size=stat.st_size,
                      last_active=datetime.fromtimestamp(stat.st_mtime))
    try:
        with path.open("rb") as f:
            _read_head(f, stat.st_size, session)
            if not session.first_prompt:
                return None
            _read_tail(f, stat.st_size, session)
    except OSError:
        if strict:
            raise
        return None
    except Exception as error:  # noqa: BLE001 - a shape this version cannot read: this transcript goes, never the list
        unreadable(error)
        return None
    return session


def _peer_text(record: dict) -> str:
    """What another session said, when the record is its message coming in (a team member's); ''
    otherwise, and for a subagent of the session handing its report back."""
    origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
    attached = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
    if record.get("type") == "attachment" and attached.get("type") == "queued_command":
        # The lead's message reaching a member that was busy (starting up, say): taken in mid-turn.
        origin = attached.get("origin") if isinstance(attached.get("origin"), dict) else {}
    elif record.get("type") != "user":
        return ""
    if origin.get("kind") != "peer" or origin.get("senderTaskId"):
        return ""
    return str(origin.get("body") or _text((record.get("message") or {}).get("content")))


def _read_head(f, size: int, session: Session) -> None:
    span = HEAD_BYTES
    while True:
        f.seek(0)
        chunk = f.read(min(span, size))
        peer = ""  # a team member starts with no message of its own: the lead's first one is its first prompt
        for record in _parse_lines(chunk, drop_first=False, drop_last=len(chunk) < size):
            if record.get("isSidechain"):
                continue
            if not session.started and record.get("timestamp"):
                try:
                    stamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
                    session.started = stamp.astimezone().replace(tzinfo=None)
                except ValueError:
                    pass
            if record.get("entrypoint") and not session.entrypoint:
                session.entrypoint = record["entrypoint"]
            if record.get("cwd") and not session.cwd:
                session.cwd = record["cwd"]
            if _is_real_prompt(record):
                session.first_prompt = _shorten(_text(record["message"]["content"]), 300)
                return
            peer = peer or _peer_text(record)
        if len(chunk) >= size or span >= MAX_HEAD_BYTES:
            # Never written to, only told by the lead: it is still a session, to see and to resume.
            # Taken for nothing to resume, such a member was missing from the list for good.
            session.first_prompt = _shorten(peer, 300) if peer else ""
            return
        span *= 4


def _read_tail(f, size: int, session: Session) -> None:
    span = TAIL_BYTES
    while True:
        start = max(0, size - span)
        f.seek(start)
        records = _parse_lines(f.read(), drop_first=start > 0, drop_last=False)
        if start == 0 or span >= MAX_TAIL_BYTES or any(
                r.get("type") in ("user", "assistant") and not r.get("isSidechain") for r in records):
            break
        span *= 4  # a few huge trailing records pushed the conversation out of view

    custom_title = ai_title = ""
    last_conversation = last_local = None
    signed_in = False  # a /login since the last of the conversation
    last_activity = last_cost = -1
    cost_state = last_prompt_record = None
    files: dict[str, None] = {}
    for i, record in enumerate(records):
        kind = record.get("type")
        if kind == "custom-title":
            custom_title = record.get("customTitle") or custom_title
        elif kind == "ai-title":
            ai_title = record.get("aiTitle") or ai_title
        elif kind == "last-prompt":
            last_prompt_record = record
        elif kind == "cost-state":
            last_cost, cost_state = i, record
        elif kind in ("mode", "permission-mode"):
            last_activity = i
        elif kind in ("user", "assistant") and not record.get("isSidechain"):
            last_activity = i
            session.cwd = record.get("cwd") or session.cwd
            session.git_branch = record.get("gitBranch") or session.git_branch
            if record.get("isMeta") or (record.get("isVisibleInTranscriptOnly")
                                        and not record.get("isCompactSummary")):
                continue
            said = _text((record.get("message") or {}).get("content")).lstrip() if kind == "user" else ""
            if said.startswith(LOCAL_PREFIXES):  # /login, /usage, a ! command: not how the session stands
                last_local = record
                signed_in = signed_in or "Login successful" in said
                continue
            last_conversation, signed_in = record, False
            content = (record.get("message") or {}).get("content")
            if kind == "assistant":
                usage = (record.get("message") or {}).get("usage")
                if isinstance(usage, dict):
                    # What the statusline calls the context: everything sent in, output aside.
                    sent = sum(v for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                               if isinstance(v := usage.get(k), int))
                    if sent:
                        session.context = sent
                        session.model = str(record["message"].get("model") or session.model)
                        session.effort = str(record.get("effort") or session.effort)
                reply = _text(content).replace("**", "").replace("`", "").strip()
                if reply:
                    session.last_reply = _shorten(reply, 500)
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" \
                            and block.get("name") in EDIT_TOOLS:
                        target = (block.get("input") or {}).get("file_path") \
                            or (block.get("input") or {}).get("notebook_path")
                        if target:
                            files.pop(target, None)
                            files[target] = None
            elif _is_real_prompt(record):
                session.last_prompt = _shorten(_text(content), 300)

    # Fields that may sit further back than the tail window. Custom titles are
    # re-written every turn, so a session without one in the tail has none.
    if not custom_title and not ai_title:
        record = _rfind_record(f, size, b'"type":"ai-title"')
        ai_title = (record or {}).get("aiTitle") or ""
    if cost_state is None:
        cost_state = _rfind_record(f, size, b'"type":"cost-state"')
    if not session.last_prompt and last_prompt_record:
        session.last_prompt = _shorten(last_prompt_record.get("lastPrompt") or "", 300)

    session.title = _shorten(custom_title or ai_title or session.first_prompt, 80)
    session.files = _relative(list(reversed(files)), session.cwd)[:8]
    last_conversation = last_conversation or last_local  # nothing but local commands: they are all there is
    session.status = _conversation_status(last_conversation) if last_conversation else Status.UNKNOWN
    if session.status is Status.ERROR and last_conversation.get("error") == "rate_limit":
        session.status, session.resets = Status.LIMIT, _limit_resets(last_conversation)
        session.signed_in_again = signed_in
    elif session.status is Status.ERROR and last_conversation.get("error") == "server_error":
        session.status = Status.DROPPED  # "Can't reach the API server", "Connection dropped" and the like
    session.exit = Exit.CLEAN if last_cost > last_activity else Exit.KILLED
    if cost_state:
        session.lines_added = cost_state.get("totalLinesAdded")
        session.lines_removed = cost_state.get("totalLinesRemoved")
        session.cost_usd = cost_state.get("totalCostUSD")


def _relative(paths: list[str], cwd: str) -> list[str]:
    """Paths inside the project folder, relative to it. Scratch files elsewhere are noise."""
    out = []
    for p in paths:
        try:
            out.append(str(Path(p).relative_to(cwd)))
        except ValueError:
            continue
    return out


# --- live sessions ----------------------------------------------------------------

def _agents_json(include_finished: bool = False) -> list[dict] | None:
    """Open sessions from `claude agents --json`, or None if that is unavailable.

    include_finished adds background sessions whose process has ended (`--all`).
    """
    claude = shutil.which("claude")
    if not claude:
        return None
    try:
        result = subprocess.run(
            [claude, "agents", "--json", *(["--all"] if include_finished else [])],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [entry for entry in data if isinstance(entry, dict) and entry.get("sessionId")]


def _windows_process_start(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # 259 = STILL_ACTIVE
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def _alive(info: dict) -> bool:
    try:
        pid = int(info["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if sys.platform == "win32":
        started = _windows_process_start(pid)
        expected = info.get("procStart")
        return started is not None and (not expected or str(expected) == str(started))
    try:
        os.kill(pid, 0)  # signal 0 only probes on POSIX (on Windows it would kill)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _registry_sessions() -> list[dict]:
    """Fallback: Claude Code's own pid files in ~/.claude/sessions (internal format)."""
    found = []
    for path in _glob(claude_dir() / "sessions", "*.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("sessionId") and _alive(info):
            found.append(info)
    return found


def live_sessions() -> dict[str, dict]:
    """session id -> record, for every session a Claude process has open right now.

    Prefers the CLI's own `claude agents --json`, which also lists background
    sessions; falls back to the internal pid files if the CLI is missing or too old.
    """
    entries = _agents_json()
    if entries is None:
        entries = _registry_sessions()
    return {entry["sessionId"]: entry for entry in entries}


def mark_live(session: Session, info: dict | None) -> None:
    """Overlay what a running process says about the session onto its transcript view."""
    if not info:
        return
    session.status = Status.BACKGROUND if info.get("kind") == "background" else Status.RUNNING
    session.exit = Exit.RUNNING
    # A background job says two things: how the job stands (working, blocked) and how its turn does
    # (busy, idle, waiting). A working job whose turn is over is idle, not at work; one stopped at a
    # permission prompt needs your OK, which only attaching to it gives (a session in a tab asks there).
    state, turn = str(info.get("state") or ""), str(info.get("status") or "")
    if "permission" in str(info.get("waitingFor") or "").lower() and (state == "blocked" or turn == "waiting"):
        session.live_state = "permission"
    elif state in ("", "working") and turn:
        session.live_state = turn
    else:
        session.live_state = state or turn


def transcript_path(session_id: str) -> Path | None:
    return next(iter(_glob(claude_dir() / "projects", f"*/{session_id}.jsonl")), None)


# --- discovery --------------------------------------------------------------------

def _to_cache(session: Session | None) -> dict | None:
    if session is None:
        return None
    data = asdict(session)
    data.pop("live_state")
    return dict(data, path=str(session.path), status=session.status.value, exit=session.exit.value,
                started=session.started.isoformat() if session.started else None,
                last_active=session.last_active.isoformat(),
                resets=session.resets.isoformat() if session.resets else None)


def _from_cache(data: dict) -> Session:
    return Session(**dict(
        data, path=Path(data["path"]), status=Status(data["status"]), exit=Exit(data["exit"]),
        started=datetime.fromisoformat(data["started"]) if data["started"] else None,
        last_active=datetime.fromisoformat(data["last_active"]),
        resets=datetime.fromisoformat(data["resets"]) if data.get("resets") else None))


def _read_cached(paths: list[Path], reader=None) -> list[Session]:
    """reader (read_session by default) for many files, skipping those unchanged since last time.

    The cache maps path -> size, mtime and the parsed session. Entries for
    transcripts that no longer exist are dropped, so it never outgrows ~/.claude.
    """
    stamp = f"{CACHE_FORMAT}:{__version__}"  # a new parser version starts afresh
    cached = read_json(CACHE_FILE)
    entries = cached.get("entries", {}) if isinstance(cached, dict) and cached.get("stamp") == stamp else {}
    fresh, sessions = {}, []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        hit = entries.get(str(path)) or {}
        session, stored, reused = None, None, False
        if hit.get("size") == stat.st_size and hit.get("mtime") == stat.st_mtime_ns:
            stored = hit.get("session")
            try:
                session = _from_cache(stored) if stored else None
                reused = True
            except (KeyError, TypeError, ValueError):
                pass  # an entry from an older layout: parse again
        if not reused:
            try:
                session = reader(path) if reader else read_session(path, strict=True)
            except OSError:
                # Held by another program a moment (a backup, an antivirus): not cached as nothing to
                # resume, which hid a finished session until its file changed, maybe for good.
                continue
            except Exception as error:  # noqa: BLE001 - another CLI's reader, say: this file goes, never the list
                unreadable(error)
                session = None
            stored = _to_cache(session)
        fresh[str(path)] = {"size": stat.st_size, "mtime": stat.st_mtime_ns, "session": stored}
        if session is not None:
            sessions.append(session)
    # Keep what a wider --days run parsed, as long as the transcript is still there.
    fresh.update({key: hit for key, hit in entries.items() if key not in fresh and Path(key).exists()})
    try:
        write_json(CACHE_FILE, {"stamp": stamp, "entries": fresh}, indent=None)
    except OSError:
        pass  # a read-only config folder only costs speed
    return sessions


def find_sessions(days: float = 14, include_all: bool = False, quick: bool = False) -> list[Session]:
    """Recent sessions across every project, newest first.

    include_all keeps non-terminal sessions too (claude -p, desktop app, IDE).
    quick takes open sessions from the pid files instead of waiting about a second
    for `claude agents --json`; refine with live_sessions() afterwards.
    """
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    paths = []
    for path in _glob(claude_dir() / "projects", "*/*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                paths.append(path)
        except OSError:
            continue
    from . import clis

    on = clis.enabled()
    sessions: list[Session] = []
    live: dict[str, dict] = {}
    if "claude" in on:
        if quick:
            sessions = _read_cached(paths)
            live = {entry["sessionId"]: entry for entry in _registry_sessions()}
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                live_query = pool.submit(live_sessions)  # spawns the CLI; overlap it with parsing
                sessions = _read_cached(paths)
                live = live_query.result()
    sessions += other_sessions(days, [name for name in on if name != "claude"])
    if not include_all:
        sessions = [s for s in sessions if s.entrypoint in ("", "cli")]
    for session in sessions:
        mark_live(session, live.get(session.session_id))
    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions


def other_sessions(days: float, names: list[str]) -> list[Session]:
    """Sessions of the other CLIs (codex, agy), whichever of them are on."""
    found: list[Session] = []
    if "codex" in names:
        from . import codex

        found += codex.find(days)
    if "agy" in names:
        from . import agy

        found += agy.find(days)
    return found
