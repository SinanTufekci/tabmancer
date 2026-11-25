"""The drawer beside the board: the picked card, as the terminal dashboard's preview says it.

A session: how it stands (sessionwords.status_row), whose it is and its title, what Enter does with it,
the buttons for what acts on it, what was said in it (said_rows: a member's messages, where the
filter's words were found, its summary, the first and last prompt, the last reply, the files edited)
and its facts (fact_rows). The inputs are the terminal dashboard's (app.update_detail), read off the
snapshot: its team and member, its effort, its folder's git, its tokens, its context cap, how it opens.

A team's card: its name, how many are open and need you, its goal, the team as a small live picture
(teamgraph, compact) and a line a member.

The buttons are the keys the terminal's footer offers on that row (footer.allowed), worded as its
footer words them; each runs the window's own action for its key, and is greyed, saying why, while
the window does not do that key yet.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import actions, agy, footer, picks, sessionwords, summarize, teams, teamwords
from .. import viewmodel as VM
from ..launch import Launch, default_mode
from ..sessions import Session, Status, folder_key
from ..store import DashSnapshot
from . import look
from .teamgraph import TeamGraph
from .widgets import facts_html, last_words, text_label, tone_colour

ROOM = 60  # the preview's room in characters, as the terminal's beside the list (said_rows clips to it)
MORE = 3  # as much of each as the terminal's preview beside the list shows
NOTHING = "Pick a card: how it stands, what was said in it, and its facts."
# The buttons: (key, the dashboard action, its words where the footer gives none). Shown when the
# footer offers the key on this row (footer.allowed is True).
BUTTONS = (("Enter", "open", "Open"), ("p", "prompt", "Tell it…"), ("h", "handover", "Hand over"),
           ("c", "continue_at_reset", "Continue"), ("M", "monitor", "Monitor"), ("f", "pin", "Pin"),
           ("x", "hide", "Hide"))
PER_ROW = 3


def cell_html(cell: sessionwords.Cell) -> str:
    """A row's words as HTML: a str as it is, parts each in its style."""
    if isinstance(cell, str):
        return look.styled(cell, look.TEXT)
    return "".join(look.styled(words, style) for words, style in cell)


def rows_html(rows: list[sessionwords.Row]) -> str:
    """Rows as a table: the quiet label left, the words beside it ('' goes on from the row above)."""
    out = []
    for label, cell in rows:
        out.append(f'<tr><td style="color:{look.MUTED};padding-right:10px;white-space:pre" valign="top">'
                   f"{escape(label)}</td><td>{cell_html(cell)}</td></tr>")
    return f'<table cellspacing="0" cellpadding="1">{"".join(out)}</table>'


def member_ids(team_states: dict[str, teams.TeamState]) -> dict[str, tuple[str, str]]:
    """The team members' sessions: session id -> (team, member)."""
    return {session.session_id: (name, member) for name, state in team_states.items()
            for member, session in state.sessions.items() if session is not None}


class Inputs:
    """What the preview says of a session, read as the terminal dashboard reads it (app.update_detail,
    meta_table, launch_for), off the snapshot this window holds."""

    def __init__(self, snap: DashSnapshot, accent: str, default_cap: int, summaries: dict[str, dict]) -> None:
        self.snap, self.accent, self.default_cap, self.summaries = snap, accent, default_cap, summaries
        self.members = member_ids(snap.team_states)

    def member(self, session: Session) -> tuple[str, str] | None:
        return self.members.get(session.session_id)

    def team(self, session: Session) -> teams.Team | None:
        found = self.member(session)
        state = self.snap.team_states.get(found[0]) if found else None
        return state.team if state is not None else None

    def launch(self, session: Session) -> Launch:
        """How a closed session opens (app.launch_for): a background one is attached to."""
        job = self.snap.background.get(session.session_id) if session.status is Status.BACKGROUND else None
        return actions.launch_for(session, default_mode(session.status), job, None)

    def messages(self, session: Session) -> list[sessionwords.Part] | None:
        """A member's messages ('3 in · 2 out · ← lead 2m: …'); None for a session in no team."""
        found = self.member(session)
        state = self.snap.team_states.get(found[0]) if found else None
        if found is None or state is None:
            return None
        return sessionwords.messages(state.said, found[1], teamwords.colours(state.team, self.accent))

    def said(self, session: Session, found: tuple[str, int, int] | None) -> list[sessionwords.Row]:
        return sessionwords.said_rows(session, messages=self.messages(session), found=found,
                                      summary=self.summaries.get(session.session_id), summarizing=False,
                                      summary_model=summarize.MODEL, room=ROOM, more=MORE, accent=self.accent)

    def facts(self, session: Session) -> list[sessionwords.Row]:
        claude = session.cli == "claude"
        return sessionwords.fact_rows(
            session, member=self.member(session),
            effort=VM.effort_of(session, self.member(session) is not None) if claude else "",
            git=self.snap.git.get(folder_key(session.cwd)), tokens=self.snap.session_tokens.get(session.session_id),
            cap=teams.cap_of(session.session_id, self.team(session)) if session.context and claude else 0,
            default_cap=self.default_cap, launch=lambda: self.launch(session), mode=default_mode(session.status),
            armed=self.snap.armed.get(session.session_id), queued=session.session_id in self.snap.queued,
            accent=self.accent)

    def enter(self, session: Session) -> str:
        return sessionwords.enter_words(session, lambda: self.launch(session), set())


