"""What the dashboard says about workspaces (named sets of project folders that open together:
workspaces.py), with no UI behind it: w's name prompt, o's list and its keys, and the notes as one is
saved, deleted or opened. Parts are (words, style) as in teamwords. The terminal dashboard (app) puts
them into rich Text, the desktop window into its own; both say the same. Kept apart from workspaces.py,
which `tabmancer open` loads without the dashboard's notes (actions)."""

from __future__ import annotations

from .actions import Note
from .palette import MUTED, TEXT
from .teamwords import Part
from .workspaces import Workspace

TITLE = "Workspaces"  # o's list
PLACEHOLDER = "e.g. PCB work"  # w's name
NAME_KEYS = [("Enter", "save"), ("Esc", "cancel")]
LIST_KEYS = [("Enter", "open"), ("d", "delete"), ("Esc", "close")]


def tick_first_note() -> Note:
    """w with nothing ticked."""
    return Note("Tick the sessions for the workspace first (Space).", severity="warning")


def none_yet_note() -> Note:
    """o with no workspace saved."""
    return Note("No workspaces yet. Tick sessions and press w to save one.", severity="warning")


def name_heading(count: int) -> str:
    """w's question, for count sessions ticked."""
    what = "this session's folder" if count == 1 else f"these {count} sessions' folders"
    return f"Name for a workspace with {what}"


def option_parts(workspace: Workspace) -> list[Part]:
    """A workspace in o's list: its name, then its folders."""
    return [(workspace.name, f"bold {TEXT}"), ("   ", ""), (workspace.summary, MUTED)]


def saved_note(workspace: Workspace) -> Note:
    return Note(f"Saved workspace '{workspace.name}': {workspace.summary}")


def deleted_note(name: str) -> Note:
    return Note(f"Deleted workspace '{name}'.")


def not_kept_note(name: str, deleting: bool = False) -> Note:
    """workspaces.keep or drop refused: the file left as it is."""
    return Note(f"Workspace '{name}' not {'deleted' if deleting else 'saved'}: workspaces.json could not be "
                "read or written, or another tabmancer was changing it, so it was left as it is.",
                severity="error", seconds=12)


def nothing_note(notes: list[str]) -> Note:
    """o found nothing to open: why, folder by folder (workspaces.resolve's notes)."""
    return Note("\n".join(notes) or "Nothing to open.", severity="warning")
