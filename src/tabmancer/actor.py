"""One actor: of the tabmancer dashboards open at once, one does what happens by itself.

A session whose limit lifted is continued, a toast is sent, claude update runs, PyPI is asked,
Wrapped says its word, the limits are kept for the stats: once, by the dashboard that holds the lock
(actor.json in tabmancer's folder). The others show the same and do what you ask of them, nothing
by itself; one takes over when the holder is gone.

A pid alone proves nothing (Windows hands out a dead process's pid again, a restart reuses them
all), so the lock names the process three ways: its pid, when it started, and its command line.
The holder is gone only when no process has that pid, or the one that has it started at another
time, or runs another command line (compared when both can be read: an elevated process's cannot
always be), or it cannot be opened at all: the holder is always one of your own processes.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import BUSY_TRIES, BUSY_WAIT, config_dir

LOCK = "actor.json"
TAKING = "actor.json.taking"  # held for the moment a lock left behind is taken over: one taker at a time
TAKING_STALE = 10.0  # a takeover mutex older than this was left by a process that died taking over
WRITING = 5.0  # an empty or half-written lock this young is being written: not left behind
LEFT_TMP = 60.0  # a temporary lock file this old was left by a write that failed or a process killed
CLAIMS = "claims"  # first_to: one file each, for something only one process may do once
CLAIM_SECONDS = 120.0

_mine: dict | None = None
_registered = False


# --- which process is it ---------------------------------------------------------------------------

def _windows_process(pid: int) -> tuple[str, str] | None:
    """(start time, command line) of a running process, or None: gone, or it cannot be opened."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.POINTER(ctypes.c_ulong)]

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        started = str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        size = ctypes.c_ulong(0)
        room = 4096
        for _ in range(3):  # ProcessCommandLineInformation (60): a UNICODE_STRING and its text after it
            buffer = ctypes.create_string_buffer(room)
            status = ntdll.NtQueryInformationProcess(handle, 60, buffer, room, ctypes.byref(size))
            if status >= 0:
                text = UnicodeString.from_buffer(buffer)
                return started, ctypes.wstring_at(text.Buffer, text.Length // 2) if text.Buffer else ""
            if status & 0xFFFFFFFF != 0xC0000004 or size.value <= room:  # not STATUS_INFO_LENGTH_MISMATCH
                break
            room = size.value
        return started, ""
    finally:
        kernel32.CloseHandle(handle)


def _posix_process(pid: int) -> tuple[str, str] | None:
    proc = Path(f"/proc/{pid}")
    # A zombie (Z; X: being removed) has exited and waits only for its parent to reap it: gone.
    if proc.is_dir():  # Linux: the state (field 3), the start in clock ticks after boot (field 22), the
        try:            # arguments NUL-separated
            stat = (proc / "stat").read_text(encoding="utf-8", errors="replace")
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return None
        fields = stat[stat.rindex(")") + 2:].split()
        return None if fields[0] in ("Z", "X") else (fields[19], command)
    try:  # macOS and the other BSDs
        state = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                               text=True, timeout=5, check=False).stdout.strip()
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    flags, _, started = state.partition(" ")
    return None if not started.strip() or flags.startswith("Z") else (started.strip(), command)


def process(pid: int) -> tuple[str, str] | None:
    """(start time, command line) of the process with this pid, or None when there is none or it
    cannot be read. Both are read the same way for every pid, this one's too, so they compare."""
    if pid <= 0:
        return None
    try:
        return _windows_process(pid) if sys.platform == "win32" else _posix_process(pid)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def me() -> dict:
    """This process as the lock names it."""
    global _mine
    if _mine is None:
        pid = os.getpid()
        started, command = process(pid) or ("", "")
        _mine = {"pid": pid, "started": started, "command": command}
    return _mine


def is_me(record: object) -> bool:
    mine = me()
    return isinstance(record, dict) and all(record.get(key) == mine[key] for key in ("pid", "started", "command"))


def running(record: object) -> bool:
    """Whether the process a lock names still runs: the same pid, started at the same time, and the
    same command line when both it and the lock have one (one that cannot be read is no proof it is
    gone). No pid or start time to check, or no such process to open: gone."""
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int) or not record.get("started"):
        return False
    found = process(record["pid"])
    if found is None or found[0] != record["started"]:
        return False
    command = record.get("command") or ""
    return not (command and found[1]) or found[1] == command


# --- the lock ------------------------------------------------------------------------------------

def _path() -> Path:
    return config_dir() / LOCK


