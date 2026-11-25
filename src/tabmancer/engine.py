"""What the dashboard does by itself, with no UI behind it: the engine the terminal dashboard runs on
and the desktop window will run on once it acts, so both decide the same way from one copy.

Each poll's results go in (a live poll's fresh sessions, a note typed or not, the sessions opened a
while ago), the engine keeps what it has said once and what waits (EngineState), and what is to be
said or done goes out through the front (Front): a note, Bit, a desktop note, a handover, a message
typed into a tab. The front draws it in its own way, and keeps its own look (the list's rows, flashes,
ticks).

Threads and timers are the front's. Every Engine method is called on the front's UI thread only:
the work off it (reading `claude agents`, the transcripts, typing into a tab) only reads and passes
its result in, so the engine has no lock and no thread of its own. Time comes from the clock it is
given, never read here, so a scripted run can say when it is."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Protocol

from . import actions, actor, alerts, store
from .sessions import Exit, Session


class Front(Protocol):
    """What the engine asks of the front, and what it has the front say and do. The terminal
    dashboard (app.SessionPicker) is one; the desktop window will be another."""

    default_cap: int  # the context cap new sessions get, in tokens

    def who(self, session: Session) -> str:
        """A session as a note names it: its project, or 'team: member'."""
        ...

    def cap_of(self, session: Session) -> int:
        """The context cap it runs under, in tokens."""
        ...

    def monitoring(self) -> bool:
        """A team's monitor, not a dashboard."""
        ...

    def tell(self, event: alerts.Event) -> None:
        """An event as the front says it: its note, the bell, Bit."""
        ...

    def say_note(self, note: actions.Note) -> None:
        ...

    def show_bit(self, bit: alerts.Bit) -> None:
        ...

    def tell_desktop(self, turned: list[Session]) -> None:
        """A desktop note for the sessions that turned to wait for you (only from the one that acts)."""
        ...

    def hand_over(self, session: Session, how: str) -> None:
        """Hand it over (how: "self" or "transcript"), its fresh session opening unasked."""
        ...

    def type_note(self, session: Session, who: str, message: str) -> None:
        """Type the message into its tab, off the UI thread; the outcome back to Engine.typed."""
        ...

    def put_on_clipboard(self, text: str) -> None:
        ...

    def go_to_tab(self, session: Session) -> None:
        ...

    def flash_changed(self, updates: dict[str, Session]) -> None:
        """Before the list takes the poll's sessions: the rows whose state changed flash."""
        ...

    def untick_open(self, updates: dict[str, Session]) -> None:
        """After: the sessions now open are no longer ticked."""
        ...

    def rows_changed(self, ids: set[str]) -> None:
        """The sessions changed: their rows, the counts, the limits as they stand now."""
        ...


@dataclass
class EngineState:
    """What the engine keeps between polls. The terminal dashboard reads and sets these by the same
    names (app forwards each one, FORWARDED)."""

    acts: bool = False  # this one holds the actor lock (actor.py): what happens by itself is its to do
    sessions: dict[str, Session] = field(default_factory=dict)  # the list as last polled
    queued: dict[str, str] = field(default_factory=dict)  # h on a busy one: id -> how, once it finishes
    unasked: set[str] = field(default_factory=set)  # queued handovers being written: their fresh one opens unasked
    notes: dict[str, tuple[str, str]] = field(default_factory=dict)  # messages for members: id -> (name, message)
    told_crowded: set[str] = field(default_factory=set)  # sessions already said to be crowded
    told_idle: bool = False  # Bit pointed out idle background sessions
    opening: dict[str, float] = field(default_factory=dict)  # session id -> when it was opened from here
    came_up: set[str] = field(default_factory=set)  # opened from here and seen open since
    full_poll_due: bool = False  # a full live poll is on its way: quick ones wait
    full_polls: int = 0  # full live polls come back so far


FORWARDED = tuple(f.name for f in fields(EngineState))


