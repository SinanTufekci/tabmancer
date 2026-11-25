"""The dashboard's actions on sessions and teams, without a screen (G3).

Each is a plain function over the sessions a front end holds: it returns what came of it, or raises
ActionError with the words to show. Dialogs, notes, threads and the list's rows stay with the front
end (the Textual dashboard, the desktop window), and so does the actor lock: an action never claims
or checks it, so a window that only watches cannot act by calling one. What only the front end
knows (the open sessions, how a session stands now) comes in as an argument.

Slice 1: the handover of one session (h). Slice 2: a whole team's (h twice on a team). Slice 3:
opening sessions (Enter, o) and going to an open one's tab. Slice 4: a team's keys (start, add,
remove, goal, close) on its row and in its view. Slice 6: continuing sessions stopped at a usage limit
(c, and by themselves when it lifts) and the handover queued on a working session (h), kept in a file
so every window shows it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import (
    actor,
    clis,
    context,
    focus,
    handover,
    picks,
    summarize,
    teambus,
    teams,
    typein,
    viewmodel,
    wrapped,
)
from .config import merge_json, read_json, write_json
from .launch import Launch, Mode, continue_prompt, run_in_background
from .sessions import Exit, Session, Status
from .tokens import human


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


# --- a whole team's handover ----------------------------------------------------------------------

def open_names(team: teams.Team, sessions: dict[str, Session | None]) -> list[str]:
    """The session names of the members open now, the tabs x closes."""
    return [team.session_name(m) for m in team.members
            if (s := sessions.get(m.name)) is not None and s.exit is Exit.RUNNING]


def close_tabs(names: list[str]) -> tuple[str, str]:
    """Close the members' tabs (Claude exits as on /exit), and stop the ones running in the background,
    which have none (claude stop: the session stays): what to say, and how loud. Slow: a worker's."""
    missed = [name for name in names if not focus.close_tab(name)]
    stopped = 0
    if missed:
        from . import bgjobs

        running = {job.name: job for job in bgjobs.jobs() or [] if job.running}
        for name in list(missed):
            if name in running:
                try:
                    bgjobs.stop(running[name].id)
                except OSError:
                    continue
                missed.remove(name)
                stopped += 1
    if missed:
        return f"Could not find the tab of {', '.join(missed)}: close it yourself (/exit).", "warning"
    closed = len(names) - stopped
    said = [f"closed {closed} tab{'s' if closed != 1 else ''}"] if closed else []
    said += [f"stopped {stopped} in the background"] if stopped else []
    return f"The team's members: {' and '.join(said)}.", "information"


def team_handover_problem(team: teams.Team, sessions: dict[str, Session | None]) -> str | None:
    """Why the team cannot be handed over now; None when it can."""
    if team.lead is None:
        return "A team of peers has no lead to write the handover: H hands the picked member over by itself."
    lead = sessions.get(team.lead.name)
    if lead is None or lead.path is None:
        return "The lead has no session yet: there is nothing to hand over."
    busy = [m.name for m in team.members if (s := sessions.get(m.name)) is not None and s.exit is Exit.RUNNING
            and s.live_state not in handover.IDLE]
    if busy:
        return (f"{', '.join(busy)} {'is' if len(busy) == 1 else 'are'} working: hand the team over once "
                f"{'it waits' if len(busy) == 1 else 'they wait'} for you.")
    return None


def handing_note(team: teams.Team, sessions: dict[str, Session | None]) -> str:
    """What h once says: what a second h does. With members open it closes them first; with none
    open, the lead writes straight away."""
    names = open_names(team, sessions)
    closing = f"it closes {len(names)} open member{'s' if len(names) != 1 else ''}, then " if names else ""
    return (f"h again hands {team.name} over: {closing}its lead writes the handover and the line-up the rest of "
            "the work needs, and you see both before the new team opens.")


@dataclass
class TeamHanded:
    """A team's handover written by its lead: the fresh lead's first message and the line-up for the rest
    (the lead's suggestion, else the team's line-up as it is)."""

    lead: Session
    written: str
    lineup: str
    suggested: bool  # the line-up is the lead's suggestion


