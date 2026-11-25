"""The Sessions panel's Table and Live views: the terminal dashboard's list, row for row and word for
word. The rows, their order, every cell's words and colours, a team's chips and its tree come from
rowwords (one copy with the terminal's list); this draws them: each cell's parts in their colours
(a badge's background behind its words), the headings over the groups, the turning star, its sweep
of light and the red pulse as the window's motion allows, and a row's Status lit for a moment after its
state changed (rowwords.changed), as in the terminal: fading, or held then put out with less motion.

The pick is shared with the Board's: it goes out (picked) in the drawer's words, a session's id or a
team's card (viewmodel.TEAM_CARD + its name). A team's row unfolds its members under it (e, a click on
its ▸, a double-click; E every team), as in the terminal, where Enter on it opens the team's view (in
the window, its Team panel tab); the cursor never rests on a heading. A row ticked (Space, or a click
on its [ ]) shows [x], as there; the Open as column says how m left it."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QModelIndex, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QFontMetricsF,
    QPainter,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTreeView

from .. import picks, rowwords
from .. import viewmodel as VM
from ..launch import Mode
from ..sessions import Session
from ..store import DashSnapshot
from . import look

KEY, PARTS, KIND, MOTION = (Qt.ItemDataRole.UserRole + n for n in range(1, 5))
SIZE = 9.5  # the table's words, in points
PAD = 1  # cells of room either side of a column's words, as the terminal's table pads them
LINE = 1.55  # a row's height, in lines of its words
HEAD_LINES = 2  # a heading's row: a blank line over it, as the terminal sets it apart
COLUMN = {name: index for index, name in enumerate(rowwords.COLUMNS)}


def in_hidden(hidden: set[str], session: Session) -> bool:
    return session.session_id in hidden


def drawer_key(key: str) -> str:
    """A row's key as the drawer and the Board know it: a team's row is the team's card."""
    return VM.TEAM_CARD + key[len(rowwords.TEAM_ROW):] if key.startswith(rowwords.TEAM_ROW) else key


class Cells(QStyledItemDelegate):
    """Draws a cell's parts (rowwords), each in its style (look.ink): a BASED column's first part's
    style under the rest; a heading's words on its row's last line; the picked row lit."""

    def __init__(self, table: Table) -> None:
        super().__init__(table)
        self.font = look.font(SIZE)
        self.bold = look.font(SIZE, bold=True)
        self.metrics = QFontMetricsF(self.font)
        self.bold_metrics = QFontMetricsF(self.bold)
        self.accent = table.accent

    def line(self) -> float:
        return round(self.metrics.height() * LINE)

    def wide(self, parts: list[tuple[str, str]]) -> float:
        """How wide the parts are drawn, padding and all: in the fonts they are drawn in (a glyph from
        another font, a bar's ▰, can be wider than a cell)."""
        drawn = sum((self.bold_metrics if look.ink(style).bold else self.metrics).horizontalAdvance(words)
                    for words, style in parts)
        return drawn + self.metrics.horizontalAdvance(" " * 2 * PAD)

    def sizeHint(self, option, index: QModelIndex) -> QSize:
        lines = HEAD_LINES if index.data(KIND) == "heading" else 1
        return QSize(int(self.wide(index.data(PARTS) or [])) + 1, int(self.line() * lines))

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        painter.save()
        rect = QRectF(option.rect)
        painter.setClipRect(rect)
        kind = index.data(KIND)
        if kind != "heading":
            if option.state & QStyle.StateFlag.State_Selected:
                painter.fillRect(rect, QColor(look.PANEL))
                if index.column() == 0:
                    painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()), QColor(self.accent))
            elif option.state & QStyle.StateFlag.State_MouseOver:
                painter.fillRect(rect, QColor(look.SUNKEN))
        parts = index.data(PARTS) or []
        under = look.ink(parts[0][1]) if parts and rowwords.COLUMNS[index.column()] in rowwords.BASED else look.Ink()
        line = self.line()
        top = rect.bottom() + 1 - line
        baseline = top + (line - self.metrics.height()) / 2 + self.metrics.ascent()
        x = rect.left() + self.metrics.horizontalAdvance(" " * PAD)
        for words, style in parts:
            colour, background, bold, italic = look.ink(style).over(under).drawn()
            font, metrics = (self.bold, self.bold_metrics) if bold else (self.font, self.metrics)
            if italic:
                font = QFont(font)
                font.setItalic(True)
                metrics = QFontMetricsF(font)
            painter.setFont(font)
            wide = metrics.horizontalAdvance(words)
            if background:
                painter.fillRect(QRectF(x, top + 2, wide, line - 4), QColor(background))
            painter.setPen(QColor(colour))
            painter.drawText(QPointF(x, baseline), words)
            x += wide
        painter.restore()


