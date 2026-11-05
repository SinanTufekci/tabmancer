"""A: the Claude accounts you use, to sign in with another one. The sign-in is Claude Code's own
(`claude auth login --email`, in a new tab), and its page opens where you pick for that account with
←/→: a browser profile already signed in to it (one click), a private window (you sign in afresh), or
the browser as links open (browsers). tabmancer only remembers the emails it has seen Claude Code
signed in with (accounts.known), and where each one's page opens."""

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

from . import accounts
from .accounts import Account
from .browsers import DEFAULT, Place, guess
from .dialogs import frame, title
from .theme import CYAN, GREEN, MUTED, TEXT, keys

ANOTHER = "+"  # the option for an account not seen yet


def seen_words(when: str) -> str:
    """'last on 2 Nov 14:05' from an ISO time; nothing when it cannot be read."""
    try:
        return f"last on {datetime.fromisoformat(when):%d %b %H:%M}"
    except ValueError:
        return ""


class AccountsDialog(ModalScreen[tuple[str, str] | None]):
    """Dismisses with (the email to sign in with, "" for another account; the browsers.Place id where
    its page opens), or None."""

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
                 places: list[Place] | None = None, picked: dict[str, str] | None = None) -> None:
        super().__init__()
        self.current, self.seen, self.stopped = current, seen, stopped
        # Where each one's page can open; with nothing but the system's own way, no choice to show.
        self.places = places if places is not None and len(places) > 1 else []
        known = {place.id for place in self.places}
        picked = picked or {}
        self.place: dict[str, str] = {}  # option id -> place id
        for account, _ in seen:
            chosen = picked.get(account.email)
            self.place[account.email] = chosen if chosen in known else guess(account.email, self.places)
        chosen = picked.get(accounts.ANY_OTHER)
        self.place[ANOTHER] = chosen if chosen in known else DEFAULT

    def is_current(self, email: str) -> bool:
        return self.current is not None and email == self.current.email

    def prompt(self, option_id: str) -> Text:
        if option_id == ANOTHER:
            text = Text.assemble(("  Another account", f"bold {TEXT}"), ("  one not listed here", MUTED))
        else:
            account, when = next((a, w) for a, w in self.seen if a.email == option_id)
            now = self.is_current(account.email)
            text = Text.assemble(
                ("● " if now else "  ", GREEN if now else MUTED), (account.email, f"bold {TEXT}"),
                (f"  {account.plan}" if account.plan else "", MUTED),
                ("  signed in now" if now else f"  {seen_words(when)}", GREEN if now else MUTED))
            if now:
                return text  # nothing to sign in to: where it would open does not matter now
        if self.places:
            words = next((p.words for p in self.places if p.id == self.place[option_id]), "default browser")
            text.append_text(Text.assemble(("\n    opens in ", MUTED), (words, CYAN)))
        return text

    def compose(self) -> ComposeResult:
        options = [Option(self.prompt(account.email), id=account.email, disabled=self.is_current(account.email))
                   for account, _ in self.seen]
        options.append(Option(self.prompt(ANOTHER), id=ANOTHER))
        if self.places:
            note = ("Claude Code signs you in, in a new tab, its page where you pick: a browser profile signed in "
                    "to that account takes one click, a private window asks you to sign in. tabmancer reads "
                    "only your browsers' profile names.")
        else:
            note = ("Claude Code signs you in, in a new tab: your browser opens with the account filled in, and "
                    "every session goes on with it. tabmancer keeps only the emails it has seen.")
        if self.stopped:
            note += (f" {self.stopped} session{' is' if self.stopped == 1 else 's are'} stopped at a usage limit: "
                     "once you are signed in with another account, c continues "
                     f"{'it' if self.stopped == 1 else 'them'} at once.")
        pairs = [("Enter", "sign in with it"), *((("←/→", "where it opens"),) if self.places else ()),
                 ("Esc", "cancel")]
        with Vertical():
            yield Label(title("Claude accounts", f"signed in as {self.current.email}" if self.current
                              else "not signed in"))
            yield OptionList(*options)
            yield Label(note, classes="note")
            yield Label(keys(*pairs), classes="hint")

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
        self.place[option.id] = ids[((ids.index(now) if now in ids else 0) + step) % len(ids)]
        accounts.keep_opens_in(accounts.ANY_OTHER if option.id == ANOTHER else option.id, self.place[option.id])
        options.replace_option_prompt(option.id, self.prompt(option.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ANOTHER
        self.dismiss(("" if option_id == ANOTHER else option_id, self.place.get(option_id, DEFAULT)))

    def action_cancel(self) -> None:
        self.dismiss(None)
