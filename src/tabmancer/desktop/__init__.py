"""The desktop window: `tabmancer desktop`, from the optional extra (pip install "tabmancer[desktop]").

Qt comes from PySide6-Essentials (LGPL, used as installed). Only this package imports it: without
the extra, tabmancer and its tests run as ever, and `tabmancer desktop` says how to add it.

Its first part is the team monitor, `tabmancer desktop --monitor [team]`. It only watches: it reads
the team through the one-team store (store.TeamStore) on a thread of its own, never claims the
actor lock (the dashboard that acts goes on acting), and starts, sends and closes nothing. The
terminal monitor (`tabmancer team watch`, M in the dashboard) stays as it is.
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
