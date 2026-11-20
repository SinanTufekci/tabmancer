"""Local message bus and live presence for mixed-CLI teams (Claude Code + Antigravity).

Pure Claude Code teams use Claude's own SendMessage/ListAgents cross-session messaging.
When a team includes any Antigravity (agy) member, Claude and agy cannot talk through
Claude's private socket, so every member in that team uses this bus instead:

- `tabmancer team send <team> <to> <message>` appends to `<bus>/<team>/messages.jsonl`.
- `tabmancer team listen <team> <member>` blocks until an unread message arrives for that
  member (or returns immediately if one is waiting), prints it and exits; only once it is
  printed is it marked read in `receipts.jsonl`, so a listener killed before that loses
  nothing (the next one delivers it again). Both Claude Code (`run_in_background`) and agy
  (`WaitMsBeforeAsync`) wake up automatically when a background command finishes, so
  neither polls in a loop.
- One listener per member: each claims `presence/<member>.listen`; a newer one takes the
  claim over and the older one leaves without marking anything (it says so, and that no
  other is to be started). A listener ends when its agent does: the pid `team run` hands
  down, else the claude/agy process up its parent chain (its own parent is only the shell).
- `tabmancer team run <team> <member> -- agy ...` sets the terminal tab title, records live
  process presence in `presence/<member>.json`, binds the agy conversation id, and cleans up
  when agy exits.
- The log is bounded: past ROTATE_BYTES, messages.jsonl and receipts.jsonl move to .1 (.1 to
  .2 ...) together, under a lock, and the oldest pair beyond KEEP_ROTATED goes. Reads take
  every file kept, so a rotation neither drops a waiting message nor brings back a read one.

The bus lives under the system temp directory (`<temp>/tabmancer-bus`, overridable via
TABMANCER_BUS_DIR) so agy's default terminal sandbox can read and write it without leaving
untracked files in the project repository.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Only the standard library: this file is also copied into the bus folder (install) and run there
# by agy members, whose sandbox cannot read the tabmancer package.

BUS_ENV = "TABMANCER_BUS_DIR"
YOU = "you"  # a person at a terminal (teams.YOU)
TEAM_FILE = "team.json"  # what a copy in the bus folder knows of its team: when it was made, its members
CLI_FILE = "teambus_cli.py"  # this file, copied into a team's bus folder
AGENT_PID_ENV = "TABMANCER_AGENT_PID"  # set by `team run`: the process a member's listener lives and dies with
BUS_FOLDER = "tabmancer-bus"
SKEW = timedelta(minutes=2)
DEFAULT_LISTEN_TIMEOUT = 3300.0  # 55 minutes: also keeps Claude's 1h prompt cache warm
POLL_SECONDS = 0.25
ROTATE_BYTES = 1_000_000  # messages.jsonl past this moves aside
KEEP_ROTATED = 3  # rotated files kept: messages.1.jsonl (newest) to messages.3.jsonl
ROTATE_LOCK = "rotate.lock"
STALE_LOCK_SECONDS = 30.0  # a rotation lock older than this was left by a process that died
AGENTS = {"claude", "agy", "node", "antigravity", "codex"}  # executables an agent runs as (node: npm's claude)
CHAIN_DEPTH = 12  # parents looked at, at most
AGENT_MARKERS = ("AI_AGENT", "CLAUDECODE")  # set in the environment of what Claude Code runs
PRINT_TIMEOUT_EXIT = 3  # agy 1.5+: an expired --print-timeout, not a failure of the model
INTERRUPTED = {130, -2, 0xC000013A, -1073741510}  # Ctrl+C on POSIX and Windows


class Superseded(Exception):
    """A newer listener for the same member took over: this one leaves without marking anything."""


def bus_root() -> Path:
    """Root folder for team buses: TABMANCER_BUS_DIR, else <temp>/tabmancer-bus."""
    override = os.environ.get(BUS_ENV)
    return Path(override) if override else Path(tempfile.gettempdir()) / BUS_FOLDER


def team_dir(team: str) -> Path:
    return bus_root() / team


def _messages_path(team: str) -> Path:
    return team_dir(team) / "messages.jsonl"


def _receipts_path(team: str) -> Path:
    return team_dir(team) / "receipts.jsonl"


def _rotated(path: Path, n: int) -> Path:
    """messages.jsonl -> messages.<n>.jsonl."""
    return path.with_name(f"{path.stem}.{n}{path.suffix}")


def _log_files(path: Path) -> list[Path]:
    """A log and the files rotated from it, oldest first."""
    return [*(_rotated(path, n) for n in range(KEEP_ROTATED, 0, -1)), path]


def _presence_dir(team: str) -> Path:
    return team_dir(team) / "presence"


def _presence_path(team: str, member: str) -> Path:
    return _presence_dir(team) / f"{member}.json"


def _claim_path(team: str, member: str) -> Path:
    return _presence_dir(team) / f"{member}.listen"


def _bindings_path(team: str) -> Path:
    return team_dir(team) / "bindings.json"


def roles_dir(team: str) -> Path:
    return team_dir(team) / "roles"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_time(stamp: object) -> datetime | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def _local_naive(stamp: object) -> datetime:
    moment = _parse_time(stamp)
    if moment is None:
        return datetime.now()
    return moment.astimezone().replace(tzinfo=None)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return out
    return out


def _read_log(path: Path) -> list[dict]:
    """Every record of a log, the rotated files' first, oldest first."""
    return [row for part in _log_files(path) for row in _read_jsonl(part)]


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def normalize_member(team: str, name: str) -> str:
    """Strip '<team>-' prefix if a caller addresses a member by its session name."""
    cleaned = name.strip()
    prefix = f"{team}-"
    if cleaned.lower().startswith(prefix.lower()) and len(cleaned) > len(prefix):
        return cleaned[len(prefix):]
    return cleaned


