"""Workspaces: named sets of project folders that open together.

A workspace remembers folders, not session ids. Ids go stale as soon as you /clear
or start a new session, so opening a workspace continues the most recent session
in each folder instead.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import picks, teams
from .config import read_json, write_json
from .launch import Launch, Mode, default_mode
from .sessions import Exit, Session

FILE = "workspaces.json"

CONTINUE = "continue"  # the folder's most recent session
NEW = "new"            # a fresh session in the folder


@dataclass
class Entry:
    cwd: str
    mode: str = CONTINUE


@dataclass
class Workspace:
    name: str
    entries: list[Entry] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return ", ".join(Path(e.cwd).name or e.cwd for e in self.entries)


def clean_name(name: str) -> str:
    """Workspace names end up in file names and wt.exe arguments; keep them plain."""
    return re.sub(r'[<>:"/\\|?*;]', "-", name).strip(" .")


def load() -> dict[str, Workspace]:
    data = read_json(FILE)
    workspaces = {}
    for item in data.get("workspaces", []) if isinstance(data, dict) else []:
        try:
            entries = [Entry(e["cwd"], e.get("mode", CONTINUE)) for e in item["entries"]]
            workspaces[item["name"]] = Workspace(item["name"], entries)
        except (KeyError, TypeError):
            continue
    return workspaces


def save(workspaces: dict[str, Workspace]) -> None:
    write_json(FILE, {"workspaces": [asdict(w) for w in workspaces.values()]}, indent=2)


def find(workspaces: dict[str, Workspace], name: str) -> Workspace | None:
    return workspaces.get(name) or next(
        (w for w in workspaces.values() if w.name.casefold() == name.casefold()), None)


def from_sessions(name: str, sessions: list[Session], modes: dict[str, Mode]) -> Workspace:
    """Picked sessions become their folders: continued, unless the row was set to new."""
    return Workspace(clean_name(name), [
        Entry(s.cwd, NEW if modes.get(s.session_id) is Mode.NEW else CONTINUE) for s in sessions])


def _folder_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def resolve(workspace: Workspace, sessions: list[Session]) -> tuple[list[Launch], list[str]]:
    """What to open for a workspace, plus notes on folders that were skipped.

    sessions must be newest first. A folder listed k times continues its k most
    recent sessions. Sessions already open count towards k, so opening a workspace
    a second time does not open the same session twice.
    """
    folders: dict[str, list[Entry]] = {}
    for entry in workspace.entries:
        folders.setdefault(_folder_key(entry.cwd), []).append(entry)

    launches: list[Launch] = []
    notes: list[str] = []
    saved = teams.load()  # a team member resumed comes back as that member
    for key, entries in folders.items():
        cwd = entries[0].cwd
        name = Path(cwd).name or cwd
        if not Path(cwd).is_dir():
            notes.append(f"{name}: folder not found ({cwd})")
            continue
        here = [s for s in sessions if s.cwd and _folder_key(s.cwd) == key]
        wanted = sum(e.mode == CONTINUE for e in entries)
        # Open in a tab or in the background (claude --bg): either way a process writes it, and a
        # background one taken for closed was resumed a second time.
        missing = max(0, wanted - sum(s.exit is Exit.RUNNING for s in here))
        if wanted and not missing:
            notes.append(f"{name}: already open")
        resumable = [s for s in here if s.exit is not Exit.RUNNING][:missing]
        launches += [picks.resumed(teams.as_member(s, Launch(f"{name}: {s.title}", s.cwd, default_mode(s.status),
                                                             s.session_id, cli=s.cli), saved)) for s in resumable]
        for _ in range(missing - len(resumable)):
            # Nothing recent enough in the list. claude --continue finds older ones,
            # but only if no session in the folder is open (it could pick that one).
            mode = Mode.CONTINUE if not here and not launches_in(launches, key) else Mode.NEW
            launches.append(Launch(f"{name}: {mode.value}", cwd, mode, ""))
        launches += [Launch(f"{name}: new", cwd, Mode.NEW, "") for e in entries if e.mode == NEW]
    return launches, notes


def launches_in(launches: list[Launch], key: str) -> bool:
    return any(_folder_key(launch.cwd) == key for launch in launches)
