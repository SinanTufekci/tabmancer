"""The dashboard's actions on sessions and teams, without a screen (G3).

Each is a plain function over the sessions a front end holds: it returns what came of it, or raises
ActionError with the words to show. Dialogs, notes, threads and the list's rows stay with the front
end (the Textual dashboard, the desktop window), and so does the actor lock: an action never claims
or checks it, so a window that only watches cannot act by calling one. What only the front end
knows (the open sessions, how a session stands now) comes in as an argument.

Slice 1: the handover of one session (h).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from . import clis, handover, summarize, teams
from .sessions import Exit, Session, Status


class ActionError(Exception):
    """An action that did not happen, with the words a front end shows for it."""

    def __init__(self, words: str, *, closed: bool = False) -> None:
        super().__init__(words)
        self.words = words
        self.closed = closed  # the session was closed on the way (a handover's)


@dataclass
class Refusal:
    """Why an action is not started: the words, and how long they stay up (None: the default)."""

    words: str
    seconds: float | None = None


@dataclass
class Handed:
    """A handover written: the fresh session's first message, and why the old one is still open ('' when
    it was closed or was not open)."""

    written: str
    left_open: str = ""


def handover_refusal(session: Session, open_ones: list[Session], queued: bool = False) -> Refusal | None:
    """Why h does not hand this session over; None when it can. A queued one (h again cancels it) is
    not checked for a reason it cannot be closed."""
    if session.status is Status.BACKGROUND:
        return Refusal("Background sessions cannot be handed over from here: attach to it first.")
    readable = session.path and session.path.suffix == ".jsonl" and session.path.is_file()
    if session.cli == "agy" and not readable and not clis.installed("agy"):
        return Refusal("Antigravity has no readable transcript for this session and agy is not on PATH.")
    if queued or session.exit is not Exit.RUNNING:
        return None
    # A busy one is handed over once it finishes; only a reason that stays stops it now.
    reason = handover.cannot_close(replace(session, live_state="idle"), open_ones)
    return Refusal(f"{session.project} cannot be handed over now: {reason}.", 10) if reason else None


def write_handover(session: Session, how: str, now: Callable[[], tuple[Session, list[Session]]],
                   member_of: Callable[[Session], object] | None = None) -> Handed:
    """Write the handover ("self": the session writes it; "transcript": Claude writes it from the
    transcript) and close the session if it is open. Blocks for minutes: never on a screen's thread.
    `now` gives the session as the list has it now and the open sessions, read when it is closed;
    `member_of` the team a session plays in, as the front end knows its teams (teams.member_of).

    The session writing it itself needs it closed first. Written from the transcript, the handover is
    there before the old session closes, so a failed close only leaves it open (a team member's never:
    its fresh session takes its name, and a message to <team>-<member> had two sessions to go to)."""
    written, left_open, closed = None, "", False
    try:
        if how == "transcript":
            written = handover.from_transcript(session)
        if session.exit is Exit.RUNNING:
            fresh, open_ones = now()  # as it is now, in case it got busy again while the handover was written
            try:
                handover.close(fresh, open_ones)
                closed = True
            except handover.HandoverError as error:
                if written is None or (member_of or teams.member_of)(session) is not None:
                    raise
                left_open = str(error)
        if written is None:
            written = handover.write_itself(session)
    except (handover.HandoverError, summarize.SummaryError, OSError) as error:
        after = " It is closed now; Enter on it resumes it." if closed else ""
        raise ActionError(f"{session.project} was not handed over: {str(error).rstrip('.')}.{after}",
                          closed=closed) from error
    return Handed(written, left_open)
