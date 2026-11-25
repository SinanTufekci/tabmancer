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
given, never read here, so a scripted run can say when it is.

The engine calls the front as it goes, not by handing back a list of effects to carry out after: the
order of what is said and done is part of what it does (a finished handover's note before the list
takes the poll, a row's flash before, its tick gone after), and a list carried out later would change
it. The desktop window is a Front as the terminal dashboard is; test_engine pins the order."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Protocol

from . import accounts, actions, actor, alerts, store
from .actions import Note
from .sessions import Exit, Session

# --- when the front runs what: its timers (SCHEDULE) -------------------------------------------------

LIVE_REFRESH_SECONDS = 2  # quick poll of the pid files
USAGE_SECONDS = 300  # plan limits change slowly; `claude -p /usage` is cheap but not free
TOKENS_SECONDS = 60  # token counts: only what transcripts gained since the last count is read
ACCOUNTS_SECONDS = 5  # who the CLIs are signed in as: /login in any session switches it
ACTOR_SECONDS = 10  # a dashboard that only watches looks this often whether the one that acts is gone
GIT_SECONDS = 60  # where each project's folder stands in git (git status: local, no network)
UPDATES_SECONDS = 6 * 3600  # a dashboard left open for days hears of a new tabmancer (PyPI: once a day)
DUE_SECONDS = 10  # the sessions to continue at their reset: whether their limit has lifted
WRAPPED_AFTER, WRAPPED_SECONDS = 8, 600  # Friday's word on the week: after Bit's hello, then looked for again


@dataclass(frozen=True)
class Every:
    """One of the front's timers: what it runs, how often, and whether only while it acts (those stop
    when the lock is lost and start when it is taken over). first: a first run this soon, then every
    seconds."""

    name: str  # the terminal dashboard's method it runs
    seconds: float
    acting: bool = False
    first: float | None = None


SCHEDULE = (
    Every("tick_actor", ACTOR_SECONDS),  # look(), off the UI thread, then Engine.seen
    Every("refresh_usage", USAGE_SECONDS),
    Every("refresh_tokens", TOKENS_SECONDS),
    Every("show_accounts", ACCOUNTS_SECONDS),
    Every("refresh_git", GIT_SECONDS),
    Every("poll_tick", LIVE_REFRESH_SECONDS),  # the live poll, its results to Engine.live
    Every("check_updates", UPDATES_SECONDS, acting=True),
    Every("continue_due", DUE_SECONDS, acting=True),  # Engine.due
    Every("tell_wrapped", WRAPPED_SECONDS, acting=True, first=WRAPPED_AFTER),
)

ALONE = Note("No tabmancer dashboard is open: until one is, nothing continues sessions when their limit lifts "
             "and no desktop notes come.", "warning", 15)
TOOK_OVER = Note("The tabmancer dashboard that did what happens by itself closed: this one does now.", seconds=8)


class Front(Protocol):
    """What the engine asks of the front, and what it has the front say and do. The terminal
    dashboard (app.SessionPicker) is one; the desktop window will be another."""

    default_cap: int  # the context cap new sessions get, in tokens
    account: accounts.Account | None  # the Claude account signed in now, as last read
    signed_as: str | None  # its key (account and organisation)

    def who(self, session: Session) -> str:
        """A session as a note names it: its project, or 'team: member'."""
        ...

    def cap_of(self, session: Session) -> int:
        """The context cap it runs under, in tokens."""
        ...

    def monitoring(self) -> bool:
        """A team's monitor, not a dashboard."""
        ...

    def other_accounts_note(self, opens: datetime | None) -> tuple[str, str]:
        """What the other accounts offer while a limit stands (accountwords.other_accounts_note)."""
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
        """The sessions changed: their rows, the counts, the limits as they stand now (Engine.limits)."""
        ...

    def limits_at(self, at: set[str]) -> None:
        """The sessions at a usage limit now (c is in the footer only while there is one)."""
        ...

    def armed_changed(self) -> None:
        """The sessions to continue at their reset changed (a dashboard that watches): their rows."""
        ...

    def continue_these(self, stopped: list[Session], unasked: bool = False) -> None:
        """Each goes on where it stopped: a closed one in a new tab, an open one with the prompt typed
        into its own tab. unasked: by itself, the limit lifted."""
        ...

    def open_handover(self, session: Session | None, how: str | None = None) -> None:
        """Hand it over as asked (how: "self", "transcript", or None to ask how)."""
        ...

    def start_acting(self) -> None:
        """It holds the actor lock now: its acting timers (SCHEDULE) start, what is due at its start runs."""
        ...

    def start_wrapped(self) -> None:
        """Taken over: Wrapped's timers too (the first load starts them otherwise)."""
        ...

    def stop_acting(self) -> None:
        """The lock is lost: its acting timers stop, and it no longer acts (acts False)."""
        ...

    def say_who_acts(self, held: dict | None = None) -> None:
        """Which dashboard acts (held: its lock), and that this one watches."""
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
    armed: dict[str, datetime] = field(default_factory=dict)  # c: session id -> when it continues (its limit lifts)
    armed_unsaved: bool = False  # the sessions to continue could not be saved, and that was said
    told_limit: set[str] = field(default_factory=set)  # sessions Bit has said are stopped at a limit
    actor_misses: int = 0  # looks in a row that found no lock it could read (seen)
    told_alone: bool = False  # a monitor said no dashboard is open


FORWARDED = tuple(f.name for f in fields(EngineState))


# --- one acts: the actor lock (actor.py) ---------------------------------------------------------------


@dataclass(frozen=True)
class Look:
    """What a look at the actor lock found (look), for Engine.seen. kind: "monitor" (alone: no
    dashboard acts), "acting" (held: the lock as read; kept: it is still this one's), "took" (it was
    free, and this one holds it now) or "watching" (armed: the sessions to continue, as saved)."""

    kind: str
    alone: bool = False
    held: dict | None = None
    kept: bool = False
    armed: dict[str, datetime] = field(default_factory=dict)


def look(monitor: bool, acts: bool) -> Look:
    """The actor lock, looked at off the UI thread (reading another process can take a moment: `ps` on
    macOS). It keeps no state: the engine's is not touched here. Only actor.claim writes, the lock's own
    atomic step (claimed where the lock is free, as before), so that the process check stays off the UI
    thread too."""
    if monitor:
        return Look("monitor", alone=not actor.running(actor.holder()))
    if acts:
        held = actor.holder()
        return Look("acting", held=held, kept=actor.is_me(held) or (held is None and actor.claim()))
    if actor.claim():
        return Look("took")
    return Look("watching", armed=store.read_armed())


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
        for event in alerts.events(state.sessions, updates, front.who, skip=handing):
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

    # --- usage limits: the sessions stopped at one, and those to continue when it lifts (c) ----------

    def limits(self) -> None:
        """Forget the sessions gone on from a limit; once per session, Bit says which ones a usage limit
        stopped and that c continues them. On which account each stopped is kept (by the one that acts):
        signed in with another since, it can go on (signed_in_again)."""
        state, front = self.state, self.front
        kept, at = actions.limited_now(state.sessions, accounts.limited_on(), front.signed_as, front.account,
                                       accounts.same)
        if state.sessions and state.acts:
            accounts.keep_limited(kept)
        state.told_limit &= at
        if state.sessions and state.acts and not state.armed.keys() <= at:
            state.armed = actions.still_armed(state.armed, at)
            self.save_armed()
        front.limits_at(at)
        stopped = actions.newly_stopped(state.sessions, at, state.told_limit, state.armed)
        if not stopped:
            return
        event = alerts.stopped_event(stopped, lambda soon: front.other_accounts_note(soon)[1], now=self.clock())
        state.told_limit.update(event.once_keys)
        front.show_bit(event.bit)

    def load_armed(self) -> None:
        """The sessions to continue at their reset, as saved: the one that acts saves them as it continues
        them, and one that watches shows them."""
        self.state.armed = store.read_armed()

    def save_armed(self) -> None:
        """The sessions to continue at their reset, saved: only by the one that acts. A monitor and a
        dashboard that watches continue nothing, and their copy is the saved one, read again."""
        state = self.state
        if not state.acts:
            return
        saved = actions.save_armed(state.armed)
        if not saved and not state.armed_unsaved:  # said once, not at each due()
            self.front.say_note(actions.ARMED_UNSAVED)
        state.armed_unsaved = not saved

    def show_armed(self, armed: dict[str, datetime]) -> None:
        """One that watches: the sessions to continue at their reset, as the one that acts saved them."""
        if armed == self.state.armed or self.state.acts:
            return
        self.state.armed = armed
        self.front.armed_changed()

    def due(self) -> None:
        """Continue the sessions whose limit has lifted (the one that acts, on its timer): a closed one in
        a new tab, an open one with the prompt typed into its own tab, which is never closed. One gone on
        by hand meanwhile is left alone."""
        due = actions.due(self.state.armed, self.state.sessions, self.clock())
        if not due.gone:
            return
        for sid in due.gone:
            del self.state.armed[sid]
        self.save_armed()
        if due.lifted:
            self.front.continue_these(due.lifted, True)

    def arm(self, session: Session) -> str | None:
        """One session to continue at its reset (the act API's continue_at_reset): why not, in a few words,
        or None once it is."""
        refused = actions.arm_refusal(session, self.state.acts)
        if refused is not None:
            return refused
        self.state.armed.update(actions.arm([session], self.clock()))
        self.save_armed()
        return None

    def choose(self, answer: str, ids: list[str], stopped: list[Session], when: str) -> bool:
        """The c dialog's answer for the ticked (ids) of the stopped: now, handover, lift or off (the last
        two only where this one acts). Whether their rows changed."""
        state, front = self.state, self.front
        refused = actions.answer_refusal(answer, state.acts)
        if refused is not None:
            front.say_note(refused)
            return False
        choice = actions.continue_choice(answer, ids, stopped, state.armed, when, self.clock())
        if choice is None:
            return False
        state.armed = choice.armed
        if choice.note is not None:
            front.say_note(choice.note)
        self.save_armed()
        if choice.go_on:
            front.continue_these(choice.go_on, False)
        for session in choice.hand_over:
            front.open_handover(session, "transcript")
        return choice.rows

    # --- one acts: the actor lock --------------------------------------------------------------------

    def mount(self) -> None:
        """At its start: a dashboard takes the lock when it is free; a monitor never acts, nor asks."""
        if not self.front.monitoring():
            self.state.acts = actor.claim()

    def seen(self, look: Look) -> None:
        """What a look at the lock found (every ACTOR_SECONDS). The one that acts: it steps back when a
        readable lock names another process, or after two looks in a row that found none it could read
        (a file held open a moment is no reason). One that watches: it takes over when the one that
        acted is gone, else shows the sessions to continue as that one saved them. A monitor: says once
        each time that no dashboard is open."""
        state = self.state
        if look.kind == "monitor":
            if look.alone and not state.told_alone:
                self.front.say_note(ALONE)
            state.told_alone = look.alone
        elif look.kind == "acting":
            if look.kept:
                state.actor_misses = 0
                return
            state.actor_misses += 1
            if look.held is not None or state.actor_misses >= 2:
                self.lost(look.held or {})
        elif look.kind == "took":
            self.took_over()
        else:
            self.show_armed(look.armed)

    def took_over(self) -> None:
        """The one that acted is gone and this one holds the lock now: it acts."""
        if self.state.acts:
            return
        self.state.acts = True
        self.state.actor_misses = 0
        self.front.start_acting()
        self.front.start_wrapped()
        self.front.say_note(TOOK_OVER)

    def lost(self, held: dict) -> None:
        """Another took the lock over (it judged this one gone): only one may act."""
        if self.state.acts:
            self.front.stop_acting()
            self.front.say_who_acts(held)

    def unmount(self) -> None:
        """At its end: the lock goes, for another to take."""
        if self.state.acts:
            actor.release()
