"""The `,` dialog: which CLIs' sessions the dashboard lists, and which of them is the main one. What it says
and what its keys do are clis.py's (Picked, Saved and the dialog's words), one copy with the desktop window's."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from . import clis
from .dialogs import frame, title
from .theme import MUTED, keys


class CliSettings(ModalScreen[clis.Saved | None]):
    """Space turns a CLI on or off, m picks the main CLI (clis.main_cli), Enter saves both (clis.Picked.save:
    dismissed with what it came to), Esc leaves things as they were."""

    DEFAULT_CSS = frame("CliSettings", "medium") + """
    CliSettings OptionList { height: auto; background: $surface; border: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "save", "Save", priority=True),
        Binding("space", "toggle", "Turn on or off", show=False),
        Binding("m", "main", "Main CLI", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.picked = clis.Picked.saved()  # nothing saved till Enter

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title(clis.SETTINGS_TITLE))
            yield Label(Text(clis.SETTINGS_ABOUT, style=MUTED))
            yield OptionList(*[Option(self.prompt(cli), id=cli.name) for cli in clis.CLIS.values()])
            yield Label(self.main_words(), id="main-cli")
            yield Label(keys(*clis.SETTINGS_KEYS), classes="hint")

    def prompt(self, cli: clis.Cli) -> Text:
        """[x] or [ ] in front, as in the session list: the words are clis.option_parts'."""
        return Text.assemble(*clis.option_parts(cli.name, cli.name in self.picked.on))

    def main_words(self) -> Text:
        """The main CLI and what it costs (clis.main_parts): tabmancer's own calls run on it."""
        return Text.assemble(*clis.main_parts(self.picked.main))

    def action_main(self) -> None:
        """Claude Code and agy in turn; agy as main is turned on in the list too (clis.Picked.turn_main)."""
        self.picked.turn_main()
        self.query_one("#main-cli", Label).update(self.main_words())
        self.redraw(self.picked.main)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()  # a click turns it on or off; Enter saves
        self.toggle(event.option.id)

    def action_toggle(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is not None:
            self.toggle(options.get_option_at_index(options.highlighted).id)

    def toggle(self, name: str | None) -> None:
        if name is not None and self.picked.toggle(name):
            self.redraw(name)

    def redraw(self, name: str) -> None:
        self.query_one(OptionList).replace_option_prompt(name, self.prompt(clis.CLIS[name]))

    def action_save(self) -> None:
        self.dismiss(self.picked.save())

    def action_cancel(self) -> None:
        self.dismiss(None)
