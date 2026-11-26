"""n in the window: fresh sessions in several projects at once, as the terminal dashboard's n dialog starts
them (NewSessionDialog: the folders to tick, the first message, the model, effort, context and advisor rows,
what will open, the help line and the keys). The rules and the words are newforms.py's NewForm, one copy with
the terminal's; this draws them. agy's Model row is newforms.choices()': its chain (Neon, then Hydrogen, by
quota) or one of the chain's models, never a Claude model and never Argon.

What takes seconds runs on a thread of its own (Asker), back on the window's thread by a Signal: the router's
pick (Ctrl+R), the first message rewritten (Ctrl+E), agy's quota (read once, when agy is picked). Enter (or
Ctrl+S) closes the dialog with what it asks for (requested: a newforms.Request); the window opens it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut, QShowEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import agy, clis, improve, newforms, picks, router
from ..newforms import Project
from . import look
from .formparts import Card, Choice, keys_html
from .handover import label

ROUTE, IMPROVE, QUOTA = "route", "improve", "quota"  # what an Asker was asked
TITLE = "New sessions"
KEYS = (("Space", "tick"), ("Enter/Ctrl+S", "start them"), ("Tab", "next box"), ("Ctrl+R", "router's pick"),
        ("Ctrl+L", "linked"), ("Ctrl+O", "the CLI"), ("Ctrl+E", "improve"), ("Esc", "cancel"))
MESSAGE_NOTE = "optional · Ctrl+E improves it"
MESSAGE_PLACEHOLDER = "Optional. A line each: a session for each line"
FILTER_PLACEHOLDER = "Filter, or type a folder path"
CLEAN = "These start clean: h in the list carries a session on."
NO_FOLDERS = "Tick folders on the left with Space."
IMPROVED = "First message improved: Ctrl+Z in the box puts yours back."
PICK_CARD = "Model · Effort · Context · Advisor"
NAME_WIDTH = 26  # a folder row's name, in characters (the rows are monospace)
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPIN_MS = 250
# The router line's tones (newforms.route_line) in the window's colours.
TONES = {"muted": look.MUTED, "muted-bold": f"bold {look.MUTED}", "warn": look.AMBER, "warn-bold": f"bold {look.AMBER}",
         "pick": f"bold {look.CYAN}", "ok": look.GREEN, "ok-bold": f"bold {look.GREEN}"}
SEVERITY = {"": look.MUTED, "information": look.MUTED, "warning": look.AMBER, "error": look.RED, "done": look.GREEN}


# --- the work done off the window's thread ----------------------------------------------------------------

def route_now(slot: tuple[str, str], folders: list[str]) -> tuple[tuple[str, str], router.Route | str]:
    """The router's answer for slot (CLI, message), or why there is none: whatever goes wrong, an answer comes
    back, so the line never stays at asking and Ctrl+R can ask again."""
    cli, key = slot
    try:
        return slot, router.suggest(key, folders, cli)
    except Exception as problem:  # noqa: BLE001 - any failure is said on the router's line
        return slot, str(problem) or type(problem).__name__


def improve_now(text: str, folders: list[str]) -> tuple[str, str, str]:
    """(the message sent, its rewrite, why there is none)."""
    try:
        return text, improve.improve_message(text, folders), ""
    except Exception as problem:  # noqa: BLE001 - no rewrite leaves the message as it was, and says why
        return text, "", str(problem) or type(problem).__name__


def quota_now() -> bool:
    """agy's quota read (a few seconds, no quota spent): whether it was."""
    try:
        return agy.refresh_quota()
    except Exception:  # noqa: BLE001 - not read: the chain's line says what it knew
        return False


class Asker(QObject):
    """What takes seconds, each on a thread of its own; back on the window's thread by a Signal: answered with
    who asked (the dialog, so a late answer never lands in the next one), what was asked and the answer. One
    for the window, settled with it."""

    answered = Signal(object, str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.workers: list[threading.Thread] = []

    def ask(self, owner: object, what: str, work: Callable[..., object], *args: object) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=self.run, args=(owner, what, work, args), daemon=True,
                                  name=f"tabmancer-{what}")
        self.workers.append(thread)
        thread.start()

    def run(self, owner: object, what: str, work: Callable[..., object], args: tuple) -> None:
        answer = work(*args)
        try:
            self.answered.emit(owner, what, answer)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def settle(self, seconds: float = 10) -> None:
        for thread in list(self.workers):
            thread.join(seconds)


