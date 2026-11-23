"""The team bus for a Codex member: its messages put into its thread with `codex queue`.

NOT TRIED against a real Codex session yet (no subscription to try it with): built from
`codex queue --help` (codex-cli 0.166.0) and one call without an account, which failed on the
thread as expected.

Codex has no background command that wakes an idle session, so the member's `tabmancer team run`
wrapper watches the bus beside it (watch) and queues what arrives into the member's thread:
`codex queue --thread <id> --message <text> --resume`. --resume asks Codex to start the turn;
when it cannot, Codex warns and the message waits for the thread's next turn. A message goes as
one command-line argument, so a batch whose quoted form is long goes as "N unread messages: call
team_inbox", and so does every batch after a codex queue that failed (until one goes through).
A message is marked read only when codex queue took it (exit 0); a summary marks nothing
(team_inbox does). Delivery is at least once: a message queued just before Codex or this wrapper
stops can come again by team_inbox. A failure is tried again later and later (AFTER_FAILURE,
doubling to MOST_BACKOFF), and so is the look for the member's thread while it has none. What
happened last is in the member's presence file (`delivery`), for who and the dashboard.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from . import teambus

MAX_INLINE = 4000  # characters a message batch goes as, quoted, at most: cmd.exe takes 8191 in all
POLL = 2.0  # seconds between looks at the bus
AFTER_FAILURE = 30.0  # seconds before a failed codex queue is tried again, doubling each time
MOST_BACKOFF = 600.0  # seconds between tries, at most (a failure, or no thread yet)
TIMEOUT = 60


def _render(messages: list[dict]) -> str:
    lines = "\n".join(f"[{m['at']:%H:%M:%S} from {m.get('from')}]: {m.get('text')}" for m in messages)
    return (f"Messages from your team (tabmancer team bus):\n{lines}\n"
            "Answer with the team_send tool where one is needed.")


def _summary(messages: list[dict]) -> str:
    return (f"{len(messages)} unread messages from your team (tabmancer team bus): call the team_inbox tool "
            "with ack: true to read them, then answer with team_send where one is needed.")


def _quoted_length(exe: str, text: str) -> int:
    """How long the text is on the command line, quoted as _command quotes it."""
    if Path(exe).suffix.lower() in (".cmd", ".bat"):
        from .launch import _for_cmd

        return len(_for_cmd(text))
    return len(subprocess.list2cmdline([text]))


def text_for(messages: list[dict], exe: str = "codex", brief: bool = False) -> tuple[str, bool]:
    """(what codex queue sends, whether it is the messages themselves). Too long once quoted for
    the command line, or brief: a note to read them with team_inbox instead."""
    text = _render(messages)
    if not brief and _quoted_length(exe, text) <= MAX_INLINE:
        return text, True
    return _summary(messages), False


def thread_of(team_name: str, member: str) -> str:
    """The member's Codex thread id: the bus's binding, else its session in ~/.codex (the
    [tabmancer:<team>-<member>] tag of its first prompt, in the team's folder, since the team began),
    bound from then on. '' while it has none yet."""
    bound = teambus.read_bindings(team_name).get(member) or {}
    if bound.get("cli") == "codex" and bound.get("session_id"):
        return str(bound["session_id"])
    from . import codex, teams

    team = teams.load().get(team_name)
    if team is None:
        return ""
    found = teams.member_sessions(team, codex.find(days=2), live={}).get(member)
    if found is None or found.cli != "codex" or found.session_id.startswith("codex:"):
        return ""
    if (teambus.read_bindings(team_name).get(member) or {}).get("session_id") != found.session_id:
        teambus.save_binding(team_name, member, found.session_id, cli="codex", created=team.created)
    return found.session_id


def _command(exe: str, args: list[str]) -> str | list[str]:
    """npm's codex.cmd goes through cmd.exe, which reads & | < > % in a message: quoted for it, as
    for npm's claude.cmd (launch._for_cmd)."""
    if Path(exe).suffix.lower() in (".cmd", ".bat"):
        from .launch import _for_cmd

        return subprocess.list2cmdline([exe]) + " " + " ".join(_for_cmd(arg) for arg in args)
    return [exe, *args]


def push(team_name: str, member: str, thread: str, already: set[str],
         run: Callable[..., subprocess.CompletedProcess] = subprocess.run, brief: bool = False) -> tuple[bool, str]:
    """Queue the member's unread messages into its thread. (tried, what happened): tried is False
    when there was nothing new to send. already: ids sent as a summary, not sent again. brief:
    a summary, whatever the length (after a failure)."""
    created, _ = teambus._read_team_file(team_name)
    waiting = teambus.unread(team_name, member, since=created)
    if not [m for m in waiting if m["id"] not in already]:
        return False, ""
    exe = shutil.which("codex")
    if exe is None:
        return True, "codex is not on PATH: the messages wait for team_inbox"
    text, inline = text_for(waiting, exe, brief)
    try:
        done = run(_command(exe, ["queue", "--thread", thread, "--message", text, "--resume"]),
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
                   check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as problem:
        return True, f"codex queue failed: {problem}"
    said = " ".join(((done.stderr or "") + " " + (done.stdout or "")).split())
    if done.returncode != 0:
        return True, f"codex queue failed ({done.returncode}): {said[-200:] or 'no output'}"
    ids = [m["id"] for m in waiting]
    if inline:
        teambus.mark_delivered(team_name, member, ids)
    else:
        already.update(ids)
    how = "queued" if inline else f"told of {len(ids)} waiting"
    if "warn" in said.lower() and "resume" in said.lower():  # exit 0 even when the resume failed
        return True, f"{how}; Codex could not start the turn, so it waits for the next one"
    return True, how


def _failed(note: str) -> bool:
    return "failed" in note or "not on PATH" in note


def watch(team_name: str, member: str, stop: threading.Event,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run, poll: float = POLL) -> None:
    """Beside a Codex member's CLI (`team run`): until stop, queue what arrives for it. Its thread
    is looked for less and less often while it has none (Codex writes it with the first turn); a
    failure sends summaries from then on, tried later and later, until one goes through."""
    thread, already, rest = "", set(), poll
    failures = misses = 0
    while not stop.wait(rest):
        rest = poll
        try:
            thread = thread or thread_of(team_name, member)
            if not thread:
                misses += 1
                rest = min(poll * 2 ** min(misses, 10), MOST_BACKOFF)
                continue
            misses = 0
            tried, note = push(team_name, member, thread, already, run, brief=failures > 0)
        except Exception as problem:  # noqa: BLE001  (the member's CLI goes on whatever this does)
            tried, note = True, f"delivery failed: {problem}"
        if not tried:
            continue
        teambus.update_presence(team_name, member, delivery=note)
        if _failed(note):
            failures += 1
            rest = min(AFTER_FAILURE * 2 ** min(failures - 1, 10), MOST_BACKOFF)
        else:
            failures = 0
