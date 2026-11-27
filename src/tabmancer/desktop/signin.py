"""A, in the window: the Claude accounts you use, to sign in with another one, as the terminal dashboard's A
dialog lists them (AccountsDialog: a heading, a row for each account seen and one for another account, a
note and the keys). Its words and steps are accountwords.py's, one copy with the terminal's: the sign-in is
Claude Code's own (`claude auth login --email`, in a new tab), its page opening where you pick for that
account with ←/→ (browsers: a profile, a private window, or the browser as links open), kept at once.

What it reads (who is signed in, the accounts seen, the browsers' profile names, each account's last limits)
is read on a thread first (Accounts.fetch), and the sign-in runs on one (Accounts.sign_in), today's claude
update first when it is due, as the window's other tabs open: the window never waits on either.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QVBoxLayout, QWidget

from .. import accounts, accountwords, actions, browsers, claudeupdate, errorlog, launch, usage
from ..accounts import Account
from ..accountwords import ANOTHER
from ..browsers import DEFAULT, Place
from ..config import UI_TRIES
from ..teamwords import Part
from . import look
from .clisettings import PickRow, parts_html
from .formparts import keys_html
from .handover import label


@dataclass
class Seen:
    """What the dialog shows, read on a thread: who is signed in now, the accounts seen (the latest first,
    the one signed in now among them), where a sign-in page can open, where each email's was picked to,
    and each account's last limits reading (usage.last_by_account), by key."""

    current: Account | None
    seen: list[tuple[Account, str]]
    places: list[Place] = field(default_factory=list)
    picked: dict[str, str] = field(default_factory=dict)
    last: dict[str, tuple[datetime, list[usage.Limit]]] = field(default_factory=dict)


def read_seen() -> Seen:
    """As the terminal dashboard's A reads them; each module looked up when called (the demo stands in)."""
    current = accounts.claude()
    return Seen(current, accountwords.listed(current, accounts.known()), browsers.places(), accounts.opens_in(),
                usage.last_by_account())


def open_tabs(launches: list[launch.Launch]) -> list[str]:
    """New tabs, by launch.open_tabs as it is when called (the demo stands in for it)."""
    return launch.open_tabs(launches)


class Accounts(QObject):
    """A's reading and its sign-in, on threads of their own; back on the window's thread by a Signal.
    read: what the dialog shows (Seen; None when it could not be read, said first); said: words for Bit, for how long, and how loud. Over the demo, no
    claude update before the sign-in (its accounts are made up), and the tab said only by the demo's
    stand-in, which says what would have opened."""

    read = Signal(object)
    said = Signal(str, float, str)

    def __init__(self, parent: QObject, demo: bool = False) -> None:
        super().__init__(parent)
        self.demo = demo
        self.workers: list[threading.Thread] = []

    def fetch(self) -> None:
        self.start(self.fetch_now)

    def fetch_now(self) -> None:
        """Read, and answered whatever happens (read: None when it failed, said in red first), so the window's
        A is never left waiting for a read that died."""
        shown: Seen | None = None
        try:
            shown = read_seen()
        except Exception as error:  # noqa: BLE001 - said; A pressed again reads them again
            self.say(accountwords.read_failed_note(error))
        finally:
            self.send("read", shown)

    def sign_in(self, email: str, place: str, places: list[Place]) -> None:
        """Claude Code's own sign-in in a new tab, its page where place says (accountwords.sign_in, which
        writes browsers' launcher); said once the tab opened, or why it did not."""
        self.start(self.sign_in_now, email, place, list(places))

    def sign_in_now(self, email: str, place: str, places: list[Place]) -> None:
        if not self.demo:
            claudeupdate.before_opening(self.tell)
        said = accountwords.sign_in(email, place, places, open_tabs, demo=self.demo)
        if said is not None:
            self.say(said)

    def start(self, work: Callable[..., None], *args: object) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        name = getattr(work, "__name__", "accounts")
        thread = threading.Thread(target=errorlog.guarded, args=(f"desktop.signin.{name}", work, *args),
                                  kwargs={"failed": lambda error, path: self.say(actions.Note(
                                      errorlog.failed_words(name, error, path), "error", 15))},
                                  daemon=True, name="tabmancer-accounts")
        self.workers.append(thread)
        thread.start()

    def send(self, signal: str, *args: object) -> None:
        try:
            getattr(self, signal).emit(*args)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def say(self, note: actions.Note) -> None:
        self.send("said", note.words, float(note.seconds or 4), note.severity)

    def tell(self, words: str) -> None:
        """claudeupdate's words while today's update runs, before the sign-in opens."""
        self.say(actions.Note(words, seconds=8))

    def settle(self, seconds: float = 10) -> None:
        for thread in list(self.workers):
            thread.join(seconds)


