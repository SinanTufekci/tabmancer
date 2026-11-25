"""What opening sessions keeps between presses, for any front end (the terminal dashboard's Enter, the
desktop window's): one copy (G3.3).

The sessions opened from here a moment ago are not opened again while they start up (choose); the
closed ones chosen become launches, a team's lead last so it finds its members up (launches); what an
Enter opened is kept (took), and the ones seen open since are noted (seen), so that a while later the
ones that never came up can be named (due: Claude Code would not resume one, its folder gone, say, and
its tab says why). The clock is a steady one, passed in: monotonic seconds, never the wall clock.

No screen, no threads, no files but the saved teams (teams.load) when no copy is given: the front end
says the words (ALREADY, due's) and opens the tabs (actions.open_launches) its own way.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from . import actions, teams
from .actions import Note, Opened, still_opening
from .launch import Launch, Mode
from .sessions import Exit, Session, Status

CAME_UP_SECONDS = 45  # by then a session opened from here has shown up open, or it is said that it did not
ALREADY = Note("Already opening: it shows as open in a moment.", "warning")


@dataclass
class Openings:
    """opening: session id -> when it was opened from here (on `clock`); came_up: of those, the ones
    seen open since; checks: (when to look, the ones opened then), for a front end that looks at its
    reads (due) rather than on a timer of its own."""

    clock: Callable[[], float] = time.monotonic
    opening: dict[str, float] = field(default_factory=dict)
    came_up: set[str] = field(default_factory=set)
    checks: list[tuple[float, set[str]]] = field(default_factory=list)

    def recent(self) -> set[str]:
        """The sessions opened from here in the last actions.OPENING_SECONDS: still starting up."""
        return still_opening(self.opening, self.clock())

    def choose(self, chosen: Iterable[Session]) -> list[Session]:
        """These, but not the ones still starting up (recent). None left: the front end says ALREADY."""
        recent = self.recent()
        return [session for session in chosen if session.session_id not in recent]

    def launches(self, chosen: Iterable[Session], mode: Callable[[Session], Mode],
                 background: Mapping[str, dict], saved: dict[str, teams.Team] | None = None) -> list[Launch]:
        """How each opens (actions.launch_for), as `mode` says for it (the front end's m), a team's lead
        last; one running in the background is attached to, by its job in `background` (the last full
        read of `claude agents`). saved: the saved teams (teams.load when not given)."""
        saved = teams.load() if saved is None else saved
        return [actions.launch_for(session, mode(session), background.get(session.session_id)
                                   if session.status is Status.BACKGROUND else None, saved)
                for session in teams.lead_last(list(chosen), saved)]

    def took(self, opened: Opened, look_later: bool = True) -> set[str]:
        """What an Enter opened, kept: not opened again while it starts; the ones it resumed, looked for
        a while later. Those (to look for) are given back. look_later: kept in checks, for due; a front
        end with a timer of its own (the terminal dashboard's) passes False and asks came_up itself."""
        if not opened.opened:
            return set()
        now = self.clock()
        self.opening.update(dict.fromkeys(opened.opened_ids, now))
        resumed = opened.resumed_ids
        self.came_up -= resumed
        if look_later:
            self.checks.append((now + CAME_UP_SECONDS, resumed))
        return resumed

    def seen(self, sessions: Mapping[str, Session]) -> None:
        """A read came back (sessions: what it read, or only what changed): the ones opened from here
        that show open now came up."""
        self.came_up |= {sid for sid, session in sessions.items()
                         if session.exit is Exit.RUNNING and sid in self.opening}

    def due(self, sessions: Mapping[str, Session]) -> str | None:
        """The checks whose time has come, done: the words for the ones opened then that never came up
        (actions.came_up_missing), or None."""
        now = self.clock()
        ready = [ids for when, ids in self.checks if when <= now]
        if not ready:
            return None
        self.checks = [(when, ids) for when, ids in self.checks if when > now]
        return actions.came_up_missing(set().union(*ready), self.came_up, dict(sessions))
