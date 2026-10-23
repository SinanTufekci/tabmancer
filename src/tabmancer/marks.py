"""Sessions kept out of sight or pinned to the top, without touching them.

Hiding a session leaves its transcript where it is: it only drops out of the list (and the
counts), and X shows it again. A whole project can be hidden the same way, test folders
say. Pinned sessions stand together above the rest. Open sessions always show.

Kept in marks.json in the config folder: session ids, and the folders of hidden projects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import read_json, write_json
from .sessions import Exit, Session, folder_key

FILE = "marks.json"


@dataclass
class Marks:
    hidden: set[str] = field(default_factory=set)    # session ids
    projects: set[str] = field(default_factory=set)  # folder keys of hidden projects
    pinned: set[str] = field(default_factory=set)    # session ids

    @classmethod
    def load(cls) -> Marks:
        data = read_json(FILE)
        data = data if isinstance(data, dict) else {}

        def ids(key: str) -> set[str]:
            value = data.get(key)
            return {str(v) for v in value} if isinstance(value, list) else set()

        return cls(ids("hidden"), ids("projects"), ids("pinned"))

    def save(self) -> None:
        try:
            write_json(FILE, {"hidden": sorted(self.hidden), "projects": sorted(self.projects),
                              "pinned": sorted(self.pinned)}, indent=2)
        except OSError:
            pass

    def is_hidden(self, session: Session) -> bool:
        """Out of sight: hidden itself or in a hidden project, and not open."""
        if session.exit is Exit.RUNNING:
            return False
        return session.session_id in self.hidden or folder_key(session.cwd) in self.projects

    def is_pinned(self, session: Session) -> bool:
        return session.session_id in self.pinned
