"""errors.log in tabmancer's own folder: an error that should not take anything down is written
there (the newest only), for a report. With no UI at all: the terminal dashboard's workers
(safework.py), the MCP server or a desktop window can write to it alike.
"""

from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

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
