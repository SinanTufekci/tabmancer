"""Where tabmancer keeps its own files (never under ~/.claude)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

OLD_NAMES = ("antyard", "ccharbor", "ccrestore")  # tabmancer's names before, newest first


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


def _for_good(path: Path, write: bool = False) -> bool:
    """A PermissionError that waiting will not end: a folder by that name, a read-only file or folder.
    Only one another process holds open ends, and only that is worth the wait."""
    if path.is_dir():
        return True
    if write:
        return (path.exists() and not os.access(path, os.W_OK)) or not os.access(path.parent, os.W_OK)
    return not os.access(path, os.R_OK)


def read_json(name: str) -> object:
    path = config_dir() / name
    for attempt in range(BUSY_TRIES):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if _for_good(path):
                return None
            if attempt + 1 < BUSY_TRIES:
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
            time.sleep(BUSY_WAIT)
        except OSError:  # elsewhere a folder by that name is IsADirectoryError: no half file left
            tmp.unlink(missing_ok=True)
            raise


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
