"""The one look every dialog shares: a frame of one of three widths, a title line in bold with
what it is about beside it in grey, and its keys on the last line.

Small is for a question (a name, yes or no), medium for a form or a choice, large for a list or
a view with much in it. A dialog that is about to delete something has an amber frame. Esc says
"cancel" where Enter would do something, "close" where it only shows.
"""

from __future__ import annotations

from rich.text import Text

from .theme import MUTED

WIDTHS = {"small": 72, "medium": 96, "large": 112}


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