def write_team_handover(team: teams.Team, members: dict[str, Session | None],
                        now: Callable[[Session], tuple[Session, list[Session]]],
                        close: Callable[[list[str]], tuple[str, str]] | None = None) -> TeamHanded | None:
    """A whole team handed over: its open members' tabs closed, its lead closed and resumed once to write
    the handover and the line-up for the rest (handover.write_team). Blocks for minutes: never on a
    screen's thread. None for a team with no lead session. `now(lead)` gives the lead as the list has
    it now and the open sessions; `close` closes the members' tabs (close_tabs). Members closed on the
    way stay closed when it fails."""
    lead = members.get(team.lead.name) if team.lead is not None else None
    if lead is None:
        return None
    try:
        left = handover.work_left(team, members)  # before anyone closes: what git shows still lying about
        others = [team.session_name(m) for m in team.members if not m.lead
                  and (s := members.get(m.name)) is not None and s.exit is Exit.RUNNING]
        if others:
            said, severity = (close or close_tabs)(others)
            if severity == "warning":
                raise handover.HandoverError(said.rstrip("."))
        if lead.exit is Exit.RUNNING:
            fresh, open_ones = now(lead)
            handover.close(fresh, open_ones, again="Close the lead's tab yourself (/exit), then h twice again")
        written, lineup = handover.write_team(lead, team, left)
    except (handover.HandoverError, summarize.SummaryError, OSError) as error:
        raise ActionError(f"{team.name} was not handed over: {str(error).rstrip('.')}. Members closed on the way "
                          "stay closed: s in the team's view starts them again.") from error
    return TeamHanded(lead, written, lineup or handover.current_lineup(team), bool(lineup))


# --- opening sessions -----------------------------------------------------------------------------

OPENING_SECONDS = 30  # a session just opened is not opened again while it starts up


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def resumes(launch: Launch) -> bool:
    """Whether it takes up a Claude Code session as it is (resume, auto-continue), where a second
    process would write to the same transcript. A fork or a fresh session gets one of its own."""
    return launch.cli == "claude" and launch.mode in (Mode.RESUME, Mode.AUTO) and bool(launch.session_id)


def launch_for(session: Session, mode: Mode, job: dict | None = None,
               saved: dict[str, teams.Team] | None = None) -> Launch:
    """How a closed session opens, in `mode` (the front end's pick for it): a team member's as that
    member (teams.as_member), one tabmancer opened with an effort at that effort again (picks.resumed).
    One running in the background (`job`, its entry in `claude agents`) is attached to, as its preview
    says: resumed, it was dropped as open already, and nothing opened."""
    title = clip(f"{session.project}: {session.title}", 48)
    if session.status is Status.BACKGROUND and job and job.get("id"):
        return Launch(title=title, cwd=session.cwd, mode=Mode.ATTACH, session_id=str(job["id"]), cli=session.cli)
    return picks.resumed(teams.as_member(session, Launch(
        title=title, cwd=session.cwd, mode=mode, session_id=session.session_id, cli=session.cli,
        prompt=continue_prompt(session.status) if mode is Mode.AUTO else None), saved),
        context=session.context or 0)


def still_opening(opening: dict[str, float], now: float) -> set[str]:
    """The sessions opened from here in the last OPENING_SECONDS (`opening`: id -> when, on the clock
    `now` is read from): they are not opened again while they start up."""
    return {sid for sid, when in opening.items() if now - when < OPENING_SECONDS}


MODES = (Mode.RESUME, Mode.AUTO, Mode.FORK, Mode.NEW)  # what m goes round, in this order


def next_mode(session: Session, now: Mode) -> Mode:
    """m: how a closed session opens after `now` (MODES in turn; a fork only where its CLI forks)."""
    cli = clis.CLIS.get(session.cli)
    modes = [mode for mode in MODES if mode is not Mode.FORK or (cli and cli.can_fork)]
    return modes[(modes.index(now) + 1) % len(modes)] if now in modes else modes[0]


def open_elsewhere(launches: list[Launch], live: Callable[[], set[str]]) -> set[str]:
    """Which of these sessions to resume a Claude process has open right now. Two processes on one
    transcript would both write to it, so it is asked afresh: `live()` gives the ids open now, and is
    only called when one of them resumes."""
    wanted = {launch.session_id for launch in launches if resumes(launch)}
    return wanted & live() if wanted else set()


@dataclass
class Opened:
    """What came of opening sessions: the ones opened, the ones left out as open already, the commands
    left to run by hand (no terminal that opens tabs) or why the terminal would not start."""

    opened: list[Launch]
    skipped: list[Launch]
    leftover: list[str]
    error: str = ""

    @property
    def opened_ids(self) -> set[str]:
        return {launch.session_id for launch in self.opened}

    @property
    def resumed_ids(self) -> set[str]:
        """The opened ones that take up a session as it is: each should show up open in a while."""
        return {launch.session_id for launch in self.opened if resumes(launch)}

    @property
    def skipped_ids(self) -> set[str]:
        return {launch.session_id for launch in self.skipped}

    @property
    def skipped_words(self) -> str:
        return f"Open already, so not opened again: {', '.join(launch.title for launch in self.skipped)}."

    @property
    def words(self) -> str:
        what = self.opened[0].title if len(self.opened) == 1 else f"{len(self.opened)} sessions"
        return f"Opened {what} in {'a new tab' if len(self.opened) == 1 else 'new tabs'}."


