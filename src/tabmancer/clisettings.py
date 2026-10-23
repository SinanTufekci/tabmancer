"""The `,` dialog: which CLIs' sessions the dashboard lists."""

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
from .theme import CYAN, GREEN, MUTED, TEXT, keys


class CliSettings(ModalScreen[list[str] | None]):
    """Space turns a CLI on or off, Enter saves, Esc leaves things as they were."""

    DEFAULT_CSS = frame("CliSettings", "medium") + """
    CliSettings OptionList { height: auto; background: $surface; border: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "save", "Save", priority=True),
        Binding("space", "toggle", "Turn on or off", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.on = set(clis.enabled())

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(title("Which CLIs do you use?"))
            yield Label(Text("Their sessions join the list, open in tabs and count in the stats. "
                             "Claude Code is the one every feature works with.", style=MUTED))
            yield OptionList(*[Option(self.prompt(cli), id=cli.name) for cli in clis.CLIS.values()])
            yield Label(keys(("Space", "turn one on or off"), ("Enter", "save"), ("Esc", "cancel")), classes="hint")

    def prompt(self, cli: clis.Cli) -> Text:
        """[x] or [ ] in front, as in the session list: a dimmed X read as a tick."""
        on = cli.name in self.on
        found = clis.installed(cli.name)
        return Text.assemble(("[x] " if on else "[ ] ", f"bold {CYAN}" if on else MUTED),
                             (f"{cli.label:<13}", f"bold {TEXT}"),
                             ("installed" if found else "not found", GREEN if found else MUTED),
                             (f"\n    {cli.reads}", MUTED))

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
        if name not in clis.CLIS:
            return
        self.on ^= {name}
        self.query_one(OptionList).replace_option_prompt(name, self.prompt(clis.CLIS[name]))

    def action_save(self) -> None:
        self.dismiss([name for name in clis.CLIS if name in self.on])

    def action_cancel(self) -> None:
        self.dismiss(None)
