"""The desktop window: `tabmancer desktop`, from the optional extra (pip install "tabmancer[desktop]").

Qt comes from PySide6-Essentials (LGPL, used as installed). Only this package imports it: without
the extra, tabmancer and its tests run as ever, and `tabmancer desktop` says how to add it.

The dashboard's window, `tabmancer desktop` (desktop.main; bare `tabmancer` opens it too), reads the
sessions through store.DashboardStore on a thread of its own and acts as the terminal dashboard does
while it holds the actor lock; another dashboard holding it, it watches and takes over when that one
is gone. Its keys, menus, key bar and palette are the dashboard's keys.

The team monitor, `tabmancer desktop --monitor [team]` (M in the window, and `tabmancer team watch`),
reads one team through store.TeamStore; what changes the team acts through a dashboard window, its
hands (monitor.py).
"""

from __future__ import annotations

import importlib.util

INSTALL = 'pip install "tabmancer[desktop]"'


def available() -> bool:
    """Whether Qt is installed (the desktop extra), found without importing it."""
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False
