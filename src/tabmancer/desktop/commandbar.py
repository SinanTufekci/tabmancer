"""The command palette over a desktop window (Ctrl+K): type a few words, Enter runs the command.

commands.find ranks what the window offers: every key of keys.py, named as its menus name it, and jumps
to a team, a session or a team's member. Enter on a key runs the window's own menu action (the one its
menu and its shortcut hold), so the palette does what the menu does; on a jump the window goes there. A
greyed command says why under the list and does nothing. Esc, or a click elsewhere, closes it. The
window hears what was picked through the chosen signal: the bar keeps no hold on its window.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import commands
from ..sessions import Session
from . import look
from .widgets import text_label

KEY = "Ctrl+K"
WIDTH = 660
ROWS = 12  # rows in sight before the list scrolls
ROW = 30
HINT = "↑ ↓ pick · Enter runs it · Esc closes"
NOTHING = "Nothing by that name."
COMMAND = Qt.ItemDataRole.UserRole  # where a row keeps its commands.Command


class Line(QLineEdit):
    """The words typed. The keys that move through the list, run a command or close it are told, not typed."""

    stepped = Signal(int)
    chosen = Signal()
    closed = Signal()

    def keyPressEvent(self, event) -> None:
        steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: ROWS, Qt.Key.Key_PageUp: -ROWS}
        key = event.key()
        if key in steps:
            self.stepped.emit(steps[key])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.chosen.emit()
        elif key == Qt.Key.Key_Escape:
            self.closed.emit()
        else:
            super().keyPressEvent(event)


class Row(QStyledItemDelegate):
    """A command's row: its words (greyed when the window does not do it), and on the right its key in a
    key cap, or what a jump goes to."""

    def __init__(self, accent: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.accent = accent

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW)

    def paint(self, painter: QPainter, option, index) -> None:
        command = index.data(COMMAND)
        if not isinstance(command, commands.Command):
            return
        box = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(box, look.qcolor(look.PANEL))
            painter.fillRect(QRectF(box.left(), box.top() + 5, 3, box.height() - 10), look.qcolor(self.accent))
        words_font, side_font = look.font(9.8, command.kind != "key"), look.font(8.6, command.kind == "key")
        side = command.key if command.kind == "key" else " · ".join(p for p in (command.kind, command.group) if p)
        side_metrics = QFontMetricsF(side_font)
        side_width = side_metrics.horizontalAdvance(side) + (14 if command.kind == "key" else 0)
        right = box.right() - 12
        painter.setFont(side_font)
        if command.kind == "key":
            cap = QRectF(right - side_width, box.center().y() - 10, side_width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(look.qcolor(look.FAINT))
            painter.drawRoundedRect(cap, 4, 4)
            painter.setPen(look.qcolor(look.TEXT if command.enabled else look.DIM))
            painter.drawText(cap, Qt.AlignmentFlag.AlignCenter, side)
        else:
            painter.setPen(look.qcolor(look.MUTED))
            painter.drawText(QRectF(right - side_width, box.top(), side_width, box.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, side)
        painter.setFont(words_font)
        room = max(20.0, right - side_width - 16 - (box.left() + 14))
        words = QFontMetricsF(words_font).elidedText(command.words, Qt.TextElideMode.ElideRight, room)
        painter.setPen(look.qcolor(look.TEXT if command.enabled else look.DIM))
        painter.drawText(QRectF(box.left() + 14, box.top(), room, box.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, words)
        painter.restore()


class CommandBar(QFrame):
    """The palette: a line to type in over the commands it finds, and what the picked one does (or why
    it is greyed) under them."""

    chosen = Signal(object)  # the commands.Command run: an enabled one

    def __init__(self, owner: QWidget, accent: str, placeholder: str = "a command, a session or a team…") -> None:
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("commandbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.entries: list[commands.Command] = []
        self.sessions: dict[str, Session] = {}
        self.shown: list[commands.Command] = []
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 8)
        box.setSpacing(6)
        self.line = Line()
        self.line.setObjectName("commandline")
        self.line.setFont(look.font(10.5))
        self.line.setPlaceholderText(placeholder)
        self.rows = QListWidget()
        self.rows.setObjectName("commands")
        self.rows.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rows.setItemDelegate(Row(accent, self.rows))
        self.rows.setUniformItemSizes(True)
        self.rows.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rows.setFixedHeight(ROW * ROWS + 4)
        self.note = text_label("", look.MUTED, 9, wrap=True)
        self.note.setMinimumHeight(int(QFontMetricsF(look.font(9)).lineSpacing() * 2) + 2)
        self.note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.line)
        box.addWidget(self.rows)
        box.addWidget(self.note)
        box.addWidget(text_label(HINT, look.DIM, 8.5))
        self.line.textChanged.connect(self.refind)
        self.line.stepped.connect(self.step)
        self.line.chosen.connect(self.run_picked)
        self.line.closed.connect(self.close)
        self.rows.itemClicked.connect(self.click)
        self.rows.currentRowChanged.connect(self.show_note)

    def offer(self, entries: list[commands.Command], sessions: dict[str, Session] | None = None) -> None:
        """Open over its window with these commands (the window's keys and jumps as they are now), nothing
        typed yet."""
        self.entries, self.sessions = list(entries), dict(sessions or {})
        self.line.blockSignals(True)
        self.line.clear()
        self.line.blockSignals(False)
        self.refind()
        owner = self.parentWidget()
        width = max(360, min(WIDTH, owner.width() - 60))
        self.setFixedWidth(width)
        self.move(owner.mapToGlobal(QPoint((owner.width() - width) // 2, max(40, owner.height() // 9))))
        self.show()
        self.raise_()
        self.activateWindow()
        self.line.setFocus()

    def refind(self) -> None:
        self.shown = commands.find(self.entries, self.line.text(), self.sessions)
        self.rows.clear()
        for command in self.shown:
            item = QListWidgetItem(command.words)
            item.setData(COMMAND, command)
            item.setToolTip(command.why or command.said)
            self.rows.addItem(item)
        if self.shown:
            self.rows.setCurrentRow(0)
        self.show_note()

    def picked(self) -> commands.Command | None:
        row = self.rows.currentRow()
        return self.shown[row] if 0 <= row < len(self.shown) else None

    def show_note(self) -> None:
        """Under the list: what the picked command does, or why it is greyed."""
        command = self.picked()
        if command is None:
            words, colour = (NOTHING if self.line.text().strip() else ""), look.MUTED
        elif not command.enabled:
            words, colour = command.why, look.AMBER
        else:
            words, colour = command.said, look.MUTED
        self.note.setText(words)
        self.note.setStyleSheet(f"color:{colour}; background:transparent;")

    def step(self, by: int) -> None:
        if self.shown:
            self.rows.setCurrentRow(max(0, min(len(self.shown) - 1, self.rows.currentRow() + by)))

    def click(self, item: QListWidgetItem) -> None:
        self.rows.setCurrentItem(item)
        self.run_picked()

    def run_picked(self) -> None:
        """Enter: the picked command runs and the bar closes; a greyed one only says why."""
        command = self.picked()
        if command is None:
            return
        if not command.enabled:
            self.show_note()
            return
        self.close()
        self.chosen.emit(command)
