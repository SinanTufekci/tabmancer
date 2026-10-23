"""Where each project's folder stands in git right now: files changed and not committed,
commits not pushed, commits to pull. Read with `git status --porcelain=v2 --branch`, which
only looks at the folder (no network: "behind" is as of the last fetch).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .sessions import folder_key

# Seconds for one folder. With 5, most folders came back without an answer once, at a first
# start, while the token count and the search index read every transcript at once.
TIMEOUT = 20
FAILED = "failed"  # read() could not tell: slow or broken, not "no repository"


@dataclass(frozen=True)
class GitState:
    branch: str = ""
    changed: int = 0  # files changed, staged or not, untracked included
    ahead: int = 0    # commits not pushed
    behind: int = 0   # commits to pull, as of the last fetch
    upstream: bool = False  # the branch follows a remote one

    def words(self) -> str:
        """'4 changed · 1 to push · 2 to pull', or 'clean'."""
        out = [f"{self.changed} changed"] if self.changed else []
        if self.ahead:
            out.append(f"{self.ahead} to push")
        if self.behind:
            out.append(f"{self.behind} to pull")
        if not self.upstream:
            out.append("no upstream")
        return " · ".join(out) or "clean"


def parse(output: str) -> GitState:
    branch, ahead, behind, upstream, changed = "", 0, 0, False, 0
    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = True
        elif line.startswith("# branch.ab "):
            for part in line.split()[2:]:
                if part.startswith("+"):
                    ahead = int(part[1:] or 0)
                elif part.startswith("-"):
                    behind = int(part[1:] or 0)
        elif line[:2] in ("1 ", "2 ", "u ", "? "):  # changed, renamed, unmerged, untracked
            changed += 1
    return GitState(branch, changed, ahead, behind, upstream)


def read(folder: str) -> GitState | str | None:
    """The folder's state; None if it is not in a git repository (or git is missing), FAILED if
    git could not answer in time."""
    git = shutil.which("git")
    if not git or not folder or not Path(folder).is_dir():
        return None
    try:
        result = subprocess.run([git, "-C", folder, "status", "--porcelain=v2", "--branch"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, check=False,
                                env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),  # its messages in English
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return FAILED
    if result.returncode == 0:
        return parse(result.stdout)
    return None if "not a git repository" in result.stderr else FAILED


def read_all(folders: list[str]) -> tuple[dict[str, GitState], set[str]]:
    """(folder key -> state for the folders in git, the keys git could not answer for); several
    folders read at once."""
    unique = {folder_key(f): f for f in folders if f}
    with ThreadPoolExecutor(max_workers=8) as pool:
        states = dict(zip(unique, pool.map(read, unique.values())))
    return ({key: state for key, state in states.items() if isinstance(state, GitState)},
            {key for key, state in states.items() if state == FAILED})