def open_launches(launches: list[Launch], live: Callable[[], set[str]],
                  open_tabs: Callable[[list[Launch]], list[str]]) -> Opened:
    """Open sessions in new tabs (`open_tabs`: launch.open_tabs aimed at a window), leaving out the ones
    a Claude process has open now (open_elsewhere). The ones brought back as they were, or told to carry
    on, are counted on the week's card (wrapped.note_opened). The front end keeps its own bookkeeping
    (what is opening, what came up) from the result."""
    taken = open_elsewhere(launches, live)
    skipped = [launch for launch in launches if resumes(launch) and launch.session_id in taken]
    rest = [launch for launch in launches if launch not in skipped]
    if not rest:
        return Opened([], skipped, [])
    try:
        leftover = open_tabs(rest)
    except OSError as error:  # the terminal would not start: said, rather than the front end falling over
        return Opened([], skipped, [], f"Could not open {'the tab' if len(rest) == 1 else 'the tabs'}: {error}")
    if leftover:
        return Opened([], skipped, leftover)
    wrapped.note_opened([(launch.session_id, "auto" if launch.mode is Mode.AUTO else "resume")
                         for launch in rest if resumes(launch)])
    return Opened(rest, skipped, [])


def came_up_missing(ids: set[str], came_up: set[str], sessions: dict[str, Session]) -> str | None:
    """The words for the sessions opened a while ago (`ids`) that never showed up open (`came_up`: the
    ones seen open since): Claude Code would not resume one (its folder gone, say), and its tab says
    why. After a reboot, with a dozen tabs opening at once, one is easy to miss. None when all came up."""
    missing = [sessions[sid] for sid in ids if sid not in came_up and sid in sessions
               and sessions[sid].exit is not Exit.RUNNING]
    if not missing:
        return None
    names = ", ".join(dict.fromkeys(s.project for s in missing))
    return f"{names} did not come up: {'its tab says' if len(missing) == 1 else 'their tabs say'} why."


@dataclass
class Note:
    """Words for the front end to show: how, and for how long (seconds; None: the default)."""

    words: str
    severity: str = "information"
    seconds: float | None = None


def go_to_tab(session: Session) -> Note:
    """Switch to the Windows Terminal tab where this session is open (focus.focus_tab). Blocks for a
    moment: not on a screen's thread."""
    if focus.focus_tab(session.title):
        return Note(f"Switched to {session.project}. Type your prompt there.", seconds=4)
    return Note(f"Could not find the tab of {session.project}. It may be open outside Windows Terminal, or its tab "
                "has been renamed.", "warning", 8)


PICK_FIRST = Note("Select sessions with Space first.", "warning")


@dataclass
class EnterChoice:
    """What Enter on the list does: open these sessions (open); bring an open one's tab to the front
    (tab); show a team (team: the front end's own, its row's); or only say why nothing happens (note)."""

    open: list[Session] | None = None
    tab: Session | None = None
    team: object | None = None
    note: Note | None = None


def enter_choice(ticked: list[Session], current: Session | None, team: object | None = None) -> EnterChoice:
    """Enter: the ticked sessions, else the one the cursor is on (current); on an open one, its tab; on a
    team's row (team, when no session is under the cursor), the team. Which of them are still opening
    is the front end's to leave out (openings.Openings.choose)."""
    if ticked:
        return EnterChoice(open=list(ticked))
    if current is None:
        return EnterChoice(team=team) if team is not None else EnterChoice(note=PICK_FIRST)
    if current.status is Status.RUNNING:  # already open: go there
        return EnterChoice(tab=current)
    return EnterChoice(open=[current])


def window_for(tabs_window: str | None, watching: bool, unasked: bool, monitor_open: Callable[[], bool],
               bring_dashboard: Callable[[], bool], dashboard_window: str) -> str:
    """The Windows Terminal window tabs belong in when they go to the dashboard's, not the one used last:
    a monitor's (its own window would be "0"), and the dashboard's unasked ones. By the window's name
    when it is known (tabs_window). Else, asked in a monitor (watching), the dashboard's tab brought to
    the front first (bring_dashboard: True when found), which makes its window "0"; unasked, never: keys
    typed meanwhile elsewhere would land in the dashboard. With no name and no tab found: the
    dashboard's go to "0", a monitor's to a new window; but the dashboard's unasked ones go to the
    window named for them (dashboard_window) when a team's monitor is open (monitor_open), as that may
    be the one used last. monitor_open and bring_dashboard are asked only when it comes to them, in
    that order: bringing a tab forward is felt."""
    if tabs_window:
        return tabs_window
    if not watching:
        return dashboard_window if unasked and monitor_open() else "0"
    return "0" if not unasked and bring_dashboard() else "new"


