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

from . import (
    accounts,
    accountwords,
    actions,
    actor,
    alerts,
    claudeupdate,
    handover,
    handoverwords,
    openings,
    store,
    teams,
    toast,
    usage,
    wrapped,
)
from .actions import Note
from .launch import Launch
from .openings import Openings
from .sessions import Exit, Session
from .viewmodel import at_limit

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
        """Write its handover off the UI thread (actions.write_handover); written, Engine.handover_ready."""
        ...

    def hand_over_team(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """A whole team's handover written by its lead off the UI thread (actions.write_team_handover);
        written, Engine.team_handover_ready."""
        ...

    def ask_handover(self, session: Session, then: Callable[[str], None]) -> None:
        """The h dialog: how its handover is written ("self" or "transcript"); then, with the answer
        given (not called when it was closed)."""
        ...

    def ask_team_start(self, team: teams.Team, written: str, lineup: str, suggested: bool,
                       then: Callable[[tuple[str, str] | None], None]) -> None:
        """The lead's handover and line-up shown; then with (line-up, cap) to start the next generation,
        or None (closed)."""
        ...

    def adopt_team(self, team: teams.Team) -> None:
        """The team as saved now, in the views of it that are open."""
        ...

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """A team's sessions opened: in tabs, its members first."""
        ...

    def update_row(self, session: Session) -> None:
        """This session's row drawn again."""
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

    def launch_for(self, session: Session) -> Launch:
        """How a closed session opens (actions.launch_for): as its m says, a member as that member, one in
        the background attached to."""
        ...

    def open_unasked(self, launches: list[Launch]) -> None:
        """Tabs no key asked for just now (a limit lifted): the dashboard's window, the keys left where
        they were; then Engine.open."""
        ...

    def continue_open(self, still_open: list[Session], opened: int) -> None:
        """The continue prompt typed into each one's tab off the UI thread (actions.type_continue), the
        outcome back to Engine.continued."""
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

    def refresh_usage(self) -> None:
        """Read the plan limits again, off the UI thread (read_usage), the reading to show_usage."""
        ...

    def show_rows(self, sessions: list[Session]) -> None:
        """These sessions' rows drawn again, and the detail."""
        ...

    def update_claude(self) -> None:
        """Run claude update off the UI thread (alerts.run_claude_update), its outcome to
        Engine.claude_updated."""
        ...

    def show_newer(self, version: str) -> None:
        """A newer tabmancer, kept in sight while the front is open (the top bar's name)."""
        ...

    def open_in_dashboard_window(self, launches: list[Launch]) -> None:
        """A monitor's tabs go to the dashboard's window (actions.window_for), looked for off the UI
        thread; then Engine.open with that window, the update seen to (updated)."""
        ...

    def live_ids(self) -> set[str]:
        """The sessions a Claude process has open right now, asked afresh (actions.open_elsewhere)."""
        ...

    def open_tabs(self, launches: list[Launch], window: str | None) -> list[str]:
        """New tabs (launch.open_tabs), in that window when given; the commands left to run by hand."""
        ...

    def untick(self, ids: set[str]) -> None:
        """These sessions are no longer ticked."""
        ...

    def poll_live(self, full: bool = True) -> None:
        """A live poll now (a full one asks `claude agents --json`)."""
        ...

    def exit_with(self, leftover: list[str]) -> None:
        """No terminal here opens tabs: close, the commands left to run by hand."""
        ...

    def later(self, seconds: float, then: Callable[[], None]) -> None:
        """then, once, on the UI thread, in that many seconds."""
        ...

    def opened_rows(self, ids: set[str]) -> None:
        """The sessions just opened: their rows (opening now), and the counts."""
        ...

    def send_toast(self, title: str, body: str) -> None:
        """A desktop note (toast.show): only from the one that acts."""
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
    background_live: dict[str, dict] = field(default_factory=dict)  # background sessions' jobs, the last full poll's
    openings: Openings = field(default_factory=Openings)  # opened from here: not again while starting, came up?
    full_poll_due: bool = False  # a full live poll is on its way: quick ones wait
    full_polls: int = 0  # full live polls come back so far
    armed: dict[str, datetime] = field(default_factory=dict)  # c: session id -> when it continues (its limit lifts)
    armed_unsaved: bool = False  # the sessions to continue could not be saved, and that was said
    told_limit: set[str] = field(default_factory=set)  # sessions Bit has said are stopped at a limit
    actor_misses: int = 0  # looks in a row that found no lock it could read (seen)
    told_alone: bool = False  # a monitor said no dashboard is open
    told_update: str | None = None  # the newer tabmancer already told of
    updating_claude: bool = False  # claude update running: sessions asked for wait for it
    after_claude_update: list[Callable[[], None]] = field(default_factory=list)  # what waits for it
    # The Claude account Claude Code is signed in with, as last read; signed_as is its key
    # (account and organisation: one email can be in more than one organisation).
    account: accounts.Account | None = None
    signed_as: str | None = None
    told_agy_out: datetime | None = None  # Bit said every agy model is out, until this time
    handing_team: str | None = None  # h once on a team (its row or view): again hands it over
    # Teams whose lead wrote a handover not taken yet (Esc in its dialog): h shows it again, unwritten.
    kept_handovers: dict[str, tuple[Session, str, str, bool]] = field(default_factory=dict)
    team_seen: dict[str, set[tuple[str, str, str]]] = field(default_factory=dict)  # what each team said
    # x, a cold s, d pressed once on a team, on its row or in its view (teamkeys.TeamKeys.presses): what on, until
    team_presses: dict[str, tuple[str, float]] = field(default_factory=dict)


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


def read_usage(acts: bool, before: accounts.Account | None) -> list[usage.Limit] | None:
    """The plan limits, read off the UI thread (claude -p /usage: seconds), for Engine.usage. It keeps no
    state. The one that acts reads and records them (the stats chart and the forecast); one that watches
    takes its last reading while fresh (one claude -p, not two) and records nothing, or they would count
    twice. before: the account signed in as the reading started; signed in with another meanwhile,
    whose limits these are is not known: None, dropped (the switch reads them again, signed_in)."""
    if not acts:
        return store.watched_limits(before)
    limits = usage.read_all()  # Codex's too, when it is turned on
    after = accounts.claude()
    if (before.key if before else None) != (after.key if after else None):
        return None
    usage.record(limits, account=after.key if after else "")
    return limits


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

    def live(self, updates: dict[str, Session], full: bool, resting: set[str],
             background: dict[str, dict] | None = None) -> None:
        """A live poll came back (updates: the sessions it brought up to date; resting: the team
        members that wait for their lead, not for you; background: the background sessions' jobs it
        kept, store.live_entries): what turned is said, a queued handover that finished is handed
        over, the list takes them, waiting messages go in."""
        state, front = self.state, self.front
        if background is not None:
            state.background_live = background
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
        state.openings.seen(updates)
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

    def open(self, launches: list[Launch], window: str | None = None, updated: bool = False) -> None:
        """Open sessions in new tabs beside the dashboard, which stays open. One that a Claude process
        has open right now, in another tab or in the background, is not opened again
        (actions.open_launches). While claude update runs, they open once it is done; one due now is
        started first (update_due), never run on the UI thread. window: the terminal window they go to
        (launch.open_tabs); a monitor finds the dashboard's first (Front.open_in_dashboard_window).
        updated: the update was seen to first. What opened is kept (openings.Openings.took), and a
        while later the ones that never came up are named (came_up_check)."""
        state, front = self.state, self.front
        if front.monitoring() and window is None:
            front.open_in_dashboard_window(launches)
            return
        if not updated:
            self.update_due(launch.cli for launch in launches)
            if self.after_update("the session opens" if len(launches) == 1 else f"the {len(launches)} sessions open",
                                 lambda: self.open(launches, window, True)):
                return
        opened = actions.open_launches(launches, front.live_ids, lambda rest: front.open_tabs(rest, window))
        if opened.skipped:
            front.say_note(Note(opened.skipped_words, "warning", 10))
            front.untick(opened.skipped_ids)
            front.poll_live()  # show them as open
        if opened.error:
            front.say_note(Note(opened.error, "error", 15))
            return
        if opened.leftover:
            front.exit_with(opened.leftover)  # no terminal that can open tabs: the commands, to run by hand
            return
        if not opened.opened:
            return
        resumed = state.openings.took(opened, look_later=False)  # looked for on a timer of the front's
        front.later(openings.CAME_UP_SECONDS, lambda: self.came_up_check(resumed))
        front.untick(opened.opened_ids)
        front.opened_rows(opened.opened_ids)
        front.say_note(Note(opened.words, seconds=4))
        front.later(5, lambda: front.poll_live(full=True))  # show them as open once started

    def came_up_check(self, ids: set[str]) -> None:
        """A while after they were opened: which of these never showed up open (Claude Code would not
        resume one, its folder gone, say; its tab says why)."""
        event = alerts.not_up_event(ids, self.state.openings.came_up, self.state.sessions)
        if event is not None:
            self.tell(event)

    # --- handing over (h): one session, or a whole team ----------------------------------------------

    def open_ones(self) -> list[Session]:
        return [s for s in self.state.sessions.values() if s.exit is Exit.RUNNING]

    def handover(self, session: Session | None, how: str | None = None) -> None:
        """h on a session: how its handover should be written (Front.ask_handover). Given how ("self" or
        "transcript", as the act API may), it starts straight away, after the same checks. h on one
        queued to hand over once it finishes: not after all."""
        state, front = self.state, self.front
        if session is None:
            return
        refusal = actions.handover_refusal(session, self.open_ones(), session.session_id in state.queued)
        if refusal:
            front.say_note(Note(refusal.words, "warning", refusal.seconds or None))
            return
        if session.session_id in state.queued:  # h again: not after all
            del state.queued[session.session_id]
            actions.save_queued(state.queued)
            front.say_note(actions.unqueued_note(session))
            front.update_row(session)
            return
        if how is not None:
            self.start_handover(session, how)
            return
        front.ask_handover(session, lambda how: self.start_handover(session, how))

    def start_handover(self, session: Session, how: str) -> None:
        """Hand it over now (how: "self" or "transcript"), or once it finishes if it is working. The h
        dialog's answer."""
        state, front = self.state, self.front
        current = state.sessions.get(session.session_id, session)
        if actions.busy(current):
            state.queued[session.session_id] = how
            actions.save_queued(state.queued)
            front.say_note(actions.queued_note(session, how))
            front.update_row(current)
            return
        # As the list has it now, not as it was when h was pressed: it may have opened or closed
        # while the dialog stood, and an open one written to by a second process was not closed.
        elsewhere = accountwords.elsewhere_note(current, "its whole conversation" if how == "self" else "a digest of it",
                                                state.account)
        if elsewhere is not None:
            front.say_note(elsewhere)
        front.say_note(handoverwords.started_note(current, how))
        front.hand_over(current, how)

    def handover_ready(self, session: Session, written: str, left_open: str = "") -> None:
        """Its handover written: the fresh session opens (unasked for a queued one: it finished while you
        were elsewhere), and it is said."""
        fresh = [handover.fresh(session, written)]
        if session.session_id in self.state.unasked:
            self.state.unasked.discard(session.session_id)
            self.front.open_unasked(fresh)
        else:
            self.open(fresh)
        self.front.say_note(handoverwords.handed_note(session, left_open))
        self.tell(alerts.handed_event(session.project))

    def team_handover(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """h on a team's row or in its view. Twice: the whole team handed over (Front.hand_over_team). A
        handover its lead wrote already, the team still closed, is shown again, not written again."""
        state, front = self.state, self.front
        if actions.open_names(team, members):  # opened again since it was written: that handover is old now
            state.kept_handovers.pop(team.name, None)
        kept = state.kept_handovers.get(team.name)
        if kept is not None:
            self.team_handover_ready(team, *kept)
            return
        reason = actions.team_handover_problem(team, members)
        if reason:
            front.say_note(Note(reason, "warning", 8))
            return
        if state.handing_team != team.name:
            state.handing_team = team.name
            front.say_note(Note(actions.handing_note(team, members), seconds=10))
            front.later(10, lambda: setattr(state, "handing_team", None))
            return
        state.handing_team = None
        front.say_note(handoverwords.team_handing_note(team))
        front.hand_over_team(team, members)

    def team_handover_ready(self, team: teams.Team, lead: Session, written: str, lineup: str,
                            suggested: bool) -> None:
        """The lead's handover and line-up, kept and shown (Front.ask_team_start); its answer to
        team_start."""
        self.state.kept_handovers[team.name] = (lead, written, lineup, suggested)
        self.front.ask_team_start(team, written, lineup, suggested,
                                  lambda answer: self.team_start(team, lead, written, answer))

    def team_start(self, team: teams.Team, lead: Session, written: str, answer: tuple[str, str] | None) -> None:
        """The team handover dialog's answer: (line-up, cap) starts the team's next generation, a fresh
        lead with the handover; None leaves it closed (h shows the handover again). A team that has no
        lead by now (made a team of peers since the handover was written) is refused: nothing is saved
        or opened, and the kept handover is dropped (it has no lead to go to)."""
        front = self.front
        if answer is None:
            front.say_note(handoverwords.team_not_started_note(team))
            return
        lines, cap = answer
        try:
            fresh = handover.next_team(teams.load().get(team.name, team), lines, cap)
        except handover.HandoverError as error:
            self.state.kept_handovers.pop(team.name, None)
            front.say_note(Note(f"{team.name} is not handed over. {error}", "warning", 10))
            return
        saved = teams.load()
        saved[fresh.name] = fresh
        teams.save(saved)
        self.state.kept_handovers.pop(team.name, None)
        front.adopt_team(fresh)
        front.open_team(fresh, handover.team_launches(fresh, lead, written))
        front.say_note(handoverwords.team_handed_note(team, fresh))
        self.tell(alerts.handed_event(team.name))

    # --- usage limits: the sessions stopped at one, and those to continue when it lifts (c) ----------

    def continue_these(self, stopped: list[Session], unasked: bool = False) -> None:
        """Each goes on where it stopped: a closed one in a new tab with the continue prompt, an open one
        with the prompt typed into its own tab (off the UI thread: Engine.continued says how it went).
        unasked: by itself, the limit lifted (Front.open_unasked); else as a key opens (open)."""
        front = self.front
        closed, still_open = actions.continue_launches(stopped, front.launch_for)
        if closed:
            if unasked:
                front.open_unasked(closed)
            else:
                self.open(closed)
        if still_open:
            front.continue_open(still_open, len(closed))
        else:
            self.tell(alerts.back_event(len(closed)))

    def continued(self, done: actions.Continued) -> None:
        """The continue prompt typed, or not: Bit says how many are back (the opened ones too), a note
        names the tabs that would not take it."""
        if done.back:
            self.tell(alerts.back_event(done.back))
        if done.missed is not None:
            self.front.say_note(done.missed)

    def limits(self) -> None:
        """Forget the sessions gone on from a limit; once per session, Bit says which ones a usage limit
        stopped and that c continues them. On which account each stopped is kept (by the one that acts):
        signed in with another since, it can go on (signed_in_again)."""
        state, front = self.state, self.front
        kept, at = actions.limited_now(state.sessions, accounts.limited_on(), state.signed_as, state.account,
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
        by hand meanwhile is left alone. Only the one that acts: one that watches continues nothing,
        whatever runs this."""
        if not self.state.acts:
            return
        due = actions.due(self.state.armed, self.state.sessions, self.clock())
        if not due.gone:
            return
        for sid in due.gone:
            del self.state.armed[sid]
        self.save_armed()
        if due.lifted:
            self.continue_these(due.lifted, True)

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
            self.continue_these(choice.go_on, False)
        for session in choice.hand_over:
            front.open_handover(session, "transcript")
        return choice.rows

    # --- one acts: the actor lock --------------------------------------------------------------------

    def mount(self) -> None:
        """At its start: a dashboard takes the lock when it is free; a monitor never acts, nor asks."""
        if not self.front.monitoring():
            self.state.acts = actor.claim()
        if not self.state.acts:
            claudeupdate.leave_to_dashboard()  # the one that acts runs it: two at once would race

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

    def started(self) -> None:
        """It acts (at its start, or taken over): claude update is its to run again, and the sessions
        to continue are as saved."""
        claudeupdate.take_back()
        self.load_armed()

    def stopped(self) -> None:
        """It no longer acts: claude update is left to the one that does (two at once would race)."""
        self.state.acts = False
        claudeupdate.leave_to_dashboard()

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

    # --- claude update, a newer tabmancer, Wrapped -------------------------------------------------------

    def update_due(self, clis: Iterable[str] | None = None) -> None:
        """Today's claude update, when it is due: at the start of acting (clis None), or before
        sessions of these CLIs open (one of them Claude Code, none running already). The front runs it
        in a worker; what is asked for meanwhile waits (after_update)."""
        due = claudeupdate.due() if clis is None else alerts.update_due(self.state.updating_claude, clis)
        if due:
            self.state.updating_claude = True
            self.front.update_claude()

    def after_update(self, what: str, then: Callable[[], None]) -> bool:
        """While claude update runs: then, once it is done, and a word that it waits. False (and
        nothing kept) when no update is running."""
        if not self.state.updating_claude:
            return False
        self.state.after_claude_update.append(then)
        self.front.say_note(alerts.updating_note(what))
        return True

    def claude_updated(self, outcome: claudeupdate.Outcome, missing: list[str] | None) -> None:
        """claude update is done: how it went, then what waited for it, in the order asked."""
        state = self.state
        state.updating_claude = False
        waiting, state.after_claude_update = state.after_claude_update, []
        for note in alerts.update_notes(outcome, missing, bool(waiting)):
            self.front.say_note(note)
        for then in waiting:
            then()

    def newer(self, version: str, command: str) -> None:
        """A newer tabmancer (found off the UI thread: alerts.newer_tabmancer, told_update read there):
        in sight while open, and told once with the command that upgrades it."""
        self.state.told_update = version
        self.front.show_newer(version)
        self.tell(alerts.newer_event(version, command))

    def wrapped(self) -> None:
        """On Fridays from noon, or the weekend after, once a week: the week is ready (W). Only the one
        that acts: wrapped.told is shared, and the desktop note goes out of the window, so one that
        watches does neither, whatever runs this."""
        if not self.state.acts:
            return
        at = self.clock()
        event = alerts.wrapped_event(at)
        if event is None:
            return
        wrapped.told(at)
        self.front.show_bit(event.bit)
        self.front.say_note(event.note)
        if event.desktop and toast.enabled():
            self.front.send_toast(*event.desktop)

    # --- the plan limits, who is signed in, what a team said ---------------------------------------------

    def usage(self, limits: list[usage.Limit], bar: dict) -> None:
        """A plan reading (bar: agy's chain, agy.bar_view, {} without agy): a limit at 85% or more, Bit
        mentions each reading; every agy model out, once until it is back."""
        said, self.state.told_agy_out = alerts.usage_events(limits, bar, self.state.told_agy_out)
        for event in said:
            self.tell(event)

    def signed_in(self, found: list[accounts.Account]) -> None:
        """Who the CLIs are signed in as (accounts.signed_in, read again only when changed): Claude
        Code's is remembered for A by the one that acts, and a switch to another account frees the
        sessions a usage limit stopped on the other. One that watches only shows who it is."""
        state = self.state
        account = next((a for a in found if a.cli == "claude"), None)
        key = account.key if account is not None else None
        if key == state.signed_as and account == state.account:
            return
        switched = state.signed_as is not None and key is not None and key != state.signed_as
        state.signed_as, state.account = key, account
        if not state.acts:
            return
        accounts.remember(account, self.clock().isoformat(timespec="seconds"))
        if switched:
            self.signed_in_elsewhere()

    def signed_in_elsewhere(self) -> None:
        """Claude Code is signed in with another account now: its plan limits are read again, and the
        sessions a limit stopped on the other account can go on (c, or at once if waiting)."""
        state = self.state
        self.front.refresh_usage()
        self.limits()
        free = [s for s in state.sessions.values() if at_limit(s) and s.signed_in_again]
        who = accounts.words(state.account, 80, org=state.account.email in accounts.shared_emails()) \
            if state.account is not None else ""
        self.tell(alerts.signed_in_event(len(free), who))
        if free:
            self.front.show_rows(free)

    def team_sessions(self, updates: dict[str, Session]) -> set[str]:
        """agy members' sessions as a team's read found them (store.read_team_states): the ones that
        changed are the list's now. Their ids, for the front to draw again."""
        changed = {sid for sid, s in updates.items() if self.state.sessions.get(sid) != s}
        if changed:
            self.state.sessions.update(updates)
        return changed

    def heard(self, states: dict) -> list[alerts.Event]:
        """The messages between each team's members since the last look, for the front to fly along
        its wires (a picture of its own, so they come back rather than go out). The first look at a
        team only takes note: its history does not fly."""
        said, seen = alerts.team_events(states, self.state.team_seen)
        self.state.team_seen.update(seen)
        return said
