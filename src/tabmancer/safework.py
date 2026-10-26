"""textual.work for an app that stays up: an error in a background worker is written to errors.log in
tabmancer's own folder and said in a notification, where Textual would close the whole app.

Claude Code's files are its own and change between releases, so something will go wrong in the
background one day; the dashboard should say so and go on, and the log is what a report needs.
"""

from __future__ import annotations

import functools
import inspect
import traceback
from datetime import datetime
from pathlib import Path

from textual import work as textual_work
from textual.app import App

from . import __version__
from .config import config_dir

LOG = "errors.log"
LOG_MOST = 256 * 1024  # the newest errors only: enough for a report, never a growing file


def log_error(where: str, error: BaseException) -> Path:
    path = config_dir() / LOG
    text = (f"--- {datetime.now():%Y-%m-%d %H:%M:%S} in {where} (tabmancer {__version__})\n"
            + "".join(traceback.format_exception(error)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        path.write_text((old + text)[-LOG_MOST:], encoding="utf-8")
    except OSError:
        pass
    return path


def work(**options):
    """@work(thread=True, ...) whose worker never takes the app down with it."""
    def decorate(method):
        if inspect.iscoroutinefunction(method):
            raise TypeError(f"{method.__qualname__}: safework.work guards thread workers only")

        @functools.wraps(method)
        def guarded(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except Exception as error:  # noqa: BLE001 - the boundary: whatever went wrong, the app stays up
                path = log_error(method.__qualname__, error)
                _tell(self, f"Something went wrong in the background ({method.__name__}): {error}. "
                            f"The details are in {path}; please report it.")
                return None

        return textual_work(**options)(guarded)
    return decorate


def _tell(node, text: str) -> None:
    try:
        app = node if isinstance(node, App) else node.app
        app.call_from_thread(app.notify, text, severity="error", timeout=20)
    except RuntimeError:  # the app has closed meanwhile (no active app then either)
        pass