NONE_WAITING =Note("No session is waiting for you.", seconds=3)


@dataclass
class Turn:
    """j: the session to go to (None: none is waiting), and where the next j goes on from."""

    session: Session | None
    group: str = ""  # "needs" while some open session needs you, else "waiting"
    turn: int = 0


def next_waiting(sessions: Iterable[Session], group: str = "", turn: int = 0) -> Turn:
    """j: the next open session that needs you (a question, a permission), one after another; when none
    does, the next one waiting for you (its turn over). From the top of the ones that need you once
    there are some. `group` and `turn`: where the last j left off (the last Turn's)."""
    open_ones = [s for s in sessions if s.exit is Exit.RUNNING and viewmodel.state_of(s)[2] < 2]
    needs = [s for s in open_ones if viewmodel.state_of(s)[2] == 0]
    waiting = sorted(needs or open_ones, key=lambda s: (s.project.casefold(), s.session_id))
    if not waiting:
        return Turn(None, group, turn)
    now = "needs" if needs else "waiting"
    turn = turn if now == group else 0
    return Turn(waiting[turn % len(waiting)], now, turn + 1)


# --- a team's keys ----------------------------------------------------------------------------------

@dataclass
class Told(Note):
    """What a team change said, and what the front end does with it: `clipboard`, a note to paste ('' when
    it went over the bus or nobody is open), and `go_to`, the tab to paste it into."""

    title: str = ""
    clipboard: str = ""
    go_to: Session | None = None


def closing_note(team: teams.Team, sessions: dict[str, Session | None]) -> str | None:
    """What x says before it closes a team's open members (None: nobody is open)."""
    open_now = [s for s in sessions.values() if s is not None and s.exit is Exit.RUNNING]
    if not open_now:
        return None
    busy = sum(s.live_state.lower() in ("busy", "working", "aside") for s in open_now)
    at_work = f" {busy} of them {'is' if busy == 1 else 'are'} working and would stop." if busy else ""
    return (f"Press x again to close the {len(open_now)} open members' tabs.{at_work} Their sessions stay, and "
            "s starts them again.")


def cold_note(team: teams.Team, sessions: dict[str, Session | None]) -> str | None:
    """What starting a closed team says first when its members' cache is gone (an hour after their
    last reply): going on writes their whole conversations again as each gets its first message,
    where a handover starts them fresh. None when no member's context is big enough to matter (the
    lead's alone is written again by a handover too)."""
    cold = [(member, session) for member in team.members
            if (session := sessions.get(member.name)) is not None and session.exit is not Exit.RUNNING
            and session.path is not None and context.cold(session)]
    members = [session for member, session in cold if not member.lead]
    if not members:
        return None
    total = sum(session.context or 0 for _, session in cold)
    spared = sum(session.context or 0 for session in members)
    last = max(session.last_active for _, session in cold if session.last_active is not None)
    return (f"{team.name} was last at work {viewmodel.ago(last)} ago and its cache is gone: going on writes "
            f"{human(total)} of conversation again ({len(cold)} sessions). Again goes on; h twice hands it over "
            f"instead: the lead writes a handover and the members start fresh, {human(spared)} less.")


def closed_note(names: list[str], close: Callable[[list[str]], tuple[str, str]] | None = None) -> Note:
    """x twice: the open members' tabs closed (`close`: close_tabs), and what it says."""
    said, severity = (close or close_tabs)(names)
    return Note(said, severity, 8 if severity == "warning" else 4)


@dataclass
class TeamStart:
    """The tabs a team's start opens, or the words when there are none."""

    launches: list[Launch]
    words: str = ""


def start_team(team: teams.Team, members: dict[str, Session | None], recent: set[str] | None = None) -> TeamStart:
    """Every member not open: a closed one resumed as itself, one never started new; the lead last.
    `recent`: the sessions opened from here a moment ago (still_opening), left out; None when the
    front end does not keep them."""
    launches = teams.start(team, members)
    if recent is not None:
        launches = [launch for launch in launches if not launch.session_id or launch.session_id not in recent]
    if launches:
        return TeamStart(launches)
    return TeamStart([], f"Every member of {team.name} is open already, or opening." if recent is not None
                     else "Every member is open already.")


@dataclass
class TeamOpening:
    """A team's sessions to open: its members that run in the background (`quiet`) and the tabs, or the
    words when they are all opening already."""

    quiet: list[Launch]
    tabs: list[Launch]
    words: str = ""