# --- a rewrite put in, as the terminal's dialogs put it -----------------------------------------------------

def put_improved(box: QPlainTextEdit, sent: str, better: str, why: str) -> tuple[bool, str, str, str, str]:
    """The rewrite into its box as one edit (Ctrl+Z puts yours back), unless there is none, it says nothing new,
    or the text was changed while it was asked for: (put in, the card's line and its colour, the note and its
    severity, '' for none). The words are the terminal's (newsessions.put_improved)."""
    if why:
        return False, f"✗ not improved: {why}", look.RED, f"Not improved: {why}", "warning"
    if box.toPlainText() != sent:
        return (False, "✗ the text changed meanwhile · Ctrl+E asks again", look.RED,
                "You changed the text meanwhile, so the rewrite was not put in: Ctrl+E asks again.", "warning")
    if " ".join(better.split()) == " ".join(sent.split()):
        return False, "= clear as it was: nothing changed", look.TEXT, "Claude found it clear as it is: nothing changed.", ""
    cursor = box.textCursor()
    cursor.beginEditBlock()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.insertText(better)
    cursor.endEditBlock()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    box.setTextCursor(cursor)  # its start in sight, not its end
    box.setFocus()
    words = f"✓ improved ({len(sent.split())} → {len(better.split())} words) · Ctrl+Z puts yours back"
    return True, words, look.GREEN, "", ""


class Improving:
    """Ctrl+E's line under its box: what the key does; while Claude rewrites, that it is at it and for how long
    (the box takes no typing meanwhile); then how it went, kept until the text changes."""

    def __init__(self, idle: str, doing: str, clock: Callable[[], float]) -> None:
        self.idle, self.doing, self.clock = idle, doing, clock
        self.since: float | None = None
        self.outcome: tuple[str, str] | None = None  # words, colour
        self.about = ""  # the text the outcome is about

    def html(self, text: str) -> str:
        if self.since is not None:
            now = self.clock()
            return look.span(f"{SPIN[int(now * 8) % len(SPIN)]} {self.doing} · {int(now - self.since)}s",
                             f"bold {look.AMBER}")
        if self.outcome is not None and text == self.about:
            return look.span(self.outcome[0], f"bold {self.outcome[1]}")
        return look.span(self.idle, look.MUTED)


# --- the folders ---------------------------------------------------------------------------------------

class FilterLine(QLineEdit):
    """The filter box: ↓ goes down to the folders."""

    down = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Down:
            self.down.emit()
            return
        super().keyPressEvent(event)


class FolderList(QListWidget):
    """The folders: Space ticks the one picked (tick), and typing a letter there goes to the filter box
    (typed), so a project is found by its name."""

    tick = Signal()
    typed = Signal(str)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        text = event.text()
        if event.key() == Qt.Key.Key_Space:
            self.tick.emit()
        elif text and text.isprintable() and not event.modifiers() & ~Qt.KeyboardModifier.ShiftModifier:
            self.typed.emit(text)
        else:
            super().keyPressEvent(event)


def folder_row(project: Project, ticked: bool) -> str:
    """A folder's row: ✓ when ticked, its name, its sessions, when it was last worked in, and any CLI but
    Claude Code it was worked with (as the terminal's n lists them)."""
    count = f"{project.sessions}" if project.sessions else "none"
    last = newforms.ago(project.last) if project.last else ""
    used = ", ".join(sorted(project.used - {"claude"}))
    return (f"{'✓' if ticked else '·'} {newforms.fit(project.name, NAME_WIDTH).ljust(NAME_WIDTH)}"
            f"{count.rjust(9)}{last.rjust(10)}" + (f"  {used}" if used else ""))