def write_role_file(team: str, session_name: str, text: str) -> Path | None:
    """Write a member's role inside the bus folder so sandboxed agy sessions can read it."""
    path = roles_dir(team) / f"{session_name}.md"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


# --- processes: whose listener is it --------------------------------------------------------

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
    """Whether the process in info ({pid, procStart}) still runs: on Windows, the same one (a pid
    is reused after a restart, its start time is not)."""
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


def _windows_parent(pid: int) -> tuple[int, str] | None:
    """(parent pid, executable name) from a Toolhelp snapshot, or None."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return None
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID), entry.szExeFile
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return None


def _parent(pid: int) -> tuple[int, str] | None:
    """(parent pid, executable name) of a process, or None when it cannot be read."""
    try:
        if sys.platform == "win32":
            return _windows_parent(pid)
        stat = Path(f"/proc/{pid}/stat")
        if stat.is_file():  # Linux: "pid (comm) state ppid ...", comm may hold spaces and parentheses
            text = stat.read_text(encoding="utf-8", errors="replace")
            name, rest = text[text.index("(") + 1:text.rindex(")")], text[text.rindex(")") + 2:].split()
            return int(rest[1]), name
        result = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                                timeout=5, check=False)
        ppid, _, name = result.stdout.strip().partition(" ")
        return (int(ppid), name.strip()) if ppid.isdigit() else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _is_agent(name: str) -> bool:
    base = Path(name.strip()).name.lower()
    return base.removesuffix(".exe") in AGENTS


def _find_agent(pid: int) -> tuple[int | None, int | None]:
    """(the first claude/agy process up pid's parent chain, pid's own parent), either None."""
    first_parent, seen, current = None, {pid}, pid
    for _ in range(CHAIN_DEPTH):
        found = _parent(current)
        if found is None:
            break
        ppid, _name = found
        if first_parent is None:
            first_parent = ppid or None
        if ppid <= 0 or ppid in seen:
            break
        up = _parent(ppid)
        if up is not None and _is_agent(up[1]):
            return ppid, first_parent
        seen.add(ppid)
        current = ppid
    return None, first_parent


def agent_pid(pid: int | None = None) -> int | None:
    """The process a listener lives and dies with: the pid `team run` handed down, else the first
    claude/agy process up the parent chain, else the parent (only the shell between them)."""
    named = os.environ.get(AGENT_PID_ENV, "")
    if named.isdigit():
        return int(named)
    found, parent = _find_agent(os.getpid() if pid is None else pid)
    return found or parent


def agent_caller() -> bool:
    """Whether a coding agent (not a person in a terminal) runs this command."""
    if any(os.environ.get(marker) for marker in AGENT_MARKERS):
        return True
    return _find_agent(os.getpid())[0] is not None


# --- messaging -------------------------------------------------------------------------

def send(team: str, sender: str, receiver: str, text: str, summary: str = "") -> dict:
    """Append a message to the team bus and return its record."""
    record = {
        "id": uuid.uuid4().hex[:12],
        "at": _now_iso(),
        "from": normalize_member(team, sender),
        "to": normalize_member(team, receiver),
        "summary": " ".join(summary.split()) if summary else "",
        "text": text,
    }
    _append_jsonl(_messages_path(team), record)
    _rotate_if_big(team)
    return record


def _take_lock(path: Path) -> bool:
    """Create the lock file, or take over one left longer than STALE_LOCK_SECONDS ago."""
    for _ in range(2):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime < STALE_LOCK_SECONDS:
                    return False
                path.unlink()
            except OSError:
                return False
        except OSError:
            return False
    return False


def _rotate_if_big(team: str) -> None:
    """Move the log aside once it is past ROTATE_BYTES: receipts with their messages, under a lock
    only one process holds. A rotation that cannot be done now (the lock held, a file open
    elsewhere on Windows) is left to the next send."""
    messages_path = _messages_path(team)
    try:
        if messages_path.stat().st_size <= ROTATE_BYTES:
            return
    except OSError:
        return
    lock = team_dir(team) / ROTATE_LOCK
    if not _take_lock(lock):
        return
    try:
        if messages_path.stat().st_size <= ROTATE_BYTES:
            return  # another process rotated it in the meantime
        for path in (_receipts_path(team), messages_path):
            _rotated(path, KEEP_ROTATED).unlink(missing_ok=True)
            for n in range(KEEP_ROTATED, 0, -1):
                source = path if n == 1 else _rotated(path, n - 1)
                if source.exists():
                    os.replace(source, _rotated(path, n))
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def _read_receipts(team: str) -> dict[str, set[str]]:
    """member -> set of message ids delivered to it."""
    seen: dict[str, set[str]] = {}
    for row in _read_log(_receipts_path(team)):
        mid, by = row.get("id"), row.get("by")
        if isinstance(mid, str) and isinstance(by, str):
            seen.setdefault(by, set()).add(mid)
    return seen


def mark_delivered(team: str, member: str, message_ids: list[str]) -> None:
    if not message_ids:
        return
    who = normalize_member(team, member)
    now = _now_iso()
    path = _receipts_path(team)
    for mid in message_ids:
        _append_jsonl(path, {"id": mid, "by": who, "at": now})


ack = mark_delivered  # what a listener calls once the messages are out


def messages(team: str, since: datetime | None = None) -> list[dict]:
    """Every bus message for this team still kept (optionally since a team generation's creation
    time), each annotated with `delivered: bool` and `when: datetime` (local naive)."""
    receipts = _read_receipts(team)
    since_aware = (since if since.tzinfo else since.astimezone()) - SKEW if since is not None else None
    out: list[dict] = []
    for row in _read_log(_messages_path(team)):
        mid = str(row.get("id") or "")
        sender = str(row.get("from") or "?")
        receiver = str(row.get("to") or "?")
        text = str(row.get("text") or "")
        if not mid or not text:
            continue
        moment = _parse_time(row.get("at"))
        if since_aware is not None and moment is not None and moment < since_aware:
            continue
        delivered = mid in receipts.get(receiver, set())
        out.append({
            "id": mid,
            "at": _local_naive(row.get("at")),
            "from": sender,
            "to": receiver,
            "summary": str(row.get("summary") or ""),
            "text": text,
            "delivered": delivered,
        })
    return out


def unread(team: str, member: str, mark_read: bool = False, since: datetime | None = None) -> list[dict]:
    """Unread messages addressed to `member` (or broadcast to '*'/'all')."""
    who = normalize_member(team, member)
    receipts = _read_receipts(team).get(who, set())
    since_aware = (since if since.tzinfo else since.astimezone()) - SKEW if since is not None else None
    pending: list[dict] = []
    for row in _read_log(_messages_path(team)):
        mid = str(row.get("id") or "")
        if not mid or mid in receipts:
            continue
        to = str(row.get("to") or "")
        sender = str(row.get("from") or "")
        if to != who and not (to in ("*", "all") and sender != who):
            continue
        moment = _parse_time(row.get("at"))
        if since_aware is not None and moment is not None and moment < since_aware:
            continue
        pending.append({
            "id": mid,
            "at": _local_naive(row.get("at")),
            "from": sender,
            "to": who,
            "summary": str(row.get("summary") or ""),
            "text": str(row.get("text") or ""),
        })
    if mark_read and pending:
        mark_delivered(team, who, [m["id"] for m in pending])
    return pending


def _pid_info(pid: int) -> dict:
    info: dict[str, object] = {"pid": pid}
    if sys.platform == "win32":
        started = _windows_process_start(pid)
        if started is not None:
            info["procStart"] = str(started)
    return info


# --- one listener per member ----------------------------------------------------------------

def _write_claim(team: str, member: str, claim: dict) -> None:
    _atomic_write_json(_claim_path(team, member), claim)


def _read_claim(team: str, member: str) -> dict:
    return _read_json_file(_claim_path(team, member))


def listener(team: str, member: str) -> dict | None:
    """The live listener's claim for a member ({pid, procStart, token, since}), or None."""
    claim = _read_claim(team, normalize_member(team, member))
    return claim if claim and _alive(claim) else None


