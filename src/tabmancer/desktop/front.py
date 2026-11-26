"""The window as the engine's front (engine.Front): each reading of the store goes to the window's own
Engine (Engine.live), which decides from the terminal dashboard's one copy what is said (a session that
needs you, is done, fell back to another model, is crowded) and what the window's own look does (a row
lit after a change, a tick gone from one that turned out open); this says it and draws it, the window's
way. h goes through the same engine (Engine.handover): its dialog asks how (desktop/handover.py), the
handover is written on a thread of its own and comes back by a Signal (Engine.handover_ready), and the
fresh session opens as the engine opens one (Engine.open). h twice on a team the same way: its lead writes
the handover on a thread (Engine.team_handover), its dialog shows it with the line-up (desktop/teamhandover.py),
and the new generation opens as the terminal's opens (teamkeys.TeamKeys.open_team: claude --bg members on a
thread, then the tabs). A handover being written is never cut short: the window closing waits for it
(handing_now), and so does settle, however long it takes.

The window never acts: its Engine's state.acts stays False and nothing here claims the actor lock, so
what happens by itself (a desktop note for the sessions that turned, continuing at a limit's reset,
keep-warm) stays the acting dashboard's. Engine.live, not acting, says things and changes the window's
look; the only thing it starts is a handover this window queued itself (h on a busy one), once that one
finishes: one queued in another window is that window's (state.queued is each window's own, and
actor.first_to lets one of two that queued the same one hand it over). The members' messages it would
type in (state.notes) are a terminal dashboard's: nothing here puts any there.

The front reaches its window as its Qt parent, not as an attribute: a window that kept a front that
kept the window would be a cycle, closed yet alive till Python's cycle collector came by."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .. import actions, actor, alerts, claudeupdate, handover, launch, picks, rowwords, teamkeys, teams
from .. import viewmodel as VM
from ..launch import Launch
from ..sessions import Session
from ..store import DashSnapshot
from . import look, opening
from .handover import HandoverDialog
from .teamhandover import TeamHandoverDialog

if TYPE_CHECKING:
    from .main import MainWindow

TONES = {"amber": look.AMBER, "green": look.GREEN, "red": look.RED}  # Bit's tones, as the terminal's mascot has them
NOW_SECONDS = 5.0  # how long a handover's thread waits for the window to say how the session is now
WAITING_BITS = 3  # Bit's words that wait their turn behind something said: the newest few
FAILED_SECONDS = 15.0  # how long a handover that failed is said
HANDOVER_THREAD = "tabmancer-handover"  # a handover's thread: never cut short on closing (settle)


@dataclass
class Asked:
    """A handover's thread asks the window how its session is now: the answer, once given (done)."""

    session: Session
    done: threading.Event = field(default_factory=threading.Event)
    found: tuple[Session, list[Session]] | None = None


