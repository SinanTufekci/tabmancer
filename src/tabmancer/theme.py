"""Black dashboard themes and the status palette."""

from __future__ import annotations

from rich.text import Text
from textual.app import App
from textual.theme import Theme

from .config import read_json

# Each colour means one thing on every screen: red, your turn (a session waits for you) or
# something about to run out; amber, work to continue (cut off, unanswered); blue, at work;
# green, open and fine; grey, done and past.
GREEN = "#3fb950"
BLUE = "#6cb6ff"
AMBER = "#d29922"
RED = "#f85149"
ORANGE = "#e3884f"
CYAN = "#39c5cf"
MUTED = "#8b949e"
TEXT = "#e6e6e6"

# name -> (label shown when switching, accent colour)
THEMES = {
    "tabmancer-black": ("black · orange", "#d97757"),
    "tabmancer-black-green": ("black · green", GREEN),
    "tabmancer-black-mono": ("black · mono", "#d0d0d0"),
}
DEFAULT_THEME = "tabmancer-black"


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


# Layers, no frames: the list on black, what sits around it a tone up (the top bar, the cards, the
# preview, the footer), the sidebar in between. The highlighted row takes the theme's own colour.
SURFACE = "#101010"
SUNKEN = "#0a0a0a"


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
    saved = read_json("config.json")
    name = saved.get("theme") if isinstance(saved, dict) else None
    for old in ("antyard-", "ccharbor-", "ccrestore-"):  # saved under an old name
        if isinstance(name, str) and name.startswith(old):
            name = "tabmancer-" + name.removeprefix(old)
    app.theme = name if name in THEMES else DEFAULT_THEME  # older saves named other themes


def mix(start: str, end: str, amount: float) -> str:
    """Blend two #rrggbb colours; amount 0 gives start, 1 gives end. Drives the fades."""
    amount = max(0.0, min(1.0, amount))
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))
