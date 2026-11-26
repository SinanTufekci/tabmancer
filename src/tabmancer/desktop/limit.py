"""c in the window: the sessions stopped at a usage limit, and what becomes of the ticked ones, as the terminal
dashboard's c dialog asks it (LimitDialog: a heading, the plan and the other accounts, a row for each stopped
session biggest context first, what going on costs on another account, where it sends a conversation that ran
on another organisation, the ways on, and the keys). Its words and rules are limitwords.py's, one copy with
the terminal's (limitwords.ask, ways, first_way, pick_refusal).

The window does not act yet (it never holds the actor lock), so it offers no "when the limit lifts" and no
"do not continue them by themselves": only the dashboard that acts carries those out, and the dialog says
where (limitwords.NOT_HERE). Given acts, it offers every way, as the terminal's does. What the answer does is
the engine's (Engine.choose), as in the terminal."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QFrame, QVBoxLayout, QWidget

from .. import limitwords
from . import look
from .clisettings import PickRow, Row, parts_html
from .formparts import keys_html
from .handover import label

TICKED, UNTICKED = "[x] ", "[ ] "
SESSIONS, WAYS = "sessions", "ways"  # where ↑↓ and Space go: Tab moves between them


def title_html(count: int, when: str) -> str:
    """'Stopped at a usage limit  2 sessions · it lifts at 16:00', as the terminal's title() sets it."""
    words, *about = limitwords.title_words(count, when)
    return look.span(words, f"bold {look.TEXT}") + "".join(
        look.span("  " if at == 0 else " · ", look.MUTED) + look.span(piece, look.MUTED) for at, piece in enumerate(about))


class LimitDialog(QDialog):
    """↑↓ pick a session or a way, Space (or a click on a session) ticks it, Tab moves between the sessions and
    the ways, Enter (or a double click on a way) chooses the picked way for the ticked ones (chosen: the way's
    id and the ticked ids, once), Esc chooses nothing. Every session starts ticked; the picked way starts as
    limitwords.first_way has it. Window-modal and never waited on (open(), not exec())."""

    chosen = Signal(str, list)

    def __init__(self, parent: QWidget, asked: limitwords.Ask, accent: str, acts: bool = False) -> None:
        super().__init__(parent)
        self.asked, self.accent, self.acts = asked, accent, acts
        self.answered = False
        self.sessions = limitwords.biggest(asked.stopped)
        self.ticked = [s.session_id for s in self.sessions]
        self.ways = limitwords.ways(asked.when, asked.room, asked.armed, bool(asked.away), acts)
        self.way = limitwords.first_way([way for way, _ in self.ways], bool(asked.away))
        self.at, self.place = 0, WAYS
        self.setWindowTitle(limitwords.title_words(len(asked.stopped), asked.when)[0])
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(760)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the keys are the dialog's: nothing in it takes the focus
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        box.addWidget(label(title_html(len(asked.stopped), asked.when), wrap=True))
        self.plan = label(look.styled(asked.plan, look.MUTED), wrap=True)  # escaped; one account a line
        self.plan.setVisible(bool(asked.plan))
        box.addWidget(self.plan)
        self.session_rows = self.rows(box, len(self.sessions), self.click_session)
        self.cost = label("", wrap=True)
        box.addWidget(self.cost)
        self.away = label("", wrap=True)
        box.addWidget(self.away)
        self.way_rows = self.rows(box, len(self.ways), self.click_way, pick=True)
        self.not_here = label(look.span(limitwords.NOT_HERE, look.MUTED), wrap=True)
        self.not_here.setVisible(not acts)
        box.addWidget(self.not_here)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        box.addWidget(label(keys_html(limitwords.KEY_PAIRS)))
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QFrame#listed {{ background:{look.BLACK}; border:none; }}")
        self.finished.connect(self.deleteLater)
        self.draw()

    def rows(self, box: QVBoxLayout, count: int, click, pick: bool = False) -> list[Row]:
        listed = QFrame()
        listed.setObjectName("listed")
        lines = QVBoxLayout(listed)
        lines.setContentsMargins(0, 4, 0, 4)
        lines.setSpacing(2)
        made: list[Row] = []
        for at in range(count):
            if pick:
                picked = PickRow(at)
                picked.chosen.connect(self.choose_way)
                row: Row = picked
            else:
                row = Row(at)
            row.clicked.connect(click)
            lines.addWidget(row)
            made.append(row)
        box.addWidget(listed)
        return made

    def draw(self) -> None:
        """Each row as the terminal's dialog words it, the picked one lit; the cost and the other organisation
        for the ticked ones."""
        for at, (session, row) in enumerate(zip(self.sessions, self.session_rows, strict=True)):
            mark = TICKED if session.session_id in self.ticked else UNTICKED
            row.setText(look.span(mark, look.TEXT) + parts_html(limitwords.line_parts(session)))
            self.light(row, self.place == SESSIONS and at == self.at)
        for at, ((_, parts), row) in enumerate(zip(self.ways, self.way_rows, strict=True)):
            row.setText(parts_html(parts))
            self.light(row, at == self.way, self.place == WAYS)
        cost = limitwords.cost_parts(limitwords.ticked_total(self.asked.stopped, self.ticked))
        self.cost.setText(parts_html([(words, style or look.MUTED) for words, style in cost]))
        self.cost.setVisible(bool(cost))
        away = limitwords.away_parts(self.ticked, self.asked.away, self.asked.where)
        self.away.setText(parts_html(away))
        self.away.setVisible(bool(away))

    def light(self, row: Row, picked: bool, here: bool = True) -> None:
        edge = (self.accent if here else look.BORDER) if picked else "transparent"
        ground = look.PANEL if picked else "transparent"
        row.setStyleSheet(f"background:{ground}; border-left:2px solid {edge}; padding:4px 10px;")

    def click_session(self, at: int) -> None:
        self.place, self.at = SESSIONS, at
        self.tick()

    def click_way(self, at: int) -> None:
        self.place, self.way = WAYS, at
        self.draw()

    def tick(self) -> None:
        sid = self.sessions[self.at].session_id
        self.ticked = [s for s in self.ticked if s != sid] if sid in self.ticked else \
            [s.session_id for s in self.sessions if s.session_id in {*self.ticked, sid}]
        self.note.setText("")
        self.draw()

    def step(self, by: int) -> None:
        if self.place == SESSIONS and self.sessions:
            self.at = (self.at + by) % len(self.sessions)
        else:
            self.way = (self.way + by) % len(self.ways)
        self.draw()

    def switch(self) -> None:
        self.place = WAYS if self.place == SESSIONS or not self.sessions else SESSIONS
        self.draw()

    def choose_way(self, at: int | None = None) -> None:
        """The picked way (or the one double-clicked) for the ticked ones; with none ticked, it says to tick one
        first and stays open (limitwords.pick_refusal)."""
        if self.answered:
            return
        if at is not None:
            self.way = at
        way = self.ways[self.way][0]
        refused = limitwords.pick_refusal(self.ticked, way)
        if refused is not None:
            self.note.setText(look.span(refused.words, look.AMBER))
            self.draw()
            return
        self.answered = True
        self.accept()
        self.chosen.emit(way, list(self.ticked))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.step(-1)
        elif key == Qt.Key.Key_Down:
            self.step(1)
        elif key == Qt.Key.Key_Space and self.place == SESSIONS:
            self.tick()
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.switch()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.choose_way()
        else:
            super().keyPressEvent(event)  # Esc: rejected, nothing chosen

    def focusNextPrevChild(self, forward: bool) -> bool:
        return False  # Tab is the dialog's own key: between the sessions and the ways
