"""The rail beside the board: the projects, each with how its sessions stand and where its folder is
in git, and a click narrows every lane to one of them; the teams, each as a small live picture; and the
week's tokens.

The lines are the terminal dashboard's sidebar (viewmodel.project_rows): All projects first, then one
per project in a stable alphabetical order, marked by its most urgent session (red waiting for you,
green open, amber to continue), ± changes not committed and ↑ commits not pushed, and how many sessions
it has in sight. The project the list is narrowed to is lit; a click on it again (or on All projects)
shows every project.

A team is split across the lanes, a card for each member's state; the rail holds it together: its
name, how many members are open and how many need you, and the Team panel's picture drawn small
(teamgraph, compact). A click picks its card on the board and its tab in the Team panel.

The chart is the terminal's sidebar chart over the last seven days, today's bar in the accent, and
follows the project picked as that one does (viewmodel.token_ids); today's tokens head it.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QToolTip, QVBoxLayout, QWidget

from .. import teams, teamwords, tokens
from .. import viewmodel as VM
from ..store import DashSnapshot
from . import look
from .teamgraph import TeamGraph
from .teampanel import tab_tip
from .widgets import text_label

ROW = 22  # a project's line
RULE = 9  # the room of the rule under All projects
MARKS = {"waiting": ("●", look.RED, "one waiting for you"), "open": ("●", look.GREEN, "open"),
         "continue": ("▲", look.AMBER, "one to continue")}
SIGNS = {"±": (look.AMBER, "changes not committed"), "↑": (look.CYAN, "commits not pushed")}
LEGEND = (f'{look.span("● ", look.RED)}{look.span("your turn  ", look.MUTED)}{look.span("● ", look.GREEN)}'
          f'{look.span("open", look.MUTED)}<br>{look.span("▲ ", look.AMBER)}{look.span("to continue  ", look.MUTED)}'
          f'{look.span("± ", look.AMBER)}{look.span("↑ ", look.CYAN)}{look.span("git", look.MUTED)}')
TEAM_HINT = "a click: its card on the board"
WEEK_DAYS = 7
PAST_BAR = "#3a3a3a"  # the days before today; today's is the accent
COUNTING = "TOKENS · COUNTING…"  # till the first count is in


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


def team_counts(view: VM.TeamView) -> tuple[str, str]:
    """A team's line on the rail: how many are open, how many need you (red then); and its colour."""
    needing = sum(1 for member in view.members if member.needs_you)
    return f"{view.open} open" + (f" · {needing} !" if needing else ""), look.RED if needing else look.MUTED


class TeamCard(QFrame):
    """A team on the rail: its name, how many are open and need you, the team as a small live picture."""

    picked = Signal(str)  # its name

    def __init__(self, name: str, accent: str) -> None:
        super().__init__()
        self.name, self.accent = name, accent
        self.setObjectName("railteam")
        self.setStyleSheet(f"QFrame#railteam {{ background: {look.SUNKEN}; border: 1px solid {look.BORDER};"
                           " border-radius: 6px; }")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.colours: dict[str, str] = {}
        self.coloured: list[str] = []  # the members the colours were picked for
        column = QVBoxLayout(self)
        column.setContentsMargins(8, 5, 8, 4)
        column.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = text_label(name, accent, 9, bold=True)
        self.counts = text_label("", look.MUTED, 8)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.counts)
        column.addLayout(top)
        self.graph = TeamGraph(compact=True)
        self.graph.hint = TEAM_HINT  # its clicks go on to the card (mouseReleaseEvent)
        self.graph.setMinimumSize(150, 172)  # narrower than the panels it was made for: taller, the lead's words clear
        column.addWidget(self.graph)

    def set(self, view: VM.TeamView, state: teams.TeamState | None) -> None:
        words, colour = team_counts(view)
        if words != self.counts.text():
            self.counts.setText(words)
            self.counts.setStyleSheet(f"color:{colour}; background:transparent;")
        self.setToolTip(f"{tab_tip(view)}\n{TEAM_HINT}")
        if state is None:
            return
        names = [member.name for member in state.team.members]
        if names != self.coloured:  # a member joined or left: the colours picked again, as the Team panel does
            self.colours, self.coloured = teamwords.colours(state.team, self.accent), names
        self.graph.set(view, state.said, self.colours)

    def mousePressEvent(self, event) -> None:
        event.accept()  # the release comes here too (from the picture, which passes its clicks on)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.picked.emit(self.name)
        super().mouseReleaseEvent(event)