def listen(team: str, member: str, timeout: float = DEFAULT_LISTEN_TIMEOUT,
           poll: float = POLL_SECONDS, since: datetime | None = None,
           watch_pid: int | None = None, ack: bool = True) -> list[dict]:
    """Wait until unread message(s) arrive for `member` and return them: marked delivered with
    ack, else left for the caller to mark once they are out (`ack()`).

    Returns at once if unread messages are already waiting, and [] if `timeout` expires or
    `watch_pid` is no longer alive. Claims the member's listener file first; when a newer
    listener takes the claim over, raises Superseded without marking anything or touching the
    newer one's claim.
    """
    who = normalize_member(team, member)
    token = uuid.uuid4().hex
    claim = {**_pid_info(os.getpid()), "token": token, "since": _now_iso()}
    _write_claim(team, who, claim)

    def mine() -> bool:
        current = _read_claim(team, who)
        if not current:  # removed from outside (a cleared presence folder): claimed again
            with contextlib.suppress(OSError):
                _write_claim(team, who, claim)
            return True
        return current.get("token") == token

    watch_info = _pid_info(watch_pid) if watch_pid else None
    msg_file = _messages_path(team)
    last_stat: tuple[int, int] | None = (-1, -1)
    deadline = time.monotonic() + max(0.0, timeout)
    checks = 0
    try:
        while True:
            if not mine():
                raise Superseded(who)
            try:
                st = msg_file.stat()
                sig: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
            except OSError:
                sig = None
            if sig != last_stat:
                last_stat = sig
                ready = unread(team, who, since=since)
                if ready:
                    if not mine():
                        raise Superseded(who)
                    if ack:
                        mark_delivered(team, who, [m["id"] for m in ready])
                    return ready
            checks += 1
            if watch_info is not None and checks % 4 == 0 and not _alive(watch_info):
                return []
            if time.monotonic() >= deadline:
                return []
            time.sleep(min(poll, max(0.01, deadline - time.monotonic())))
    finally:
        if _read_claim(team, who).get("token") == token:
            with contextlib.suppress(OSError):
                _claim_path(team, who).unlink()


