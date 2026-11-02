"""A: the Claude accounts you use, to sign in with another one. The sign-in is Claude Code's own
(`claude auth login --email`, in a new tab: the browser opens with that account filled in); tabmancer
only remembers the emails it has seen Claude Code signed in with (accounts.known)."""

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

from .accounts import Account
from .dialogs import frame, title
from .theme import GREEN, MUTED, TEXT, keys

ANOTHER = "+"  # the option for an account not seen yet


def seen_words(when: str) -> str:
    """'last on 2 Nov 14:05' from an ISO time; nothing when it cannot be read."""
    try:
        return f"last on {datetime.fromisoformat(when):%d %b %H:%M}"
    except ValueError:
        return ""


class AccountsDialog(ModalScreen[str | None]):
    """Dismisses with the email to sign in with ("" for another account), or None."""

    DEFAULT_CSS = frame("AccountsDialog") + """
    AccountsDialog OptionList { height: auto; margin-top: 1; background: $surface; border: none; }
    AccountsDialog .note { color: $text-muted; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: Account | None, seen: list[tuple[Account, str]], stopped: int) -> None:
        super().__init__()
        self.current, self.seen, self.stopped = current, seen, stopped

    def compose(self) -> ComposeResult:
        options = []
        for account, when in self.seen:
            now = self.current is not None and account.email == self.current.email
            options.append(Option(Text.assemble(
                ("● " if now else "  ", GREEN if now else MUTED), (account.email, f"bold {TEXT}"),
                (f"  {account.plan}" if account.plan else "", MUTED),
                ("  signed in now" if now else f"  {seen_words(when)}", GREEN if now else MUTED)),
                id=account.email, disabled=now))
        options.append(Option(Text.assemble(("  Another account", f"bold {TEXT}"), ("  one not listed here", MUTED)),
                              id=ANOTHER))
        note = ("Claude Code signs you in, in a new tab: your browser opens with the account filled in, and every "
                "session goes on with it. tabmancer keeps only the emails it has seen.")
        if self.stopped:
            note += (f" {self.stopped} session{' is' if self.stopped == 1 else 's are'} stopped at a usage limit: "
                     "once you are signed in with another account, c continues "
                     f"{'it' if self.stopped == 1 else 'them'} at once.")
        with Vertical():
            yield Label(title("Claude accounts", f"signed in as {self.current.email}" if self.current
                              else "not signed in"))
            yield OptionList(*options)
            yield Label(note, classes="note")
            yield Label(keys(("Enter", "sign in with it"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.focus()
        first = next((i for i in range(options.option_count) if not options.get_option_at_index(i).disabled), None)
        if first is not None:
            options.highlighted = first

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss("" if event.option.id == ANOTHER else event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
