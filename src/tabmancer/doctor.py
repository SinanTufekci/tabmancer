"""`tabmancer doctor`: check everything tabmancer leans on, and say what is off.

The transcript format is internal to Claude Code, so the most useful check is
whether recent transcripts still read the way tabmancer expects.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

from . import __version__, clis, summarize, tokens, workspaces
from .config import config_dir, read_json
from .launch import clean_env, terminal_kind
from .sessions import (
    CACHE_FILE,
    Exit,
    Status,
    _agents_json,
    _registry_sessions,
    claude_dir,
    other_sessions,
    read_session,
)
from .shutdowns import recent_shutdowns

SAMPLE = 30
MARKS = {"ok": "✓", "warn": "!", "fail": "✗"}
Result = tuple[str, str]  # (level, message); level is ok, warn or fail
# The Claude Code tabmancer was last checked with: see CONTRIBUTING.md, "Releasing".
TESTED_WITH = "2.1.310"
# What tabmancer runs claude with, by subcommand ("" for claude itself), and what each is for.
USES = {
    "": {
        "--resume": "reopening sessions", "--fork-session": "fork", "--continue": "workspaces",
        "--bg": "background sessions", "--print": "summaries, handovers, reports, renames, plan limits",
        "--model": "summaries, handovers, teams", "--tools": "summaries and plan limits",
        "--no-session-persistence": "summaries and plan limits", "--name": "teams",
        "--append-system-prompt": "teams", "--settings": "teams", "--allowedTools": "a lead that picks its team",
        "agents": "which sessions are open", "attach": "background sessions", "stop": "background sessions",
        "rm": "background sessions",
    },
    "agents": {"--json": "which sessions are open", "--all": "background sessions (B)"},
}


def _claude_version(claude: str) -> str:
    try:
        result = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20,
                                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def version_check(version: str) -> Result:
    """Claude Code's version, and a word when it is newer than the one tabmancer was checked with."""
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    tested = tuple(int(part) for part in TESTED_WITH.split("."))
    if found and tuple(int(part) for part in found.groups())[:2] > tested[:2]:
        return ("warn", (f"Claude Code CLI: {version}, newer than the {TESTED_WITH} tabmancer was last checked "
                         "with. If something looks off, its formats may have changed: please report it."))
    return ("ok", f"Claude Code CLI: {version} (tabmancer was last checked with {TESTED_WITH}).")


