"""T in the window: a new team, as the terminal dashboard's new-team form makes one (NewTeamDialog: a name, a
project, a goal and the line-up, one member a line; on the right the team as it stands, what opens and how, or
what is still needed; where the members run, the context cap and the lead's advisor). The rules and the words
are teamforms.py's TeamForm, one copy with the terminal's; this draws them. An agy member is drawn with the model
it will run (agy.resolve: its chain, Neon then Hydrogen by quota, never a Claude model), and a Claude model
asked of one is said, as the team says it where it starts (teams.agy_notes).

What takes seconds runs on a thread of its own (newsession.Asker), back on the window's thread by a Signal: the
line-up Claude suggests (Ctrl+R) and the goal rewritten (Ctrl+E). Ctrl+S closes it with what it makes (made: a
teams.Team, or a teamforms.JoinTeam or TellTeam for a team at work); the window saves and opens it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator, QKeyEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import agy, improve, picks, summarize, teamforms, teams
from ..actions import Note
from ..newforms import ADVISOR_SHORT, CAP_SHORT, home_as_tilde
from ..teamforms import JoinTeam, TellTeam
from . import look
from .formparts import Card, Choice, keys_html
from .handover import label
from .naming import NameDialog
from .newsession import SEVERITY, SPIN_MS, Asker, Improving, put_improved

SUGGEST, IMPROVE = "suggest", "improve-goal"  # what the Asker was asked
# The words are teamforms' (TITLE, KEEP_AS, ...), one copy with the terminal's. The window's key line has room
# for Ctrl+E and for the Runs card's keys, which the terminal names on its rows.
KEYS = [*teamforms.keys_with_improve(teamforms.KEYS)[:-1], ("Ctrl+B", "tabs or background"), ("Ctrl+O", "worktrees"),
        teamforms.KEYS[-1]]
ROLE_KEYS = (("Esc", "close"),)
# The form's tones (teamforms: 'strong', 'muted', 'amber', 'green') in the window's colours.
TONES = {"strong": f"bold {look.TEXT}", "muted": look.MUTED, "amber": f"bold {look.AMBER}", "green": f"bold {look.GREEN}"}
BOX_IDS = ("team-name", "team-folder", "team-goal", "team-members", "team-most", "team-cap", "team-advisor")


# --- the work done off the window's thread ----------------------------------------------------------------

def suggest_now(prompt: str, here: list[teams.Team], goal: str) -> tuple[teams.Suggestion | None, str]:
    """Claude's line-up for the goal (one call on your plan), or why there is none."""
    try:
        answer = summarize.ask(prompt, "", timeout=180, effort=teams.SUGGEST_EFFORT)
        suggestion = teams.read_suggestion(answer, here, goal)
        error = "" if suggestion.lines or suggestion.kind != teams.OWN_TEAM else teamforms.NAMED_NONE
    except Exception as problem:  # noqa: BLE001 - a failed suggestion leaves the box as it was, and says why
        return None, str(problem) or type(problem).__name__
    return suggestion, error


def improve_goal_now(text: str, folder: str) -> tuple[str, str, str]:
    """(the goal sent, its rewrite, why there is none)."""
    try:
        return text, improve.improve_goal(text, folder), ""
    except Exception as problem:  # noqa: BLE001 - no rewrite leaves the goal as it was, and says why
        return text, "", str(problem) or type(problem).__name__


# --- words drawn ------------------------------------------------------------------------------------

def toned(parts: list[tuple[str, str]], skip: int = 0) -> str:
    """The form's (words, tone) parts as HTML, the first skip characters left out."""
    out = []
    for words, tone in parts:
        shown, skip = words[skip:], max(0, skip - len(words))
        if shown:
            out.append(look.span(shown, TONES[tone]))
    return "".join(out)


def still_needed(missing: list[str], loud: bool) -> str:
    return toned(teamforms.still_needed(missing, loud))


