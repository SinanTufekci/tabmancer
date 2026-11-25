"""Sessions kept out of sight or pinned to the top, without touching them.

Hiding a session leaves its transcript where it is: it only drops out of the list (and the
counts), and X shows it again. A whole project can be hidden the same way, test folders
say. Pinned sessions stand together above the rest. Open sessions always show.

Kept in marks.json in the config folder: session ids, and the folders of hidden projects. Every
dashboard and window changes it through change(): what another one changed since this one read it
stays, and a file that is there but could not be read, or a change that could not be written, leaves it
as it was (Refused: UNREAD, UNSAVED).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from . import viewmodel as VM
from .config import config_dir, read_json, write_json
from .sessions import Exit, Session, folder_key

FILE = "marks.json"
OPEN_ALWAYS_SHOW = "Open sessions always show: close one to hide it."
T = TypeVar("T")


@dataclass(frozen=True)
class Refused:
    """Why change() left marks.json as it was: the words the key says."""

    words: str


UNREAD = Refused("marks.json could not be read: nothing changed.")
UNSAVED = Refused("marks.json could not be saved: nothing changed.")


@dataclass
class Marks:
    hidden: set[str] = field(default_factory=set)    # session ids
    projects: set[str] = field(default_factory=set)  # folder keys of hidden projects
    pinned: set[str] = field(default_factory=set)    # session ids

    @classmethod
    def load(cls) -> Marks:
        """At start-up: the marks as read(), or none when it found nothing it could use."""
        found = cls.read()
        return found if found is not None else cls()

    @classmethod
    def read(cls, tries: int | None = None) -> Marks | None:
        """marks.json as it is now; none at all when there is no such file. None when it is there but
        could not be read (another process held it too long, or it is not readable) or holds no dict:
        whatever is saved over that would wipe every pin and hide it keeps, as config.merge_json refuses.
        tries: as config.read_json takes it (1 on a UI thread: a held file is no wait, and read again at
        the next poll)."""
        data = read_json(FILE, tries)
        if not isinstance(data, dict):
            if os.path.lexists(config_dir() / FILE):
                return None
            data = {}

        def ids(key: str) -> set[str]:
            value = data.get(key)
            return {str(v) for v in value} if isinstance(value, list) else set()

        return cls(ids("hidden"), ids("projects"), ids("pinned"))

    def save(self) -> bool:
        """Written whole or not at all (config.write_json); False when it could not be (another process
        held it too long, or the folder is read-only), the file as it was."""
        try:
            write_json(FILE, {"hidden": sorted(self.hidden), "projects": sorted(self.projects),
                              "pinned": sorted(self.pinned)}, indent=2)
        except OSError:
            return False
        return True

    def is_hidden(self, session: Session) -> bool:
        """Out of sight: hidden itself or in a hidden project, and not open."""
        if session.exit is Exit.RUNNING:
            return False
        return session.session_id in self.hidden or folder_key(session.cwd) in self.projects

    def is_pinned(self, session: Session) -> bool:
        return session.session_id in self.pinned


@dataclass
class Changed:
    """What a key did to the marks: the words that say it, how long they stay, and the sessions it
    took out of sight (to untick: Enter would open a session the list no longer shows)."""

    words: str
    seconds: float = 4
    hid: set[str] = field(default_factory=set)


def change(how: Callable[[Marks], T]) -> tuple[Marks, T] | Refused:
    """marks.json changed as it is now, not as this dashboard read it last (another one, or a window,
    may have changed it since): read, how(marks), saved. The marks as saved, and what how gave. Refused,
    for the caller to say, when the file stays as it was: UNREAD, how never called, when it is there but
    could not be read (Marks.read); UNSAVED when the change could not be written."""
    marks = Marks.read()
    if marks is None:
        return UNREAD
    done = how(marks)
    if not marks.save():
        return UNSAVED
    return marks, done


def counted(sessions: list[Session]) -> str:
    """One session by its title, more by how many."""
    return f"“{sessions[0].title}”" if len(sessions) == 1 else f"{len(sessions)} sessions"


def pin(marks: Marks, chosen: list[Session]) -> Changed:
    """f: these pinned above the rest; unpinned when all of them were."""
    marks.pinned, pinned = VM.toggle_pins(marks.pinned, {s.session_id for s in chosen})
    return Changed(f"Pinned {counted(chosen)} above the rest." if pinned else f"Unpinned {counted(chosen)}.", 3)


def hide(marks: Marks, chosen: list[Session]) -> Changed:
    """x: these out of sight (the open ones always show); back in sight, their projects with them,
    when all of them were hidden."""
    chosen = [s for s in chosen if s.exit is not Exit.RUNNING]
    if not chosen:
        return Changed(OPEN_ALWAYS_SHOW)
    if all(marks.is_hidden(s) for s in chosen):
        for session in chosen:
            marks.hidden.discard(session.session_id)
            marks.projects.discard(folder_key(session.cwd))  # its project comes back with it
        return Changed(f"Back in sight: {counted(chosen)}.")
    ids = {s.session_id for s in chosen}
    marks.hidden |= ids
    return Changed(f"Hid {counted(chosen)}. Nothing is deleted: X shows hidden ones, and x on one brings it back.",
                   6, ids)


def hide_project(marks: Marks, key: str, sessions: list[Session]) -> Changed:
    """x on a project (key: its folder key; sessions: those in it): out of sight, its open ones aside;
    back in sight when it was hidden."""
    name = sessions[0].project if sessions else key
    if key in marks.projects:
        marks.projects.discard(key)
        return Changed(f"The project {name} is back in sight.")
    marks.projects.add(key)
    return Changed(f"Hid the project {name} ({counted(sessions)}; open ones still show). X shows it again, and x "
                   "on it brings it back.", 6, {s.session_id for s in sessions if s.exit is not Exit.RUNNING})
