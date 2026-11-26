"""`tabmancer cleanup` in the window: the sessions on disk, biggest first, the ones picked moved to the Recycle
Bin (Trash elsewhere), as the terminal's clean-up screen does it (CleanupDialog: a line about them, a table,
the picked one's detail, a note and the keys). Measuring them and moving them run off the window's thread
(Disk: diskclean.collect walks every transcript; the Recycle Bin can take a while). What it says and what its
keys pick are cleanupwords.py's, one copy with the terminal's; an open session is never picked, and which are
open is asked again as they move (diskclean.trash)."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import cleanupwords, diskclean
from ..actions import Note
from ..diskclean import Item
from . import look
from .formparts import keys_html
from .handover import label

NOTE_COLOURS = {"information": look.TEXT, "warning": look.AMBER, "error": look.RED}


class Disk(QObject):
    """The sessions on disk measured, and the picked ones moved, on threads of their own; back on the window's
    thread by a Signal. read: the items (diskclean.Item, biggest first); moved: the ones moved and the
    problems, as diskclean.trash gives them."""

    read = Signal(object)
    moved = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.workers: list[threading.Thread] = []

    def collect(self) -> None:
        self.start(lambda: self.send("read", diskclean.collect()))  # looked up when called: tests stand in for it

    def trash(self, items: list[Item]) -> None:
        self.start(lambda: self.send("moved", *diskclean.trash(items)))

    def start(self, work: Callable[[], None]) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=work, daemon=True, name="tabmancer-cleanup")
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


class CleanupDialog(QDialog):
    """Space (or a click on a row's mark) picks a row and goes to the next, o picks those older than
    cleanupwords.OLDER_DAYS, a all or none; Enter asks first (y moves them, n or Esc does not); Esc closes it.
    changed: sessions moved, so the window reads its sessions again. Window-modal, never waited on."""

    changed = Signal()

    def __init__(self, parent: QWidget | None, accent: str, disk: Disk | None = None) -> None:
        super().__init__(parent)
        self.disk = disk or Disk(self)
        self.items: dict[str, Item] = {}
        self.order: list[str] = []
        self.selected: set[str] = set()
        self.asked: cleanupwords.Ask | None = None  # Enter's question, waiting for y or n
        self.busy = True  # measuring or moving: no key picks or moves
        self.setWindowTitle(cleanupwords.TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(860)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label("", wrap=True)
        box.addWidget(self.heading)
        self.table = QTableWidget(0, len(cleanupwords.COLUMNS))
        self.table.setHorizontalHeaderLabels([words for _, words in cleanupwords.COLUMNS])
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
        self.table.currentCellChanged.connect(self.moved_to)
        self.table.cellClicked.connect(self.clicked)
        self.table.installEventFilter(self)
        box.addWidget(self.table, 1)
        self.detail = label(look.span(cleanupwords.MEASURING, look.MUTED), wrap=True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.detail)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        self.keys = label(keys_html(cleanupwords.KEY_PAIRS))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QTableWidget {{ background:{look.BLACK}; color:{look.TEXT}; border:none; outline:none; }}"
                           f"QTableWidget::item:selected {{ background:{look.PANEL}; border-bottom:1px solid {accent}; }}"
                           f"QHeaderView::section {{ background:{look.SURFACE}; color:{look.MUTED}; border:none;"
                           " padding:2px 8px; }")
        self.disk.read.connect(self.show_items)
        self.disk.moved.connect(self.came)
        self.finished.connect(self.deleteLater)
        self.disk.collect()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.table.setFocus()  # ↑↓ and the keys go to the list at once

    # --- the list -----------------------------------------------------------------------------------

    def show_items(self, items: list[Item]) -> None:
        """The sessions as rows, biggest first; the picked row kept where it still is."""
        keep = self.current()
        self.items = {item.session.session_id: item for item in items}
        self.order = list(self.items)
        self.selected &= set(self.items)
        self.busy = False
        self.table.setRowCount(len(self.order))
        for at, sid in enumerate(self.order):
            self.draw_row(at)
        if self.order:
            sid = keep.session.session_id if keep is not None else None
            self.table.setCurrentCell(self.order.index(sid) if sid in self.items else 0, 0)
        self.draw()

    def draw_row(self, at: int) -> None:
        for column, (words, style) in enumerate(cleanupwords.row_parts(self.items[self.order[at]], self.selected)):
            item = QTableWidgetItem(words)
            colour, _, bold, _ = look.ink(style).drawn()  # dim: the quiet grey, as the terminal dims an open one
            item.setForeground(look.qcolor(colour))
            if bold:
                font = QFont(self.table.font())
                font.setBold(True)
                item.setFont(font)
            self.table.setItem(at, column, item)

    def draw(self) -> None:
        """The line about them all and the picked one's detail."""
        self.heading.setText(look.span(cleanupwords.status(list(self.items.values()), self.selected), look.MUTED))
        self.detail.setText("".join(look.styled(words, style) for words, style in
                                    cleanupwords.detail_parts(self.current())))

    def current(self) -> Item | None:
        at = self.table.currentRow()
        return self.items.get(self.order[at]) if 0 <= at < len(self.order) else None

    def moved_to(self, *_cells: int) -> None:
        if self.items:
            self.draw()

    def say(self, notes: list[Note]) -> None:
        self.note.setText("<br>".join(look.span(note.words, NOTE_COLOURS.get(note.severity, look.TEXT))
                                      for note in notes))

    # --- the keys -----------------------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """The list's keys are the dialog's: Space, Enter, o, a, y and n never reach the table."""
        if watched is self.table and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self.key(event)
        return super().eventFilter(watched, event)

    def key(self, event: QKeyEvent) -> bool:
        key, text = event.key(), event.text().lower()
        if self.asked is not None:
            if text == "y":
                self.move_them()
            elif text == "n" or key == Qt.Key.Key_Escape:
                self.unask()
            return key not in (Qt.Key.Key_Up, Qt.Key.Key_Down)
        if self.busy:
            return key not in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Escape)
        if key == Qt.Key.Key_Space:
            self.toggle()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.ask()
        elif text == "o":
            self.pick(cleanupwords.with_older(self.items, self.selected))
        elif text == "a":
            self.pick(cleanupwords.all_or_none(self.items, self.selected))
        else:
            return False
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.asked is not None:
            self.unask()  # Esc while asked: not moved, and the list stays
            return
        super().keyPressEvent(event)  # Esc: closed

    def clicked(self, row: int, column: int) -> None:
        if column == 0 and not self.busy and self.asked is None:
            self.table.setCurrentCell(row, 0)
            self.toggle(step=False)

    def pick(self, selected: set[str]) -> None:
        self.selected = selected
        for at in range(len(self.order)):
            self.draw_row(at)
        self.say([])
        self.draw()

    def toggle(self, step: bool = True) -> None:
        item = self.current()
        if item is None or item.is_open:
            return
        at = self.table.currentRow()
        self.selected = cleanupwords.toggled(self.items, self.selected, item.session.session_id)
        self.draw_row(at)
        if step and at + 1 < len(self.order):
            self.table.setCurrentCell(at + 1, 0)
        self.say([])
        self.draw()

    def ask(self) -> None:
        asked = cleanupwords.ask(self.items, self.selected)
        if not isinstance(asked, cleanupwords.Ask):
            self.say([asked])
            return
        self.asked = asked
        self.note.setText(look.span(asked.question, f"bold {look.AMBER}"))
        self.keys.setText(keys_html(cleanupwords.ASK_KEYS))

    def unask(self) -> None:
        self.asked = None
        self.note.setText("")
        self.keys.setText(keys_html(cleanupwords.KEY_PAIRS))

    def move_them(self) -> None:
        """y: the asked ones to the Recycle Bin, on a thread (came: what it came to)."""
        asked = self.asked
        self.unask()
        if asked is None:
            return
        self.busy = True
        self.disk.trash(asked.chosen)

    def came(self, moved: list[Item], problems: list[str]) -> None:
        """Moved: what it came to said, the rest listed, and the window told to read its sessions again."""
        rest, self.selected = cleanupwords.after_move(self.items, self.selected, moved)
        self.show_items(rest)
        self.say(cleanupwords.moved_notes(moved, problems))
        if moved:
            self.changed.emit()
