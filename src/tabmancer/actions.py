"""The dashboard's actions on sessions and teams, without a screen (G3).

Each is a plain function over the sessions a front end holds: it returns what came of it, or raises
ActionError with the words to show. Dialogs, notes, threads and the list's rows stay with the front
end (the Textual dashboard, the desktop window), and so does the actor lock: an action never claims
or checks it, so a window that only watches cannot act by calling one. What only the front end
knows (the open sessions, how a session stands now) comes in as an argument.

Slice 1: the handover of one session (h). Slice 2: a whole team's (h twice on a team). Slice 3:
opening sessions (Enter, o) and going to an open one's tab.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from . import clis, focus, handover, picks, summarize, teams, wrapped
from .launch import Launch, Mode, continue_prompt
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
        from . import background  # here: it holds the B screen too, and actions loads no UI

        running = {job.name: job for job in background.jobs() or [] if job.running}
        for name in list(missed):
            if name in running:
                try:
                    background.stop(running[name].id)
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
    """What h once says: what a second h does."""
    names = open_names(team, sessions)
    closing = f"closes {len(names)} open member{'s' if len(names) != 1 else ''}, " if names else ""
    return (f"h again hands {team.name} over: it {closing}its lead writes the handover and the line-up the rest of "
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
