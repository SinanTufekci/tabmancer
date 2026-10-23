"""Free disk space: move old Claude Code sessions to the Recycle Bin.

A session is more than its transcript: Claude Code also keeps a folder of subagent
transcripts and tool output next to it, plus file snapshots for rewind under
~/.claude/file-history. All of it goes to the Recycle Bin (Trash elsewhere), so a
mistake can be undone. Sessions that are open right now are never touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, Static

from . import summarize
from .dialogs import frame, title
from .sessions import (
    Exit,
    Session,
    _glob,
    _read_cached,
    claude_dir,
    live_sessions,
    mark_live,
)
from .theme import keys, use_saved_theme
from .widgets import TopBar

DEFAULT_CLEANUP_DAYS = 30  # Claude Code's own cleanupPeriodDays default


@dataclass
class Item:
    session: Session
    files: list[Path]
    size: int

    @property
    def is_open(self) -> bool:
        return self.session.exit is Exit.RUNNING


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def disk_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return 0


def session_files(session_id: str, transcript: Path) -> list[Path]:
    """Everything Claude Code keeps for one session."""
    root = claude_dir()
    candidates = [transcript, transcript.with_suffix(""),  # <project>/<id>/ holds subagents
                  root / "file-history" / session_id, root / "session-env" / session_id]
    return [p for p in candidates if p.exists()]


def claude_cleanup_days() -> int:
    """How long Claude Code itself keeps transcripts (cleanupPeriodDays)."""
    try:
        settings = json.loads((claude_dir() / "settings.json").read_text(encoding="utf-8"))
        return int(settings.get("cleanupPeriodDays", DEFAULT_CLEANUP_DAYS))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_CLEANUP_DAYS


def collect() -> list[Item]:
    """Every session on disk, biggest first, including ones with nothing to resume."""
    paths = _glob(claude_dir() / "projects", "*/*.jsonl")
    parsed = {s.path: s for s in _read_cached(paths)}
    live = live_sessions()
    items = []
    for path in paths:
        session = parsed.get(path)
        if session is None:
            try:
                stat = path.stat()
            except OSError:
                continue
            session = Session(session_id=path.stem, path=path, cwd=path.parent.name,
                              title="(no prompt: nothing to resume)", size=stat.st_size,
                              last_active=datetime.fromtimestamp(stat.st_mtime))
        mark_live(session, live.get(session.session_id))
        files = session_files(session.session_id, path)
        items.append(Item(session, files, sum(disk_size(f) for f in files)))
    items.sort(key=lambda item: item.size, reverse=True)
    return items


def _trash_one(path: Path) -> None:
    from send2trash import send2trash  # imported late: only deleting needs it

    send2trash(str(path))


def trash(items: list[Item]) -> tuple[list[Item], list[str]]:
    """Move sessions to the Recycle Bin. Returns the ones moved and any problems."""
    root = claude_dir().resolve()
    moved, problems = [], []
    for item in items:
        if item.is_open:
            problems.append(f"{item.session.project}: open right now, skipped")
            continue
        ok = True
        for path in item.files:
            if not path.resolve().is_relative_to(root):
                problems.append(f"{path}: outside {root}, skipped")
                ok = False
                continue
            try:
                _trash_one(path)
            except OSError as error:
                problems.append(f"{path}: {error}")
                ok = False
        if ok:
            moved.append(item)
    summarize.forget([item.session.session_id for item in moved])
    return moved, problems


class Confirm(ModalScreen[bool]):
    DEFAULT_CSS = frame("Confirm", "small", tone="$warning")  # amber: it is about to delete
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title(self.question))
            yield Label(keys(("y", "yes"), ("n", "no"), ("Esc", "cancel")), classes="hint")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


class CleanupApp(App[None]):
    TITLE = "tabmancer: clean up"
    # The dashboard's look: its black theme, top bar and preview panel.
    CSS = """
    Screen { background: $background; }
    ModalScreen { background: $background 65%; }
    #table { height: 1fr; margin: 1 1 0 1; background: $background; }
    #table > .datatable--header { background: $background; color: $text-muted; text-style: bold; }
    #detail { height: auto; max-height: 40%; margin: 1 1 0 1; background: $surface;
              border-top: tall $panel-lighten-2; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("space", "toggle", "Select"),
        Binding("o", "select_older", "Select older than 7 days"),
        Binding("a", "toggle_all", "All/none"),
        Binding("enter", "delete", "Move to Recycle Bin", priority=True),
        Binding("q,escape", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[str, Item] = {}
        self.selected: set[str] = set()

    def compose(self) -> ComposeResult:
        yield TopBar(live=False, id="topbar")
        yield DataTable(id="table", cursor_type="row", cursor_foreground_priority="renderable")
        yield Static("Measuring sessions…", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        use_saved_theme(self)
        self.topbar = self.query_one(TopBar)
        self.topbar.show_info(self.sub_title)
        self.table = self.query_one(DataTable)
        self.detail = self.query_one("#detail", Static)
        for key, label in [("sel", ""), ("project", "Project"), ("title", "Title"),
                           ("active", "Last active"), ("size", "Size")]:
            self.table.add_column(label, key=key)
        self.load()

    def watch_sub_title(self, sub_title: str) -> None:
        if self.is_mounted:
            self.topbar.show_info(sub_title)

    @work(thread=True)
    def load(self) -> None:
        items = collect()
        self.call_from_thread(self.show, items)

    def show(self, items: list[Item]) -> None:
        self.items = {item.session.session_id: item for item in items}
        self.selected &= set(self.items)
        self.table.clear()
        for item in items:
            self.table.add_row(*self.cells(item), key=item.session.session_id)
        self.update_status()

    def cells(self, item: Item) -> tuple[Text, ...]:
        s = item.session
        mark = Text(" · ", style="dim") if item.is_open else Text(
            "[x]" if s.session_id in self.selected else "[ ]")
        dim = "dim" if item.is_open else ""
        return (mark, Text(s.project[:22], style=dim), Text(s.title[:52], style=dim),
                Text(f"{s.last_active:%Y-%m-%d}", style="dim"), Text(human(item.size), style="bold"))

    def current(self) -> Item | None:
        if self.table.row_count == 0:
            return None
        key = self.table.coordinate_to_cell_key(Coordinate(self.table.cursor_row, 0)).row_key.value
        return self.items.get(key)

    def update_status(self) -> None:
        total = sum(item.size for item in self.items.values())
        chosen = sum(self.items[sid].size for sid in self.selected)
        self.sub_title = (f"{len(self.items)} sessions use {human(total)} · {len(self.selected)} selected "
                          f"({human(chosen)}) · Claude Code deletes sessions older than "
                          f"{claude_cleanup_days()} days on its own")
        self.update_detail()

    def update_detail(self) -> None:
        item = self.current()
        if item is None:
            self.detail.update("No sessions on disk.")
            return
        s = item.session
        text = Text()
        text.append(s.title + "\n", style="bold")
        text.append(s.cwd, style="cyan")
        text.append(f"\nLast active {s.last_active:%d %b %Y %H:%M}")
        if s.first_prompt:
            text.append("\nFirst prompt  ", style="bold")
            text.append(s.first_prompt[:200])
        text.append("\n\nOn disk:", style="bold")
        for path in item.files:
            text.append(f"\n  {human(disk_size(path)):>9}  {path}", style="dim")
        if item.is_open:
            text.append("\n\nOpen right now, so it cannot be deleted.", style="yellow")
        self.detail.update(text)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.update_detail()

    def refresh_marks(self) -> None:
        for sid, item in self.items.items():
            self.table.update_cell(sid, "sel", self.cells(item)[0])
        self.update_status()

    def action_toggle(self) -> None:
        item = self.current()
        if item is None or item.is_open:
            return
        self.selected ^= {item.session.session_id}
        self.table.action_cursor_down()
        self.refresh_marks()

    def action_select_older(self) -> None:
        cutoff = datetime.now().timestamp() - 7 * 86400
        self.selected |= {sid for sid, item in self.items.items()
                          if not item.is_open and item.session.last_active.timestamp() < cutoff}
        self.refresh_marks()

    def action_toggle_all(self) -> None:
        closed = {sid for sid, item in self.items.items() if not item.is_open}
        self.selected = set() if closed <= self.selected else closed
        self.refresh_marks()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return not (action == "delete" and isinstance(self.screen, ModalScreen))

    def action_delete(self) -> None:
        chosen = [self.items[sid] for sid in self.selected]
        if not chosen:
            self.notify("Select sessions with Space first.", severity="warning")
            return
        size = human(sum(item.size for item in chosen))

        def answered(yes: bool | None) -> None:
            if not yes:
                return
            moved, problems = trash(chosen)
            self.selected -= {item.session.session_id for item in moved}
            freed = human(sum(item.size for item in moved))
            self.notify(f"Moved {len(moved)} sessions ({freed}) to the Recycle Bin.", timeout=8)
            for problem in problems[:5]:
                self.notify(problem, severity="error", timeout=10)
            self.show([item for item in self.items.values() if item not in moved])

        self.push_screen(Confirm(f"Move {len(chosen)} sessions ({size}) to the Recycle Bin?\n"
                                 "They disappear from Claude Code's /resume until restored."), answered)
