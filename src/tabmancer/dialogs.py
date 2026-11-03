"""The one look every dialog shares: a frame of one of three widths, a title line in bold with
what it is about beside it in grey, and its keys on the last line.

Small is for a question (a name, yes or no), medium for a form or a choice, large for a list or
a view with much in it. A dialog that is about to delete something has an amber frame. Esc says
"cancel" where Enter would do something, "close" where it only shows.
"""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static

from .theme import CYAN, MUTED, TEXT

WIDTHS = {"small": 72, "medium": 96, "large": 112}
GAP = "   "


class Choice(Static, can_focus=True):
    """One of a few words, shown side by side with the one picked marked: ● opus   ○ sonnet.
    ←/→ (or Space) moves the mark while it has the focus; a click picks a word."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("left", "step(-1)", "Previous", show=False),
        Binding("right,space", "step(1)", "Next", show=False),
    ]
    DEFAULT_CSS = """
    Choice { height: 1; width: 1fr; }
    Choice:focus { background: $panel-lighten-2; }
    Choice.-off { opacity: 0.5; }
    """

    class Changed(Message):
        def __init__(self, choice: Choice, value: str) -> None:
            super().__init__()
            self.choice, self.value = choice, value

        @property
        def control(self) -> Choice:
            return self.choice

    def __init__(self, words: list[str], value: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self.words = list(words)
        self.value = value if value in self.words else self.words[0]

    def render(self) -> Text:
        out = Text()
        for word in self.words:
            on = word == self.value
            out.append(("● " if on else "○ ") + word, style=f"bold {CYAN if self.has_focus else TEXT}" if on else MUTED)
            out.append(GAP)
        return out

    def pick(self, value: str) -> None:
        if value in self.words and value != self.value:
            self.value = value
            self.refresh()
            self.post_message(self.Changed(self, value))

    def action_step(self, step: int) -> None:
        self.pick(self.words[(self.words.index(self.value) + step) % len(self.words)])

    def on_click(self, event: events.Click) -> None:
        start = 0
        for word in self.words:
            end = start + len(word) + 2
            if start <= event.x < end:
                self.pick(word)
                return
            start = end + len(GAP)

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


def frame(name: str, size: str = "medium", height: str = "auto", tone: str = "$primary") -> str:
    """A dialog's CSS: centred on the dimmed dashboard, its box of that width, its key line last."""
    return f"""
    {name} {{ align: center middle; }}
    {name} > Vertical {{ width: {WIDTHS[size]}; max-width: 95%; height: {height}; max-height: 90%;
                        border: round {tone}; background: $surface; padding: 1 2; }}
    {name} .hint {{ color: $text-muted; margin-top: 1; height: auto; }}
    """


def title(words: str, *about: str | tuple[str, str]) -> Text:
    """'Hand over  api-server · “Rate limiting”': the title in bold, then what it is about (each
    piece grey unless given its own style), set apart by dots."""
    out = Text(words, style="bold")
    for index, piece in enumerate(about):
        text, style = piece if isinstance(piece, tuple) else (piece, MUTED)
        out.append("  " if index == 0 else " · ", style=MUTED)
        out.append(text, style=style)
    return out
