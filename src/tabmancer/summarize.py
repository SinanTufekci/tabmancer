"""On-demand AI summaries of a session, written by Claude Code itself.

Runs `claude -p` with a small model, no tools and no session persistence, so it uses
whatever plan or API key Claude Code is logged in with and leaves no transcript
behind. Summaries are cached until the transcript changes: summaries.json, changed by one
process at a time (config.locked), so the terminal dashboard and a window summarizing at once each
keep the other's.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from typing import NamedTuple

from .config import config_dir, locked, read_json, write_json
from .sessions import Session, _is_real_prompt, _shorten, _text, sanitize
from .teambus import PRINT_TIMEOUT_EXIT

FILE = "summaries.json"

MODEL = os.environ.get("TABMANCER_SUMMARY_MODEL", "haiku")
MAX_PROMPTS = 40
INSTRUCTIONS = """Summarise this coding-agent session for someone deciding whether to reopen it.
At most three short sentences and 60 words, in the same language as the user's messages:
what the goal was, what got done, and what was left open or where it stopped. Plain text
only: no headings, no lists, no preamble. Do not use any tools.
"""

FAILED = "Summary failed: {error}"
UNSAVED = ("The summary is shown but not saved: summaries.json is busy, or there and unreadable (left as it "
           "is). s again saves it.")

_lock = threading.Lock()


class SummaryError(Exception):
    pass


class Summary(NamedTuple):
    """A summary written: its cache entry, and whether summaries.json took it (saved; else it is shown
    from memory only, and the front says so: UNSAVED)."""

    entry: dict
    saved: bool


def digest(session: Session, max_prompts: int = MAX_PROMPTS, keep_replies: int = 3) -> str:
    """The user's prompts, Claude's last replies and the edited files, trimmed to size."""
    prompts: list[str] = []
    replies: list[str] = []
    with session.path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            # Conversation records start with parentUuid; request blobs and
            # bookkeeping records (the bulk of a big transcript) do not.
            if not line.startswith('{"parentUuid"'):
                continue
            is_user = '"type":"user"' in line
            if not is_user and '"type":"assistant"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            record = sanitize(record)
            content = (record.get("message") or {}).get("content")
            if record.get("type") == "user" and _is_real_prompt(record):
                prompts.append(_shorten(_text(content), 300))
            elif record.get("type") == "assistant":
                reply = _text(content).strip()
                if reply:
                    replies = [*replies[-(keep_replies - 1):], _shorten(reply, 600)] if keep_replies > 1 else \
                        [_shorten(reply, 600)]
    if len(prompts) > max_prompts:
        prompts = [*prompts[:5], "(…)", *prompts[-(max_prompts - 5):]]
    parts = [f"Title: {session.title}", f"Folder: {session.cwd}",
             f"How it ended: {session.status.value}", "", "User messages, oldest first:"]
    parts += [f"- {p}" for p in prompts]
    if replies:
        parts += ["", "Claude's last replies:", *[f"- {r}" for r in replies]]
    if session.files:
        parts += ["", "Files edited near the end: " + ", ".join(session.files)]
    return "\n".join(parts)


def load_cache() -> dict[str, dict]:
    data = read_json(FILE)
    return data if isinstance(data, dict) else {}


def _store(session_id: str, entry: dict | None, tries: int | None = None) -> bool:
    """One summary saved (entry None: dropped), the rest of the file kept: read and written under its
    lock (config.locked), so another process's summary saved meanwhile is kept. False, the file left
    as it was, when it is there but unreadable or holds no dict (writing ours alone would drop every
    other summary), its lock could not be got (config.Busy), or the write failed. tries: as
    config.locked's."""
    with _lock:  # several worker threads of this process wait here, not on the lock file's tries
        try:
            with locked(FILE, tries):
                data = read_json(FILE, tries)
                if not isinstance(data, dict):
                    if os.path.lexists(config_dir() / FILE):
                        return False
                    data = {}
                if entry is None:
                    if session_id not in data:
                        return True  # nothing to drop: not written
                    data.pop(session_id)
                else:
                    data[session_id] = entry
                write_json(FILE, data)
        except OSError:  # config.Busy, the lock not got, among them
            return False
    return True


def forget(session_ids: list[str]) -> bool:
    """Drop cached summaries, for sessions that were deleted. False when one could not be dropped."""
    dropped = [_store(session_id, None) for session_id in session_ids]  # each tried, a failure or not
    return all(dropped)


def session_digest(session: Session) -> str:
    """What an AI is shown of a session."""
    if session.cli == "agy":
        from .agy import digest as agy_digest

        try:
            return agy_digest(session)
        except OSError:
            raise SummaryError("Antigravity has no readable JSONL transcript for this session.") from None
    if session.cli == "codex":
        from .codex import digest as codex_digest

        return codex_digest(session)
    return digest(session)


# Claude Code signs in other ways than claude.ai: an API key or token in the environment, a cloud
# provider, or a key it keeps in .claude.json.
CLAUDE_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
               "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