def team_opening(team: teams.Team, launches: list[Launch]) -> TeamOpening:
    """A team's sessions, split into background members and tabs. A member opened a moment ago, here or
    by `tabmancer team start`, is not opened again: s twice, while the first claude --bg calls were
    still running, started every member twice. The ones that go ahead are noted as opening."""
    coming = teams.opening()
    launches = [launch for launch in launches if teams.session_name_of(team, launch) not in coming]
    if not launches:
        return TeamOpening([], [], f"The members of {team.name} are opening already.")
    teams.note_opening([teams.session_name_of(team, launch) for launch in launches])
    quiet = [launch for launch in launches if teams.in_background(team, launch)]
    return TeamOpening(quiet, [launch for launch in launches if launch not in quiet])


def start_in_background(team: teams.Team, launches: list[Launch],
                        run: Callable[[list[str]], object] | None = None) -> list[Note]:
    """claude --bg for each (a few seconds each: not on a screen's thread), and what came of it. `run`:
    launch.run_in_background."""
    started, failed = [], []
    for launch in launches:
        try:
            (run or run_in_background)(teams.background_start(team, launch))
            started.append(launch.title.partition(": ")[2])
        except OSError as error:
            failed.append(f"{launch.title.partition(': ')[2]}: {error}")
    notes = []
    if started:
        notes.append(Note(f"Started {', '.join(started)} of {team.name} in the background. B lists them; T shows "
                          "the team.", seconds=6))
    if failed:
        notes.append(Note("Could not start in the background: " + "; ".join(failed), "warning", 12))
    return notes


@dataclass
class Added:
    """New members written to a team: the team as saved now, the new ones, and what was left out."""

    team: teams.Team
    new: list[teams.Member]
    warning: str = ""

    @property
    def launches(self) -> list[Launch]:
        """Their tabs, for a team at work: each introduces itself to the lead (teams.joining)."""
        return teams.joining(self.team, self.new)

    @property
    def joined_words(self) -> str:
        """For a closed team: they start with it."""
        return f"{', '.join(m.name for m in self.new)} joined {self.team.name}: they start with the team (Enter on its row)."


def add_members(team: teams.Team, text: str) -> Added:
    """New members for a team, from a line as name: area (teams.add_members)."""
    new, note = teams.add_members(team, text)
    return Added(teams.load().get(team.name, team) if new else team, new,
                 note[0].upper() + note[1:] + "." if note else "")


@dataclass
class Changed:
    """A team changed: as saved now, the note for its lead (tell_lead) and what was done."""

    team: teams.Team
    note: str
    done: str


def removal_refusal(member: teams.Member | None) -> Note | None:
    """Why d takes no member off; None when it can."""
    if member is None:
        return Note("Pick a member with ← → first.", seconds=3)
    if member.lead:
        return Note("The lead stays: a team has one. h hands it over to a fresh session instead.", "warning", 6)
    return None


def removal_ask(member: teams.Member, session: Session | None) -> Note:
    """What the first d says: a second one takes the member off."""
    still = " Its tab stays open, as an ordinary session." if session and session.exit is Exit.RUNNING else ""
    return Note(f"Press d again to take {member.name} off the team. Its sessions stay.{still}", seconds=6)


def remove_member(team: teams.Team, name: str) -> Changed | None:
    """A member taken off the team. Its session stays, an ordinary one from then on, and the lead (or,
    among peers, the others) is to be told: it started with the old line-up."""
    left = teams.remove_member(team.name, name)
    return None if left is None else Changed(left, teams.left_note(left, name), f"{name} is off the team.")


def change_goal(team: teams.Team, goal: str) -> Changed | None:
    """The team's goal, changed. Members started from now on get the new one; the lead is to be told.
    None when it is empty or the same."""
    goal = teams.tidy(goal)
    if not goal or goal == teams.tidy(team.goal):
        return None
    changed = teams.set_goal(team.name, goal)
    return None if changed is None else Changed(changed, teams.goal_note(changed), "The goal is changed.")


def tell_lead(team: teams.Team, sessions: dict[str, Session | None], note: str, done: str) -> Told:
    """A change, for the lead to hear: over the team bus when this team uses one (sent here), else a
    note for the clipboard and the tab to paste it into. A closed lead gets it in its role. Peers each
    started with the old team in their role: the note is for every open one."""
    if team.lead is None:
        open_ones = [name for name, s in sessions.items()
                     if s is not None and s.exit is Exit.RUNNING and any(m.name == name for m in team.members)]
        if not open_ones:
            return Told(f"{done} Nobody is open: each starts with the change in its role.", seconds=6)
        if team.uses_bus:
            for name in open_ones:
                teambus.send(team.name, teams.YOU, name, note, summary="team change")
            return Told(f"{done} Sent the update over the team bus to {', '.join(open_ones)}.", seconds=8,
                        title="Told the team")
        return Told(f"{done} A note for the team is on the clipboard: paste it into each open member's tab "
                    f"({', '.join(open_ones)}; Ctrl+V, Enter).", seconds=14, title="Tell the team", clipboard=note,
                    go_to=sessions[open_ones[0]])
    lead = sessions.get(team.lead.name)
    if lead is None or lead.exit is not Exit.RUNNING:
        return Told(f"{done} The lead is not open: it starts with the change in its role.", seconds=6)
    if team.uses_bus:
        teambus.send(team.name, teams.YOU, team.lead.name, note, summary="team change")
        return Told(f"{done} Sent the update to {team.lead.name} over the team bus.", seconds=8, title="Told the lead")
    return Told(f"{done} A note for the lead is on the clipboard: paste it into its tab (Ctrl+V, Enter).",
                seconds=12, title="Tell the lead", clipboard=note, go_to=lead)


