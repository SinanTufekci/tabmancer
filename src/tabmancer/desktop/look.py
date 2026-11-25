"""How the desktop window looks: the dashboard's black, its colours and its accent, in Qt.

The colours are the dashboard's own (palette.py) and so are the words for a member (monitorview):
the window draws what the terminal monitor says, in its colours, from the same functions.
"""

from __future__ import annotations

import functools
import os
import re
import sys
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


def accent() -> str:
    """The accent of the theme picked in the dashboard (orange unless another was picked)."""
    return palette.THEMES[palette.saved_theme()][1]


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
