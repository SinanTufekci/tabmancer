"""The ? menu: every key of the dashboard and what it does, in groups, and a way to run it.

The footer shows the keys used most. Everything else is here: ↑↓ picks a line, Enter runs
it as if its key had been pressed, so a feature nobody remembers the key of is still a few
keystrokes away. A test checks that every key the dashboard binds is listed.
"""

from __future__ import annotations

from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from .dialogs import frame
from .keys import KEYS  # the list itself; app imports it from here too
from .theme import MUTED, TEXT, keys


def line(key: str, what: str) -> Table:
    """A line of the menu: the key, and what it does wrapping under itself."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, width=6)
    grid.add_column()
    grid.add_row(Text(key, style=f"bold {TEXT}"), Text(what, style=TEXT))
    return grid


def options(groups: list[tuple[str, list[tuple[str, str, str]]]] | None = None) -> list[Option | None]:
    out: list[Option | None] = []
    for i, (group, rows) in enumerate(KEYS if groups is None else groups):
        if i:
            out.append(None)
        out.append(Option(Text(group.upper(), style=f"bold {MUTED}"), disabled=True))
        for key, action, what in rows:
            out.append(Option(line(key, what), id=action or None, disabled=not action or action == "keys"))
    return out


class KeysScreen(ModalScreen[str | None]):
    """The ? menu. Dismissed with the action picked, or None."""

    DEFAULT_CSS = frame("KeysScreen", "large", height="90%") + """
    KeysScreen > Vertical { max-height: 48; }
    KeysScreen OptionList { height: 1fr; margin-top: 1; border: none; background: $surface; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape,q,question_mark", "close", "Close")]

    def __init__(self, groups: list[tuple[str, list[tuple[str, str, str]]]] | None = None) -> None:
        super().__init__()
        self.groups = groups  # another screen's keys (the team monitor's); else the dashboard's

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text.assemble(("Every key", "bold"),
                                      ("   the footer shows the common ones; Enter runs any of these", MUTED)))
            yield OptionList(*options(self.groups))
            yield Label(keys(("↑↓", "pick"), ("Enter", "run it"), ("Esc", "close")), classes="hint")

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.focus()
        menu.action_first()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_close(self) -> None:
        self.dismiss(None)