class WindowFront(QObject):
    """engine.Front for a main window (its parent): what a live reading turns up, said on Bit's line and
    drawn in the list; h's dialog, its thread, and the fresh session's tab. update: today's claude update
    may run before Claude Code sessions open from here (never over the demo's made-up ones)."""

    handed = Signal(object, str, str)  # a handover written: the old session, the handover, why it is still open
    failed = Signal(str)  # a handover that was not written or not closed: why
    asked = Signal(object)  # a handover's thread asks how its session is now (Asked)
    updated = Signal(object, object)  # claude update done: its claudeupdate.Outcome, the flags it no longer lists
    team_handed = Signal(object, object, str, str, bool)  # a team's handover written: team, lead, handover, line-up, suggested
    noted = Signal(object)  # a thread's actions.Note, said on the window's thread
    started = Signal(object)  # a team's background members started: the tabs to open now (Launches)
    ended = Signal()  # a handover's thread is over, whatever came of it

    def __init__(self, window: MainWindow, update: bool = True) -> None:
        super().__init__(window)
        self.update = update
        self.members: dict[str, tuple[str, str]] = {}  # session id -> (team, member), as the last reading had them
        self.workers: list[threading.Thread] = []
        self.handing: dict[threading.Thread, str] = {}  # the handovers being written: thread -> what is handed over
        self.waiting: list[tuple[str, float, str]] = []  # Bit's words waiting their turn: (words, seconds, colour)
        self.handed.connect(self.handover_ready)
        self.failed.connect(self.handover_failed)
        self.asked.connect(self.answer)
        self.updated.connect(self.claude_updated)
        self.team_handed.connect(self.team_handover_ready)
        self.noted.connect(self.say_note)
        self.started.connect(self.open_sessions)
        self.ended.connect(self.handover_ended)

    def window(self) -> MainWindow:
        return self.parent()  # type: ignore[return-value]

    def live(self, snap: DashSnapshot) -> None:
        """A reading of the store to the window's Engine: every session listed (it says what changed since
        its last look), none to set resting (the store did, as it read them), the background sessions'
        jobs. The members as the reading has them, for who() and cap_of(). Who Claude Code is signed in as
        first (Engine.signed_in, which changes nothing while it is the same): h says what of a session that
        ran on another organisation goes to this one (accountwords.elsewhere_note), as the terminal does."""
        self.window().engine.signed_in(list(snap.accounts))
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

    def live_ids(self) -> set[str]:
        """The sessions a Claude process has open now, asked just before tabs open (opening.live_ids)."""
        return opening.live_ids(self.window().engine.state.background_live)

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
        """Bit's words in its tone. Its line is one: while something else is said there (a note, what a key
        did), they wait their turn (said), so neither covers the other, as the terminal's note and Bit
        stand side by side."""
        window = self.window()
        said = (bit.words, float(bit.seconds or 4), look.TEXT if bit.tone == "plain" else TONES.get(bit.tone, window.accent))
        if window.said_timer.isActive():
            self.waiting = [*self.waiting, said][-WAITING_BITS:]
            return
        window.say(*said)

    def said(self) -> None:
        """What was said had its time (the window's said_timer): Bit's words that waited, the oldest first."""
        if self.waiting:
            self.window().say(*self.waiting.pop(0))

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

    def update_row(self, session: Session) -> None:
        """Queued to hand over, or not after all: read again, and the store's reading (actions.read_queued)
        marks it."""
        self.window().poll()

    def untick(self, ids: set[str]) -> None:
        window = self.window()
        window.selected -= ids
        window.show_ticked()

    def opened_rows(self, ids: set[str]) -> None:
        """Just opened: unticked already (untick); the next reading shows them open (poll_live, later)."""
        self.window().show_ticked()

    def poll_live(self, full: bool = True) -> None:
        """Read again now (the store decides how fully)."""
        self.window().poll()

    # --- h ----------------------------------------------------------------------------------------

    def ask_handover(self, session: Session, then: Callable[[str], None]) -> None:
        """The h dialog, over the window; then with the way picked, never when it was closed."""
        window = self.window()
        HandoverDialog(window, session, then, window.accent).open()

    def hand_over(self, session: Session, how: str | None = None) -> None:
        """The handover written on a thread of its own (actions.write_handover: a minute or more, the old
        session closed on the way); back on the window's thread by a Signal, Engine.handover_ready opens
        the fresh one. Not told how (H on a member, teamkeys.Surface.hand_over): asked first, as h asks
        (Engine.handover: its checks, the dialog, a busy one queued till it finishes), which then comes
        back here with the way picked."""
        if how is None:
            self.window().engine.handover(session)
            return
        self.start(self.write, session, how, handing=session.project)

    def write(self, session: Session, how: str) -> None:
        try:
            handed = actions.write_handover(session, how, partial(self.now, session), teams.member_of)
        except actions.ActionError as error:
            actor.let_go(f"handover-{session.session_id}")  # a queued one: h again may queue it afresh
            self.emit("failed", error.words)
            return
        self.emit("handed", session, handed.written, handed.left_open)

    def now(self, session: Session) -> tuple[Session, list[Session]]:
        """On a handover's thread, just before the old session closes: it and the open ones as the window's
        engine has them now (it may have got busy again meanwhile), asked on the window's thread. A
        window gone, or too busy to answer: it is not closed (handover.HandoverError says why)."""
        asked = Asked(session)
        if not self.emit("asked", asked) or not asked.done.wait(NOW_SECONDS) or asked.found is None:
            raise handover.HandoverError("the window did not say how it is now, so it was not closed")
        return asked.found

    def answer(self, asked: Asked) -> None:
        engine = self.window().engine
        asked.found = (engine.state.sessions.get(asked.session.session_id, asked.session), engine.open_ones())
        asked.done.set()

    def handover_ready(self, session: Session, written: str, left_open: str) -> None:
        self.window().engine.handover_ready(session, written, left_open)

    def handover_failed(self, words: str) -> None:
        self.window().say_note(words, FAILED_SECONDS, "error")

    def handover_ended(self) -> None:
        """A handover's thread over (after what came of it, by its own Signal): a window closing meanwhile
        closes now, if that was the last one."""
        self.window().finish_closing()

    def handing_now(self) -> list[str]:
        """What the handovers being written hand over (a session's project, a team): closing waits for them."""
        return list(self.handing.values())

    # --- h twice on a team ----------------------------------------------------------------------------

    def hand_over_team(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """The whole team handed over on a thread of its own (actions.write_team_handover: its members'
        tabs closed, its lead resumed once to write the handover and the line-up, minutes); back by a
        Signal, Engine.team_handover_ready shows them (ask_team_start)."""
        self.start(self.write_team, team, members, handing=f"team {team.name}")

    def write_team(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        try:
            handed = actions.write_team_handover(team, members, self.now, actions.close_tabs)
        except actions.ActionError as error:
            self.emit("failed", error.words)
            return
        if handed is not None:  # None: no lead session (Engine.team_handover said so before)
            self.emit("team_handed", team, handed.lead, handed.written, handed.lineup, handed.suggested)

    def team_handover_ready(self, team: teams.Team, lead: Session, written: str, lineup: str, suggested: bool) -> None:
        self.window().engine.team_handover_ready(team, lead, written, lineup, suggested)

    def ask_team_start(self, team: teams.Team, written: str, lineup: str, suggested: bool,
                       then: Callable[[tuple[str, str] | None], None]) -> None:
        """The team handover dialog, over the window; then with (the line-up, the cap), or None."""
        window = self.window()
        TeamHandoverDialog(window, team, written, lineup, suggested, then, window.accent).open()

    def adopt_team(self, team: teams.Team) -> None:
        """The team as saved now: read again (the store reads the teams with the sessions)."""
        self.window().poll()

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """A team's sessions, as the terminal's open them (teamkeys.TeamKeys.open_team, one copy): its
        members in the background first where it runs them there, then the tabs; one opened a moment
        ago not again. What the team keys ask of a dashboard besides, the window answers below."""
        self.team_keys().open_team(team, launches)

    def team_keys(self) -> teamkeys.TeamKeys:
        """The team keys (teamkeys.TeamKeys, the terminal's one copy) on this window; what was pressed once
        kept with the engine's state (EngineState.team_presses), so a second s finds the first."""
        return teamkeys.TeamKeys(cast("teamkeys.Dashboard", self), time.monotonic,
                                 self.window().engine.state.team_presses)

    def start_team(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """s on a team: every member not open, a closed one resumed as itself, one never started new, the lead
        last; one opened a moment ago left to come up; its cache gone, said first (s again starts it). A team
        handover written and kept is dropped (TeamKeys.start)."""
        self.team_keys().start(team, members)

    def cold_start_ok(self, team: teams.Team, members: dict[str, Session | None]) -> bool:
        return self.team_keys().cold_start_ok(team, members)

    def recent(self) -> set[str] | None:
        """The sessions opened from here a moment ago, still starting (the Opener's openings: Enter's and the
        engine's one copy)."""
        return self.window().opener.openings.recent()

    def kept_handovers(self) -> dict:
        """The teams' handovers written and not taken yet (EngineState.kept_handovers)."""
        return self.window().engine.state.kept_handovers

    def adopt(self, team: teams.Team) -> None:
        """The team as saved now: the sessions and the teams read again (the store reads both)."""
        self.window().poll()

    def go_to_tab(self, session: Session) -> None:
        """Its tab to the front, on a thread of its own (Opener.go_to: what actions.go_to_tab says)."""
        self.window().opener.go_to(session)

    def put_on_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def close_tabs(self, names: list[str]) -> None:
        """These members' tabs closed, as on /exit, on a thread of its own (Opener.close_team)."""
        self.window().opener.close_team(names)

    def say(self, note: actions.Note) -> None:
        self.say_note(note)

    def open_sessions(self, launches: list[Launch]) -> None:
        self.window().engine.open(launches)

    def after_update(self, what: str, then: Callable[[], None]) -> bool:
        return self.window().engine.after_update(what, then)

    def start_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        """claude --bg for each on a thread of its own (a few seconds each), then the tabs: the lead
        finds its members up."""
        self.start(self.run_in_background, team, launches, then)

    def run_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        for note in actions.start_in_background(team, launches, launch.run_in_background):
            self.emit("noted", note)
        if then:
            self.emit("started", then)

    # --- opening (the fresh session's tab) ---------------------------------------------------------

    def open_unasked(self, launches: list[Launch]) -> None:
        """A queued handover's fresh session, which no key asked for just now: opened as any other (the
        window has no terminal window of its own to keep it in)."""
        self.window().engine.open(launches)

    def open_tabs(self, launches: list[Launch], window: str | None) -> list[str]:
        """New tabs (opening.open_tabs: the demo stands in for it), in that terminal window when given."""
        return opening.open_tabs(launches) if window is None else launch.open_tabs(launches, window)

    def exit_with(self, leftover: list[str]) -> None:
        """No terminal here opens tabs: the commands on the clipboard, to run by hand (the window stays)."""
        QApplication.clipboard().setText("\n".join(leftover))
        self.window().say_note(opening.NO_TERMINAL, 15, "warning")

    def later(self, seconds: float, then: Callable[[], None]) -> None:
        """then, once, on the window's thread: gone with the front, so never after the window."""
        QTimer.singleShot(int(seconds * 1000), self, then)

    def update_claude(self) -> None:
        """Today's claude update, before Claude Code sessions open (Engine.update_due), on a thread of its
        own; its outcome back to Engine.claude_updated. Over the demo's made-up sessions none runs, nor while
        a dashboard acts: it runs it. Who acts as the last reading had it (DashSnapshot.acting, read on the
        store's thread): the process check (actor.running; two `ps` on macOS) stays off the UI thread."""
        snap = self.window().snap
        if not self.update or (snap is not None and snap.acting is not None):
            self.window().engine.claude_updated(claudeupdate.Outcome(True, "", ""), None)
            return
        self.start(self.run_update)

    def run_update(self) -> None:
        self.emit("updated", *alerts.run_claude_update())

    def claude_updated(self, outcome: claudeupdate.Outcome, missing: list[str] | None) -> None:
        """Done: what waited for it opens (a handover's fresh session among them); a window closing meanwhile
        closes now."""
        window = self.window()
        window.engine.claude_updated(outcome, missing)
        window.finish_closing()

    # --- threads ------------------------------------------------------------------------------------

    def start(self, work: Callable[..., None], *args: object, handing: str = "") -> None:
        """work(*args) on a thread of its own. handing: a handover's, what it hands over (handing_now): the
        window closing waits for it, and so does settle, however long it takes."""
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=self.work_on, args=(work, args, handing), daemon=True,
                                  name=HANDOVER_THREAD if handing else "tabmancer-front")
        if handing:
            self.handing[thread] = handing
        self.workers.append(thread)
        thread.start()

    def work_on(self, work: Callable[..., None], args: tuple[object, ...], handing: str) -> None:
        try:
            work(*args)
        finally:
            if handing:
                self.handing.pop(threading.current_thread(), None)
                self.emit("ended")

    def settle(self, seconds: float = 10) -> None:
        """Wait for what is under way (on closing, and in tests): a handover till it is written, however long
        (cut short, a session closed to write its own would lose it: the process ends with the window's
        thread, and these threads with it); the rest up to seconds each."""
        for thread in list(self.workers):
            thread.join(None if thread.name == HANDOVER_THREAD else seconds)

    def emit(self, signal: str, *args: object) -> bool:
        """One of the front's Signals, by name, from a thread: False when the window has gone meanwhile."""
        try:
            getattr(self, signal).emit(*args)
        except RuntimeError:
            return False
        return True