class AccountsLabel(QLabel):
    """The top bar's accounts: a click opens A, as a click on them does in the terminal dashboard."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFont(look.font(9.5))
        self.setStyleSheet(f"color:{look.TEXT}; background:transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("A: the Claude accounts you use, to sign in with another one")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)


class AccountsDialog(QDialog):
    """↑↓ pick an account (the one signed in now is not one to pick), ←/→ where its page opens (kept at once,
    for its email), Enter (or a double click) signs in with it (chosen: the email, "" for another account; the
    place; the places), Esc leaves it. Window-modal, never waited on."""

    chosen = Signal(str, str, object)

    def __init__(self, parent: QWidget, shown: Seen, stopped: int, accent: str) -> None:
        super().__init__(parent)
        self.current, self.seen, self.last = shown.current, shown.seen, shown.last
        # Where each one's page can open; with nothing but the system's own way, no choice to show.
        self.places = shown.places if len(shown.places) > 1 else []
        self.shared = accountwords.shared(shown.seen)  # shown with their organisation
        # option id -> the email it opens for, option id -> place id
        self.email, self.place = accountwords.opens_in(shown.seen, self.places, shown.picked)
        self.ids = [account.key for account, _ in shown.seen] + [ANOTHER]
        self.accent = accent
        self.answered = False
        self.at = next((at for at, key in enumerate(self.ids) if not self.is_current(key)), len(self.ids) - 1)
        self.setWindowTitle(accountwords.TITLE)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(720)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the keys are the dialog's: nothing in it takes the focus
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 12)
        box.setSpacing(8)
        self.heading = label(look.span(accountwords.TITLE, f"bold {look.TEXT}") + "&nbsp;&nbsp;"
                             + look.span(accountwords.title_words(self.current), look.MUTED))
        box.addWidget(self.heading)
        listed = QFrame()
        listed.setObjectName("listed")
        rows = QVBoxLayout(listed)
        rows.setContentsMargins(0, 4, 0, 4)
        rows.setSpacing(2)
        self.rows: list[PickRow] = []  # a click picks an account, a double click signs in with it
        for at, key in enumerate(self.ids):
            row = PickRow(at)
            if self.is_current(key):
                row.setCursor(Qt.CursorShape.ArrowCursor)
            row.clicked.connect(self.click)
            row.chosen.connect(self.double_click)
            rows.addWidget(row)
            self.rows.append(row)
        box.addWidget(listed)
        self.note = label(look.span(accountwords.note(bool(self.places), stopped), look.MUTED), wrap=True)
        box.addWidget(self.note)
        self.not_kept = label(look.span(accountwords.NOT_KEPT, look.AMBER), wrap=True)  # ←/→ not kept: said here
        self.not_kept.setVisible(False)
        box.addWidget(self.not_kept)
        self.keys = label(keys_html(accountwords.key_pairs(bool(self.places))))
        box.addWidget(self.keys)
        self.setStyleSheet(f"QDialog {{ background:{look.SURFACE}; }}"
                           f"QFrame#listed {{ background:{look.BLACK}; border:none; }}")
        self.finished.connect(self.deleteLater)
        self.draw()

    def is_current(self, key: str) -> bool:
        return self.current is not None and key == self.current.key

    def parts(self, key: str) -> list[Part]:
        where = accountwords.place_words(self.places, self.place[key]) if self.places else ""
        if key == ANOTHER:
            return accountwords.another_parts(where)
        account, when = next((a, w) for a, w in self.seen if a.key == key)
        return accountwords.option_parts(account, when, current=self.is_current(key),
                                         shared=account.email in self.shared, last=self.last.get(key), place=where)

    def draw(self) -> None:
        """Each account's row as the terminal's dialog words it, the picked one lit."""
        for at, (key, row) in enumerate(zip(self.ids, self.rows, strict=True)):
            row.setText(parts_html(self.parts(key)))
            edge, ground = (self.accent, look.PANEL) if at == self.at else ("transparent", "transparent")
            row.setStyleSheet(f"background:{ground}; border-left:2px solid {edge}; padding:4px 10px;")

    def step(self, by: int) -> None:
        """↑↓: the next account to pick, round the list, past the one signed in now."""
        at = self.at
        for _ in self.ids:
            at = (at + by) % len(self.ids)
            if not self.is_current(self.ids[at]):
                break
        self.at = at
        self.draw()

    def click(self, at: int) -> None:
        if not self.is_current(self.ids[at]):
            self.at = at
            self.draw()

    def double_click(self, at: int) -> None:
        if not self.is_current(self.ids[at]):
            self.at = at
            self.choose()

    def turn_place(self, step: int) -> None:
        """←/→: the next place for the picked account's page, its email's other organisations' too, kept for
        it at once (accounts.keep_opens_in), Esc or not; not kept (accounts.json unreadable, or held by
        another tabmancer past UI_TRIES), said under the note (this sign-in still opens there)."""
        if not self.places:
            return
        turned = accountwords.turn(self.place, self.email, self.ids[self.at], [place.id for place in self.places], step)
        self.not_kept.setVisible(not accounts.keep_opens_in(turned.email, turned.place, UI_TRIES))  # held: ~75 ms
        self.draw()

    def choose(self) -> None:
        """Enter: the picked account's sign-in asked for, once (the window opens it, on a thread)."""
        if self.answered:
            return
        self.answered = True
        key = self.ids[self.at]
        email = "" if key == ANOTHER else self.email.get(key, key)
        self.accept()
        self.chosen.emit(email, self.place.get(key, DEFAULT), list(self.places))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.step(-1)
        elif key == Qt.Key.Key_Down:
            self.step(1)
        elif key == Qt.Key.Key_Left:
            self.turn_place(-1)
        elif key == Qt.Key.Key_Right:
            self.turn_place(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.choose()
        else:
            super().keyPressEvent(event)  # Esc: rejected, nothing signed in
