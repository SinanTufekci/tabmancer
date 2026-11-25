"""The dashboard's colours and its themes' names, with no UI behind them: the terminal dashboard
(theme.py) and the desktop window (desktop/look.py) both draw from here, and so can a command that
runs with neither Textual nor Qt."""

from __future__ import annotations

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

# Layers, no frames: the list on black, what sits around it a tone up (the top bar, the cards, the
# preview, the footer), the sidebar in between. The highlighted row takes the theme's own colour.
SURFACE = "#101010"
SUNKEN = "#0a0a0a"

# name -> (label shown when switching, accent colour)
THEMES = {
    "tabmancer-black": ("black · orange", "#d97757"),
    "tabmancer-black-green": ("black · green", GREEN),
    "tabmancer-black-mono": ("black · mono", "#d0d0d0"),
}
DEFAULT_THEME = "tabmancer-black"
OLD_NAMES = ("antyard-", "ccharbor-", "ccrestore-")  # what the themes were saved under before


def saved_theme() -> str:
    """The theme picked last (config.json), under its name today; the default when none was, or
    when what was saved names a theme there is no longer."""
    saved = read_json("config.json")
    name = saved.get("theme") if isinstance(saved, dict) else None
    if isinstance(name, str):
        for old in OLD_NAMES:
            if name.startswith(old):
                name = "tabmancer-" + name.removeprefix(old)
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME


def mix(start: str, end: str, amount: float) -> str:
    """Blend two #rrggbb colours; amount 0 gives start, 1 gives end. Drives the fades."""
    amount = max(0.0, min(1.0, amount))
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))