AGY_MIN_WAIT = 30  # seconds: an agy call took 9 to 13 s (agy 1.5.15, Hydrogen, 2026-11-23)
AGY_MAX_ARG = 30_000  # characters of prompt on agy's command line (Windows' holds 32,767 in all)
CUT = "\n\n[... part of this was cut to fit agy's command line ...]\n\n"


def _claude_ready() -> bool:
    """claude is on PATH and signed in: a claude.ai account, or a key Claude Code takes."""
    if not shutil.which("claude"):
        return False
    if any(os.environ.get(key) for key in CLAUDE_KEYS):
        return True
    from . import accounts

    return accounts.claude() is not None or bool(accounts._read(accounts.claude_file()).get("primaryApiKey"))


def backend() -> str:
    """Which CLI answers tabmancer's background calls: agy when it is the main CLI (clis.main_cli),
    or when Claude Code is not on PATH or not signed in, and agy is; Claude Code otherwise."""
    from . import clis

    if shutil.which("agy") and (clis.main_cli() == "agy" or not _claude_ready()):
        return "agy"
    return "claude"


def ask(prompt: str, model: str, timeout: float = 180, effort: str = "") -> str:
    """One answer to a prompt, from the CLI backend() names. model and effort are Claude Code's and
    go to claude only: agy always runs the chain's model (agy.resolve), and none when the chain is
    out of quota.

    claude: `claude -p` with no tools, no transcript left behind, run from a neutral folder so no
    project CLAUDE.md is loaded into the request. An empty model is your default. No MCP server is
    started either (--strict-mcp-config with none given): with no tools they are of no use, and
    starting them took half of a short call's time (4.9 s against 2.6 s)."""
    if backend() == "agy":
        return _ask_agy(prompt, timeout)
    claude = shutil.which("claude")
    if not claude:
        raise SummaryError("The claude CLI is not on PATH, nor is agy.")
    chosen = [*(["--model", model] if model else []), *(["--effort", effort] if effort else [])]
    try:
        result = subprocess.run(
            [claude, "-p", *chosen, "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as error:
        raise SummaryError(str(error)) from error
    answer = result.stdout.strip()
    if result.returncode != 0 or not answer:
        raise SummaryError(result.stderr.strip()[:300] or "claude returned nothing.")
    return answer


def _ask_agy(prompt: str, timeout: float) -> str:
    """One answer from `agy -p` on the chain's model, run in agy.background_dir() so the call is never
    listed as a conversation of yours. With every chain model out of quota nothing runs, and the
    error says which are out and until when; a run that fails on quota is noted (note_quota_error),
    so the next call goes on to the chain's next model. agy stops the turn itself a few seconds
    before tabmancer would (--print-timeout, exit 3: an answer cut short is no answer here)."""
    from . import agy

    exe = shutil.which("agy")
    if not exe:
        raise SummaryError("agy is not on PATH.")
    picked, why = agy.resolve("")
    if not picked:
        raise SummaryError(str(agy.no_model(why)))
    wait = max(AGY_MIN_WAIT, int(timeout))
    folder = agy.background_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SummaryError(f"no folder for agy's call: {error}") from error
    named = agy.label(picked) or picked
    argv = [exe, "--model", picked, "--disable-slash-commands", "--print-timeout", f"{wait}s",
            "-p", fit_argument(prompt)]
    code, out, err = _run(argv, wait + 15, folder)
    if code is None:
        raise SummaryError(f"agy ({named}) gave no answer in {wait} s{': ' + err[:200] if err else ''}.")
    answer = out.strip()
    if code == 0 and answer:
        return answer
    back = agy.note_quota_error(err + "\n" + out, picked)
    if back is not None:
        raise SummaryError(f"{named} is out of quota until {agy._when_back(back)}.")
    if code == PRINT_TIMEOUT_EXIT:
        raise SummaryError(f"agy ({named}) ran out of time ({wait} s).")
    raise SummaryError(err.strip()[:300] or f"agy returned nothing (exit {code}).")


def fit_argument(prompt: str, most: int = AGY_MAX_ARG) -> str:
    """The prompt as one command-line argument (agy takes it as -p's value only): one longer than
    `most` loses its middle, keeping the instructions at the top and the newest part at the end."""
    if len(prompt) <= most:
        return prompt
    head = most * 2 // 3
    return prompt[:head] + CUT + prompt[len(prompt) - (most - head - len(CUT)):]


def _run(argv: list[str], timeout: float, cwd) -> tuple[int | None, str, str]:
    """(exit code, stdout, stderr), or (None, ...) when it would not start or ran out of time. On
    time out the whole process tree goes: a child of agy holding the pipe kept a read waiting (Windows)."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as error:
        return None, "", str(error)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return None, "", ""
    except (OSError, subprocess.SubprocessError) as error:
        return None, "", str(error)


def summarize(session: Session) -> Summary:
    """Ask for a summary (ask: Claude's MODEL, or agy's chain), cache it and return its entry, and
    whether the cache took it."""
    text = session_digest(session)
    size = session.path.stat().st_size
    summary = " ".join(ask(INSTRUCTIONS + "\n" + text, MODEL).split())
    entry = {"summary": summary, "size": size, "model": MODEL,
             "at": datetime.now().isoformat(timespec="seconds")}
    return Summary(entry, _store(session.session_id, entry))
