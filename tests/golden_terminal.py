"""Goldens of the terminal dashboard's view on the made-up sessions of `tabmancer --demo`, for the desktop
window to be checked against once the terminal dashboard is archived (0.2.0): the view model (VM.to_json),
the list's rows (rowwords), each session's preview (sessionwords.detail) and each team member's line
(teamwords.member_status).

make_terminal_golden.py writes them from the terminal dashboard itself; test_terminal_golden.py builds the
same words from the store's snapshot, as the window does, and compares. Both run in a child process that
calls freeze() before anything of tabmancer is imported: the clock stands at noon (local time) on a fixed
day, so the demo's times, day groups and "ago" words come out the same on every machine and in every zone.

The files go into a public repository: mask() takes out the temporary folder, the user's name and home,
the machine's name, the demo's own root (a drive letter on Windows) and any UTC offset."""

import datetime as _datetime
import json
import os
import re
import socket
import sys
import time
from enum import Enum
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden" / "terminal"
PARTS = ("view", "rows", "detail", "members")
# Noon, local time, on a fixed day: the made-up sessions of the last 14 days fall on the same days and
# clock times in every zone (no daylight-saving change in the fortnight before, in the zones CI runs in).
FIXED = time.mktime((2026, 11, 18, 12, 0, 0, 0, 0, -1))
WIDTH = (190, 46)  # the terminal dashboard's size: wide enough for the preview beside the list (app.SPLIT_FROM)


def freeze() -> None:
    """The clock stands still at FIXED: time.time() and datetime.now()/today(). Call before importing tabmancer:
    its modules take datetime when they are imported. date.today() is left alone (a subclass of date would
    break isinstance checks on datetimes); no golden word reads it."""
    real = _datetime.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(FIXED, tz)

        @classmethod
        def today(cls):
            return cls.fromtimestamp(FIXED)

    time.time = lambda: FIXED
    time.time_ns = lambda: int(FIXED * 1e9)
    _datetime.datetime = Frozen


def prepare(home: Path):
    """The demo's made-up sessions under home, its stand-ins in place (as `tabmancer --demo` sets them):
    returns tabmancer.demo and the stand-ins' told words. Files the demo writes without dating them carry
    the real clock's time, after FIXED: they are dated FIXED, as if written at the frozen moment."""
    os.environ.update({"TABMANCER_CONFIG_DIR": str(home / "config"), "CLAUDE_CONFIG_DIR": str(home / "claude"),
                       "CODEX_HOME": str(home / "codex"),  # before tabmancer is imported (demo.prepare sets these)
                       "TABMANCER_BUS_DIR": str(home / "bus")})  # demo.prepare does not: the machine's bus is shared
    from tabmancer import demo

    told: list[str] = []
    live = demo.prepare(home)
    for path in home.rglob("*"):
        if path.is_file() and path.stat().st_mtime > FIXED + 60:
            os.utime(path, (FIXED, FIXED))
    for module, name, value in demo.stand_ins(home, live, told.append):
        setattr(module, name, value)
    return demo, told


def masks(home: Path) -> list[tuple[re.Pattern, str]]:
    """(pattern, put) pairs, longest first: what would tell whose machine wrote the file. Folders in any case
    (Windows ones are compared lower-cased, VM.folder_key) and with either slash; the user's name only as a
    folder of a path, the machine's as a word (the demo's words say "user")."""
    from tabmancer import demo

    folders: dict[str, str] = {}
    for text, put in ((str(home), "<home>"), (str(home.resolve()), "<home>"), (demo.ROOT, "<root>"),
                      (str(Path.home()), "<user-home>")):
        folders.setdefault(text.replace("\\", "/"), put)
    pairs = [(re.compile(re.escape(text), re.IGNORECASE), put)
             for text, put in sorted(folders.items(), key=lambda pair: -len(pair[0]))]
    for name in {os.environ.get(key, "") for key in ("USERNAME", "USER", "LOGNAME")} - {""}:
        pairs.append((re.compile(rf"(?<=/){re.escape(name)}(?=/|$)", re.IGNORECASE), "<user>"))
    host = socket.gethostname()
    if len(host) > 2:
        pairs.append((re.compile(rf"\b{re.escape(host)}\b", re.IGNORECASE), "<host>"))
    return pairs


OFFSET = re.compile(r"(\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)(?:[+-]\d{2}:\d{2}|Z)\b")


def mask(value, pairs: list[tuple[re.Pattern, str]]):
    """value with every string masked (keys too): every backslash made '/' first (a path's separator: the
    goldens are the same on every system), then the pairs (masks()), then any UTC offset."""
    if isinstance(value, dict):
        return {mask(key, pairs): mask(item, pairs) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(item, pairs) for item in value]
    if not isinstance(value, str):
        return value
    value = value.replace("\\", "/")
    for pattern, put in pairs:
        value = pattern.sub(put, value)
    return OFFSET.sub(r"\1<offset>", value)


def leftovers(text: str) -> list[str]:
    """What should never be in a golden: a drive letter, a user folder, the temp folder, the machine's name."""
    found = re.findall(r"\b[A-Za-z]:[\\/]", text) + re.findall(r"(?i)[\\/](?:users|home)[\\/]\w+", text)
    found += re.findall(r"(?i)[\\/](?:temp|tmp)[\\/]", text)
    host = socket.gethostname()
    if host and len(host) > 2 and host in text:
        found.append("<the host's name>")
    return found


def dump(parts: dict[str, object]) -> dict[str, str]:
    """Each part as the text its file holds."""
    return {name: json.dumps(parts[name], ensure_ascii=False, indent=1, sort_keys=False) + "\n" for name in PARTS}


def save(parts: dict[str, object]) -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, text in dump(parts).items():
        (GOLDEN / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")


def load() -> dict[str, str]:
    return {name: (GOLDEN / f"{name}.json").read_text(encoding="utf-8") for name in PARTS}


def cells(words) -> dict:
    """A row's words (rowwords.RowWords) as JSON: each column's (text, style) parts, and its motion."""
    from tabmancer import rowwords

    return {"cells": dict(zip(rowwords.COLUMNS, [[list(part) for part in cell] for cell in words.cells])),
            "motion": words.motion}


def plain(value):
    """dataclasses and tuples as JSON-ready data; datetimes as ISO text (as VM.to_json)."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, _datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [plain(item) for item in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, Enum):
        return value.value
    return value


if __name__ == "__main__":
    sys.exit("Run make_terminal_golden.py to write the goldens.")