class Table(QTreeView):
    """The list: rowwords' rows over the sessions shown (the window's filters), picker's columns or
    the live view's. picked: the row picked now, in the drawer's words ('' for none); ticking: a click on a
    session's [ ], or a closed team's (its key in the drawer's words, for the window to tick); menu: the
    picked row's menu asked for (a right-click on a row picks it; the menu key), where on the screen."""

    picked = Signal(str)
    ticking = Signal(str)
    menu = Signal(QPoint)

    def __init__(self, accent: str, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self.accent = accent
        self.clock = clock  # when a lit row goes out
        self.flash: dict[str, float] = {}  # session id -> when its row's light goes out
        self.snap: DashSnapshot | None = None
        self.shown: list[Session] = []
        self.live = False
        self.unfolded: set[str] = set()  # teams whose members are listed under them
        self.ticked: frozenset[str] = frozenset()  # the sessions ticked (Space), by id
        self.modes: Mapping[str, Mode] = {}  # how a session opens, where m changed it (the window's)
        self.listing: rowwords.Listing | None = None
        self.keys: list[str] = []
        self.sessions: list[Session | None] = []
        self.motions: dict[int, str] = {}  # row -> how it moves
        self.pick: str | None = None  # the key of the row picked
        self.row = 0
        self.width_now, self.model_shown = rowwords.TITLE_WIDEST, True
        self.motion = "full"  # the window's View ▸ Reduce motion
        self.cells = Cells(self)
        self.setItemDelegate(self.cells)
        self.grid = QStandardItemModel(0, len(rowwords.COLUMNS), self)
        self.setModel(self.grid)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setUniformRowHeights(False)
        self.setAllColumnsShowFocus(True)
        self.setMouseTracking(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFont(look.font(SIZE))
        header = self.header()
        header.setSectionsMovable(False)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setFont(look.font(9, bold=True))
        self.setStyleSheet(
            f"QTreeView {{ background:{look.BLACK}; border:none; outline:none; }}"
            f"QHeaderView::section {{ background:{look.BLACK}; color:{look.MUTED}; border:none;"
            f" border-bottom:1px solid {look.BORDER}; padding:4px 0 4px {self.cells.metrics.horizontalAdvance(' ')}px; }}")
        self.doubleClicked.connect(self.fold_at)
        self.label_columns()

    # --- what it shows ----------------------------------------------------------------------------

    def set(self, snap: DashSnapshot, shown: list[Session], ticked: Collection[str] | None = None) -> None:
        """The list as the store read it, over the sessions shown (VM.visible with the window's filters,
        live when the live view is shown); ticked: the sessions ticked now (None: as they were)."""
        self.snap, self.shown = snap, shown
        if ticked is not None:
            self.ticked = frozenset(ticked)
        self.show_rows()

    def set_ticked(self, ids: Collection[str]) -> None:
        if frozenset(ids) != self.ticked:
            self.ticked = frozenset(ids)
            self.show_rows()

    def set_live(self, live: bool) -> None:
        if live != self.live:
            self.live = live
            self.fit(force=True)

    def set_motion(self, motion: str) -> None:
        self.motion = motion
        if motion == "off":
            self.tick(0.0)  # still now

    def light(self, ids: Iterable[str]) -> None:
        """These sessions' rows lit (their state changed since the last reading), for FLASH_SECONDS: drawn
        at the next tick."""
        until = self.clock() + rowwords.FLASH_SECONDS
        self.flash.update(dict.fromkeys(ids, until))

    def label_columns(self) -> None:
        self.grid.setHorizontalHeaderLabels(list(rowwords.labels(self.live, self.model_shown)))

    def listing_now(self, star: str = rowwords.STILL_STAR) -> rowwords.Listing | None:
        snap = self.snap
        if snap is None:
            return None
        grouped = VM.group(self.shown, snap.pinned, snap.team_states, teams_too=not self.live)
        return rowwords.Listing(
            grouped, snap.sessions, snap.team_states, self.accent, self.width_now, model_shown=self.model_shown,
            live=self.live, unfolded=frozenset(self.unfolded), selected=self.ticked, pinned=snap.pinned,
            is_hidden=partial(in_hidden, snap.hidden), armed=snap.armed, queued=snap.queued, tokens=snap.session_tokens,
            default_cap=picks.cap_tokens(picks.default_cap()), modes=dict(self.modes), star=star)

    def show_rows(self) -> None:
        """The rows again: in place where they are the same rows, else made anew; the pick kept on its row,
        or on the one that took its place (VM.kept_row)."""
        self.listing = listing = self.listing_now()
        if listing is None:
            return
        listed = rowwords.keys(listing)
        keys = [key for key, _ in listed]
        said = [rowwords.row(listing, key, session) for key, session in listed]
        same = keys == self.keys
        self.keys, self.sessions = keys, [session for _, session in listed]
        self.motions = {row: words.motion for row, words in enumerate(said) if words.motion}
        if same:
            for row, words in enumerate(said):
                for column, parts in enumerate(words.cells):
                    item = self.grid.item(row, column)
                    if item.data(PARTS) != parts:
                        item.setData(parts, PARTS)
        else:
            self.grid.removeRows(0, self.grid.rowCount())
            for key, words in zip(keys, said, strict=True):
                kind = ("heading" if key.startswith(rowwords.HEADING) else
                        "team" if key.startswith(rowwords.TEAM_ROW) else "session")
                items = []
                for parts in words.cells:
                    item = QStandardItem()
                    item.setData(parts, PARTS)
                    item.setData(kind, KIND)
                    item.setData(key, KEY)
                    if kind == "heading":
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # never picked
                    items.append(item)
                self.grid.appendRow(items)
        self.size_columns(said)
        place = VM.kept_row(keys, self.pick, self.row, True)
        place = self.off_heading(place if place is not None else 0, 1)
        before = self.pick
        if place is None:
            self.pick, self.row = None, 0
            self.clearSelection()
        else:
            self.pick, self.row = keys[place], place
            if self.currentIndex().row() != place:
                self.setCurrentIndex(self.grid.index(place, 0))
        if self.pick != before:
            self.picked.emit(drawer_key(self.pick or ""))

    def size_columns(self, said: list[rowwords.RowWords]) -> None:
        """Each column as wide as its widest words with their padding (the terminal's table does the same),
        as they are drawn; the last takes what is left."""
        labels = rowwords.labels(self.live, self.model_shown)
        heading = QFontMetricsF(self.header().font())
        pad = self.cells.metrics.horizontalAdvance(" " * 2 * PAD)
        for column in range(len(rowwords.COLUMNS) - 1):
            widest = max([heading.horizontalAdvance(labels[column]) + pad] + [
                self.cells.wide(words.cells[column]) for words in said])
            self.header().resizeSection(column, math.ceil(widest))

    def off_heading(self, row: int, step: int) -> int | None:
        """The nearest row from this one that is not a heading, this way first, then the other."""
        for direction in (step, -step):
            at = row
            while 0 <= at < len(self.keys) and self.keys[at].startswith(rowwords.HEADING):
                at += direction
            if 0 <= at < len(self.keys):
                return at
        return None

    # --- room -------------------------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self, force: bool = False) -> None:
        """The title's room and the model's in the width there is (rowwords.title_room, in the
        terminal's cells): the rows again where it changed."""
        cells = int(self.viewport().width() // self.cells.metrics.horizontalAdvance(" "))
        room = rowwords.title_room(cells, self.live)
        if force or room != (self.width_now, self.model_shown):
            self.width_now, self.model_shown = room
            self.label_columns()
            self.show_rows()

    # --- picking ----------------------------------------------------------------------------------

    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        super().currentChanged(current, previous)
        row = current.row()
        if not 0 <= row < len(self.keys):
            return
        if self.keys[row].startswith(rowwords.HEADING):  # on past it, the way the cursor went
            place = self.off_heading(row, 1 if row >= previous.row() else -1)
            if place is not None:
                self.setCurrentIndex(self.grid.index(place, 0))
            return
        if self.keys[row] != self.pick:
            self.pick, self.row = self.keys[row], row
            self.picked.emit(drawer_key(self.pick))

    def show_key(self, key: str) -> bool:
        """Pick this row (a session's id or a team's card), unfolding its team for a member; False when
        it is not in the list."""
        if key.startswith(VM.TEAM_CARD):
            key = rowwords.TEAM_ROW + key[len(VM.TEAM_CARD):]
        listing = self.listing
        if key not in self.keys and listing is not None and key in listing.grouped.members:
            self.unfolded.add(listing.grouped.members[key][0])
            self.show_rows()
        if key not in self.keys:
            return False
        self.setCurrentIndex(self.grid.index(self.keys.index(key), 0))
        return True

    def fold(self, name: str) -> None:
        """A team's members listed under its row, or folded into it again."""
        self.unfolded ^= {name}
        self.show_rows()

    def fold_teams(self) -> bool:
        """E: every team in the list unfolded, or every one folded again when all of them were (one that
        came in since makes the next press unfold); the pick on its team's row when it folded away. False
        when the list has no team."""
        names = {key[len(rowwords.TEAM_ROW):] for key in self.keys if key.startswith(rowwords.TEAM_ROW)}
        if not names:
            return False
        pick = self.pick
        self.unfolded = self.unfolded - names if names <= self.unfolded else self.unfolded | names
        self.show_rows()
        listing = self.listing
        if pick is not None and pick not in self.keys and listing is not None and pick in listing.grouped.members:
            self.show_key(VM.TEAM_CARD + listing.grouped.members[pick][0])
        return True

    def step(self, by: int) -> None:
        """The pick a row down (by 1) or up (-1), past the headings, as the arrows move it."""
        if self.keys:
            place = self.off_heading(max(0, min(self.row + by, len(self.keys) - 1)), by)
            if place is not None:
                self.setCurrentIndex(self.grid.index(place, 0))

    def fold_at(self, index: QModelIndex) -> None:
        key = index.data(KEY) or ""
        if key.startswith(rowwords.TEAM_ROW):
            self.fold(key[len(rowwords.TEAM_ROW):])

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        super().mousePressEvent(event)
        if index.isValid() and index.column() == COLUMN["sel"] and event.button() == Qt.MouseButton.LeftButton:
            box = "".join(words for words, _ in index.data(PARTS) or [])
            if box.startswith("["):  # a box: a session's, or a closed team's
                self.ticking.emit(drawer_key(index.data(KEY) or ""))
            else:
                self.fold_at(index)  # the ▸ of an open team's row

    def contextMenuEvent(self, event) -> None:
        """A right-click on a row: picked, then its menu (on a heading: nothing); the menu key: the picked
        row's, at the row."""
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            index = self.currentIndex()
            if self.pick is None or not index.isValid():
                event.ignore()
                return
            rect = self.visualRect(self.grid.index(index.row(), COLUMN["title"]))
            at = self.viewport().mapToGlobal(QPoint(rect.left() + 24, rect.center().y()))
        else:
            index = self.indexAt(event.pos())
            if not index.isValid() or self.keys[index.row()].startswith(rowwords.HEADING):
                event.ignore()
                return
            self.setCurrentIndex(self.grid.index(index.row(), 0))
            at = event.globalPos()
        self.setFocus()
        self.menu.emit(at)

    # --- motion -----------------------------------------------------------------------------------

    def tick(self, seconds: float) -> None:
        """What moves, at this moment: a session at work's star and the light across its words, the red
        of one that needs you, a folded team's members at work, a row lit after a change (not one that
        needs you: its red says it) fading. Calm: the stars turn, a lit row is held till it goes out,
        nothing else; no motion: all still, a lit row held too."""
        listing = self.listing
        if listing is None or not (self.motions or self.flash) or not self.isVisible():
            return
        now = self.clock()
        still = self.motion == "off"
        turning = listing if still else replace(listing, star=rowwords.star_at(seconds))
        rows = dict(self.motions)
        if self.flash:  # a lit row's light fades, and one that went out is drawn once more without it
            rows.update({row: self.motions.get(row, "") for row, session in enumerate(self.sessions)
                         if session is not None and session.session_id in self.flash})
        for row, motion in rows.items():
            if row >= len(self.keys):
                continue
            key, session = self.keys[row], self.sessions[row]
            if session is None:  # a folded team's row: its members' stars, in the chips
                column = COLUMN["title"]
                parts = rowwords.row(turning, key, None).cells[column]
            else:
                column = COLUMN["status"]
                parts = rowwords.row(listing, key, session).cells[column]
                if motion and not still and not (self.motion == "calm" and motion == "pulse"):
                    parts = rowwords.moving(parts, motion, self.accent, seconds, sweep=self.motion == "full")
                if motion != "pulse":
                    parts = rowwords.lit(parts, rowwords.flash_background(
                        self.accent, self.flash.get(session.session_id, now) - now, fade=self.motion == "full"))
            item = self.grid.item(row, column)
            if item is not None and item.data(PARTS) != parts:
                item.setData(parts, PARTS)
        self.flash = {sid: until for sid, until in self.flash.items() if until > now}
