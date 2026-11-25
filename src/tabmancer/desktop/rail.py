"""The rail beside the board: the projects, each with how its sessions stand and where its folder is
in git, and a click narrows every lane to one of them.

The lines are the terminal dashboard's sidebar (viewmodel.project_rows): All projects first, then one
per project in a stable alphabetical order, marked by its most urgent session (red waiting for you,
green open, amber to continue), ± changes not committed and ↑ commits not pushed, and how many sessions
it has in sight. The project the list is narrowed to is lit; a click on it again (or on All projects)
shows every project.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QFrame, QScrollArea, QToolTip, QVBoxLayout, QWidget

from .. import viewmodel as VM
from ..store import DashSnapshot
from . import look
from .widgets import text_label

ROW = 22  # a project's line
RULE = 9  # the room of the rule under All projects
MARKS = {"waiting": ("●", look.RED, "one waiting for you"), "open": ("●", look.GREEN, "open"),
         "continue": ("▲", look.AMBER, "one to continue")}
SIGNS = {"±": (look.AMBER, "changes not committed"), "↑": (look.CYAN, "commits not pushed")}
LEGEND = (f'{look.span("● ", look.RED)}{look.span("your turn  ", look.MUTED)}{look.span("● ", look.GREEN)}'
          f'{look.span("open", look.MUTED)}<br>{look.span("▲ ", look.AMBER)}{look.span("to continue  ", look.MUTED)}'
          f'{look.span("± ", look.AMBER)}{look.span("↑ ", look.CYAN)}{look.span("git", look.MUTED)}')


def project_rows(snap: DashSnapshot) -> list[VM.ProjectRow]:
    """The rail's lines over the sessions in sight, as the terminal dashboard's sidebar has them."""
    shown = VM.in_sight(snap.sessions.values(), lambda s: s.session_id in snap.hidden, False)
    return VM.project_rows(shown, None, VM.project_signs(snap.view.projects))


class ProjectList(QWidget):
    """The projects' lines, one picked: a click picks another, or every project again."""

    picked = Signal(str)  # a project's folder key, or VM.ALL_PROJECTS

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.rows: list[VM.ProjectRow] = []
        self.pick = VM.ALL_PROJECTS
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, rows: list[VM.ProjectRow], pick: str) -> None:
        if (rows, pick) == (self.rows, self.pick):
            return  # read again, the same: nothing is drawn
        self.rows, self.pick = rows, pick
        self.setFixedHeight(int(self.top_of(len(rows))))
        self.update()

    def top_of(self, index: int) -> float:
        return index * ROW + (RULE if index >= 1 else 0)

    def row_at(self, y: float) -> int | None:
        for index in range(len(self.rows)):
            if self.top_of(index) <= y < self.top_of(index) + ROW:
                return index
        return None

    def mousePressEvent(self, event) -> None:
        index = self.row_at(event.position().y())
        if index is not None:
            key = self.rows[index].key
            self.picked.emit(VM.ALL_PROJECTS if key == self.pick else key)
        super().mousePressEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.row_at(event.pos().y())
            if index is not None:
                QToolTip.showText(event.globalPos(), self.tip(self.rows[index]), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def tip(self, row: VM.ProjectRow) -> str:
        words = [f"{row.count} session{'s' if row.count != 1 else ''} in sight"]
        if row.mark:
            words.append(MARKS[row.mark][2])
        words += [SIGNS[sign][1] for sign in row.git if sign in SIGNS]
        if row.key == VM.ALL_PROJECTS:
            return f"{row.name}: {words[0]}"
        what = "a click shows every project again" if row.key == self.pick else "a click shows only its sessions"
        return f"{row.name}: {' · '.join(words)}. {what}"

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        plain, bold = look.font(9), look.font(9, True)
        fm = QFontMetricsF(plain)
        width = self.width()
        for index, row in enumerate(self.rows):
            top = self.top_of(index)
            box = QRectF(0, top, width, ROW)
            if row.key == self.pick:
                p.fillRect(box, QColor(look.PANEL))
                p.fillRect(QRectF(0, top + 3, 2, ROW - 6), QColor(self.accent))
            right = box.right() - 6
            p.setFont(plain)
            p.setPen(QColor(look.MUTED))
            count = str(row.count)
            count_w = fm.horizontalAdvance(count)
            p.drawText(QRectF(right - count_w, top, count_w + 1, ROW), Qt.AlignmentFlag.AlignVCenter, count)
            right -= count_w + (6 if row.git else 0)
            for sign in reversed(row.git):
                colour = SIGNS.get(sign, (look.MUTED, ""))[0]
                sign_w = fm.horizontalAdvance(sign)
                p.setPen(QColor(colour))
                p.drawText(QRectF(right - sign_w, top, sign_w + 1, ROW), Qt.AlignmentFlag.AlignVCenter, sign)
                right -= sign_w + 1
            left = 8.0
            if row.mark in MARKS:
                glyph, colour, _ = MARKS[row.mark]
                p.setPen(QColor(colour))
                p.drawText(QRectF(left, top, 14, ROW), Qt.AlignmentFlag.AlignVCenter, glyph)
            if row.key != VM.ALL_PROJECTS:
                left += 16
            first = row.key == VM.ALL_PROJECTS
            p.setFont(bold if first else plain)
            p.setPen(QColor(self.accent if row.key == self.pick and not first else look.TEXT))
            room = right - left - 8
            p.drawText(QRectF(left, top, max(0.0, room), ROW), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(row.name, Qt.TextElideMode.ElideRight, max(0.0, room)))
            if first and len(self.rows) > 1:
                p.fillRect(QRectF(8, top + ROW + RULE / 2 - 0.5, width - 14, 1), QColor(look.BORDER))
        p.end()


class Rail(QScrollArea):
    """The rail's sections, scrolled as one when they are taller than the window."""

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.setObjectName("rail")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget"
                           " { background: transparent; }")
        inside = QWidget()
        inside.setObjectName("railinside")
        column = QVBoxLayout(inside)
        column.setContentsMargins(4, 8, 6, 8)
        column.setSpacing(6)
        column.addWidget(text_label("PROJECTS", look.MUTED, 8.5, bold=True))
        self.projects = ProjectList(accent)
        column.addWidget(self.projects)
        self.legend = text_label("", look.MUTED, 8.2)
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend.setText(f'<span style="white-space:pre">{LEGEND}</span>')
        column.addWidget(self.legend)
        column.addStretch(1)
        self.setWidget(inside)

    def set(self, snap: DashSnapshot, project: str) -> None:
        self.projects.set(project_rows(snap), project)
