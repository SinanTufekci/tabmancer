"""Handing a crowded session over to a fresh one, when you decide to.

A session whose context has filled up gets slow, costly and forgetful. A handover moves
the work to a fresh session without losing the thread. A full handover text becomes the
fresh session's first message; it is not written to a file. The text comes from one of
two places:

- the old session itself, which knows everything: it is resumed once without a tab
  (`claude -p --resume`), writes the handover and exits;
- or Claude, writing it from the transcript with `claude -p`.

An open session is closed on the way (its tab is closed, so Claude exits cleanly), and
only while it waits for you: one that is working is left alone. It stays resumable.

Nothing happens on its own: the dashboard only says when a session has grown crowded.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import focus, summarize, teams
from .launch import Launch, Mode, clean_env
from .sessions import Session, _registry_sessions, _shorten

MODEL = os.environ.get("TABMANCER_HANDOVER_MODEL", "sonnet")
WRITE_SECONDS = 900  # a crowded session reads its whole context back before it writes
CLOSE_SECONDS = 15
IDLE = {"idle", "done"}  # live states in which a session waits for you
AGAIN = "Close it yourself (/exit), then press h again"  # when an open session cannot be closed
# The handover travels on the command line of the new tab, which Windows caps at 32K characters.
MAX_CHARS = 20_000
START, END = "<handover>", "</handover>"
REQUEST = (
    "We are handing this work over to a fresh session that will not see this conversation. Write the "
    "handover now, complete enough for it to carry on without asking anything: the goal; the decisions "
    "made and why; what is done, with the files; what is open or broken; the gotchas you ran into; and the "
    "exact next steps, with any commands to run. At most 1,200 words. Write it in the language we have been "
    f"using. Put the whole handover between {START} and {END}, and write nothing after it.")
INSTRUCTIONS = """Write a handover from the notes below on a coding-agent session, for a fresh session
that will carry on the work without the old conversation. Markdown with these sections: Goal,
Decisions, Done (with the files), Open or broken, Gotchas, Next steps. Concrete, complete, at
most 500 words, in the same language as the user's messages. Only what the notes support. No
preamble. Do not use any tools.
"""


def opening(session: Session) -> str:
    return (f"You are taking over from an earlier session in this folder ({session.last_active:%d %b %H:%M}, "
            f"“{_shorten(session.title, 60)}”), which had grown too long. Its handover follows. Read it, check "
            "the current state of the files, then carry on.")


def from_transcript(session: Session) -> str:
    """Claude writes the handover from the transcript (one claude -p call)."""
    if session.cli != "claude":
        notes = summarize.session_digest(session)
    else:
        notes = summarize.digest(session, max_prompts=80, keep_replies=12)
        made = commits(session.path)
        if made:  # the surest record of what got done
            notes += "\n\nCommits made, oldest first:\n" + "\n".join(f"- {m}" for m in made[-40:])
    return summarize.ask(INSTRUCTIONS + "\n" + notes, MODEL, timeout=300).strip()


COMMIT = re.compile(r"""git\s+commit\b[^\n]*?-m\s+(?:"([^"]+)"|'([^']+)'|@'\s*\n([^\n]+))""")


def commits(path: Path | None) -> list[str]:
    """The subject lines of the commits the session made, from its shell tool calls."""
    if path is None:
        return []
    found: list[str] = []
    try:
        with path.open("rb") as f:
            for raw in f:
                if b"git commit" not in raw or b'"type":"assistant"' not in raw:
                    continue
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                content = (record.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    command = (block.get("input") or {}).get("command") if isinstance(block, dict) else None
                    if block.get("type") == "tool_use" and isinstance(command, str):
                        found += [next(g for g in match.groups() if g).strip()
                                  for match in COMMIT.finditer(command)]
    except OSError:
        return found
    return list(dict.fromkeys(found))


class HandoverError(Exception):
    pass


def cannot_close(session: Session, open_sessions: list[Session], again: str = AGAIN) -> str | None:
    """Why this open session cannot be closed now; None if it can. `again` says what to do instead."""
    if session.live_state not in IDLE:
        return "it is working. Hand it over once it waits for you"
    if not session.title or sum(s.title == session.title for s in open_sessions if s.cli == "claude") > 1:
        return f"another open session has the same title, so its tab cannot be told apart. {again}"
    return None


def close(session: Session, open_sessions: list[Session], again: str = AGAIN) -> None:
    """Close an open session by closing its Windows Terminal tab, and wait until it has exited."""
    reason = cannot_close(session, open_sessions, again)
    if reason:
        raise HandoverError(reason)
    if not focus.close_tab(session.title):
        raise HandoverError(f"its tab was not found in Windows Terminal. {again}")
    deadline = time.monotonic() + CLOSE_SECONDS
    while time.monotonic() < deadline:
        if not any(info.get("sessionId") == session.session_id for info in _registry_sessions()):
            return
        time.sleep(0.5)
    raise HandoverError("its tab closed, but Claude did not exit")


def write_itself(session: Session) -> str:
    """The session writes its own handover: resumed once without a tab, it answers and exits."""
    claude = shutil.which("claude")
    if not claude:
        raise HandoverError("the claude CLI is not on PATH")
    if not session.cwd or not Path(session.cwd).is_dir():
        raise HandoverError(f"its folder is gone ({session.cwd})")
    model = ["--model", session.model] if session.model else []
    try:
        # The request goes in on stdin: through a claude.cmd shim, cmd.exe would take < and > for redirects.
        result = subprocess.run(
            [claude, "-p", "--resume", session.session_id, *model], input=REQUEST, cwd=session.cwd,
            env=clean_env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=WRITE_SECONDS, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise HandoverError(f"it was still writing after {WRITE_SECONDS // 60} minutes") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise HandoverError(str(error)) from error
    answer = result.stdout.strip()
    if result.returncode != 0 or not answer:
        raise HandoverError(result.stderr.strip()[:300] or "claude returned nothing")
    if START in answer:
        answer = answer.split(START, 1)[1].split(END, 1)[0].strip()
    return answer


def fresh(session: Session, handover: str) -> Launch:
    """The fresh session: same folder and CLI, the handover as its first message, and for a team
    member the same name and role."""
    if len(handover) > MAX_CHARS:
        handover = (handover[:MAX_CHARS].rsplit("\n", 1)[0]
                    + "\n\n(The handover was cut here: it was too long to send. Ask for the rest if you need it.)")
    prompt = f"{opening(session)}\n\n{handover}"
    role = teams.member_of(session)
    if role is not None:
        team, member = role
        return teams.launch(team, member, prompt=prompt)
    return Launch(title=f"{session.project}: handed over", cwd=session.cwd, mode=Mode.NEW, session_id="",
                  prompt=prompt, cli=session.cli)
