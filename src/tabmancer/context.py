"""How full a session's context window is.

A session's context is what its last reply was sent with: fresh input plus what was read
from and written to the cache (the statusline's used_percentage counts the same). The
window is 1M tokens for the models that have it natively and 200K for the rest, as in
Claude Code's model configuration docs; a context already past 200K proves a 1M window.
A session opened with a context cap (--autocompact, see picks) has the cap for its window.

Every reply reads the whole context again, from Claude's cache, and the cache lasts an hour:
a session left longer than that writes all of its context afresh with the next message
(two weeks of one user's sessions: 97% of the replies after an hour's gap did).
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from .sessions import Session

SMALL, LARGE = 200_000, 1_000_000
WARN_SHARE = float(os.environ.get("TABMANCER_CONTEXT_WARN", "0.6"))  # when a handover is worth suggesting
CAPPED_WARN = 0.75  # a capped session: some way before Claude Code compacts it (33k before the cap)
CACHE_SECONDS = 3600  # how long Claude's cache keeps a conversation
COLD_WORTH = 150_000  # a context big enough that writing it again is worth a word
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


def share(session: Session, cap: int = 0) -> float:
    """0..1 of the window in use (of the cap, for a session opened with one); 0 when nothing is known."""
    if not session.context:
        return 0.0
    return min(1.0, session.context / (cap or window(session.model, session.context)))


def warn_at(session: Session, cap: int = 0, default_cap: int = 0) -> int:
    """The context from which a handover is worth suggesting: for a capped session some way before
    Claude Code compacts it; for any other, WARN_SHARE of its window, or the default cap a new
    session gets, whichever comes first (past that, it costs what tabmancer's own sessions never do)."""
    if cap:
        return int(cap * CAPPED_WARN)
    point = int(window(session.model, session.context) * WARN_SHARE)
    return min(point, default_cap) if default_cap else point


def crowded(session: Session, cap: int = 0, default_cap: int = 0) -> bool:
    """Full enough that a handover to a fresh session is worth suggesting."""
    return session.cli == "claude" and bool(session.context) and session.context >= warn_at(session, cap, default_cap)


def cold(session: Session, now: datetime | None = None) -> bool:
    """Its cache gone, with a context big enough to matter: the next message writes all of it again."""
    if session.cli != "claude" or (session.context or 0) < COLD_WORTH or session.last_active is None:
        return False
    return ((now or datetime.now()) - session.last_active).total_seconds() >= CACHE_SECONDS