# --- usage limits (c) ---------------------------------------------------------------------------------

LIMIT_MARGIN = timedelta(minutes=1)  # continue this long after a limit lifts, to be sure it has
ARMED_UNSAVED = Note("The sessions to continue at their reset could not be saved: config.json could not be read, "
                     "so it was left as it is. They still continue while this dashboard is open.", "warning", 15)
NONE_STOPPED = Note("No session is stopped at a usage limit.", seconds=4)
ARMED_OFF = Note("Sessions at a limit will not continue by themselves.", seconds=5)
# Only the dashboard that acts (actor.py) continues sessions at their reset; one that watches says so.
ACTS_ELSEWHERE = Note("Another tabmancer dashboard continues sessions at their reset: c there sets which. This one "
                      "only shows them (it takes over when that one closes).", "warning", 10)
ARM_ELSEWHERE = "continued at its reset only by the dashboard that acts (actor.py): ask there"


def stopped_at_limit(sessions: dict[str, Session]) -> list[Session]:
    """The sessions c can continue: stopped at a usage limit, and not working in the background."""
    return [s for s in sessions.values() if viewmodel.at_limit(s) and s.status is not Status.BACKGROUND]


def continue_when(stopped: list[Session]) -> str:
    """When the first of them can go on: 'now', or 'at 16:00'."""
    soon = min(viewmodel.free_at(s) or datetime.now() for s in stopped)
    return "now" if soon <= datetime.now() else f"at {viewmodel.lifts(soon)}"


def plan_words(limits: list, others: list[str]) -> tuple[str, bool]:
    """The plan as last read (/usage, every few minutes) and the other accounts, for the c dialog, and
    whether there is room on it: room means the limit no longer stands in the way (another account, or
    it lifted), and continuing now comes first."""
    session_limit = next((limit for limit in limits if limit.label == "session"), None)
    plan = ("Your plan now: " + " · ".join(f"{limit.label} {limit.percent}%" for limit in limits
                                             if limit.label in ("session", "week"))) if limits else ""
    plan = "\n".join([*([plan] if plan else []), *others])
    return plan, session_limit is not None and session_limit.percent < 95


def arm(these: list[Session], now: datetime | None = None) -> dict[str, datetime]:
    """Each continues by itself when its limit lifts: at its reset, or now when it has none."""
    return {s.session_id: s.resets or now or datetime.now() for s in these}


def arm_refusal(session: Session, acts: bool) -> str | None:
    """Why one session cannot be set to continue at its reset (the act API's continue_at_reset), in a
    few words: not at a limit (or working in the background), or this dashboard does not act. None: it can."""
    if not viewmodel.at_limit(session) or session.status is Status.BACKGROUND:
        return "not at a limit"
    return None if acts else ARM_ELSEWHERE


def armed_words(moment: datetime) -> str:
    """'continues at 16:00': what the act API says once one is set to continue."""
    return f"continues at {viewmodel.lifts(moment)}"


def answer_refusal(answer: str, acts: bool) -> Note | None:
    """The c dialog's answer refused: continuing when the limit lifts, or no longer by themselves, is set
    only where the dashboard acts. Now and the handover go on anywhere."""
    return ACTS_ELSEWHERE if answer in ("lift", "off") and not acts else None


def still_armed(armed: dict[str, datetime], at: set[str]) -> dict[str, datetime]:
    """The armed that are still stopped at a limit (at: their ids): one gone on meanwhile is forgotten."""
    return {sid: moment for sid, moment in armed.items() if sid in at}


@dataclass
class Choice:
    """What the c dialog's answer comes to: the sessions to continue at their reset from now on, the
    ones to continue now and the ones to hand over now, the note to show, and whether their rows change."""

    armed: dict[str, datetime]
    go_on: list[Session]
    hand_over: list[Session]
    note: Note | None = None
    rows: bool = False


