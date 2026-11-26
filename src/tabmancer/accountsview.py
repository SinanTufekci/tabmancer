"""A: the Claude accounts you use, to sign in with another one. The sign-in is Claude Code's own
(`claude auth login --email`, in a new tab), and its page opens where you pick for that account with
←/→: a browser profile already signed in to it (one click), a private window (you sign in afresh), or
the browser as links open (browsers). tabmancer only remembers the accounts it has seen Claude Code
signed in with (accounts.known), and where each email's page opens. An email signed in to more than one
organisation is listed once for each, with the organisation's name."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from . import accounts, accountwords, usage
from .accounts import Account
from .accountwords import ANOTHER, NOT_KEPT
from .browsers import DEFAULT, Place
from .config import UI_TRIES
from .dialogs import frame, title
from .theme import keys


class AccountsDialog(ModalScreen[tuple[str, str] | None]):
    """Dismisses with (the email to sign in with, "" for another account; the browsers.Place id where
    its page opens), or None. Each option's id is its account's key; where it opens is kept by email
    (the sign-in page is the same for each of an email's organisations)."""

    DEFAULT_CSS = frame("AccountsDialog") + """
    AccountsDialog OptionList { height: auto; margin-top: 1; background: $surface; border: none; }
    AccountsDialog .note { color: $text-muted; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("left", "place(-1)", "Opens in", show=False),
        Binding("right", "place(1)", "Opens in", show=False),
    ]

    def __init__(self, current: Account | None, seen: list[tuple[Account, str]], stopped: int,
                 places: list[Place] | None = None, picked: dict[str, str] | None = None,
                 last: dict[str, tuple[datetime, list[usage.Limit]]] | None = None) -> None:
        super().__init__()
        self.current, self.seen, self.stopped = current, seen, stopped
        self.last = last or {}  # each account's last limits reading (usage.last_by_account), by key
        # Where each one's page can open; with nothing but the system's own way, no choice to show.
        self.places = places if places is not None and len(places) > 1 else []
        self.shared = accountwords.shared(seen)  # shown with their organisation
        # option id -> the email it opens for, option id -> place id
        self.email, self.place = accountwords.opens_in(seen, self.places, picked or {})

    def is_current(self, key: str) -> bool:
        return self.current is not None and key == self.current.key

    def prompt(self, option_id: str) -> Text:
        where = accountwords.place_words(self.places, self.place[option_id]) if self.places else ""
        if option_id == ANOTHER:
            return Text.assemble(*accountwords.another_parts(where))
        account, when = next((a, w) for a, w in self.seen if a.key == option_id)
        return Text.assemble(*accountwords.option_parts(
            account, when, current=self.is_current(account.key), shared=account.email in self.shared,
            last=self.last.get(account.key), place=where))

    def compose(self) -> ComposeResult:
        options = [Option(self.prompt(account.key), id=account.key, disabled=self.is_current(account.key))
                   for account, _ in self.seen]
        options.append(Option(self.prompt(ANOTHER), id=ANOTHER))
        with Vertical():
            yield Label(title("Claude accounts", accountwords.title_words(self.current)))
            yield OptionList(*options)
            yield Label(accountwords.note(bool(self.places), self.stopped), classes="note")
            yield Label(keys(*accountwords.key_pairs(bool(self.places))), classes="hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.focus()
        first = next((i for i in range(options.option_count) if not options.get_option_at_index(i).disabled), None)
        if first is not None:
            options.highlighted = first

    def action_place(self, step: int) -> None:
        """←/→: the next place for the highlighted account's page, kept for it at once."""
        options = self.query_one(OptionList)
        if not self.places or options.highlighted is None:
            return
        option = options.get_option_at_index(options.highlighted)
        if option.disabled or option.id is None:
            return
        ids = [place.id for place in self.places]
        now = self.place.get(option.id, DEFAULT)
        self.place[option.id] = accountwords.next_place(ids, now, step)
        email = self.email.get(option.id, option.id)
        for option_id, its_email in self.email.items():  # the same email's other organisations open there too
            if its_email == email:
                self.place[option_id] = self.place[option.id]
                options.replace_option_prompt(option_id, self.prompt(option_id))
        if not accounts.keep_opens_in(email, self.place[option.id], UI_TRIES):
            self.notify(NOT_KEPT, severity="warning", timeout=10)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ANOTHER
        email = "" if option_id == ANOTHER else self.email.get(option_id, option_id)
        self.dismiss((email, self.place.get(option_id, DEFAULT)))

    def action_cancel(self) -> None:
        self.dismiss(None)
