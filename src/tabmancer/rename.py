"""Rename a session, the way /rename does inside it.

A closed Claude Code session is renamed by running /rename in it once, without a tab
(`claude -p --resume <id>`). Claude writes the new name into its own transcript, so its
own /resume list shows it too, and no model is asked. An open session has to run /rename
itself: renamed from outside, it would write its old name back after its next turn.

What r says, with no UI behind it, is here too: the terminal dashboard and the desktop window
both say it (refusal, heading, enter_words and the notes).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import clis, teams
from .actions import Note, Told, clip
from .launch import clean_env
from .sessions import Exit, Session, Status

TITLE_CHARS = 60  # the session's title in the rename dialog's heading, clipped
PLACEHOLDER = "a new name"
OPEN_WORDS = "It is open, so it renames itself."
FAILED_SECONDS = 10.0


def refusal(session: Session) -> str | None:
    """Why r does not rename this one (said as a warning), or None. Only Claude Code has /rename; a team
    member's name is how its team finds it; a background session has no tab to paste into."""
    if session.cli != "claude":
        return f"Only Claude Code sessions can be renamed: {clis.label(session.cli)} has no /rename."
    if teams.member_of(session) is not None:
        return "Team members keep their names: the team finds them by it."
    if session.status is Status.BACKGROUND:
        return "A background session renames itself: attach to it (B) and type /rename there."
    return None


def heading(session: Session) -> str:
    return f"Rename {session.project} · “{clip(session.title, TITLE_CHARS)}”"


def enter_words(session: Session) -> str:
    """What Enter does in the rename dialog: an open one renames itself, a closed one is renamed here."""
    if session.exit is Exit.RUNNING:
        return "copy /rename and go to its tab, to paste"
    return "rename it, in Claude Code's own list too"


def open_told(session: Session, name: str) -> Told:
    """An open one gets the command to paste into its own tab (the front end puts it on the clipboard and
    brings the tab forward)."""
    return Told(f"{session.project} is open, so it renames itself: /rename is on the clipboard, paste it into "
                "its tab (Ctrl+V, Enter).", seconds=12, title="Rename", clipboard=command(name), go_to=session)


def failed_note(session: Session, error: object) -> Note:
    return Note(f"{session.project} was not renamed: {error}", "error", FAILED_SECONDS)


def renamed_note(title: str) -> Note:
    return Note(f"Renamed to “{title}”.", seconds=5)


def command(name: str) -> str:
    return f"/rename {' '.join(name.split())}"


def rename(session: Session, name: str) -> None:
    """Rename a closed session. Raises OSError if Claude did not."""
    claude = shutil.which("claude")
    if not claude:
        raise OSError("renaming is Claude Code's own (/rename), and the claude CLI is not on PATH")
    cwd = session.cwd if session.cwd and Path(session.cwd).is_dir() else str(Path.home())
    try:
        # On stdin: through a claude.cmd shim, cmd.exe would act on & | < > in the name.
        result = subprocess.run(
            [claude, "-p", "--resume", session.session_id], input=command(name), cwd=cwd, env=clean_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.SubprocessError as error:
        raise OSError(str(error)) from error
    if result.returncode != 0 or not result.stdout.strip().startswith("Session renamed"):
        raise OSError((result.stderr or result.stdout).strip()[:300] or "claude did not rename it")
