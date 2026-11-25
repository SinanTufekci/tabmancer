"""Black dashboard themes in Textual, and the key lines under the dialogs. The colours and the
themes' names live in palette.py (no UI behind them); they are named here too, as the terminal
dashboard's modules import them from here."""

from __future__ import annotations

from rich.text import Text
from textual.app import App
from textual.theme import Theme

from .palette import (  # noqa: F401  (named here for the terminal dashboard's modules)
    AMBER,
    BLUE,
    CYAN,
    DEFAULT_THEME,
    GREEN,
    MUTED,
    ORANGE,
    RED,
    SUNKEN,
    SURFACE,
    TEXT,
    THEMES,
    mix,
    saved_theme,
)


def keys(*pairs: tuple[str, str]) -> Text:
    """The key line under a dialog, the same everywhere: 'Enter  open · Esc  cancel'."""
    out = Text()
    for i, (key, action) in enumerate(pairs):
        if i:
            out.append(" · ", style=MUTED)
        out.append(key, style=f"bold {TEXT}")
        out.append(f"  {action}", style=MUTED)
    return out


def keys_in(width: int, *pairs: tuple[str, str]) -> Text:
    """The key line on as many lines as it needs in this width, a key never parted from what it
    does: wrapped as words, 'start the rest' went on with 'rest' alone on the next line."""
    lines: list[Text] = []
    for pair in pairs:
        one = keys(pair)
        if lines and lines[-1].cell_len + 3 + one.cell_len <= width:
            lines[-1].append(" · ", style=MUTED)
            lines[-1].append_text(one)
        else:
            lines.append(one)
    return Text("\n").join(lines)


def _black(name: str, accent: str) -> Theme:
    return Theme(
        name=name, primary=accent, secondary=MUTED, accent=accent,
        warning=AMBER, error=RED, success=GREEN, foreground=TEXT,
        background="#000000", surface=SURFACE, panel="#181818", dark=True,
        variables={
            "border": "#2a2a2a",
            "border-blurred": "#1c1c1c",
            "sunken": SUNKEN,
            "block-cursor-background": mix("#000000", accent, 0.36),
            "block-cursor-foreground": "#ffffff",
            "block-cursor-text-style": "bold",
            "block-cursor-blurred-background": mix("#000000", accent, 0.22),
            "block-cursor-blurred-foreground": TEXT,
            "block-cursor-blurred-text-style": "none",
            "block-hover-background": "#151515",
            "footer-background": SURFACE,
            "footer-item-background": SURFACE,
            "footer-key-background": SURFACE,
            "footer-description-background": SURFACE,
            "footer-foreground": TEXT,
            "footer-key-foreground": accent,
            "footer-description-foreground": MUTED,
            "scrollbar": "#2a2a2a",
            "scrollbar-hover": "#3a3a3a",
            "scrollbar-active": accent,
            "scrollbar-background": "#000000",
            "scrollbar-background-hover": "#000000",
            "scrollbar-background-active": "#000000",
            "scrollbar-corner-color": "#000000",
            "input-cursor-background": accent,
        },
    )


def themes() -> list[Theme]:
    return [_black(name, accent) for name, (_, accent) in THEMES.items()]


def use_saved_theme(app: App) -> None:
    """Register the black themes and switch to the one saved last (the default otherwise)."""
    for theme in themes():
        app.register_theme(theme)
    app.theme = saved_theme()
