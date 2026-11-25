"""Background sessions (`claude --bg`): see them all, stop them, remove them.

A background session's process stays alive after Claude finishes, waiting for more,
and its entry stays after the process ends, so both pile up. `claude stop` ends the
process and keeps the conversation. `claude rm` deletes the entry and its worktree;
the transcript stays on disk, so the session can still be resumed from the list.

The terminal's B screen; what it says, when a key waits for a second press and what the commands do are
backgroundwords.py's, the copy the desktop window uses too.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label

from . import backgroundwords as words

# What lists the jobs, named here for the dashboard and older callers. What acts on them (stop, remove,
# _run) is bgjobs' alone: a stand-in for it set here would not reach the CLI, so it fails loudly instead.
from .bgjobs import IDLE, LABELS, NEEDS_YOU, WORKING, Job, ago, jobs, kind, state_of  # noqa: F401
from .dialogs import frame, title
from .safework import work
from .theme import AMBER, GREEN, MUTED, RED, TEXT, keys

CONFIRM_SECONDS = words.CONFIRM_SECONDS  # a second press within this long confirms
REFRESH_SECONDS = words.REFRESH_SECONDS
PLAIN = {"project": TEXT}  # a cell backgroundwords.row leaves plain ('') that this screen colours


class BackgroundDialog(ModalScreen[Job | None]):
    """The list of background sessions. Dismisses with a job to attach to, or None."""

    DEFAULT_CSS = frame("BackgroundDialog", "large") + """
    BackgroundDialog DataTable { height: auto; max-height: 20; background: $surface; }
    BackgroundDialog #note { margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close"),
        Binding("x", "stop", "Stop"),
        Binding("d,delete", "remove", "Remove"),
        Binding("c", "clean_up", "Clean up"),
        Binding("enter", "attach", "Attach", priority=True),
    ]

    def __init__(self, found: list[Job]) -> None:
        super().__init__()
        self.jobs = found
        self.confirm = words.Confirm()  # x, d and c wait for a second press
        self.busy = False

    @property
    def armed(self) -> tuple[str, str] | None:
        """(action, job id or '*') waiting for a second press, or None."""
        return self.confirm.armed

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("", id="title")
            yield DataTable(cursor_type="row", id="jobs")
            yield Label("", id="note")
            yield Label(keys(*words.KEYS), classes="hint")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, label in words.COLUMNS:
            table.add_column(label, key=key)
        self.show_jobs(self.jobs)
        table.focus()
        self.set_interval(REFRESH_SECONDS, self.refresh_jobs)

    @work(thread=True, exclusive=True, group="refresh")
    def refresh_jobs(self) -> None:
        if self.busy or self.confirm.armed:
            return  # never move rows under a pending confirmation
        found = jobs()
        if found is not None and not self.busy:
            self.app.call_from_thread(self.show_jobs, found)

    def show_jobs(self, found: list[Job]) -> None:
        self.jobs = found
        table = self.query_one(DataTable)
        keep = self.current()
        table.clear()
        for job in found:
            table.add_row(*(Text(text, style=style or PLAIN.get(column, ""))
                            for (column, _), (text, style) in zip(words.COLUMNS, words.row(job), strict=True)),
                          key=job.id)
        if keep is not None and keep.id in table.rows:
            table.move_cursor(row=table.get_row_index(keep.id))
        self.query_one("#title", Label).update(title(*words.heading(found)))
        if not found:
            self.note(words.EMPTY)

    def current(self) -> Job | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return next((job for job in self.jobs if job.id == key), None)

    def note(self, message: str, style: str = MUTED) -> None:
        self.query_one("#note", Label).update(Text(message, style=style))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self.confirm.disarm():
            self.note("")

    def ask(self, asked: words.Asked) -> None:
        """What a key came to (backgroundwords): its words (amber when a second press would do it), then the
        commands, run on a thread."""
        if asked.words:
            self.note(asked.words, AMBER if asked.warning else MUTED)
        if asked.commands:
            self.run_commands(asked.commands)

    def action_stop(self) -> None:
        if not self.busy:
            self.ask(words.stop(self.current(), self.confirm))

    def action_remove(self) -> None:
        if not self.busy:
            self.ask(words.remove(self.current(), self.confirm))

    def action_clean_up(self) -> None:
        if not self.busy:
            self.ask(words.clean_up(self.jobs, self.confirm))

    def action_attach(self) -> None:
        job = self.current()
        if job is not None:
            self.dismiss(job)

    def action_close(self) -> None:
        self.dismiss(None)

    @work(thread=True, exclusive=True)
    def run_commands(self, commands: list[tuple[str, Job]]) -> None:
        self.busy = True
        self.app.call_from_thread(self.note, words.WORKING)
        ran = words.run(commands)  # a running one is stopped before it is removed; then the list read again
        self.busy = False
        self.app.call_from_thread(self.finished, ran)

    def finished(self, ran: words.Ran) -> None:
        if ran.found is not None:
            self.show_jobs(ran.found)
        self.note(ran.words, RED if ran.failures else GREEN)
        refresh = getattr(self.app, "poll_live", None)
        if refresh is not None:
            refresh(full=True)  # the dashboard shows the change straight away
