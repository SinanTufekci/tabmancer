"""The window as the engine's front (engine.Front): each reading of the store goes to the window's own
Engine (Engine.live), which decides from the terminal dashboard's one copy what is said (a session that
needs you, is done, fell back to another model, is crowded) and what the window's own look does (a row
lit after a change, a tick gone from one that turned out open); this says it and draws it, the window's
way.

The window never acts: its Engine's state.acts stays False and nothing here claims the actor lock, so
what happens by itself (a desktop note for the sessions that turned, continuing at a limit's reset,
today's claude update, keep-warm) stays the acting dashboard's. Engine.live, not acting, says things
and changes the window's look; it hands over only what this window queued itself (h), and there is no
h here yet, so nothing. The members' messages it would type in (state.notes) are a terminal
dashboard's: nothing here puts any there.

The front reaches its window as its Qt parent, not as an attribute: a window that kept a front that
kept the window would be a cycle, closed yet alive till Python's cycle collector came by."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

from .. import actions, alerts, picks, rowwords, teams
from .. import viewmodel as VM
from ..sessions import Session
from ..store import DashSnapshot
from . import look

if TYPE_CHECKING:
    from .main import MainWindow

TONES = {"amber": look.AMBER, "green": look.GREEN, "red": look.RED}  # Bit's tones, as the terminal's mascot has them


class WindowFront(QObject):
    """engine.Front for a main window (its parent): what a live reading turns up, said on Bit's line and
    drawn in the list."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.members: dict[str, tuple[str, str]] = {}  # session id -> (team, member), as the last reading had them

    def window(self) -> MainWindow:
        return self.parent()  # type: ignore[return-value]

    def live(self, snap: DashSnapshot) -> None:
        """A reading of the store to the window's Engine: every session listed (it says what changed since
        its last look), none to set resting (the store did, as it read them), the background sessions'
        jobs. The members as the reading has them, for who() and cap_of()."""
        self.members = {session.session_id: (state.team.name, name) for state, block in
                        VM.team_blocks(list(snap.sessions.values()), snap.team_states) for name, session in block}
        self.window().engine.live(dict(snap.sessions), False, set(), dict(snap.background))

    # --- what the engine asks ---------------------------------------------------------------------

    @property
    def default_cap(self) -> int:
        return picks.cap_tokens(picks.default_cap())

    def who(self, session: Session) -> str:
        return VM.who(session, self.members)

    def cap_of(self, session: Session) -> int:
        found = self.members.get(session.session_id)
        snap = self.window().snap
        state = snap.team_states.get(found[0]) if found and snap is not None else None
        return teams.cap_of(session.session_id, state.team if state is not None else None)

    def monitoring(self) -> bool:
        return False  # a dashboard's window, not a team's monitor

    # --- what it says -----------------------------------------------------------------------------

    def tell(self, event: alerts.Event) -> None:
        """An event on Bit's line: its note, else Bit's own words (one line: the note says more). No
        bell: the window has no terminal to ring."""
        if event.note is not None:
            self.say_note(event.note)
        elif event.bit is not None:
            self.show_bit(event.bit)

    def say_note(self, note: actions.Note) -> None:
        self.window().say_note(note.words, float(note.seconds or 4), note.severity)

    def show_bit(self, bit: alerts.Bit) -> None:
        colour = look.TEXT if bit.tone == "plain" else TONES.get(bit.tone, self.window().accent)
        self.window().say(bit.words, float(bit.seconds or 4), colour)

    def tell_desktop(self, turned: list[Session]) -> None:
        """Never: only the dashboard that acts sends the desktop note (Engine.live asks only then)."""

    # --- what it draws ----------------------------------------------------------------------------

    def flash_changed(self, updates: dict[str, Session]) -> None:
        """The rows whose state changed since the engine's last look (it has not taken these yet), lit."""
        window = self.window()
        window.table.light(rowwords.changed(window.engine.state.sessions, updates.values()))

    def untick_open(self, updates: dict[str, Session]) -> None:
        """Ticked at the start (open at the last shutdown) and open after all, or one that cannot be ticked
        now: unticked. The ticks are the window's own, kept in memory (marks.json keeps no ticks)."""
        window = self.window()
        window.selected, window.preselected = VM.untick_open(window.selected, window.preselected, updates.values())

    def rows_changed(self, ids: set[str]) -> None:
        """Nothing more: the window draws the reading whole once the engine has taken it (MainWindow.take)."""
