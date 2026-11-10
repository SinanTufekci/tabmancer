"""Claude Code brought up to date before tabmancer opens a session: once a day, `claude update`
runs first, and the sessions open when it is done, on the new version.

Claude Code updates itself in the background too, but an update it downloads takes effect only
the next time it starts, so the sessions opened in the morning would start on yesterday's version.
So, when this is on (`tabmancer claude-updates on`, off by default), the dashboard runs `claude
update` as it opens, the first time each day, and a session asked for meanwhile (Enter, n, a team
started, a prompt sent) waits for it; `tabmancer new` and `tabmancer team start` run it first too.
It is asked once a process: one that failed (no network, say) is said and tried again the next
time the dashboard opens; the sessions then open on the version there is.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from .config import read_json, write_json

STATE = "claude-update.json"  # the day it last ran, and what it found
TIMEOUT = 300  # seconds: a download on a slow line, and no longer
VERSION = re.compile(r"\d+\.\d+\.\d+")

_lock = threading.RLock()  # one update at a time: the dashboard's, or a launch waiting for it
_tried = False            # asked in this process already: never twice, whatever came of it


def enabled() -> bool:
    if os.environ.get("TABMANCER_CLAUDE_UPDATE", "").strip() == "0":
        return False
    saved = read_json("config.json")
    return isinstance(saved, dict) and saved.get("claude_update") is True


def set_enabled(on: bool) -> None:
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    saved["claude_update"] = on
    write_json("config.json", saved, indent=2)


def last() -> dict:
    saved = read_json(STATE)
    return saved if isinstance(saved, dict) else {}


def due(today: date | None = None) -> bool:
    """On, not yet asked in this process, and not yet done today."""
    return enabled() and not _tried and last().get("day") != (today or date.today()).isoformat()


@dataclass(frozen=True)
class Outcome:
    ok: bool
    before: str  # the version before, "" when it could not be read
    after: str
    detail: str = ""  # what claude update said, when it failed

    @property
    def changed(self) -> bool:
        return self.ok and bool(self.before) and bool(self.after) and self.before != self.after

    def words(self) -> str:
        if not self.ok:
            on = f" Sessions open on {self.before}." if self.before else ""
            return f"Claude Code could not update: {self.detail or 'claude update failed'}.{on}"
        if self.changed:
            return f"Claude Code updated: {self.before} → {self.after}."
        return f"Claude Code is up to date ({self.after or self.before})."


def _quiet(claude: str, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    from .launch import clean_env  # launch imports this module: imported when it runs

    return subprocess.run([claude, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False, env=clean_env(),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def version(claude: str) -> str:
    try:
        found = VERSION.search(_quiet(claude, "--version", timeout=20).stdout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return found.group(0) if found else ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def run(claude: str | None = None, timeout: float = TIMEOUT) -> Outcome:
    """`claude update`, and the version before and after; the day is kept when it worked."""
    global _tried
    with _lock:
        _tried = True
        claude = claude or shutil.which("claude")
        if not claude:
            return Outcome(False, "", "", "claude is not on PATH")
        before = version(claude)
        try:
            done = _quiet(claude, "update", timeout=timeout)
        except subprocess.TimeoutExpired:
            return Outcome(False, before, before, f"no answer in {timeout // 60:g} minutes")
        except (OSError, subprocess.SubprocessError) as error:
            return Outcome(False, before, before, str(error))
        after = version(claude) or before
        if done.returncode != 0:
            return Outcome(False, before, after, _last_line(done.stderr or done.stdout) or f"exit {done.returncode}")
        outcome = Outcome(True, before, after)
        try:
            write_json(STATE, {"day": date.today().isoformat(), "at": datetime.now().isoformat(timespec="seconds"),
                               "before": before, "after": after}, indent=2)
        except OSError:
            pass  # asked again tomorrow's first time instead
        return outcome


def before_opening(say: Callable[[str], None] | None = None) -> Outcome | None:
    """Before a session opens: today's update, if it is due, or the one running now finished first
    (the dashboard's, in another thread). None when there was nothing to wait for."""
    with _lock:  # one running now (the dashboard's): waited for
        if not due():
            return None
        if say:
            say("Updating Claude Code first (claude update)…")
        outcome = run()
    if say:
        say(outcome.words())
    return outcome
