"""Rename a session, the way /rename does inside it.

A closed Claude Code session is renamed by running /rename in it once, without a tab
(`claude -p --resume <id>`). Claude writes the new name into its own transcript, so its
own /resume list shows it too, and no model is asked. An open session has to run /rename
itself: renamed from outside, it would write its old name back after its next turn.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .launch import clean_env
from .sessions import Session


def command(name: str) -> str:
    return f"/rename {' '.join(name.split())}"


def rename(session: Session, name: str) -> None:
    """Rename a closed session. Raises OSError if Claude did not."""
    claude = shutil.which("claude")
    if not claude:
        raise OSError("the claude CLI is not on PATH")
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
