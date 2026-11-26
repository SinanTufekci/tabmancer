"""What the clean-up screen says and what its keys do, with no UI behind it: the line about the sessions on
disk, each row, the picked one's detail, the picks (Space, o, a), the question before moving and what moving
came to. Parts are (words, style) as in teamwords. The terminal's screen (cleanup.py, CleanupApp) puts them
into rich Text, the desktop window into its own (desktop/cleanup.py); both say the same. Measuring and
moving are diskclean's: an open session is never picked, and which are open is asked again as they move."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from .actions import Note
from .diskclean import BIN, Item, claude_cleanup_days, disk_size, human, older_than
from .teamwords import Part

TITLE = "tabmancer: clean up"
OLDER_DAYS = 7  # o picks the closed sessions last active longer ago than this
MEASURING = "Measuring sessions…"
NONE_ON_DISK = "No sessions on disk."
OPEN_NOW = "Open right now, so it cannot be deleted."
SELECT_FIRST = Note("Select sessions with Space first.", "warning")
MOVE_WORDS = f"Move to {BIN}"
KEY_PAIRS = [("Space", "select"), ("o", f"older than {OLDER_DAYS} days"), ("a", "all/none"),
             ("Enter", f"move to the {BIN}"), ("Esc", "close")]
ASK_KEYS = [("y", "yes"), ("n", "no"), ("Esc", "cancel")]
COLUMNS = [("sel", ""), ("project", "Project"), ("title", "Title"), ("active", "Last active"), ("size", "Size")]


@dataclass(frozen=True)
class Ask:
    """Enter with sessions picked: the ones to move, and the question asked first."""

    chosen: list[Item]
    question: str


def status(items: Collection[Item], selected: Collection[str]) -> str:
    """'12 sessions use 1.2 GB · 2 selected (300 MB) · Claude Code deletes sessions older than 30 days on its
    own'."""
    total = sum(item.size for item in items)
    chosen = sum(item.size for item in items if item.session.session_id in selected)
    return (f"{len(items)} sessions use {human(total)} · {len(selected)} selected ({human(chosen)}) · Claude Code "
            f"deletes sessions older than {claude_cleanup_days()} days on its own")


def row_parts(item: Item, selected: Collection[str]) -> list[Part]:
    """The row's cells, as COLUMNS: the mark ('[x]', '[ ]', or ' · ' for an open one, which is never picked),
    the project, the title, the day it was last active and its size. An open one is dim."""
    s = item.session
    mark = (" · ", "dim") if item.is_open else ("[x]" if s.session_id in selected else "[ ]", "")
    dim = "dim" if item.is_open else ""
    return [mark, (s.project[:22], dim), (s.title[:52], dim), (f"{s.last_active:%Y-%m-%d}", "dim"),
            (human(item.size), "bold")]


def detail_parts(item: Item | None) -> list[Part]:
    """The picked one: its title, folder, when it was last active, its first prompt, each file it keeps on
    disk and how big, and that an open one cannot be moved."""
    if item is None:
        return [(NONE_ON_DISK, "")]
    s = item.session
    out: list[Part] = [(s.title + "\n", "bold"), (s.cwd, "cyan"), (f"\nLast active {s.last_active:%d %b %Y %H:%M}", "")]
    if s.first_prompt:
        out += [("\nFirst prompt  ", "bold"), (s.first_prompt[:200], "")]
    out.append(("\n\nOn disk:", "bold"))
    out += [(f"\n  {human(disk_size(path)):>9}  {path}", "dim") for path in item.files]
    if item.is_open:
        out.append((f"\n\n{OPEN_NOW}", "yellow"))
    return out


def toggled(items: dict[str, Item], selected: set[str], sid: str) -> set[str]:
    """Space on a row: picked, or no longer; an open one (or one gone) stays as it is."""
    item = items.get(sid)
    return selected if item is None or item.is_open else selected ^ {sid}


def with_older(items: dict[str, Item], selected: set[str], now: datetime | None = None) -> set[str]:
    """o: the closed ones last active more than OLDER_DAYS ago picked too."""
    return selected | {item.session.session_id for item in older_than(list(items.values()), OLDER_DAYS, now)}


def all_or_none(items: dict[str, Item], selected: set[str]) -> set[str]:
    """a: every closed one picked, or none when they all are."""
    closed = {sid for sid, item in items.items() if not item.is_open}
    return set() if closed <= selected else closed


def ask(items: dict[str, Item], selected: Collection[str]) -> Ask | Note:
    """Enter: what is to move and the question asked first, or the note that nothing is picked."""
    chosen = [items[sid] for sid in selected if sid in items]
    if not chosen:
        return SELECT_FIRST
    size = human(sum(item.size for item in chosen))
    return Ask(chosen, f"Move {len(chosen)} sessions ({size}) to the {BIN}?\n"
                       "They disappear from Claude Code's /resume until restored.")


def moved_notes(moved: list[Item], problems: list[str]) -> list[Note]:
    """What moving came to: how many and how much, then the first few problems, as plain words (a path and
    an OS error, never markup)."""
    freed = human(sum(item.size for item in moved))
    return ([Note(f"Moved {len(moved)} sessions ({freed}) to the {BIN}.", seconds=8)]
            + [Note(problem, "error", 10) for problem in problems[:5]])


def after_move(items: dict[str, Item], selected: set[str], moved: list[Item]) -> tuple[list[Item], set[str]]:
    """The list once these moved: the rest, in order, and the picks left."""
    gone = {item.session.session_id for item in moved}
    return [item for sid, item in items.items() if sid not in gone], selected - gone