class TeamList(QWidget):
    """A TeamCard a team, in the view's order."""

    picked = Signal(str)

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.cards: list[TeamCard] = []
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(6)

    def set(self, snap: DashSnapshot) -> None:
        views = snap.view.teams
        if [view.name for view in views] != [card.name for card in self.cards]:
            for card in self.cards:
                card.hide()
                card.setParent(None)
                card.deleteLater()
            self.cards = []
            for view in views:
                card = TeamCard(view.name, self.accent)
                card.picked.connect(self.picked)
                self.column.addWidget(card)
                self.cards.append(card)
        for card, view in zip(self.cards, views, strict=True):
            card.set(view, snap.team_states.get(view.name))

    def set_motion(self, mode: str) -> None:
        for card in self.cards:
            card.graph.set_motion(mode)


class WeekChart(QWidget):
    """Tokens a day over the week, a bar a day, today's in the accent, the day's letters under them."""

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.days: list[VM.DayTokens] = []
        self.where = ""  # the project counted, when one is picked
        self.setFixedHeight(64)
        self.setMouseTracking(True)

    def set(self, days: list[VM.DayTokens], where: str = "") -> None:
        if (days, where) != (self.days, self.where):
            self.days, self.where = days, where
            self.update()

    def bar_at(self, x: float) -> int | None:
        if not self.days or self.width() <= 0:
            return None
        index = int(x / (self.width() / len(self.days)))
        return index if 0 <= index < len(self.days) else None

    def tip(self, index: int) -> str:
        entry = self.days[index]
        when = "today" if entry.day == date.today() else f"{entry.day:%a} {entry.day.day} {entry.day:%b}"
        where = f" in {self.where}" if self.where else ""
        return f"{when}: {tokens.human(entry.total)} tokens{where}"

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.bar_at(event.pos().x())
            if index is not None:
                QToolTip.showText(event.globalPos(), self.tip(index), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:
        if not self.days:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(look.font(7))
        most = max(entry.total for entry in self.days)
        step = self.width() / len(self.days)
        room = self.height() - 16
        for index, entry in enumerate(self.days):
            last = index == len(self.days) - 1
            high = room * entry.total / most if most else 0.0
            if entry.total:
                high = max(high, 2.0)  # any tokens show
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.accent if last else PAST_BAR))
            p.drawRoundedRect(QRectF(index * step + step * 0.22, room - high, step * 0.56, max(high, 1.0)), 2, 2)
            p.setPen(QColor(look.TEXT if last else look.MUTED))
            p.drawText(QRectF(index * step, room + 2, step, 14), Qt.AlignmentFlag.AlignCenter, f"{entry.day:%a}"[:2])
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
        column.addSpacing(6)
        self.teams_heading = text_label("TEAMS", look.MUTED, 8.5, bold=True)
        column.addWidget(self.teams_heading)
        self.teams = TeamList(accent)
        column.addWidget(self.teams)
        column.addSpacing(6)
        self.tokens_heading = text_label(COUNTING, look.MUTED, 8.5, bold=True)
        column.addWidget(self.tokens_heading)
        self.week = WeekChart(accent)
        column.addWidget(self.week)
        column.addStretch(1)
        self.setWidget(inside)

    def set(self, snap: DashSnapshot, project: str) -> None:
        self.projects.set(project_rows(snap), project)
        self.teams.set(snap)
        self.teams_heading.setVisible(bool(self.teams.cards))
        self.teams.setVisible(bool(self.teams.cards))
        report = snap.report
        if report is None:
            self.week.set([])
            return
        days = VM.week_tokens(report, VM.token_ids(report, snap.sessions, project), WEEK_DAYS)
        where = next((row.name for row in self.projects.rows if row.key == project), "") \
            if project != VM.ALL_PROJECTS else ""
        self.week.set(days, where)
        self.tokens_heading.setText(f"TOKENS · TODAY {tokens.human(days[-1].total if days else 0)}")
        self.tokens_heading.setToolTip(f"Tokens a day, the last {WEEK_DAYS}" + (f", in {where}" if where else ""))