def _help(claude: str, command: str) -> str | None:
    try:
        result = subprocess.run([claude, *([command] if command else []), "--help"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
                                env=clean_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 and result.stdout.strip() else None


def _listed(name: str, text: str) -> bool:
    """Whether the help lists it as a flag or a subcommand of its own, not only in another's words."""
    if name.startswith("-"):  # "  --bg, --background", "  -n, --name <name>"
        line = rf"^\s+(?:-\w, )?(?:--[\w-]+, )*{re.escape(name)}(?![\w-])"
    else:                     # "  stop|kill <id>", "  agents [options]"
        line = rf"^\s+(?:[\w-]+\|)*{re.escape(name)}(?:\|[\w-]+)*(?:\s|$)"
    return re.search(line, text, re.MULTILINE) is not None


def missing_from_cli(claude: str) -> list[str] | None:
    """What tabmancer runs claude with that its help no longer lists, each with what it is for;
    None when the help could not be read."""
    missing = []
    for command, uses in USES.items():
        text = _help(claude, command)
        if text is None:
            return None
        missing += [f"{' '.join(filter(None, ['claude', command, name]))} ({what})"
                    for name, what in uses.items() if not _listed(name, text)]
    return missing


def checks() -> list[Result]:
    found: list[Result] = []
    claude = shutil.which("claude")
    if claude:
        found.append(version_check(_claude_version(claude) or claude))
        missing = missing_from_cli(claude)
        if missing:
            found.append(("fail", ("Claude Code no longer lists what tabmancer runs it with: "
                                   f"{'; '.join(missing)}. Please report it with your Claude Code "
                                   f"version (tabmancer {__version__}).")))
        elif missing is not None:
            found.append(("ok", "Every flag and command tabmancer runs claude with is still there."))
    else:
        found.append(("fail", "Claude Code CLI (claude) is not on PATH: sessions cannot be reopened."))

    live = _agents_json() if claude else None
    if live is not None:
        found.append(("ok", "Open sessions come from `claude agents --json`."))
    else:
        live = _registry_sessions()
        found.append(("warn", ("`claude agents --json` did not answer; open sessions come from the "
                               "internal pid files in ~/.claude/sessions instead.")))

    terminal = terminal_kind()
    if terminal == "none":
        found.append(("warn", ("No supported terminal found (Windows Terminal or tmux): tabmancer "
                               "prints the commands instead of opening tabs.")))
    else:
        found.append(("ok", f"Tabs open in: {terminal}"))

    found += _transcript_checks({entry["sessionId"] for entry in live})

    for name in clis.enabled():
        if name == "claude":
            continue
        where = clis.installed(name)
        listed = len(other_sessions(30, [name]))
        if where:
            found.append(("ok", f"{clis.label(name)}: {where}, {listed} sessions in the last 30 days."))
        else:
            found.append(("warn", (f"{clis.label(name)} is on but not on PATH: its {listed} sessions are "
                                   "listed but cannot be opened.")))

    if sys.platform == "win32":
        shutdowns = recent_shutdowns()
        if shutdowns:
            found.append(("ok", f"Shutdown records readable (latest {shutdowns[0]:%d %b %H:%M})."))
        else:
            found.append(("warn", ("No shutdown records in the System event log: sessions open at the "
                                   "last shutdown cannot be ticked automatically.")))

    cache = read_json(CACHE_FILE)
    cached = len(cache.get("entries", {})) if isinstance(cache, dict) else 0
    found.append(("ok", (f"Own files in {config_dir()}: {len(workspaces.load())} workspaces, "
                         f"{len(summarize.load_cache())} summaries, {cached} cached sessions.")))
    return found


def _transcript_checks(open_ids: set[str]) -> list[Result]:
    projects = claude_dir() / "projects"
    if not projects.is_dir():
        return [("fail", f"No transcripts folder at {projects}.")]
    cutoff = (datetime.now() - timedelta(days=30)).timestamp()
    recent = []
    for path in projects.glob("*/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            recent.append((mtime, path))
    recent.sort(reverse=True)
    sessions = [s for s in (read_session(p) for _, p in recent[:SAMPLE]) if s is not None]
    if not sessions:
        return [("warn", f"No readable sessions in the last 30 days under {projects}.")]

    found: list[Result] = [("ok", f"Read {len(sessions)} of the {min(len(recent), SAMPLE)} newest transcripts.")]
    unknown = sum(s.status is Status.UNKNOWN for s in sessions)
    no_reply = sum(not s.last_reply for s in sessions)
    if unknown > len(sessions) * 0.3 or no_reply > len(sessions) * 0.5:
        found.append(("fail", (f"{unknown} sessions with an unreadable ending and {no_reply} where no "
                               "reply from Claude could be found: Claude Code's transcript format may "
                               "have changed. Please report it with your Claude Code version "
                               f"(tabmancer {__version__}).")))
    else:
        found.append(("ok", f"Endings readable: {len(sessions) - unknown} of {len(sessions)}."))

    counted = tokens.collect(30).per_session(whole_life=True)
    replied = [s for s in sessions if s.last_reply]
    uncounted = sum(not counted.get(s.session_id, tokens.Tokens()).total for s in replied)
    if replied and uncounted > len(replied) * 0.5:
        found.append(("warn", (f"No token counts in {uncounted} of {len(replied)} sessions with replies: "
                               "Claude Code may have changed how it records usage, so the token "
                               "figures are too low.")))
    else:
        found.append(("ok", f"Token counts readable: {len(replied) - uncounted} of {len(replied)}."))

    # Open sessions have no exit record yet; they would all look killed.
    ended = [s for s in sessions if s.session_id not in open_ids]
    killed = sum(s.exit is Exit.KILLED for s in ended)
    if len(ended) >= 5 and killed > len(ended) * 0.6:
        found.append(("warn", (f"{killed} of {len(ended)} sessions look killed. If you closed them "
                               "normally, Claude Code may have stopped writing its exit record and the "
                               "Process column is unreliable.")))
    return found


def run() -> int:
    results = checks()
    for level, message in results:
        print(f"{MARKS[level]} {message}")
    return 1 if any(level == "fail" for level, _ in results) else 0
