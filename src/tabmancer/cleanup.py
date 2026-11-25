"""Free disk space: move old Claude Code sessions to the Recycle Bin.

A session is more than its transcript: Claude Code also keeps a folder of subagent
transcripts and tool output next to it, plus file snapshots for rewind under
~/.claude/file-history. All of it goes to the Recycle Bin (Trash elsewhere), so a
mistake can be undone. Sessions that are open right now are never touched.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Label, Static

from . import diskclean
from .dialogs import frame, title

# What reads the disk, named here for older callers. Moving to the Recycle Bin (trash, _trash_one)
# is diskclean's alone: a stand-in for it set here would not reach it, so it fails loudly instead.
from .diskclean import (  # noqa: F401
    DEFAULT_CLEANUP_DAYS,
    Item,
    claude_cleanup_days,
    collect,
    disk_size,
    human,
    session_files,
)
from .safework import work
from .theme import keys, use_saved_theme
from .widgets import TopBar


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

    def notify(self, message: str, *, title: str = "", severity: str = "information",
               timeout: float | None = None, markup: bool = True) -> None:
        """Every note as plain words, never markup: a problem carries a path and an OS error, and a
        stray '[/b]' in one would not show (Textual's MarkupError)."""
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=False)

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
        self.selected |= {item.session.session_id for item in diskclean.older_than(list(self.items.values()), 7)}
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
            moved, problems = diskclean.trash(chosen)
            self.selected -= {item.session.session_id for item in moved}
            freed = human(sum(item.size for item in moved))
            self.notify(f"Moved {len(moved)} sessions ({freed}) to the Recycle Bin.", timeout=8)
            for problem in problems[:5]:
                self.notify(problem, severity="error", timeout=10)
            self.show([item for item in self.items.values() if item not in moved])

        self.push_screen(Confirm(f"Move {len(chosen)} sessions ({size}) to the Recycle Bin?\n"
                                 "They disappear from Claude Code's /resume until restored."), answered)