class Engine:
    def __init__(self, front: Front, clock: Callable[[], datetime], state: EngineState | None = None) -> None:
        self.front, self.clock = front, clock
        self.state = state if state is not None else EngineState()

    def tell(self, event: alerts.Event) -> None:
        """An event said: a crowded one is said once per session."""
        if event.kind == alerts.CROWDED:
            self.state.told_crowded |= set(event.once_keys)
        self.front.tell(event)

    def crowded(self, sessions: Iterable[Session]) -> None:
        """Once per session, an open session whose context passed the handover point. Nothing is
        handed over unless you ask."""
        event = alerts.crowded_event(sessions, self.front.cap_of, self.front.default_cap, self.state.told_crowded,
                                     now=self.clock())
        if event is not None:
            self.tell(event)

    def idle(self, count: int) -> None:
        """A full poll found count background sessions idle: Bit says so once."""
        if not self.state.told_idle and (event := alerts.idle_event(count)) is not None:
            self.state.told_idle = True
            self.front.show_bit(event.bit)

    def live(self, updates: dict[str, Session], full: bool, resting: set[str]) -> None:
        """A live poll came back (updates: the sessions it brought up to date; resting: the team
        members that wait for their lead, not for you): what turned is said, a queued handover that
        finished is handed over, the list takes them, waiting messages go in."""
        state, front = self.state, self.front
        if full:
            state.full_poll_due = False
            state.full_polls += 1
        self.crowded(updates.values())
        store.rest(updates.values(), resting)  # a member done for now waits for its lead, not for you
        turned: list[Session] = []  # were working and now wait for you, or stopped at a limit
        handing = {sid for sid, fresh in updates.items() if sid in state.queued and actions.finished(fresh)}
        said: dict[str, list[alerts.Event]] = {}
        for event in alerts.events(state.sessions, updates, state.told_crowded, front.cap_of, front.default_cap,
                                   front.who, skip=handing):
            said.setdefault(event.session, []).append(event)
        for sid, fresh in updates.items():
            if sid in handing:
                how = state.queued.pop(sid)  # it finished: the handover asked for with h
                actions.save_queued(state.queued)
                done = actions.queued_finished(sid, fresh, front.monitoring(), actor.first_to)
                front.say_note(done.note)
                if not done.hand_over:
                    continue
                state.unasked.add(sid)  # its fresh session opens by itself: in the dashboard's window
                front.hand_over(fresh, how)
                continue
            for event in said.get(sid, []):  # fell back; stopped at a limit; needs you or done
                if event.toast:
                    turned.append(fresh)
                self.tell(event)
        if turned and state.acts:  # the one that acts sends them: a second would be a second note
            front.tell_desktop(turned)
        changed = {sid for sid, fresh in updates.items() if state.sessions.get(sid) != fresh}
        front.flash_changed(updates)
        state.sessions.update(updates)
        state.came_up |= {sid for sid, s in updates.items() if s.exit is Exit.RUNNING and sid in state.opening}
        front.untick_open(updates)
        if state.notes:
            self.deliver()  # a member that waits for you now gets its message
        if changed:
            front.rows_changed(changed)

    def deliver(self) -> None:
        """The messages waiting for team members: typed in once one waits for you, never while it works
        or asks you something (Enter would answer a question on its screen); a closed one's on the
        clipboard."""
        for due in alerts.deliveries(self.state.notes, self.state.sessions):
            del self.state.notes[due.sid]
            if due.closed is not None:
                self.front.put_on_clipboard(due.closed.clipboard)
                self.front.say_note(due.closed)
            else:
                self.front.type_note(due.session, due.who, due.message)

    def typed(self, told: actions.Told) -> None:
        """A message typed into a tab, or not: nothing could type it (not Windows, or its console would
        not take the keys), so it is on the clipboard and you in its tab."""
        if told.go_to is not None:
            self.front.put_on_clipboard(told.clipboard)
            self.front.go_to_tab(told.go_to)
        self.front.say_note(told)

    def came_up_check(self, ids: set[str]) -> None:
        """A while after they were opened: which of these never showed up open (Claude Code would not
        resume one, its folder gone, say; its tab says why)."""
        event = alerts.not_up_event(ids, self.state.came_up, self.state.sessions)
        if event is not None:
            self.tell(event)