# --- presence and session bindings -----------------------------------------------------

def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_presence(team: str, member: str, cli: str = "agy", session_id: str = "",
                   pid: int | None = None, state: str = "working") -> dict:
    who = normalize_member(team, member)
    existing = _read_json_file(_presence_path(team, who))
    proc = _pid_info(os.getpid() if pid is None else pid)
    data = {
        **existing,
        "team": team,
        "member": who,
        "cli": cli,
        "pid": proc["pid"],
        "procStart": proc.get("procStart"),
        "session_id": session_id or str(existing.get("session_id") or ""),
        "state": state,
        "started": str(existing.get("started") or _now_iso()),
        "updated": _now_iso(),
    }
    try:
        _atomic_write_json(_presence_path(team, who), data)
    except OSError:
        pass
    return data


def update_presence(team: str, member: str, **fields: object) -> None:
    who = normalize_member(team, member)
    path = _presence_path(team, who)
    existing = _read_json_file(path)
    if not existing and not fields.get("pid"):
        existing = {"team": team, "member": who}
    existing.update(fields)
    existing["updated"] = _now_iso()
    try:
        _atomic_write_json(path, existing)
    except OSError:
        pass


def clear_presence(team: str, member: str) -> None:
    who = normalize_member(team, member)
    try:
        _presence_path(team, who).unlink(missing_ok=True)
    except OSError:
        pass


