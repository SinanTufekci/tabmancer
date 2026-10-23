"""Where tabmancer keeps its own files (never under ~/.claude)."""

from __future__ import annotations

import json
import os
import sys
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


def read_json(name: str) -> object:
    try:
        return json.loads((config_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(name: str, data: object, indent: int | None = 1) -> None:
    """Write atomically, so a crash or a second instance never leaves half a file."""
    path = config_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)
