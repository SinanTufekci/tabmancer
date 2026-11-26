"""Where tabmancer keeps its own files (never under ~/.claude)."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

OLD_NAMES = ("antyard", "ccharbor", "ccrestore")  # tabmancer's names before, newest first
NOT_SAVED = "Not saved: config.json could not be read, so it was left as it is."  # merge_json refused


def config_dir() -> Path:
    if os.environ.get("TABMANCER_CONFIG_DIR"):
        return Path(os.environ["TABMANCER_CONFIG_DIR"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"])
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _taken_over(base / "tabmancer", [base / old for old in OLD_NAMES])


def _taken_over(new: Path, olds: list[Path]) -> Path:
    """Move the folder of an old name (the newest there is) to the new one, once, so nothing
    saved is lost."""
    for old in olds:
        if new.exists():
            break
        if old.is_dir():
            try:
                old.rename(new)
            except OSError:
                pass  # in use, or another instance moved it first
    return new


# On Windows a file being replaced can't be opened, and one open can't be replaced: a read and a
# write of the same file by two processes (the dashboard's poll and `tabmancer team add`, say)
# collide for a moment. Both wait it out, as a read that gave up would read as no file, and the
# load-then-save after it would write the file back with the rest of it gone.
BUSY_TRIES = 40
BUSY_WAIT = 0.025
UI_TRIES = 4  # on a UI thread: a held lock or file waited for ~75 ms, not BUSY_TRIES' ~1 s (the window freezes)
_HELD: list[Path] = []  # the locks this process holds now (locked): kept fresh while it waits inside one


def _freshen() -> None:
    """A wait inside a lock (a held file read or replaced again, BUSY_WAIT apart) keeps the lock fresh, so
    however long it takes, no other process takes the lock over as left by one that died."""
    now = time.time()
    for held in _HELD:
        try:
            os.utime(held, (now, now))
        except OSError:
            pass


def _for_good(path: Path, write: bool = False) -> bool:
    """A PermissionError that waiting will not end: a folder by that name, a read-only file or folder.
    Only one another process holds open ends, and only that is worth the wait."""
    if path.is_dir():
        return True
    if write:
        return (path.exists() and not os.access(path, os.W_OK)) or not os.access(path.parent, os.W_OK)
    return not os.access(path, os.R_OK)


def read_json(name: str, tries: int | None = None) -> object:
    """The file's JSON; None when there is none, or it could not be read. tries: how often a file another
    process holds is tried, BUSY_WAIT apart (None: BUSY_TRIES; 1, no wait at all, for a UI thread that
    reads it again soon)."""
    path = config_dir() / name
    tries = BUSY_TRIES if tries is None else max(1, tries)
    for attempt in range(tries):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if _for_good(path):
                return None
            if attempt + 1 < tries:
                _freshen()
                time.sleep(BUSY_WAIT)
        except (OSError, ValueError):
            return None
    return None


def write_json(name: str, data: object, indent: int | None = 1) -> None:
    """Write atomically, so a crash or a second instance never leaves half a file."""
    path = config_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    for attempt in range(BUSY_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt + 1 == BUSY_TRIES or _for_good(path, write=True):
                tmp.unlink(missing_ok=True)
                raise
            _freshen()
            time.sleep(BUSY_WAIT)
        except OSError:  # elsewhere a folder by that name is IsADirectoryError: no half file left
            tmp.unlink(missing_ok=True)
            raise


# A lock this old was left by a process that died mid-change: taken over. A live holder never is: it
# holds it for a read and a write (milliseconds), and a wait inside them keeps it fresh (_freshen).
LOCK_STALE_SECONDS = 3.0


class Busy(OSError):
    """locked could not get its lock in time: another process is changing that file."""


@contextmanager
def locked(name: str, tries: int | None = None) -> Iterator[None]:
    """One process at a time in a read-then-write of the config file `name` (two that read it at once
    would each write back its own, and the first one's change would be lost): `<name>.lock` beside it,
    made with O_EXCL, tried `tries` times (None: BUSY_TRIES; UI_TRIES on a UI thread) BUSY_WAIT apart. One
    older than LOCK_STALE_SECONDS is taken over. Raises Busy when it could not be got: the caller leaves the file
    as it is and says so."""
    path = config_dir() / f"{name}.lock"
    tries = BUSY_TRIES if tries is None else max(1, tries)
    waited = 0
    while True:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
                    path.unlink()
                    continue  # a dead process's, gone: made again at once, no try spent
            except FileNotFoundError:
                continue  # let go of just now: made again at once, no try spent
            except OSError:
                pass
        except OSError:
            pass  # held for a moment (a scanner), or no folder to make it in
        waited += 1
        if waited >= tries:
            raise Busy(f"another tabmancer process is changing {name}")
        time.sleep(BUSY_WAIT)
    _HELD.append(path)
    try:
        yield
    finally:
        _HELD.remove(path)
        try:
            path.unlink()
        except OSError:
            pass  # taken over as stale meanwhile, or held: it goes stale


def merge_json(name: str, **keys: object) -> bool:
    """Set these keys in a saved dict, keeping the rest of it. Refused (False) when the file is there
    but could not be read, or holds no dict: writing the keys alone would wipe whatever else it holds
    (config.json keeps the theme, Bit, the split and the sessions to continue side by side). False too
    when the write fails."""
    path = config_dir() / name
    saved = read_json(name)
    if not isinstance(saved, dict):
        if os.path.lexists(path):
            return False
        saved = {}
    saved.update(keys)
    try:
        write_json(name, saved, indent=2)
    except OSError:
        return False
    return True
