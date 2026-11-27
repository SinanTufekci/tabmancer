"""errors.log in tabmancer's own folder: an error that should not take anything down is written
there (the newest only), for a report. With no UI at all: the terminal dashboard's workers
(safework.py), the MCP server or a desktop window can write to it alike.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import config_dir

LOG = "errors.log"
LOG_MOST = 256 * 1024  # the newest errors only: enough for a report, never a growing file
_writing = threading.Lock()  # two threads failing at once: each error whole, neither written over


def log_error(where: str, error: BaseException) -> Path:
    path = config_dir() / LOG
    text = (f"--- {datetime.now():%Y-%m-%d %H:%M:%S} in {where} (tabmancer {__version__})\n"
            + "".join(traceback.format_exception(error)))
    with _writing:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            path.write_text((old + text)[-LOG_MOST:], encoding="utf-8")
        except OSError:
            pass
    return path


def failed_words(name: str, error: BaseException, path: Path) -> str:
    """What a front end says of work in the background that failed: where the details are, and to report it."""
    return f"Something went wrong in the background ({name}): {error}. The details are in {path}; please report it."


def guarded(where: str, work: Callable[..., object], *args: object,
            failed: Callable[[BaseException, Path], object] | None = None) -> None:
    """work(*args), the boundary of a thread of its own: what it raises is logged (log_error, as where) and,
    with failed, handed on with the log's path, for the front end to say it or to answer what waits on the
    work (a dialog that would wait for ever). Nothing is raised: a thread that dies says nothing."""
    try:
        work(*args)
    except Exception as error:  # noqa: BLE001 - the boundary: whatever went wrong, the app stays up
        path = log_error(where, error)
        if failed is not None:
            try:
                failed(error, path)
            except Exception as again:  # noqa: BLE001 - its answer failed too: logged, nothing more to do
                log_error(f"{where} (failed)", again)