def pills(words: list[str], picked: str) -> str:
    return look.span("   ").join(look.span(f"● {word}", f"bold {look.TEXT}") if word == picked
                                  else look.span(f"○ {word}", look.MUTED) for word in words)


def member_words(member: teams.Member) -> str:
    """What a member runs, as its line in the drawing says it: an agy member the model agy will run (its chain,
    never a Claude model), a Claude Code one its pick."""
    if member.cli == "agy":
        model, why = agy.resolve(member.model)
        return f"agy · {agy.name(model)}" if model else f"agy · no model now: {why}"
    return member.pick.words() or "default"


def drawing(team: teams.Team) -> str:
    """The team as it will open: the lead on top, the members under it (peers side by side in one list)."""
    lines = []
    for member in sorted(team.members, key=lambda m: not m.lead):
        mark = "◆" if member.lead else "●"
        colour = look.AMBER if member.lead else look.CYAN
        lines.append(look.span(f"{mark} ", colour) + look.span(team.session_name(member), f"bold {look.TEXT}")
                     + look.span(f"  {member_words(member)}", look.MUTED)
                     + (look.span(f"  {member.focus}", look.TEXT) if member.focus and not member.lead else ""))
    lines += [look.span(note, look.AMBER) for note in teams.agy_notes(team)]
    return "<br>".join(lines)


class RoleDialog(QDialog):
    """Ctrl+G: what a member is told when it starts: its role word for word, and its first message
    (teamforms.role_words). Esc, Enter or Ctrl+G closes it."""

    def __init__(self, parent: QWidget, team: teams.Team, member: teams.Member) -> None:
        super().__init__(parent)
        said = teamforms.role_words(team, member)
        self.setWindowTitle(said.head)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(640, 420)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label(look.span(said.head, f"bold {look.TEXT}")
                             + "".join(look.span(f"  {part}", look.MUTED) for part in said.about), wrap=True)
        box.addWidget(self.heading)
        self.role = QPlainTextEdit(said.role)
        self.role.setReadOnly(True)
        self.role.setFont(look.font(9.5))
        box.addWidget(self.role, 1)
        self.first = label(look.span("Its first message: ", look.MUTED) + look.span(said.first, look.TEXT)
                           if said.first else look.span(said.instead, look.MUTED), wrap=True)
        box.addWidget(self.first)
        box.addWidget(label(keys_html(ROLE_KEYS)))
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QPlainTextEdit {{ color:{look.TEXT}; background:{look.BLACK}; border:none; }}")
        closer = QShortcut(QKeySequence("Ctrl+G"), self)
        closer.activated.connect(self.reject)
        self.finished.connect(self.deleteLater)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.reject()
        else:
            super().keyPressEvent(event)


def row(name: str, widget: QWidget, aside: str = "") -> QWidget:
    """A box with its name on the left (and a muted word after it)."""
    holder = QWidget()
    line = QHBoxLayout(holder)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(8)
    named = label(look.span(name, look.MUTED))
    named.setFixedWidth(64)
    line.addWidget(named)
    line.addWidget(widget, 1 if not aside else 0)
    if aside:
        line.addWidget(label(look.span(aside, look.MUTED)), 1)
    return holder


