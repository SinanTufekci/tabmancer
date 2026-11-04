"""The one look every dialog shares: a frame of one of three widths, a title line in bold with
what it is about beside it in grey, and its keys on the last line.

Small is for a question (a name, yes or no), medium for a form or a choice, large for a list or
a view with much in it. A dialog that is about to delete something has an amber frame. Esc says
"cancel" where Enter would do something, "close" where it only shows.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.suggester import Suggester
from textual.widgets import Static

from .theme import CYAN, MUTED, TEXT

WIDTHS = {"small": 72, "medium": 96, "large": 112}
GAP = "  "
PICKED = "#2d333b"  # the ground of the word picked, while its row is not in focus
ON_CYAN = "#06181a"  # a word on cyan


def pill(word: str, on: bool, focused: bool = False) -> Text:
    """One of a few words: ' ● word ' on a ground of its own when it is the one picked (cyan while its
    row has the focus), else '○ word' in grey."""
    if not on:
        return Text(f"○ {word}", style=MUTED)
    return Text(f" ● {word} ", style=f"bold {ON_CYAN} on {CYAN}" if focused else f"bold {TEXT} on {PICKED}")


def pills(words: list[str], picked: str, focused: bool = False, width: int = 0,
          labels: dict[str, str] | None = None) -> Text:
    """Words side by side, the one picked marked (pill), each shown as labels has it, if it does.
    Given a width, they go on as many lines as they need, each word whole on its line (wrapped by
    the terminal, a word broke in two)."""
    out, used = Text(), 0
    for word in words:
        piece = pill((labels or {}).get(word, word), word == picked, focused)
        if used and width and used + len(GAP) + piece.cell_len > width:
            out.append("\n")
            used = 0
        elif used:
            out.append(GAP)
            used += len(GAP)
        out.append_text(piece)
        used += piece.cell_len
    return out


def cards(name: str) -> str:
    """CSS for a dialog's cards (.card): a part of it in a frame of its own, its name in the frame;
    the card the cursor is in (-lit, light_rows) framed in the theme's colour."""
    return f"""
    {name} .card {{ border: round $panel-lighten-2; border-title-color: $text-muted; border-title-style: bold;
                    border-subtitle-color: $text-muted; padding: 0 1; background: $surface; }}
    {name} .card.-lit {{ border: round $accent; border-title-color: $accent; }}
    {name} Input, {name} TextArea {{ margin: 0; background: $panel; }}
    {name} Input:focus, {name} TextArea:focus {{ background: $panel-lighten-1; }}
    {name} .hint {{ dock: bottom; }}
    """


def light_rows(screen) -> None:
    """A form's rows and cards (.row, .card) with the ones that hold the focus lit (-lit). Textual's
    :focus-within kept the style a row had when the dialog opened, whatever had the focus after."""
    focused = screen.focused
    for row in screen.query(".row, .card"):
        row.set_class(focused is not None and row in focused.ancestors_with_self, "-lit")


class FolderSuggester(Suggester):
    """Completes a folder from the ones given, whatever the case, and whether its / and \\ are written
    as they are there or the other way (on Windows "C:/Users/me/Desk" completes to its Desktop too, in
    the way it was begun)."""

    def __init__(self, folders: list[str]) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self.folders = folders

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        begun = value.replace("\\", "/").casefold()
        for form in (form for folder in self.folders for form in (folder, tilde(folder))):  # ~ for home too
            if len(form) > len(value) and form.replace("\\", "/").casefold().startswith(begun):
                rest = form[len(value):]
                return value + (rest.replace("\\", "/") if "/" in value and "\\" not in value else rest)
        return None


def tilde(path: str) -> str:
    """A folder with your home folder written ~ (expanded again by os.path.expanduser)."""
    home = str(Path.home())
    inside = path.casefold().startswith(home.casefold()) and path[len(home):][:1] in ("", "\\", "/")
    return "~" + path[len(home):] if path and inside else path


class Choice(Static, can_focus=True):
    """One of a few words, shown side by side with the one picked marked: ● opus   ○ sonnet, on
    two lines or more where one is too narrow. ←/→ (or Space) moves the mark while it has the
    focus; a click picks a word. labels: how a word is shown, where it is not shown as it is."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("left", "step(-1)", "Previous", show=False),
        Binding("right,space", "step(1)", "Next", show=False),
    ]
    DEFAULT_CSS = """
    Choice { height: auto; width: 1fr; }
    Choice.-off { opacity: 0.5; }
    """

    class Changed(Message):
        def __init__(self, choice: Choice, value: str) -> None:
            super().__init__()
            self.choice, self.value = choice, value

        @property
        def control(self) -> Choice:
            return self.choice

    def __init__(self, words: list[str], value: str = "", labels: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.words = list(words)
        self.labels = labels or {}
        self.value = value if value in self.words else self.words[0]

    def render(self) -> Text:
        return pills(self.words, self.value, self.has_focus, width=self.content_size.width, labels=self.labels)

    def pick(self, value: str) -> None:
        if value in self.words and value != self.value:
            self.value = value
            self.refresh()
            self.post_message(self.Changed(self, value))

    def action_step(self, step: int) -> None:
        self.pick(self.words[(self.words.index(self.value) + step) % len(self.words)])

    def on_click(self, event: events.Click) -> None:
        # Where each word stands, line by line, as pills() lays them out.
        line, start, width = 0, 0, self.content_size.width
        for word in self.words:
            size = len(self.labels.get(word, word)) + (4 if word == self.value else 2)  # ' ● word ', '○ word'
            if start and width and start + len(GAP) + size > width:
                line, start = line + 1, 0
            elif start:
                start += len(GAP)
            if event.y == line and start <= event.x < start + size:
                self.pick(word)
                return
            start += size

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