def continue_choice(answer: str, ids: list[str], stopped: list[Session], armed: dict[str, datetime],
                    when: str, now: datetime | None = None) -> Choice | None:
    """The c dialog's answer: now, handover (from the transcript: the old context is not sent again on
    the new account), lift (by themselves when the limit lifts) or off. None for anything else. Lift and
    off are for the dashboard that acts alone (the front end checks)."""
    these = [s for s in stopped if s.session_id in ids]
    if answer in ("now", "handover"):
        left = {sid: moment for sid, moment in armed.items() if sid not in ids}
        return Choice(left, these if answer == "now" else [], these if answer == "handover" else [])
    if answer == "lift":
        words = (f"{len(these)} {'session continues' if len(these) == 1 else 'sessions continue'} {when}, when the "
                 "limit lifts. Leave the dashboard open.")
        return Choice(arm(these, now), [], [], Note(words, seconds=8), rows=True)
    if answer == "off":
        return Choice({}, [], [], ARMED_OFF, rows=True)
    return None


def save_armed(armed: dict[str, datetime]) -> bool:
    """The sessions to continue at their reset, saved in config.json (store.read_armed reads them), so a
    dashboard that watches shows them. False when config.json could not be read: it is left as it is."""
    return merge_json("config.json", continue_at_reset={sid: moment.isoformat() for sid, moment in armed.items()})


@dataclass
class Due:
    """Of the sessions to continue at their reset: those whose time has come (no longer to continue
    later), and of them the ones still stopped at the limit (one gone on by hand meanwhile is not)."""

    gone: list[str]
    lifted: list[Session]


def due(armed: dict[str, datetime], sessions: dict[str, Session], now: datetime) -> Due:
    """Due once the limit lifted (LIMIT_MARGIN after its reset), or at once when signed in again in it
    (/login, another account)."""
    gone = [sid for sid, moment in armed.items()
            if moment + LIMIT_MARGIN <= now or getattr(sessions.get(sid), "signed_in_again", False)]
    lifted = [sessions[sid] for sid in gone if sid in sessions and viewmodel.at_limit(sessions[sid])]
    return Due(gone, lifted)


def continue_launches(stopped: list[Session],
                      launch_for: Callable[[Session], Launch]) -> tuple[list[Launch], list[Session]]:
    """How each goes on where it stopped: a closed one opens in a new tab with the continue prompt (the
    launches), an open one gets the prompt typed into its own tab (the sessions, for type_continue)."""
    closed = [replace(launch_for(s), mode=Mode.AUTO, prompt=continue_prompt(Status.LIMIT))
              for s in stopped if s.exit is not Exit.RUNNING]
    return closed, [s for s in stopped if s.exit is Exit.RUNNING]


def back_words(back: int) -> str:
    return f"{back} back at work"


@dataclass
class Continued:
    """How many went on (opened ones too), and what to say of the open ones that would not take the prompt."""

    back: int
    missed: Note | None = None


def type_continue(still_open: list[Session], opened: int) -> Continued:
    """The continue prompt goes into each one's tab, as if you typed it, when it waits. Its tab is never
    closed: one that will not take the prompt is left as it is. Blocks: not on a screen's thread."""
    prompt = continue_prompt(Status.LIMIT)
    missed = [s for s in still_open
              if not (s.live_state in handover.IDLE and typein.type_into(s.session_id, prompt))]
    wrapped.note_opened([(s.session_id, "limit") for s in still_open if s not in missed])  # for the card
    note = None
    if missed:
        names = ", ".join(dict.fromkeys(s.project for s in missed))
        note = Note(f"Could not write in the tab of {names}: type “continue” there yourself.", "warning", 15)
    return Continued(opened + len(still_open) - len(missed), note)


def limited_now(sessions: dict[str, Session], limited: dict[str, str], signed_as: str | None,
                current: object, same: Callable[[str, object], bool]) -> tuple[dict[str, str], set[str]]:
    """On which account each session at a limit stopped (limited: as kept before; else the one signed in
    now), and the ones at a limit. One signed in with another account since can go on: its
    signed_in_again is set here."""
    kept = {}
    for session in sessions.values():
        if not viewmodel.at_limit(session):
            continue
        stopped_on = limited.get(session.session_id) or signed_as
        if stopped_on:
            kept[session.session_id] = stopped_on
        if stopped_on and current is not None and not same(stopped_on, current):
            session.signed_in_again = True
    return kept, {s.session_id for s in sessions.values() if viewmodel.at_limit(s)}


def newly_stopped(sessions: dict[str, Session], at: set[str], told: set[str],
                  armed: dict[str, datetime]) -> list[Session]:
    """The ones at a limit not told of yet, nor to continue by themselves, and not in the background."""
    return [sessions[sid] for sid in at - told - armed.keys() if sessions[sid].status is not Status.BACKGROUND]