class NewTeamDialog(QDialog):
    """A name, a project, a goal and the line-up; Ctrl+S makes the team (made, once, and it closes), or says what
    is still needed. Enter in a one-line box goes on to the next box. Ctrl+T the next line-up, Ctrl+L keeps the
    one in the box under a name (twice on a kept one: forgets it), Ctrl+B tabs or the background, Ctrl+O own
    worktrees, Ctrl+R a line-up suggested, Ctrl+E the goal rewritten, Ctrl+G what the member on the cursor's line
    is told, Ctrl+N a new team after Ctrl+R pointed at one at work; Esc makes nothing. Window-modal, never waited
    on."""

    made = Signal(object)  # teams.Team, teamforms.JoinTeam or teamforms.TellTeam

    def __init__(self, parent: QWidget, folder: str, taken: set[str], folders: list[str],
                 working: list[tuple[teams.Team, set[str]]], asker: Asker, accent: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self.form = teamforms.TeamForm(taken, working)
        self.asker = asker
        self.accent = accent
        self.answered = False
        self.asking = False  # Ctrl+R: Claude is being asked for a line-up
        self.improving: str | None = None  # Ctrl+E: the goal being rewritten, as it was sent
        self.improve_note = Improving(teamforms.GOAL_NOTE, f"improving the goal with {improve.MODEL}", clock)
        self.clock = clock
        self.setWindowTitle(teamforms.TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(1040, 640)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        top = QHBoxLayout()
        self.title = label("")
        self.ready = label("")
        top.addWidget(self.title, 1)
        top.addWidget(self.ready)
        box.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(10)
        box.addLayout(body, 1)
        left = QVBoxLayout()
        left.setSpacing(10)
        body.addLayout(left, 1)
        # The team: its name, its project, its goal.
        self.team_card = Card("Team")
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. checkout")
        self.folder = QLineEdit(home_as_tilde(folder) if folder else "")
        completer = QCompleter([home_as_tilde(f) for f in folders], self.folder)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.folder.setCompleter(completer)
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText("e.g. Add Stripe checkout with tests")
        self.goal.setTabChangesFocus(True)
        self.goal.setFixedHeight(72)
        for widget in (self.name, self.folder, self.goal):
            widget.setFont(look.font(10))
        self.team_card.add(row("Name", self.name))
        self.team_card.add(row("Project", self.folder))
        self.team_card.add(row("Goal", self.goal))
        self.improve_line = label("")
        self.team_card.add(self.improve_line)
        left.addWidget(self.team_card)
        # The line-up.
        self.lineup_card = Card("Line-up")
        self.lineups = label("", wrap=True)
        self.lineup_card.add(self.lineups)
        self.members = QPlainTextEdit(teamforms.first_lineup())
        self.members.setFont(look.font(10))
        self.members.setTabChangesFocus(True)
        self.lineup_card.add(self.members, 1)
        self.most = QLineEdit(str(teams.DECIDE_MOST))
        self.most.setValidator(QIntValidator(0, 9, self.most))
        self.most.setMaxLength(1)
        self.most.setFixedWidth(40)
        self.most_row = row("At most", self.most, teamforms.DECIDE_ASIDE)
        self.lineup_card.add(self.most_row)
        left.addWidget(self.lineup_card, 1)
        # On the right: the team as it stands, and where it runs.
        right = QVBoxLayout()
        right.setSpacing(10)
        body.addLayout(right, 1)
        self.graph_card = Card("")
        self.at_work = label("", wrap=True)
        self.graph_card.add(self.at_work)
        self.preview = label("", wrap=True)
        self.graph_card.add(self.preview)
        self.drawn = label("", wrap=True)
        self.drawn.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.graph_card.add(self.drawn, 1)
        right.addWidget(self.graph_card, 1)
        self.runs_card = Card("Runs")
        self.where = label("", wrap=True)
        self.runs_card.add(self.where)
        self.cap = Choice(teamforms.CAPS, picks.default_cap(), accent, CAP_SHORT)
        self.advisor = Choice(teamforms.ADVISOR_CHOICES, picks.NO_ADVISOR, accent, ADVISOR_SHORT)
        self.runs_card.add(row("Context", self.cap))
        self.runs_card.add(row("Advisor", self.advisor))
        right.addWidget(self.runs_card)
        self.help = label("", wrap=True)
        box.addWidget(self.help)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        self.keys = label(keys_html(KEYS), wrap=True)
        box.addWidget(self.keys)
        self.ids: dict[QWidget, str] = dict(zip((self.name, self.folder, self.goal, self.members, self.most, self.cap, self.advisor),
                            BOX_IDS, strict=True))
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QLineEdit, QPlainTextEdit {{ color:{look.TEXT}; background:{look.BLACK};"
                           f" border:1px solid {look.BORDER}; border-radius:4px; padding:4px 6px; }}"
                           f"QLineEdit:focus, QPlainTextEdit:focus {{ border-color:{accent}; }}")
        self.shortcuts: list[QShortcut] = []
        for key, act in (("Ctrl+S", self.create_team), ("Ctrl+T", self.next_lineup), ("Ctrl+L", self.keep_lineup),
                         ("Ctrl+B", self.toggle_background), ("Ctrl+O", self.toggle_worktrees),
                         ("Ctrl+R", self.suggest), ("Ctrl+G", self.show_role), ("Ctrl+E", self.improve),
                         ("Ctrl+N", self.new_instead)):
            made = QShortcut(QKeySequence(key), self)
            made.activated.connect(act)
            self.shortcuts.append(made)
        self.spinner = QTimer(self)
        self.spinner.setInterval(SPIN_MS)
        self.spinner.timeout.connect(self.show_improving)
        for line in (self.name, self.folder, self.most):
            line.textChanged.connect(self.show_preview)
        self.members.textChanged.connect(self.show_preview)
        self.goal.textChanged.connect(self.goal_changed)
        self.cap.changed.connect(self.show_help)
        self.advisor.changed.connect(self.show_help)
        asker.answered.connect(self.answered_by)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        focus = app.focusChanged
        focus.connect(self.focus_moved)
        self.finished.connect(lambda: focus.disconnect(self.focus_moved))
        self.finished.connect(self.deleteLater)
        self.show_improving()
        self.show_preview()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.name.setFocus()

    # --- the boxes, as the form's rules read them -------------------------------------------------------

    def fields(self) -> teamforms.Fields:
        return teamforms.Fields(self.name.text(), self.folder.text(), self.goal.toPlainText(),
                                self.members.toPlainText(), self.most.text(), self.cap.value, self.advisor.value)

    def set_members(self, text: str) -> None:
        self.members.setPlainText(text)

    def focused_id(self) -> str:
        focused = self.focusWidget()
        return self.ids.get(focused, "") if focused is not None else ""

    def show_help(self, *_value: str) -> None:
        self.help.setText(look.span(self.form.help(self.focused_id(), self.fields()), look.MUTED))

    def focus_moved(self, _old: QWidget | None, new: QWidget | None) -> None:
        if new is not None and self.isAncestorOf(new):
            self.show_help()

    def say(self, note: Note) -> None:
        """A note over the keys, as the terminal's form says it in a toast."""
        self.note.setText(look.span(note.words, SEVERITY.get(note.severity, look.MUTED)))

    # --- what opens, or what is still needed -----------------------------------------------------------

    def show_preview(self) -> None:
        """The cards as the boxes now say: the line-ups, where the members run, and on the right the team as
        it stands under a line on what opens and how (or what is still needed)."""
        fields = self.fields()
        folder = fields.read()[1]
        if self.form.toward_gone(folder):
            self.leave_toward()  # another project now: that team is not at work in it
            return
        self.show_at_work(folder)
        if self.form.toward is not None and not self.asking:
            self.show_toward(fields)
            return
        self.title.setText(look.span(teamforms.TITLE, f"bold {look.TEXT}"))
        self.lineup_card.set_title("Line-up")
        self.lineups.setVisible(True)
        self.lineups.setText(pills([*self.form.lineups, teamforms.OWN], self.form.shown_lineup(fields.members)))
        self.where.setText(self.where_html())
        self.most_row.setVisible(self.form.deciding(fields.members))
        self.show_help()
        self.drawn.setText(drawing(self.form.drawn(fields)))  # as it stands, whatever is still missing
        shown = self.form.preview(fields, self.asking)
        if shown.asking:
            self.graph_card.set_title(shown.title)
            self.ready.setText("")
            self.preview.setText(look.span(shown.head[0][0], f"italic {look.MUTED}"))
            return
        if shown.missing:
            self.graph_card.set_title(shown.title)
            self.ready.setText("")
            self.preview.setText(still_needed(shown.missing, shown.loud))
            return
        self.graph_card.title.setText(look.span(shown.tabs, f"bold {look.GREEN}"))
        self.ready.setText(self.ready_html("Ctrl+S creates it"))
        self.preview.setText(toned(shown.head, len(shown.tabs) + 2))  # the title says what opens; this, how

    def ready_html(self, does: str) -> str:
        return look.span("● ", look.GREEN) + look.span("ready", f"bold {look.GREEN}") + look.span(f"  {does}", look.MUTED)

    def where_html(self) -> str:
        """Where the members run (Ctrl+B) and whether in worktrees of their own (Ctrl+O)."""
        where = self.form.where(self.form.peers(self.members.toPlainText()))
        (first, on), *others = where.runs
        out = pills([first], first if on else "") + look.span(where.note, look.MUTED)
        if where.key:
            out += look.span(f"  {where.key}", look.MUTED)
        for words, on in others:
            out += "<br>" + pills([words], words if on else "")
        out += "<br>" + (look.span("[x] ", f"bold {look.CYAN}") + look.span(where.worktrees_words, f"bold {look.TEXT}")
                         if where.worktrees else look.span(f"[ ] {where.worktrees_words}", look.MUTED))
        return out + look.span(f"  {where.worktrees_key}", look.MUTED)

    # --- a team at work in the project -------------------------------------------------------------------

    def show_at_work(self, folder: str) -> None:
        names = self.form.at_work(folder)
        self.at_work.setVisible(bool(names) and self.form.toward is None)
        if names:
            self.at_work.setText(look.span("▲ ", look.AMBER) + look.span(names, f"bold {look.AMBER}")
                                 + look.span(" at work in this project: Ctrl+R weighs the goal against it.", look.MUTED))

    def show_toward(self, fields: teamforms.Fields) -> None:
        """Ctrl+R said to add members to a team at work, or that it has the goal: what joins it (or what its lead
        is told), that team drawn, and what Ctrl+S does."""
        view = self.form.toward_view(fields)
        if view is None:
            self.leave_toward()
            return
        self.lineups.setVisible(False)
        self.most_row.setVisible(False)
        self.show_help()
        self.lineup_card.set_title(view.lineup_title)
        self.drawn.setText(drawing(view.team))
        self.title.setText(look.span(view.title[0], f"bold {look.TEXT}") + look.span(f"  {view.title[1]}", look.MUTED))
        self.graph_card.title.setText(look.span(view.heading[0], TONES[view.heading[1]]))
        if view.missing:
            self.ready.setText("")
            self.preview.setText(still_needed(view.missing, view.loud))
        else:
            self.ready.setText(self.ready_html(view.does))
            self.preview.setText(toned(view.how))

    def leave_toward(self) -> None:
        """A team of its own after all: the line-up that was in the box before, as you left it."""
        self.set_members(self.form.leave_toward(self.members.toPlainText()))
        self.show_preview()

    def new_instead(self) -> None:
        if self.form.toward is not None:
            self.leave_toward()

    # --- the line-up keys ---------------------------------------------------------------------------------

    def next_lineup(self) -> None:
        self.set_members(self.form.next_lineup(self.members.toPlainText()))
        self.show_preview()

    def toggle_background(self) -> None:
        note = self.form.toggle_background(self.members.toPlainText())
        if note is not None:
            self.say(note)
            return
        self.show_preview()

    def toggle_worktrees(self) -> None:
        self.form.toggle_worktrees()
        self.show_preview()

    def keep_lineup(self) -> None:
        """Ctrl+L: the members in the box kept as a line-up of your own, under a name asked for (NameDialog); on
        a kept one as it is, twice: forgotten."""
        step = self.form.keep_step(self.members.toPlainText())
        if step.note is not None:
            self.say(step.note)
        if step.changed:
            self.show_preview()
        if not step.ask:
            return
        asked = NameDialog(self, teamforms.KEEP_AS, "", teamforms.KEEP_EXAMPLE, (("Enter", "keep"), ("Esc", "cancel")), self.accent)
        asked.named.connect(lambda name: self.keep(name, step))
        asked.open()

    def keep(self, name: str, step: teamforms.Keep) -> None:
        kept = self.form.keep(name, step.members, step.peers)
        if kept.note is not None:
            self.say(kept.note)
        if kept.changed:
            self.show_preview()

    def show_role(self) -> None:
        """Ctrl+G: what the member on the cursor's line is told when it starts."""
        lines = self.members.toPlainText().split("\n")
        at = self.members.textCursor().blockNumber()
        found = self.form.role_of(self.fields(), lines[at] if at < len(lines) else "")
        if isinstance(found, Note):
            self.say(found)
        elif found is not None:
            RoleDialog(self, *found).open()

    # --- Ctrl+R and Ctrl+E, asked off the window's thread -------------------------------------------------

    def suggest(self) -> None:
        if self.asking:
            return
        ask = self.form.suggest_ask(self.fields())
        if ask.refusal is not None:
            self.say(ask.refusal)
            return
        if ask.box is not None:  # weighed afresh, from the line-up you had
            self.set_members(ask.box)
        self.asking = True
        self.show_preview()
        self.asker.ask(self, SUGGEST, suggest_now, ask.prompt, ask.here, ask.goal)

    def suggested(self, suggestion: teams.Suggestion | None, error: str) -> None:
        self.asking = False
        done = self.form.suggested(suggestion, error, self.fields())
        if done.box is not None:
            self.set_members(done.box)
        if done.most is not None:
            self.most.setText(done.most)
        self.say(done.note)
        self.show_preview()

    def goal_changed(self) -> None:
        self.show_improving()  # how Ctrl+E went stays only while the goal is what it went for
        self.show_preview()

    def improve(self) -> None:
        if self.improving is not None:
            return
        text = self.goal.toPlainText()
        refusal = teamforms.improve_refusal(text)
        if refusal is not None:
            self.say(refusal)
            return
        self.improving = text
        self.improve_note.since, self.improve_note.outcome = self.clock(), None
        self.goal.setReadOnly(True)
        self.spinner.start()
        self.show_improving()
        self.asker.ask(self, IMPROVE, improve_goal_now, text, self.fields().read()[1])

    def improved(self, sent: str, better: str, why: str) -> None:
        self.improving = None
        self.spinner.stop()
        self.improve_note.since = None
        self.goal.setReadOnly(False)
        put, words, colour, note, severity = put_improved(self.goal, sent, better, why)
        self.improve_note.outcome, self.improve_note.about = (words, colour), self.goal.toPlainText()
        self.show_improving()
        if put:
            self.say(teamforms.IMPROVED)
        elif note:
            self.say(Note(note, severity or "information", 8))

    def show_improving(self) -> None:
        self.improve_line.setText(self.improve_note.html(self.goal.toPlainText()))

    def answered_by(self, owner: object, what: str, answer: Any) -> None:
        if owner is not self:
            return  # another dialog's, closed meanwhile
        if what == SUGGEST:
            self.suggested(*answer)
        elif what == IMPROVE:
            self.improved(*answer)

    # --- Enter, Ctrl+S, Esc -------------------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.focusNextChild()  # Enter goes on to the next box; Ctrl+S creates
        else:
            super().keyPressEvent(event)  # Esc: rejected, nothing made

    def create_team(self) -> None:
        if self.answered:
            return
        made: teams.Team | JoinTeam | TellTeam | None = self.form.create(self.fields())
        if made is None:
            self.show_preview()  # what is still needed, said louder
            return
        self.answered = True
        self.accept()
        self.made.emit(made)
