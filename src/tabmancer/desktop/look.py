"""How the desktop window looks: the dashboard's black, its colours and its accent, in Qt.

The colours are the dashboard's own (palette.py) and so are the words for a member (monitorview):
the window draws what the terminal monitor says, in its colours, from the same functions.
"""

from __future__ import annotations

import functools
import os
import re
import sys
from dataclasses import astuple, dataclass
from html import escape

from PySide6.QtGui import QColor, QFont, QFontDatabase

from .. import palette

GREEN, BLUE, AMBER, RED, ORANGE, CYAN = (palette.GREEN, palette.BLUE, palette.AMBER, palette.RED, palette.ORANGE,
                                         palette.CYAN)
MUTED, TEXT = palette.MUTED, palette.TEXT
BLACK, SUNKEN, SURFACE = "#000000", palette.SUNKEN, palette.SURFACE
PANEL, BORDER, FAINT, DIM = "#181818", "#2a2a2a", "#262626", "#5a5a5a"
HEX = re.compile(r"#[0-9a-fA-F]{6}")
SPACES = re.compile(r"(?<= ) ")  # every space after another one
MONO_FAMILIES = ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Menlo",
                 "Liberation Mono", "Courier New")


ACCENT = palette.THEMES[palette.DEFAULT_THEME][1]  # the dashboard's own orange


def accent() -> str:
    """The window's accent, always the dashboard's default theme's: one look here, no theme read or cycled (t and
    config.json's theme are the terminal dashboard's)."""
    return ACCENT


def colour_of(style: str, default: str = TEXT) -> tuple[str, bool]:
    """A Rich style the dashboard words a thing in ('bold #f85149') as (colour, bold)."""
    found = HEX.search(style or "")
    return (found.group(0) if found else default), "bold" in (style or "").split()


def span(words: str, style: str = "", default: str = TEXT) -> str:
    """Words as HTML in a style's colour, for a QLabel."""
    colour, bold = colour_of(style, default)
    weight = "font-weight:600;" if bold else ""
    text = SPACES.sub("&nbsp;", escape(words))  # 'NEEDS YOU  tests': HTML would fold the two spaces into one
    return f'<span style="color:{colour};{weight}">{text}</span>'


@dataclass(frozen=True)
class Ink:
    """A style the dashboard's words come in ('bold #f85149 on #3d0f0d', 'dim', 'yellow'), read: what it
    sets, None where it says nothing (so a style drawn over another keeps the other's there)."""

    colour: str | None = None
    background: str | None = None
    bold: bool | None = None
    dim: bool | None = None
    italic: bool | None = None
    reverse: bool | None = None

    def over(self, under: Ink) -> Ink:
        """This style drawn over another, as rich combines them: what this one says wins."""
        return Ink(*(mine if mine is not None else theirs
                     for mine, theirs in zip(astuple(self), astuple(under), strict=True)))

    def drawn(self, default: str = TEXT) -> tuple[str, str | None, bool, bool]:
        """(colour, background, bold, italic) to draw: dim toward black (the quiet grey with no colour of
        its own), reverse the colour behind black words."""
        colour = self.colour or default
        if self.dim:
            colour = palette.mix(colour, BLACK, 0.4) if self.colour else DIM
        background = self.background
        if self.reverse:
            colour, background = BLACK, colour
        return colour, background, bool(self.bold), bool(self.italic)


# The terminal's named colours, as the dashboard's own (the live view's 'bold red' and 'yellow').
NAMED = {"red": RED, "yellow": AMBER, "green": GREEN, "blue": BLUE, "cyan": CYAN, "white": "#ffffff",
         "black": BLACK}
FLAGS = ("bold", "dim", "italic", "reverse")


@functools.lru_cache(maxsize=512)
def ink(style: str) -> Ink:
    """A rich style string read (Ink): a colour (#rrggbb or a name), 'on' a background, the flags."""
    found: dict = {}  # Ink's fields by name
    words = (style or "").split()
    for index, word in enumerate(words):
        if word == "on" or (index and words[index - 1] == "on"):
            continue
        if word in FLAGS:
            found[word] = True
        elif HEX.fullmatch(word) or word in NAMED:
            found["colour"] = NAMED.get(word, word)
    if "on" in words and words.index("on") + 1 < len(words):
        behind = words[words.index("on") + 1]
        found["background"] = NAMED.get(behind, behind if HEX.fullmatch(behind) else None)
    return Ink(**found)