def _read(path: Path) -> dict | None:
    """The record in a lock file, or None: none there, or not a record. A file another process holds
    open for a moment (Windows refuses the read; so may a virus scanner) is waited for, as
    config.read_json waits: a read that gave up would make a holder look gone."""
    for attempt in range(BUSY_TRIES):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt + 1 < BUSY_TRIES and not path.is_dir():
                time.sleep(BUSY_WAIT)
                continue
            return None
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None
    return None


def _age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 0.0


def _record() -> str:
    return json.dumps({**me(), "since": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False)


def _register() -> None:
    global _registered
    if not _registered:
        atexit.register(release)
        _registered = True


def holder() -> dict | None:
    """The lock as it is written: whose it is, and since when. None when there is none."""
    return _read(_path())


def holds() -> bool:
    """Whether this process holds the lock (checked afresh: another may have taken it over)."""
    return is_me(holder())


def claim() -> bool:
    """Take the lock, unless another process that still runs holds it. True when this one holds it
    now: it was free, left behind (its holder gone: start time and command line checked), or ours."""
    if nameless():
        return False  # a lock without its start time would be judged gone by the next to look
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    _sweep(path)
    try:
        _create(path)
    except FileExistsError:
        record = _read(path)
        if is_me(record):
            return True
        if record is None and _age(path) < WRITING:
            return False  # another one is writing it this moment (where links cannot be made)
        if record is not None and running(record):
            return False
        return _take_over(path, record)
    except OSError:
        return False
    _register()
    return True


def nameless() -> bool:
    """Whether this process could not read its own start time: it never takes the lock then, since
    the others could not tell it is still running and would take the lock over at once."""
    return not me()["started"]


def _sweep(path: Path) -> None:
    """Temporary lock files a write that failed or a process killed between write and link left."""
    try:
        left = list(path.parent.glob(f"{path.name}.*.tmp"))
    except OSError:
        return
    for tmp in left:
        if _age(tmp) > LEFT_TMP:
            try:
                tmp.unlink()
            except OSError:
                pass


def _create(path: Path, text: str | None = None) -> None:
    """A file written whole, then put in place only if none is there (FileExistsError if one is): a
    hard link to a finished temporary file, so nobody ever reads it half-written, however long its
    writer is held up in between. Where hard links cannot be made (FAT): created, then written."""
    text = _record() if text is None else text
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.link(tmp, path)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)


def _take_over(path: Path, left: dict | None) -> bool:
    """The lock a gone process left: replaced with this one's, while holding TAKING, and only if it is
    still the one judged gone (another taker may have been quicker). TAKING holds its taker's token:
    a stale one (its taker died, or slept past TAKING_STALE) is replaced with ours, never deleted and
    made again, and each taker removes its own only."""
    taking = path.with_name(TAKING)
    token = f"{os.getpid()} {uuid.uuid4().hex}"
    try:
        _create(taking, token)
    except FileExistsError:
        if _age(taking) < TAKING_STALE:
            return False  # another is taking it over now
        tmp = taking.with_name(f"{TAKING}.{os.getpid()}.tmp")
        try:
            tmp.write_text(token, encoding="utf-8")
            os.replace(tmp, taking)
        except OSError:
            tmp.unlink(missing_ok=True)
            return False
    except OSError:
        return False
    try:
        if _token_in(taking) != token:
            return False  # another taker replaced the same stale one: it is theirs
        if _read(path) != left:
            return False  # taken meanwhile
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(_record(), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    finally:
        if _token_in(taking) == token:
            try:
                taking.unlink()
            except OSError:
                pass
    _register()
    return True


def _token_in(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def release() -> None:
    """Give the lock up, if this process holds it: the next dashboard to look takes it."""
    path = _path()
    if is_me(_read(path)):
        try:
            path.unlink()
        except OSError:
            pass


# --- once, by whichever process gets there first -------------------------------------------------

def first_to(what: str, within: float = CLAIM_SECONDS) -> bool:
    """True for the first process to ask for this (a queued handover of one session, say), False for
    any other within the next `within` seconds. Two dashboards that both queued it hand it over once."""
    folder = config_dir() / CLAIMS
    path = folder / _claim_name(what)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():  # the stale ones go, so the folder stays small
            if old != path and _age(old) > within:
                old.unlink(missing_ok=True)
    except OSError:
        pass
    for _ in range(2):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            if _age(path) <= within:
                return False
            try:
                path.unlink()
            except OSError:
                return False
        except OSError:
            return True  # no folder to note it in: better done than never
    return False


def let_go(what: str) -> None:
    """first_to's claim given back: what it was for failed, so it may be asked for again at once."""
    try:
        (config_dir() / CLAIMS / _claim_name(what)).unlink(missing_ok=True)
    except OSError:
        pass


def _claim_name(what: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in what)[:150]
