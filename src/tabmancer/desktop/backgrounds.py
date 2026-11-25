"""B in the window: the background sessions (`claude --bg`) as the terminal dashboard's B screen lists them
(BackgroundDialog: a heading, a table, a note and the keys), read off the window's thread (Jobs: `claude
agents --json` takes half a second or more) when B is pressed and every few seconds while the list is
shown. x, d and c ask twice, then run on a thread too; Enter attaches (the window opens the tab). What it
says, and what the keys do, is backgroundwords.py's: one copy with the terminal's.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QKeySequence, QPalette, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import backgroundwords, bgjobs
from ..bgjobs import Job
from . import look
from .formparts import keys_html
from .handover import label

OPEN, REFRESH = "open", "refresh"  # why the list was read: B pressed, or the list shown read again
NOTE_COLOURS = {"": look.MUTED, "warning": look.AMBER, "done": look.GREEN, "failed": look.RED}


class Jobs(QObject):
    """The background sessions read, and the commands run, on threads of their own; back on the window's
    thread by a Signal. read: why (OPEN or REFRESH) and the jobs, or None when the CLI did not answer;
    ran: what the commands came to (backgroundwords.Ran)."""

    read = Signal(str, object)
    ran = Signal(object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.workers: list[threading.Thread] = []

    def fetch(self, why: str) -> None:
        self.start(self.fetch_now, why)

    def fetch_now(self, why: str) -> None:
        self.send("read", why, bgjobs.jobs())  # looked up when called: the demo and the tests stand in for it

    def run(self, commands: list[tuple[str, Job]]) -> None:
        self.start(self.run_now, commands)

    def run_now(self, commands: list[tuple[str, Job]]) -> None:
        self.send("ran", backgroundwords.run(commands))

    def start(self, work: Callable[..., None], *args: object) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=work, args=args, daemon=True, name="tabmancer-background")
        self.workers.append(thread)
        thread.start()

    def send(self, signal: str, *args: object) -> None:
        try:
            getattr(self, signal).emit(*args)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def settle(self, seconds: float = 10) -> None:
        for thread in list(self.workers):
            thread.join(seconds)


class Kept(QStyledItemDelegate):
    """The picked row keeps each cell's colour (a state's red, amber or green), as the Table keeps it."""

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        if brush is not None:
            option.palette.setBrush(QPalette.ColorRole.HighlightedText, brush)


class BackgroundDialog(QDialog):
    """The list: Enter (or a double click) attaches to the picked one (attach, once, and it closes); x stops
    it, d removes it, c clears away the idle and stopped ones, each on a second press; Esc closes it.
    changed: commands ran, so the window reads its sessions again. Window-modal, never waited on."""

    attach = Signal(object)  # the job to attach to (bgjobs.Job)
    changed = Signal()

    def __init__(self, parent: QWidget, found: list[Job], jobs: Jobs, accent: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self.found: list[Job] = []
        self.jobs = jobs
        self.confirm = backgroundwords.Confirm(clock)
        self.busy = False  # commands running: no key acts, the list is not read again
        self.reading = False
        self.answered = False
        self.setWindowTitle(backgroundwords.TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(820)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label("")
        box.addWidget(self.heading)
        self.table = QTableWidget(0, len(backgroundwords.COLUMNS))
        self.table.setHorizontalHeaderLabels([words for _, words in backgroundwords.COLUMNS])
        self.table.setFont(look.font(9.5))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setTabKeyNavigation(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItemDelegate(Kept(self.table))
        self.table.activated.connect(self.attach_now)  # Enter, or a double click
        self.table.currentCellChanged.connect(self.moved)
        box.addWidget(self.table)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        self.keys = label(keys_html(backgroundwords.KEYS))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QTableWidget {{ background:{look.BLACK}; color:{look.TEXT}; border:none; outline:none; }}"
                           f"QTableWidget::item:selected {{ background:{look.PANEL}; border-bottom:1px solid {accent}; }}"
                           f"QHeaderView::section {{ background:{look.SURFACE}; color:{look.MUTED}; border:none;"
                           " padding:2px 8px; }")
        self.shortcuts: list[QShortcut] = []
        for key, act in (("x", self.stop), ("d", self.remove), ("Delete", self.remove), ("c", self.clean_up)):
            made = QShortcut(QKeySequence(key), self)
            made.activated.connect(act)
            self.shortcuts.append(made)
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.refresh)
        self.ticker.start(int(backgroundwords.REFRESH_SECONDS * 1000))
        jobs.read.connect(self.read)
        jobs.ran.connect(self.finished_running)
        self.finished.connect(self.deleteLater)
        self.show_jobs(found)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.table.setFocus()  # ↑↓, Enter and the keys go to the list at once

    # --- the list -----------------------------------------------------------------------------------

    def show_jobs(self, found: list[Job]) -> None:
        """The jobs as rows, the picked one kept picked where it still is."""
        keep = self.current()
        self.found = found
        self.table.setRowCount(len(found))
        for at, job in enumerate(found):
            for column, (words, style) in enumerate(backgroundwords.row(job)):
                item = QTableWidgetItem(words)
                colour, bold = look.colour_of(style) if style else (look.TEXT, False)
                item.setForeground(look.qcolor(colour))
                if bold:
                    font = QFont(self.table.font())
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(at, column, item)
        if found:
            ids = [job.id for job in found]
            self.table.setCurrentCell(ids.index(keep.id) if keep is not None and keep.id in ids else 0, 0)
        title, *about = backgroundwords.heading(found)
        self.heading.setText(look.span(title, f"bold {look.TEXT}")
                             + "".join(look.span(("  " if n == 0 else " · ") + piece, look.MUTED)
                                       for n, piece in enumerate(about)))
        if not found:
            self.say(backgroundwords.EMPTY)

    def current(self) -> Job | None:
        at = self.table.currentRow()
        return self.found[at] if 0 <= at < len(self.found) else None

    def say(self, words: str, tone: str = "") -> None:
        self.note.setText(look.span(words, NOTE_COLOURS[tone]))

    def moved(self, *_cells: int) -> None:
        """Another row picked: a first press waiting for its second is forgotten."""
        if self.confirm.disarm():
            self.say("")

    def refresh(self) -> None:
        """Every few seconds: the list read again, never under a first press waiting or commands running."""
        if self.busy or self.reading or self.confirm.armed is not None:
            return
        self.reading = True
        self.jobs.fetch(REFRESH)

    def read(self, why: str, found: list[Job] | None) -> None:
        if why != REFRESH:
            return
        self.reading = False
        if found is not None and not self.busy and self.confirm.armed is None:
            self.show_jobs(found)

    # --- the keys ------------------------------------------------------------------------------------

    def stop(self) -> None:
        if not self.busy:
            self.ask(backgroundwords.stop(self.current(), self.confirm))

    def remove(self) -> None:
        if not self.busy:
            self.ask(backgroundwords.remove(self.current(), self.confirm))

    def clean_up(self) -> None:
        if not self.busy:
            self.ask(backgroundwords.clean_up(self.found, self.confirm))

    def ask(self, asked: backgroundwords.Asked) -> None:
        if asked.commands:
            self.busy = True
            self.say(backgroundwords.WORKING)
            self.jobs.run(asked.commands)
        elif asked.words:
            self.say(asked.words, "warning" if asked.warning else "")

    def finished_running(self, ran: backgroundwords.Ran) -> None:
        self.busy = False
        if ran.found is not None:
            self.show_jobs(ran.found)
        self.say(ran.words, "failed" if ran.failures else "done")
        self.changed.emit()

    def attach_now(self, *_index: object) -> None:
        job = self.current()
        if job is None or self.answered:
            return
        self.answered = True
        self.accept()
        self.attach.emit(job)
