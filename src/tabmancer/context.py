"""How full a session's context window is.

A session's context is what its last reply was sent with: fresh input plus what was read
from and written to the cache (the statusline's used_percentage counts the same). The
window is 1M tokens for the models that have it natively and 200K for the rest, as in
Claude Code's model configuration docs; a context already past 200K proves a 1M window.
"""

from __future__ import annotations

import os
import re

from .sessions import Session

SMALL, LARGE = 200_000, 1_000_000
WARN_SHARE = float(os.environ.get("TABMANCER_CONTEXT_WARN", "0.6"))  # when a handover is worth suggesting
# The version after the family, one or two digits each: the date of an older id (claude-opus-4-20250514,
# claude-3-7-sonnet-20250219) was read as a version above every one, and their windows as 1M.
MODEL = re.compile(r"(opus|sonnet|haiku|fable)-(\d{1,2})(?:-(\d{1,2}))?(?!\d)")


def window(model: str, seen: int = 0) -> int:
    """Context window of a model id such as claude-opus-5-5 or claude-sonnet-4-6[1m]."""
    name = model.lower()
    if "[1m]" in name or seen > SMALL:
        return LARGE
    match = MODEL.search(name)
    if not match:
        return SMALL
    family, major, minor = match.group(1), int(match.group(2)), int(match.group(3) or 0)
    version = (major, minor)
    large = (family == "fable" or (family == "sonnet" and version >= (5, 0))
             or (family == "haiku" and version >= (5, 5)) or (family == "opus" and version >= (4, 7)))
    return LARGE if large else SMALL


def share(session: Session) -> float:
    """0..1 of the window in use; 0 when nothing is known."""
    if not session.context:
        return 0.0
    return min(1.0, session.context / window(session.model, session.context))


def crowded(session: Session) -> bool:
    """Full enough that a handover to a fresh session is worth suggesting."""
    return session.cli == "claude" and share(session) >= WARN_SHARE