def styled(words: str, style: str = "", default: str = TEXT) -> str:
    """Words as HTML in any style sessionwords and teamwords give: a colour, bold, dim (toward black,
    or the quiet grey), italic, reverse (the colour behind black words) and a background ('on');
    a line break kept."""
    colour, background, bold, italic = ink(style).drawn(default)
    css = (f"color:{colour};" + (f"background:{background};" if background else "")
           + ("font-weight:600;" if bold else "") + ("font-style:italic;" if italic else ""))
    text = SPACES.sub("&nbsp;", escape(words)).replace("\n", "<br>")
    return f'<span style="{css}">{text}</span>'


def percent_colour(percent: float) -> str:
    """A limit or context used: red from 85%, amber from 60%, as the terminal monitor says it."""
    return RED if percent >= 85 else AMBER if percent >= 60 else TEXT


def fonts_for_offscreen() -> None:
    """Qt's offscreen platform finds no fonts on Windows unless told where they are (measured)."""
    if sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


@functools.cache
def mono_family() -> str:
    """The first monospace family installed (Cascadia on Windows), else Qt's fixed font. Looked up once: the
    list of families is long, and a picture that moves asks for its fonts many times a second."""
    families = set(QFontDatabase.families())
    found = next((f for f in MONO_FAMILIES if f in families), None)
    return found or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def font(size: float, bold: bool = False, family: str = "") -> QFont:
    out = QFont(family or mono_family())
    out.setPointSizeF(size)
    out.setBold(bold)
    out.setStyleHint(QFont.StyleHint.Monospace)
    return out


def qcolor(colour: str) -> QColor:
    return QColor(colour)


def sheet(accent_colour: str) -> str:
    """The window's style sheet: black, hairline borders, the accent where something is picked."""
    return f"""
    QMainWindow, QWidget#central {{ background: {BLACK}; }}
    QMainWindow::separator {{ background: {BORDER}; width: 3px; height: 3px; }}
    QWidget {{ color: {TEXT}; }}
    QMenuBar {{ background: {SUNKEN}; color: {TEXT}; padding: 2px 6px; }}
    QMenuBar::item {{ padding: 3px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {PANEL}; }}
    QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}
    QMenu::item {{ padding: 4px 22px 4px 14px; }}
    QMenu::item:selected {{ background: {PANEL}; color: {accent_colour}; }}
    QMenu::item:disabled {{ color: {DIM}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    QDockWidget {{ color: {MUTED}; }}
    QDockWidget::title {{ background: {PANEL}; padding: 5px 10px; text-align: left;
                          border-bottom: 1px solid {BORDER}; }}
    QMainWindow > QTabBar {{ background: {SUNKEN}; }}
    QMainWindow > QTabBar::tab {{ background: {SUNKEN}; color: {MUTED}; padding: 4px 14px; border: none;
                                  border-top: 2px solid transparent; }}
    QMainWindow > QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; border-top: 2px solid {accent_colour}; }}
    QMainWindow > QTabBar::tab:hover {{ color: {TEXT}; }}
    QTabBar#teams {{ background: {BLACK}; }}
    QTabBar#teams::tab {{ background: {BLACK}; color: {MUTED}; padding: 5px 14px; border: none;
                          border-bottom: 2px solid transparent; }}
    QTabBar#teams::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {accent_colour}; }}
    QTabBar#teams::tab:hover {{ color: {TEXT}; }}
    QSplitter::handle {{ background: {BORDER}; }}
    QFrame#lane {{ background: {SUNKEN}; border: 1px solid {FAINT}; border-radius: 8px; }}
    QPushButton#view {{ color: {MUTED}; background: {PANEL}; border: 1px solid {BORDER}; padding: 3px 14px; }}
    QPushButton#view:checked {{ color: {BLACK}; background: {accent_colour}; border-color: {accent_colour}; }}
    QPushButton#view:disabled {{ color: {DIM}; }}
    QFrame#commandbar {{ background: {SURFACE}; border: 1px solid {accent_colour}; }}
    QLineEdit#commandline {{ background: {SUNKEN}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 4px;
                             padding: 6px 10px; selection-background-color: {accent_colour}; selection-color: {BLACK}; }}
    QListWidget#commands {{ background: transparent; border: none; outline: none; }}
    QLineEdit#filter {{ background: {BLACK}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 4px;
                        padding: 3px 8px; selection-background-color: {accent_colour}; selection-color: {BLACK}; }}
    QLineEdit#filter:focus {{ border-color: {accent_colour}; }}
    QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
    QStatusBar::item {{ border: none; }}
    QScrollArea, QTextBrowser {{ background: {SUNKEN}; border: none; }}
    QScrollBar:vertical {{ background: {SUNKEN}; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {FAINT}; border-radius: 4px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
    """
