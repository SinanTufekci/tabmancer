"""Is there a newer tabmancer? Once a day the dashboard asks PyPI for tabmancer's latest version
(only that: nothing of yours goes with the question) and, when there is a newer one, says so with
the command that upgrades it the way it was installed. `tabmancer updates off` turns it off, and
so does TABMANCER_UPDATE_CHECK=0. A checkout of the source (an editable install) is never told:
git pull brings it up to date."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import metadata

from . import __version__
from .config import read_json, write_json

PYPI = "https://pypi.org/pypi/tabmancer/json"
EVERY = timedelta(days=1)  # PyPI is asked this seldom; in between, its last answer stands
STATE = "updates.json"     # when PyPI last answered, and what it said


def enabled() -> bool:
    if os.environ.get("TABMANCER_UPDATE_CHECK", "").strip() == "0":
        return False
    saved = read_json("config.json")
    return not (isinstance(saved, dict) and saved.get("update_check") is False)


def set_enabled(on: bool) -> None:
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    saved["update_check"] = on
    write_json("config.json", saved, indent=2)


def _parts(version: str) -> tuple[int, ...] | None:
    """1.2.3 as (1, 2, 3); None for a pre-release or anything else, which is never offered."""
    try:
        return tuple(int(part) for part in version.split("."))
    except (AttributeError, ValueError):
        return None


def newer(latest: str, current: str = __version__) -> bool:
    found, have = _parts(latest), _parts(current)
    return found is not None and have is not None and found > have


def ask_pypi(timeout: float = 5) -> str | None:
    """The latest version on PyPI, or None when it cannot be asked (offline, a proxy...)."""
    request = urllib.request.Request(PYPI, headers={"Accept": "application/json", "User-Agent": "tabmancer"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return str(json.load(reply)["info"]["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest(now: datetime | None = None, ask: Callable[[], str | None] | None = None) -> str | None:
    """The latest version PyPI has: asked once a day at most, its answer remembered in between
    (asked again next time when it did not answer). None until it has answered once."""
    now = now or datetime.now()
    state = read_json(STATE)
    state = state if isinstance(state, dict) else {}
    try:
        answered = datetime.fromisoformat(state["answered"])
    except (KeyError, TypeError, ValueError):
        answered = None
    if answered is None or not timedelta(0) <= now - answered < EVERY:
        found = (ask or ask_pypi)()
        if found:
            state = {"answered": now.isoformat(timespec="seconds"), "latest": found}
            write_json(STATE, state, indent=2)
    found = state.get("latest")
    return found if isinstance(found, str) else None


def upgrade_command() -> str | None:
    """The command that upgrades tabmancer the way it was installed; None for an editable install
    of its source, which is never told of a newer version."""
    try:
        direct = metadata.distribution("tabmancer").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    try:
        if direct and json.loads(direct).get("dir_info", {}).get("editable"):
            return None
    except (ValueError, AttributeError):
        pass
    where = sys.prefix.replace("\\", "/").lower()
    if "/uv/tools/" in where:
        return "uv tool upgrade tabmancer"
    if "/pipx/" in where:
        return "pipx upgrade tabmancer"
    if "/uv/cache/" in where or "/archive-v" in where:  # uvx: a passing environment
        return "uvx tabmancer@latest"
    return "pip install --upgrade tabmancer"


def news(now: datetime | None = None, ask: Callable[[], str | None] | None = None) -> tuple[str, str] | None:
    """(the newer version, the command that upgrades to it) when there is one to tell of."""
    if not enabled():
        return None
    command = upgrade_command()
    if command is None:
        return None
    found = latest(now, ask)
    return (found, command) if found and newer(found) else None
