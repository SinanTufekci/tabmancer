"""r in the window: a name asked for as the terminal dashboard's NamePrompt asks it (NameDialog: a heading,
one line to type it in with the old name there and picked, so typing replaces it, and the keys at its foot),
and a closed session renamed off the window's thread (Renamer: rename.rename runs /rename in it once). What
r says is rename.py's, the terminal dashboard's words. p asks for a prompt the same way (PromptDialog), in
prompting.py's words."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import replace

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout, QWidget

from .. import prompting, rename
from ..sessions import Session, read_session
from . import look
from .formparts import keys_html
from .handover import label


class NameDialog(QDialog):
    """Enter with a name closes it and says the name (named, once); Esc, or Enter on nothing, closes it with
    none said. about: a muted line under the heading. Window-modal and never waited on (open(), not exec())."""

    named = Signal(str)

    def __init__(self, parent: QWidget, heading: str, value: str, placeholder: str,
                 keys: Iterable[tuple[str, str]], accent: str, about: str = "") -> None:
        super().__init__(parent)
        self.answered = False
        self.setWindowTitle(heading)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label(look.span(heading, f"bold {look.TEXT}"), wrap=True)
        box.addWidget(self.heading)
        self.about = label(look.span(about, look.MUTED), wrap=True)
        self.about.setVisible(bool(about))
        box.addWidget(self.about)
        self.line = QLineEdit(value)
        self.line.setPlaceholderText(placeholder)
        self.line.setFont(look.font(10))
        self.line.returnPressed.connect(self.answer)
        box.addWidget(self.line)
        self.keys = label(keys_html(keys))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QLineEdit {{ color:{look.TEXT}; background:{look.BLACK}; border:1px solid {look.BORDER};"
                           " border-radius:4px; padding:5px 8px; }"
                           f"QLineEdit:focus {{ border-color:{accent}; }}")
        self.finished.connect(self.deleteLater)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.line.setFocus()
        self.line.selectAll()  # typing replaces the old name

    def answer(self) -> None:
        if self.answered:
            return
        self.answered = True
        name = self.line.text().strip()
        self.accept()
        if name:
            self.named.emit(name)


class PromptDialog(NameDialog):
    """p: what the session should do next, as the terminal dashboard's PromptDialog asks it. Enter sends it
    to a new tab, Ctrl+B to the background (sent, once, with where: prompting.TAB or BACKGROUND); Enter on
    nothing stays, Esc sends nothing."""

    sent = Signal(str, str)

    def __init__(self, parent: QWidget, session: Session, accent: str) -> None:
        super().__init__(parent, prompting.heading(session), "", prompting.placeholder(session),
                         prompting.keys(session), accent)
        self.background = QShortcut(QKeySequence("Ctrl+B"), self)
        self.background.activated.connect(self.to_background)

    def answer(self, how: str = prompting.TAB) -> None:
        prompt = self.line.text().strip()
        if self.answered or not prompt:
            return
        self.answered = True
        self.accept()
        self.sent.emit(how, prompt)

    def to_background(self) -> None:
        self.answer(prompting.BACKGROUND)


class Renamer(QObject):
    """A closed session renamed on a thread of its own (rename.rename: claude -p once, seconds); back on the
    window's thread by a Signal: done with the session read again (its new title), or failed with the note."""

    done = Signal(object)  # the session renamed (sessions.Session)
    failed = Signal(object)  # why not (actions.Note)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.workers: list[threading.Thread] = []

    def rename(self, session: Session, name: str) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=self.run, args=(session, name), daemon=True, name="tabmancer-rename")
        self.workers.append(thread)
        thread.start()

    def run(self, session: Session, name: str) -> None:
        try:
            rename.rename(session, name)
        except OSError as error:
            self.send("failed", rename.failed_note(session, error))
            return
        fresh = read_session(session.path) if session.path else None
        self.send("done", fresh or replace(session, title=name))

    def send(self, signal: str, *args: object) -> None:
        try:
            getattr(self, signal).emit(*args)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def settle(self, seconds: float = 10) -> None:
        for thread in list(self.workers):
            thread.join(seconds)