class Drawer(QScrollArea):
    """The picked card's preview, and buttons for what acts on it."""

    act = Signal(str)  # a button's key: the window runs its action

    def __init__(self, accent: str) -> None:
        super().__init__()
        self.accent = accent
        self.why: dict[str, str | None] = {}  # a key -> None when the window does it, else why not
        self.default_cap = picks.cap_tokens(picks.default_cap())  # as the terminal reads it, once
        self.summaries = summarize.load_cache()
        self.key: str | None = None  # the card shown: a session id, or VM.TEAM_CARD + a team's name
        self.offered: list[tuple[str, str]] = []  # the buttons shown: (key, words)
        self.rows: tuple[list[sessionwords.Row], list[sessionwords.Row]] = ([], [])  # a session's: said, facts
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inside = QWidget()
        inside.setObjectName("drawerinside")
        inside.setStyleSheet(
            "QWidget#drawerinside { background: transparent; }"
            f" QPushButton#drawerbutton {{ background: {look.PANEL}; color: {look.TEXT}; border: 1px solid {look.BORDER};"
            " border-radius: 4px; padding: 4px 6px; }"
            f" QPushButton#drawerbutton:hover {{ border-color: {accent}; }}"
            f" QPushButton#drawerbutton:disabled {{ color: {look.DIM}; background: {look.SUNKEN}; }}")
        column = QVBoxLayout(inside)
        column.setContentsMargins(2, 2, 6, 8)
        column.setSpacing(8)
        self.status = self.label(column, 9.2)
        self.name = self.label(column, 11, bold=True)
        self.title = self.label(column, 10)
        self.enter = self.label(column, 9)
        self.buttons = QWidget()
        self.grid = QGridLayout(self.buttons)
        self.grid.setContentsMargins(0, 2, 0, 2)
        self.grid.setSpacing(6)
        column.addWidget(self.buttons)
        self.graph = TeamGraph(compact=True)
        self.graph.setMinimumSize(150, 190)
        self.graph.hint = ""
        column.addWidget(self.graph)
        self.said = self.label(column, 9)
        self.facts_heading = text_label("FACTS", look.MUTED, 8.5, bold=True)
        column.addWidget(self.facts_heading)
        self.facts = self.label(column, 9)
        column.addStretch(1)
        self.setWidget(inside)
        self.show_nothing()

    @staticmethod
    def label(column: QVBoxLayout, size: float, bold: bool = False) -> QLabel:
        made = text_label("", look.TEXT, size, bold=bold, wrap=True)
        made.setTextFormat(Qt.TextFormat.RichText)
        made.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(made)
        return made

    # --- what it shows ------------------------------------------------------------------------------

    def set(self, snap: DashSnapshot | None, key: str | None, found: tuple[str, int, int] | None = None,
            why: dict[str, str | None] | None = None) -> None:
        """The card picked on the board (key), as the snapshot has it; found: the filter's words where they
        were said in it (search.Index.snippet), when they were; why: each button's key -> None when the
        window does it, else why not (greyed, saying so; a key left out is greyed)."""
        self.key, self.why = key, dict(why or {})
        if snap is None or not key:
            self.show_nothing()
        elif key.startswith(VM.TEAM_CARD):
            self.show_team(snap, key[len(VM.TEAM_CARD):])
        elif key in snap.sessions:
            self.show_session(snap, snap.sessions[key], found)
        else:
            self.show_nothing()

    def show_nothing(self) -> None:
        self.rows = ([], [])
        for made in (self.name, self.title, self.enter, self.said, self.facts):
            made.setText("")
        self.status.setText(look.span(NOTHING, look.MUTED))
        self.offer([])
        self.graph.hide()
        self.facts_heading.hide()

    def show_session(self, snap: DashSnapshot, session: Session, found: tuple[str, int, int] | None) -> None:
        inputs = Inputs(snap, self.accent, self.default_cap, self.summaries)
        words, means = sessionwords.status_row(session)
        self.status.setText(cell_html(words) + look.span("   " + means, look.MUTED))
        member = inputs.member(session)
        whose = f"{member[0]} · {member[1]}" if member else session.project
        self.name.setText(look.span(whose, f"bold {self.accent}"))
        self.title.setText(look.styled(session.title, look.TEXT))
        try:
            enter = look.span("⏎ ", f"bold {self.accent}") + look.styled(inputs.enter(session), self.accent)
        except agy.NoModel as why:  # agy's chain all out of quota: it would not open (its facts say so too)
            enter = look.span(str(why), look.AMBER)
        self.enter.setText(enter)
        row = footer.row(snap.sessions.values(), None, session, 0, inputs.members)
        self.offer(self.offered_for(snap, row, pinned=session.session_id in snap.pinned, at_limit=VM.at_limit(session)))
        self.graph.hide()
        self.rows = (inputs.said(session, found), inputs.facts(session))
        self.said.setText(rows_html(self.rows[0]))
        self.facts_heading.show()
        self.facts.setText(rows_html(self.rows[1]))

    def show_team(self, snap: DashSnapshot, name: str) -> None:
        view = next((team for team in snap.view.teams if team.name == name), None)
        state = snap.team_states.get(name)
        if view is None or state is None:
            self.show_nothing()
            return
        colours = teamwords.colours(state.team, self.accent)
        self.status.setText(cell_html(teamwords.head(state, state.tokens(snap.session_tokens), self.accent)))
        needing = [member.name for member in view.members if member.needs_you]
        self.name.setText(look.span(f"needs you: {', '.join(needing)}", f"bold {look.RED}") if needing else "")
        self.title.setText(look.span("Goal  ", look.MUTED) + look.styled(teamwords.one_line(state.team.goal), look.TEXT))
        if view.open:
            self.enter.setText(look.span("⏎ ", f"bold {self.accent}") + look.span("team view", self.accent))
        else:
            last = f", {state.team.lead.name} last" if state.team.lead is not None else ""
            self.enter.setText(look.span(f"Closed. Enter starts it: each member resumed as itself{last}.", look.AMBER))
        at_work = bool(view.open)
        row = footer.row(snap.sessions.values(), (name, at_work, False), None, 0, member_ids(snap.team_states))
        stopped = any(session is not None and VM.at_limit(session) for session in state.sessions.values())
        self.offer(self.offered_for(snap, row, pinned=False, at_limit=stopped))
        self.graph.set(view, state.said, colours)
        self.graph.show()
        lines = []
        for member in view.members:
            lines.append(look.span(member.name, f"bold {colours.get(member.name, look.TEXT)}")
                         + look.span("  ·  ", look.DIM) + look.span(member.label, tone_colour(member.tone))
                         + look.span("  ·  ", look.DIM) + facts_html(member, leave_out_model=False)
                         + "<br>" + last_words(member, colours))
        self.said.setText("<br><br>".join(lines))
        self.rows = ([], [])
        self.facts_heading.hide()
        self.facts.setText("")

    # --- the buttons --------------------------------------------------------------------------------

    def offered_for(self, snap: DashSnapshot, row: footer.Row, pinned: bool, at_limit: bool) -> list[tuple[str, str]]:
        """The keys the terminal's footer offers on this row, as it words them; Continue only for what a
        limit stopped (the footer shows c while anything is: here it is this card's button)."""
        out = []
        for key, action, words in BUTTONS:
            if action == "continue_at_reset" and not at_limit:
                continue
            if footer.allowed(action, on_top=False, monitor=False, projects_focused=False, live_mode=False,
                              own=(), sessions=snap.sessions.values(), at=lambda: row) is not True:
                continue
            said = footer.words(action, row, 0) or ("Unpin" if action == "pin" and pinned else words)
            out.append((key, said))
        return out

    def offer(self, offered: list[tuple[str, str]]) -> None:
        if offered != self.offered:
            while self.grid.count():
                gone = self.grid.takeAt(0).widget()
                if gone is not None:
                    gone.hide()
                    gone.setParent(None)
                    gone.deleteLater()
            for index, (key, words) in enumerate(offered):
                made = QPushButton(f"{words}  {key}")
                made.setObjectName("drawerbutton")
                made.setFont(look.font(8.6))
                made.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                made.setProperty("key", key)
                made.clicked.connect(self.clicked)
                self.grid.addWidget(made, index // PER_ROW, index % PER_ROW)
            self.offered = offered
        self.buttons.setVisible(bool(offered))
        for index in range(self.grid.count()):
            made = self.grid.itemAt(index).widget()
            key = str(made.property("key"))
            made.setEnabled(key in self.why and self.why[key] is None)
            made.setToolTip(self.why.get(key) or "")

    def clicked(self) -> None:
        made = self.sender()
        if made is not None:
            self.act.emit(str(made.property("key")))

    def plain(self) -> str:
        """What it says, as plain text (the tests read it)."""
        parts = []
        for made in (self.status, self.name, self.title, self.enter, self.said, self.facts):
            document = QTextDocument()
            document.setHtml(made.text())
            parts.append(document.toPlainText())
        return "\n".join(part for part in parts if part)

