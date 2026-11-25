"""The h dialog: hand a session over to a fresh one, written by the session itself or from its transcript.
An open session is closed on the way."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from . import handoverwords, sessionwords
from .dialogs import frame, title
from .sessions import Session, _shorten
from .theme import CYAN, MUTED, TEXT, keys


def fill(session: Session, timed: bool = True, cap: int = 0) -> Text:
    """'917K of 1M (92%) · going for 6h 12m', or '240K of a 300K cap (80%)' for a session opened with a
    context cap, coloured by how full it is (without the time, where it has a line of its own)."""
    parts = sessionwords.fill_parts(session, timed, cap)
    return Text(*parts[0]) if len(parts) == 1 else Text.assemble(*parts)


class HandoverDialog(ModalScreen[str | None]):
    """Dismisses with "self" (the session writes it) or "transcript" (Claude writes it from the transcript)."""

    DEFAULT_CSS = frame("HandoverDialog", "medium") + """
    HandoverDialog OptionList { height: auto; margin-top: 1; background: $surface; border: none; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        session = self.session
        options = [Option(Text.assemble((way.title, f"bold {TEXT}"), (f"  {way.why}\n", MUTED), (f"  {way.how}", MUTED)),
                          id=way.id) for way in handoverwords.ways(session)]
        with Vertical():
            yield Label(title("Hand over", (session.project, f"bold {CYAN}"), f"“{_shorten(session.title, 50)}”"))
            yield Label(Text.assemble(("Context ", MUTED), fill(session)))
            yield Label(Text(handoverwords.about(session), style=MUTED), id="handover-what")
            yield OptionList(*options)
            yield Label(keys(("Enter", "hand over"), ("↑↓", "the other way"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
