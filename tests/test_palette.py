"""The colours and themes' names with no UI behind them: what the terminal dashboard and the desktop
window both draw from."""

import ast
from pathlib import Path

import pytest

from tabmancer import config, palette, theme


def test_the_palette_imports_no_ui():
    tree = ast.parse(Path(palette.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append("." * node.level + (node.module or ""))
    assert imported == ["__future__", ".config"]


@pytest.mark.parametrize(("saved", "shown"), [
    (None, palette.DEFAULT_THEME),
    ({"theme": "tabmancer-black-green"}, "tabmancer-black-green"),
    ({"theme": "ccharbor-black-mono"}, "tabmancer-black-mono"),  # saved under an old name
    ({"theme": "antyard-black"}, "tabmancer-black"),
    ({"theme": "textual-dark"}, palette.DEFAULT_THEME),  # an older save named another theme
    ({"theme": 3}, palette.DEFAULT_THEME),
    (["tabmancer-black-green"], palette.DEFAULT_THEME),  # a config.json that is not an object
])
def test_the_saved_theme(saved, shown):
    if saved is not None:
        config.write_json("config.json", saved)
    assert palette.saved_theme() == shown


def test_the_dashboard_names_the_same_colours():
    assert (theme.GREEN, theme.RED, theme.SURFACE, theme.THEMES, theme.mix) == (
        palette.GREEN, palette.RED, palette.SURFACE, palette.THEMES, palette.mix)
    assert palette.mix("#000000", "#ffffff", 0.5) == "#808080"