def read_presence(team: str) -> dict[str, dict]:
    """member -> live presence dict, only for members whose runner or listener process is alive.
    The listener is its claim file's (a listener_pid in the presence file, from before claims,
    counts too)."""
    folder = _presence_dir(team)
    if not folder.is_dir():
        return {}
    names = {path.stem for path in folder.glob("*.json")} | {path.stem for path in folder.glob("*.listen")}
    live: dict[str, dict] = {}
    for name in sorted(names):
        info = _read_json_file(folder / f"{name}.json")
        who = str(info.get("member") or name)
        runner_alive = bool(info.get("pid")) and _alive(info)
        listener_alive = listener(team, name) is not None
        if not listener_alive and info.get("listener_pid"):
            linfo = {"pid": info["listener_pid"]}
            if info.get("listener_start"):
                linfo["procStart"] = info["listener_start"]
            listener_alive = _alive(linfo)
        if not runner_alive and not listener_alive:
            continue
        live[who] = {
            **info,
            "member": who,
            "runner_alive": runner_alive,
            "listening": listener_alive,
        }
    return live


def is_alive(team: str, member: str) -> bool:
    info = read_presence(team).get(normalize_member(team, member))
    return bool(info and info.get("runner_alive"))


def read_bindings(team: str) -> dict[str, dict]:
    """member -> {session_id, cli, created, updated} for this team."""
    data = _read_json_file(_bindings_path(team))
    raw = data.get("members") if isinstance(data.get("members"), dict) else {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def save_binding(team: str, member: str, session_id: str, cli: str = "agy", created: str = "") -> None:
    """Remember which conversation/session id belongs to this team member."""
    if not session_id:
        return
    who = normalize_member(team, member)
    path = _bindings_path(team)
    data = _read_json_file(path)
    members = data.get("members") if isinstance(data.get("members"), dict) else {}
    members[who] = {
        "session_id": session_id,
        "cli": cli,
        "created": created or _now_iso(),
        "updated": _now_iso(),
    }
    try:
        _atomic_write_json(path, {"members": members})
    except OSError:
        pass


# --- transparent tab runner for agy team members ---------------------------------------

def _set_tab_title(title: str) -> None:
    """Set the terminal tab/console title so Windows Terminal focus/close can find it."""
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(Exception):
            ctypes.windll.kernel32.SetConsoleTitleW(title)
    try:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
    except OSError:
        pass


def _after_agy_failed(code: int) -> None:
    """agy ended with a failure: read its quota afresh, so the next pick follows a model that ran
    out (its interactive screen is the tab's, so its words cannot be read here). An expired
    --print-timeout and Ctrl+C are no failure of the model."""
    if code in (0, PRINT_TIMEOUT_EXIT) or code in INTERRUPTED:
        return
    with contextlib.suppress(Exception):  # a quota read is a courtesy: the member's exit code stands
        from . import agy

        agy.refresh_quota(timeout=25)
        model, why = agy.pick_model()
        print(f"agy ended (code {code}); its quota was read again: next pick {model or 'none'}"
              f"{f' ({why})' if why else ''}.", file=sys.stderr)


def run_member(team: str, member: str, argv: list[str], cli: str = "agy", created: str = "") -> int:
    """Run a team member CLI inside its tab while tracking its live process presence."""
    who = normalize_member(team, member)
    session_name = f"{team}-{who}"
    _set_tab_title(session_name)

    session_id = ""
    if "--conversation" in argv:
        idx = argv.index("--conversation")
        if idx + 1 < len(argv):
            session_id = argv[idx + 1]
            save_binding(team, who, session_id, cli=cli, created=created)

    env = dict(os.environ)
    env["TABMANCER_TEAM"] = team
    env["TABMANCER_MEMBER"] = who
    env[BUS_ENV] = str(bus_root())
    env[AGENT_PID_ENV] = str(os.getpid())  # this runner lives exactly as long as the member's CLI

    write_presence(team, who, cli=cli, session_id=session_id, pid=os.getpid(), state="working")
    try:
        proc = subprocess.Popen(argv, env=env)
        write_presence(team, who, cli=cli, session_id=session_id, pid=proc.pid, state="working")
        code = proc.wait()
    except OSError as error:
        print(f"Could not start {argv[0] if argv else cli}: {error}", file=sys.stderr)
        return 127
    finally:
        clear_presence(team, who)
    if cli == "agy":
        _after_agy_failed(code)
    return code


# --- the commands: `tabmancer team send/listen/who`, and this file run on its own -----------

def send_command(team: str, to: str, text: str, sender: str = "", summary: str = "",
                 members: set[str] | None = None) -> int:
    """`send`: one message on the bus. The sender is --from, else the member this runs for
    (TABMANCER_MEMBER); with neither, a person at a terminal is you, an agent is told to say."""
    sender = sender or os.environ.get("TABMANCER_MEMBER", "")
    if not sender:
        if agent_caller():  # a member without its name: never sent as you
            print(f"Say who sends it: add --from <your member name> (`who {team}` lists them).", file=sys.stderr)
            return 1
        sender = YOU
    text = text.strip()
    if not text:
        print("Message cannot be empty.", file=sys.stderr)
        return 1
    to = normalize_member(team, to)
    if members and to not in ("*", "all") and to not in members:
        print(f"No member {to} on team {team}. Members: {', '.join(sorted(members))}.", file=sys.stderr)
        return 1
    record = send(team, sender, to, text, summary=summary)
    print(f"Sent to {record['to']} (id {record['id']}).")
    return 0


def listen_command(team: str, member: str, timeout: float = DEFAULT_LISTEN_TIMEOUT,
                   since: datetime | None = None) -> int:
    """`listen`: wait for this member's messages, print them, and only then mark them read."""
    who = normalize_member(team, member)
    try:
        # It lives as long as its agent: the shell between them is not the agent.
        arrived = listen(team, who, timeout=timeout, since=since, watch_pid=agent_pid(), ack=False)
    except Superseded:
        print(f"[superseded] A newer listen for {who} on team {team} is waiting for {who}'s messages; "
              "it delivers them. Nothing to do here: do not start another.")
        return 0
    if not arrived:
        print(f"[timeout] No messages for {who} in team {team}.")
        return 0
    for msg in arrived:
        stamp = msg["at"].strftime("%H:%M:%S")
        print(f"[{stamp} from {msg['from']}]: {msg['text']}")
    sys.stdout.flush()
    ack(team, who, [msg["id"] for msg in arrived])  # read only once it is out
    return 0


def who_command(team: str, members: list[str]) -> int:
    """`who` as a copy in the bus folder sees it: each member, open or not, listening or not."""
    live = read_presence(team)
    for name in members or sorted(live):
        info = live.get(name)
        state = "listening" if info and info.get("listening") else "open" if info else "closed"
        print(f"{name} ({team}-{name}) · {info.get('cli', '?') if info else '?'} · {state}")
    return 0


def _team_file(team: str) -> Path:
    return team_dir(team) / TEAM_FILE


def _read_team_file(team: str) -> tuple[datetime | None, list[str]]:
    data = _read_json_file(_team_file(team))
    members = data.get("members") if isinstance(data.get("members"), list) else []
    return _parse_time(data.get("created")), [str(m) for m in members]


def _plain_path(path: str) -> str:
    """A path as a shell takes it without quotes: Windows' short form when it holds a space
    (quotes would make PowerShell take the path for a string, not a command)."""
    if " " not in path:
        return path
    if sys.platform == "win32":
        import ctypes

        with contextlib.suppress(Exception):
            buffer = ctypes.create_unicode_buffer(1024)
            if ctypes.windll.kernel32.GetShortPathNameW(path, buffer, 1024) and " " not in buffer.value:
                return buffer.value
    return f'"{path}"'


def install(team: str, created: str = "", members: list[str] | None = None) -> str:
    """Put this file and what it needs to know of the team into the team's bus folder, for members
    whose sandbox cannot read the tabmancer package (agy): they run it with the plain interpreter
    and no site-packages. Rewritten only when it changed. Returns the command that runs it, for
    their prompts: `<command> listen <team> <member>`, `<command> send <team> <to> "..." --from x`."""
    folder = team_dir(team)
    folder.mkdir(parents=True, exist_ok=True)
    code = (f"# Generated by tabmancer from teambus.py when the team starts: edits here are overwritten.\n"
            f"{Path(__file__).read_text(encoding='utf-8')}\n\n"
            "if __name__ == \"__main__\":\n    raise SystemExit(main(standalone=True))\n")
    target = folder / CLI_FILE
    with contextlib.suppress(OSError):
        if not target.is_file() or target.read_text(encoding="utf-8") != code:
            tmp = target.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(code, encoding="utf-8")
            os.replace(tmp, target)
    info = {"name": team, "created": created, "members": list(members or [])}
    with contextlib.suppress(OSError):
        if _read_json_file(_team_file(team)) != info:
            _atomic_write_json(_team_file(team), info)
    return command(team)


def command(team: str) -> str:
    """The command that runs the team's copy of this file (what install writes), for prompts:
    `<command> listen <team> <member>`. Writes nothing."""
    python = getattr(sys, "_base_executable", "") or sys.executable  # not a venv's: its files may be hidden
    return f"{_plain_path(python)} -I {_plain_path(str(team_dir(team) / CLI_FILE))}"


def main(argv: list[str] | None = None, standalone: bool = False) -> int:
    """This file run on its own (`python -I teambus_cli.py listen|send|who <team> ...`): the bus
    folder is the one it lies in unless TABMANCER_BUS_DIR says otherwise."""
    import argparse

    if standalone and not os.environ.get(BUS_ENV):
        os.environ[BUS_ENV] = str(Path(__file__).resolve().parent.parent)
    parser = argparse.ArgumentParser(prog=CLI_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    listening = commands.add_parser("listen", help="wait for this member's messages, print them and exit")
    listening.add_argument("team")
    listening.add_argument("member", nargs="?", default="")
    listening.add_argument("--timeout", type=float, default=DEFAULT_LISTEN_TIMEOUT)
    sending = commands.add_parser("send", help="send a message to a member (or 'all')")
    sending.add_argument("team")
    sending.add_argument("--from", dest="sender", default="")
    sending.add_argument("--summary", default="")
    sending.add_argument("to")
    sending.add_argument("message", nargs="+")
    who = commands.add_parser("who", help="the team's members, and who is listening")
    who.add_argument("team")
    try:
        opts = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    created, members = _read_team_file(opts.team)
    if opts.command == "listen":
        member = opts.member or os.environ.get("TABMANCER_MEMBER", "")
        if not member:
            print("Name the member to listen for (or set TABMANCER_MEMBER).", file=sys.stderr)
            return 1
        return listen_command(opts.team, member, timeout=opts.timeout, since=created)
    if opts.command == "send":
        return send_command(opts.team, opts.to, " ".join(opts.message), sender=opts.sender, summary=opts.summary,
                            members=set(members) or None)
    return who_command(opts.team, members)
