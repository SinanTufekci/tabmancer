"""textual.work for an app that stays up: an error in a background worker is written to errors.log in
tabmancer's own folder and said in a notification, where Textual would close the whole app.

Claude Code's files are its own and change between releases, so something will go wrong in the
background one day; the dashboard should say so and go on, and the log is what a report needs.
"""

from __future__ import annotations

import functools
import inspect

from textual import work as textual_work
from textual.app import App

from .errorlog import LOG, LOG_MOST, log_error  # noqa: F401  (named here for older callers)


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