@dataclass
class Say:
    """Bit's words: said plainly when mood is '', else Bit reacts so (mascot.react)."""

    words: str
    mood: str = ""
    seconds: float = 10


def limit_say(stopped: list[Session], others_for: Callable[[datetime], str], now: datetime | None = None) -> Say:
    """Once per session, what Bit says of the ones a usage limit stopped: until when, and that c
    continues them (or what the other accounts offer: others_for(when it lifts))."""
    names = ", ".join(sorted({s.project for s in stopped})[:3])

    def free(session: Session) -> datetime | None:  # viewmodel.free_at, by this clock
        return (now or datetime.now()) if session.signed_in_again else viewmodel.free_at(session)
    soon = min((free(s) for s in stopped if free(s)), default=None)
    if soon and soon > (now or datetime.now()):
        others = others_for(soon)
        return Say(f"{names}: limit until {viewmodel.lifts(soon)}. {others or 'c continues then'}",
                   seconds=12 if others else 10)
    if any(s.signed_in_again for s in stopped):
        return Say(f"{names}: signed in again. c continues now", "lifted")
    return Say(f"{names}: the limit has lifted. c continues now", "lifted")


# --- the handover queued on a working session (h) -------------------------------------------------

QUEUE = "handover-queue.json"  # {session id: {"how": ..., "by": actor.me() of the window that queued it}}


def busy(session: Session) -> bool:
    """Working: h hands it over once it finishes, not now."""
    return session.exit is Exit.RUNNING and session.live_state not in handover.IDLE


def finished(session: Session) -> bool:
    """A queued one is handed over now: closed, or waiting."""
    return session.exit is not Exit.RUNNING or session.live_state in handover.IDLE


def writer_words(how: str) -> str:
    return "it writes its handover" if how == "self" else f"Claude ({handover.MODEL}) writes the handover"


def queued_note(session: Session, how: str) -> Told:
    return Told(f"{session.project} is working: it is handed over as soon as it finishes ({writer_words(how)}). "
                "h again cancels.", seconds=10, title=f"Handing over {session.project}")


def unqueued_note(session: Session) -> Note:
    return Note(f"{session.project} will not be handed over when it finishes.", seconds=5)


def _writer(record: object) -> tuple | None:
    if not isinstance(record, dict):
        return None
    return record.get("pid"), record.get("started"), record.get("command")


def read_queued() -> dict[str, str]:
    """The handovers queued in every tabmancer window: session id -> how. Only the windows still open
    queue anything: one that closed or crashed (its process gone, or its pid now another process's:
    actor.running checks the start time and command line too) left nothing queued."""
    saved = read_json(QUEUE)
    if not isinstance(saved, dict):
        return {}
    alive: dict[tuple, bool] = {}
    found = {}
    for sid, entry in saved.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("how"), str):
            continue
        writer = _writer(entry.get("by"))
        if writer is None:
            continue
        if writer not in alive:
            alive[writer] = actor.running(entry["by"])
        if alive[writer]:
            found[sid] = entry["how"]
    return found


def save_queued(mine: dict[str, str]) -> bool:
    """This window's queued handovers, saved beside the other open windows' (theirs are kept, a closed
    one's go). False when the file could not be written."""
    saved = read_json(QUEUE)
    entries = {}
    for sid, entry in (saved.items() if isinstance(saved, dict) else ()):
        if isinstance(entry, dict) and not actor.is_me(entry.get("by")) and actor.running(entry.get("by")):
            entries[sid] = entry
    if not entries and not mine and saved in (None, {}):
        return True  # nothing queued anywhere, and nothing to clear
    me = actor.me()
    entries.update({sid: {"how": how, "by": me} for sid, how in mine.items()})
    try:
        write_json(QUEUE, entries, indent=2)
    except OSError:
        return False
    return True


@dataclass
class Finished:
    """A queued session finished: what to say, and whether this window hands it over now."""

    note: Told
    hand_over: bool


def queued_finished(sid: str, session: Session, monitor: bool, claim: Callable[[str], bool]) -> Finished:
    """It finished: handed over by one window only (claim: actor.first_to, the first to ask). A monitor
    hands over only what is still open as it finishes: closed in its tab meanwhile (from the dashboard,
    say), it was taken care of there."""
    if monitor and session.exit is not Exit.RUNNING:
        return Finished(Told(f"{session.project} closed before it finished: not handed over from here.", "warning",
                             10), False)
    if not claim(f"handover-{sid}"):  # queued in another dashboard or monitor too
        return Finished(Told(f"{session.project} finished: another tabmancer window hands it over.", seconds=8),
                        False)
    return Finished(Told(f"{session.project} finished: handing it over now.", seconds=10,
                         title=f"Handing over {session.project}"), True)