HEADING = "  " + "PROJECT".ljust(NAME_WIDTH) + "SESSIONS".rjust(9) + "LAST".rjust(10)


class NewSessionDialog(QDialog):
    """Space ticks a folder; Enter (or Ctrl+S) starts a fresh session in each folder ticked, one for each line of
    the first message (requested, once, and it closes); in the message box Enter is a line more. Ctrl+O the
    CLI, Ctrl+L the lines linked as a team of peers, Ctrl+R the router's pick into the rows, Ctrl+E the message
    rewritten; Esc starts nothing. Window-modal, never waited on."""

    requested = Signal(object)  # what to open (newforms.Request)

    def __init__(self, parent: QWidget, found: list[Project], ticked: set[str], asker: Asker, accent: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self.form = newforms.NewForm(found=found, ticked=set(ticked))
        self.asker = asker
        self.accent = accent
        self.clock = clock
        self.answered = False
        self.improving: str | None = None  # Ctrl+E: the first message being rewritten, as it was sent
        self.improve_note = Improving(MESSAGE_NOTE, f"improving with {improve.MODEL}", clock)
        self.setWindowTitle(TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(1000, 600)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        top = QHBoxLayout()
        self.title = label("")
        self.cli_line = label("")
        top.addWidget(self.title, 1)
        top.addWidget(self.cli_line)
        box.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(10)
        box.addLayout(body, 1)
        # The folders on the left.
        self.folders_card = Card("Folders")
        self.filter = FilterLine()
        self.filter.setPlaceholderText(FILTER_PLACEHOLDER)
        self.filter.setFont(look.font(10))
        self.folders_card.add(self.filter)
        self.folders_card.add(label(look.span(HEADING, f"bold {look.MUTED}")))
        self.folders = FolderList()
        self.folders.setFont(look.font(9.5))
        self.folders_card.add(self.folders, 1)
        self.where = label("")
        self.folders_card.add(self.where)
        body.addWidget(self.folders_card, 5)
        # On the right: the first message, the rows, what will open.
        side = QVBoxLayout()
        side.setSpacing(10)
        body.addLayout(side, 4)
        self.message_card = Card("First message")
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText(MESSAGE_PLACEHOLDER)
        self.message.setFont(look.font(10))
        self.message.setTabChangesFocus(True)
        self.message.setFixedHeight(96)
        self.message_card.add(self.message)
        self.improve_line = label("")
        self.message_card.add(self.improve_line)
        self.link = label("", wrap=True)
        self.message_card.add(self.link)
        side.addWidget(self.message_card)
        self.pick_card = Card(PICK_CARD)
        self.rows: dict[str, Choice] = {}
        for row in newforms.choices():
            choice = Choice(row.values, row.value, accent, row.labels)
            choice.changed.connect(self.show_pick)
            self.rows[row.name] = choice
            self.pick_card.add(choice)
        self.pick_words = label("", wrap=True)
        self.pick_card.add(self.pick_words)
        self.route_line = label("", wrap=True)
        self.pick_card.add(self.route_line)
        side.addWidget(self.pick_card)
        self.opens_card = Card("")
        self.opens = label("")
        self.opens_card.add(self.opens, 1)
        self.opens_card.add(label(look.span(CLEAN, look.MUTED), wrap=True))
        side.addWidget(self.opens_card, 1)
        self.help = label("", wrap=True)
        box.addWidget(self.help)
        self.note = label("", wrap=True)
        box.addWidget(self.note)
        self.keys = label(keys_html(KEYS), wrap=True)
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QLineEdit, QPlainTextEdit {{ color:{look.TEXT}; background:{look.BLACK};"
                           f" border:1px solid {look.BORDER}; border-radius:4px; padding:4px 6px; }}"
                           f"QLineEdit:focus, QPlainTextEdit:focus {{ border-color:{accent}; }}"
                           f"QListWidget {{ background:{look.BLACK}; color:{look.TEXT}; border:none; outline:none; }}"
                           f"QListWidget::item:selected {{ background:{look.PANEL}; border-left:2px solid {accent}; }}")
        self.shortcuts: list[QShortcut] = []
        for key, act in (("Ctrl+S", lambda: self.start(True)), ("Ctrl+O", self.next_cli), ("Ctrl+L", self.toggle_link),
                         ("Ctrl+R", self.route), ("Ctrl+E", self.improve)):
            made = QShortcut(QKeySequence(key), self)
            made.activated.connect(act)
            self.shortcuts.append(made)
        self.spinner = QTimer(self)
        self.spinner.setInterval(SPIN_MS)
        self.spinner.timeout.connect(self.show_improving)
        self.filter.textChanged.connect(self.show_folders)
        self.filter.down.connect(self.folders.setFocus)
        self.folders.tick.connect(lambda: self.toggle(self.current_key()))
        self.folders.typed.connect(self.typed)
        self.folders.itemDoubleClicked.connect(lambda item: self.toggle(item.data(Qt.ItemDataRole.UserRole)))
        self.folders.currentRowChanged.connect(self.show_where)
        self.message.textChanged.connect(self.message_changed)
        asker.answered.connect(self.answered_by)
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        focus = app.focusChanged
        focus.connect(self.focus_moved)
        self.finished.connect(lambda: focus.disconnect(self.focus_moved))
        self.finished.connect(self.deleteLater)
        self.show_folders()
        self.show_cli()
        self.show_link()
        self.show_improving()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.folders.setFocus()

    # --- the folders -------------------------------------------------------------------------------------

    def current_key(self) -> str | None:
        item = self.folders.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def show_folders(self) -> None:
        """What the filter finds (newforms.find, kept as the form's shown), the picked row kept where it was."""
        keep = max(self.folders.currentRow(), 0)
        typed = self.filter.text()
        self.form.listed(typed)
        self.folders.clear()
        for key, project in self.form.shown.items():
            item = QListWidgetItem(folder_row(project, key in self.form.ticked))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.paint(item, key)
            self.folders.addItem(item)
        if self.form.shown:
            self.folders.setCurrentRow(min(keep, self.folders.count() - 1))
        else:  # said, rather than an empty box
            words = (f"Nothing matches “{typed.strip()}”. A folder path works too." if typed.strip() else
                     "No recent projects. Type a folder path in the box above.")
            item = QListWidgetItem(words)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(look.qcolor(look.MUTED))
            self.folders.addItem(item)
        everything = len(self.form.everything())
        self.folders_card.set_title("Folders", f"{len(self.form.shown)} of {everything}" if typed.strip()
                                    else f"{everything} project{'s' * (everything != 1)}")
        self.show_where()
        self.show_title()

    def paint(self, item: QListWidgetItem, key: str) -> None:
        ticked = key in self.form.ticked
        item.setForeground(look.qcolor(look.CYAN if ticked else look.TEXT))
        item.setFont(look.font(9.5, bold=ticked))

    def show_where(self, *_row: int) -> None:
        """Under the list: where the picked folder is, your home as ~."""
        project = self.form.shown.get(self.current_key() or "")
        self.where.setText(look.span("▸ " + newforms.home_as_tilde(project.cwd), look.MUTED) if project else "")

    def typed(self, text: str) -> None:
        self.filter.setFocus()
        self.filter.insert(text)

    def toggle(self, key: str | None) -> None:
        if key is None or not self.form.toggle(key):
            return
        for at in range(self.folders.count()):
            item = self.folders.item(at)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                item.setText(folder_row(self.form.shown[key], key in self.form.ticked))
                self.paint(item, key)
        self.show_title()
        self.show_link()

    def show_title(self) -> None:
        count = len(self.form.ticked)
        self.title.setText(look.span(TITLE, f"bold {look.TEXT}")
                           + (look.span(f"  {count} folder{'s' * (count != 1)} ticked", look.MUTED) if count else ""))

    # --- the CLI and the rows ------------------------------------------------------------------------------

    def show_cli(self) -> None:
        many = len(clis.enabled()) > 1
        self.cli_line.setText(look.span("with ", look.MUTED) + look.span(clis.label(self.form.cli), f"bold {look.CYAN}")
                              + (look.span("  Ctrl+O", look.MUTED) if many else ""))
        for name, (shown, on) in newforms.rows(self.form.cli).items():
            self.rows[name].setVisible(shown)
            self.rows[name].setEnabled(on)
        if self.form.wants_quota():  # once a dialog: what agy has left, for the chain's line
            self.asker.ask(self, QUOTA, quota_now)
        self.show_pick()

    def next_cli(self) -> None:
        self.form.next_cli()
        self.show_cli()
        self.show_link()

    def values(self) -> dict[str, str]:
        """Each row's value, by its name (newforms.ROWS)."""
        return {name: self.rows[name].value for name in newforms.ROWS}

    def pick(self) -> picks.Pick:
        """What the rows say (newforms.pick_of: for agy its own row only, never a Claude model)."""
        return newforms.pick_of(self.form.cli, self.values())

    def focused_row(self) -> str | None:
        return next((name for name, choice in self.rows.items() if self.focusWidget() is choice), None)

    def show_pick(self, *_value: str) -> None:
        """A line on the row in focus, or on what is picked; then what opens and the router's line."""
        self.pick_words.setText(look.span(newforms.pick_words(self.form.cli, self.values(), self.focused_row()),
                                          look.MUTED))
        self.show_opens()
        self.show_route()

    def show_help(self) -> None:
        focused = self.focusWidget()
        kind = ("message" if focused is self.message else "choice" if isinstance(focused, Choice) else "folders")
        self.help.setText(look.span(newforms.help_words(kind), look.MUTED))

    def focus_moved(self, _old: QWidget | None, new: QWidget | None) -> None:
        if new is not None and self.isAncestorOf(new):
            self.show_pick()
            self.show_help()

    # --- the lines, apart or linked; what opens ---------------------------------------------------------

    def text(self) -> str:
        return self.message.toPlainText()

    def message_changed(self) -> None:
        self.show_improving()  # how Ctrl+E went stays only while the text is what it went for
        self.show_link()
        self.show_route()  # the router is asked only with Ctrl+R: never half-way through a message

    def toggle_link(self) -> None:
        self.form.toggle_link()
        self.show_link()

    def show_link(self) -> None:
        """Under the message, once there are two lines or more: apart, or linked as a team of peers."""
        lines = len(newforms.topics(self.text()))
        self.link.setVisible(lines >= 2)
        if lines >= 2:
            why = self.form.can_link(self.text())
            linked = self.form.linked

            def pill(words: str, on: bool) -> str:
                return look.span(f"● {words}", f"bold {look.TEXT}") if on else look.span(f"○ {words}", look.MUTED)

            self.link.setText(pill(f"{lines} sessions apart", not linked or bool(why)) + look.span("   ")
                              + pill("linked, as peers", linked and not why) + look.span("   ")
                              + look.span(f"Ctrl+L ({why})" if why and linked else "Ctrl+L",
                                          look.AMBER if why and linked else look.MUTED))
        self.show_opens()

    def show_opens(self) -> None:
        """The card of what will open: a line for each tab, its folder and its message."""
        what = self.form.opens(self.text())
        if not what.rows:
            self.opens_card.set_title(what.title)
            self.opens.setText(look.span(NO_FOLDERS, look.MUTED))
            return
        about = " · ".join([clis.label(self.form.cli), *newforms.pick_parts(self.pick())])
        self.opens_card.title.setText(look.span(what.title, f"bold {look.GREEN}") + look.span(f"  {about}", look.MUTED))
        self.opens.setText("<br>".join(
            look.span("● ", look.GREEN) + look.span(folder, f"bold {look.CYAN}")
            + look.span(f"  {line}" if line else "  a fresh session", look.TEXT if line else look.MUTED)
            for folder, line in what.rows))

    # --- the router -------------------------------------------------------------------------------------

    def route(self) -> None:
        """Ctrl+R: the router's pick into the rows, asked for now if it is not there yet."""
        refusal = newforms.route_refusal(self.form.cli, self.text())
        if refusal is not None:
            words, seconds = refusal
            self.say(words, "warning", seconds)
            return
        self.ask_route(take=True)

    def ask_route(self, take: bool = False) -> None:
        asked = self.form.ask(self.text(), take)
        self.put(asked.take)
        if asked.slot is not None:
            self.asker.ask(self, ROUTE, route_now, asked.slot, self.form.chosen_names())
        self.show_route()

    def routed(self, slot: tuple[str, str], found: router.Route | str) -> None:
        values = self.form.routed(slot, found, self.form.slot(self.text()))
        if values is None:
            return  # the message or the CLI has changed since: kept, should it come back
        self.put(values)
        self.show_route()

    def put(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            self.rows[name].pick(value)

    def show_route(self) -> None:
        """The router's line under the rows: what it suggests and why, that it is asking, or why not."""
        parts = self.form.route_line(self.text(), self.pick())
        self.route_line.setVisible(parts is not None)
        if parts is not None:
            self.route_line.setText("".join(look.span(words, TONES[tone]) for words, tone in parts))

    # --- the first message rewritten ------------------------------------------------------------------

    def improve(self) -> None:
        """Ctrl+E: Claude rewrites the first message clearer, line for line; it goes in as one edit, so Ctrl+Z
        in the box puts yours back."""
        if self.improving is not None:
            return
        text = self.text()
        refusal = newforms.improve_refusal(text)
        if refusal:
            self.say(refusal, "warning", 5)
            return
        self.improving = text
        self.improve_note.since, self.improve_note.outcome = self.clock(), None
        self.message.setReadOnly(True)
        self.spinner.start()
        self.show_improving()
        self.asker.ask(self, IMPROVE, improve_now, text, self.form.chosen_names())

    def improved(self, sent: str, better: str, why: str) -> None:
        self.improving = None
        self.spinner.stop()
        self.improve_note.since = None
        self.message.setReadOnly(False)
        put, words, colour, note, severity = put_improved(self.message, sent, better, why)
        self.improve_note.outcome, self.improve_note.about = (words, colour), self.text()
        self.show_improving()
        if put:
            self.say(IMPROVED, "done", 6)
        elif note:
            self.say(note, severity, 8)

    def show_improving(self) -> None:
        self.improve_line.setText(self.improve_note.html(self.text()))

    # --- the answers, back on the window's thread ------------------------------------------------------

    def answered_by(self, owner: object, what: str, answer: Any) -> None:
        if owner is not self:
            return  # another dialog's, closed meanwhile
        if what == ROUTE:
            self.routed(*answer)
        elif what == IMPROVE:
            self.improved(*answer)
        elif what == QUOTA and answer:
            self.show_pick()

    # --- a note, Enter, Esc -----------------------------------------------------------------------------

    def say(self, words: str, severity: str = "", seconds: float | None = None) -> None:
        """A note over the keys, as the terminal's dialog says it in a toast; it stays till the next one (seconds:
        how long the toast stood, kept for the words' callers)."""
        self.note.setText(look.span(words, SEVERITY.get(severity, look.MUTED)))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.start()
        else:
            super().keyPressEvent(event)  # Esc: rejected, nothing starts

    def start(self, now: bool = False) -> None:
        """Enter: start them, but in the message box a line more (the box's own); Ctrl+S: start. Where
        the focus is, is asked of the dialog (focusWidget): a key can reach it before it is active."""
        if self.answered or (self.focusWidget() is self.message and not now):
            return
        typed = self.filter.text() if self.focusWidget() is self.filter else None
        key = self.current_key()
        step = self.form.enter(typed, key, key is not None)
        if step.kind == newforms.TICK:  # Enter on a typed folder ticks it; the next Enter starts
            if step.key not in self.form.shown:
                self.show_folders()
            self.toggle(step.key)
            self.filter.clear()
            self.folders.setFocus()
            return
        if step.kind == newforms.REFUSE:
            self.say(step.note, "warning")
            return
        if step.key is not None:  # Enter on one folder without ticking anything opens that one
            self.toggle(step.key)
        self.answered = True
        asked = self.form.request(self.text(), self.pick())
        self.accept()
        self.requested.emit(asked)
