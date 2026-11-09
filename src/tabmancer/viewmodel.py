"""What the dashboard shows, as plain data: each session's state in the list's words, the groups
the list heads, the teams with their wires, the projects, the cards and the plan limits.

The terminal dashboard draws it with Textual; SessionPicker.view() gives it whole, and
to_json() as JSON. Nothing here imports Textual, so it carries over to any front end.

Colours are named by their role (a tone), not by a colour: red is your turn, amber is to
continue, blue is working, green is open, cyan is aside (background, a shell), grey is done. A
"!" makes it loud (bold).
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import context, picks, teams
from .sessions import INTERRUPTED, Exit, Session, Status, folder_key
from .teams import TeamState, counts
from .tokens import duration

TONES = ("your-turn!", "your-turn", "working", "open", "aside", "to-continue!", "to-continue", "done", "plain")

# How a closed session ended: the words and symbol the list shows, and their tone. Only plain
# text symbols: Windows Terminal draws ⚠ ⏸ ✉ and friends as colour emoji.
STATUS = {
    Status.RUNNING: ("● running", "open"),
    Status.BACKGROUND: ("◆ background", "aside"),
    Status.DONE: ("✓ done", "done"),
    Status.STOPPED: ("‖ stopped", "done"),
    Status.CUT_OFF: ("▲ cut off", "to-continue!"),
    Status.UNANSWERED: ("○ unanswered", "to-continue!"),
    Status.ERROR: ("× error", "to-continue"),
    Status.LIMIT: ("◐ limit", "to-continue"),  # with the time it lifts: limit_words
    Status.DROPPED: ("× connection lost", "to-continue"),
    Status.UNKNOWN: ("? unknown", "done"),
}
STATUS_HELP = {
    Status.RUNNING: "Open in another terminal right now.",
    Status.BACKGROUND: "Running as a background session (claude --bg). Enter attaches to it; B stops or "
                       "removes background sessions.",
    Status.DONE: "Claude had finished its reply; the session was waiting for you.",
    Status.STOPPED: "You pressed Esc while Claude was replying.",
    Status.CUT_OFF: "Closed while Claude was still working: a tool call or reply was in flight.",
    Status.UNANSWERED: "Your last message never got a reply.",
    Status.ERROR: "The last reply was an API error.",
    Status.LIMIT: "Stopped at a usage limit.",  # the preview says when it lifts: limit_help
    Status.DROPPED: "The connection to the API dropped, so Claude's reply never came.",
    Status.UNKNOWN: "Could not tell how this session ended.",
}
EXIT_HELP = {  # after the word in the preview's Process row
    Exit.CLEAN: "closed normally: /exit, a closed window or a shutdown",
    Exit.KILLED: "died without cleaning up: a crash, power loss or killed",
    Exit.RUNNING: "",
}
OUTCOME = {  # the heading over how a session ended, when its row is opened
    Status.DONE: "Finished",
    Status.STOPPED: "Stopped by you",
    Status.CUT_OFF: "Cut off while working",
    Status.UNANSWERED: "Your last message got no reply",
    Status.ERROR: "Ended on an API error",
    Status.LIMIT: "Stopped at a usage limit",
    Status.DROPPED: "The connection dropped",
    Status.UNKNOWN: "Unknown ending",
}
# Raw states from `claude agents --json` -> words, tone, sort rank (lowest first), and the state's id.
LIVE = {
    "blocked": ("needs you", "your-turn!", 0, "needs-you"),
    "waiting": ("needs you", "your-turn!", 0, "needs-you"),
    "permission": ("needs your OK", "your-turn!", 0, "needs-you"),  # stopped at a permission prompt
    "resting": ("idle", "done", 3, "idle"),  # a team member done for now: it waits for its lead, not you
    "idle": ("waiting for you", "your-turn", 1, "waiting"),
    "done": ("waiting for you", "your-turn", 1, "waiting"),
    "busy": ("working", "working", 2, "working"),
    "working": ("working", "working", 2, "working"),
    "shell": ("shell", "aside", 2, "shell"),
}
AT_LIMIT = ("at the limit", "to-continue", 3, "limit")  # open and idle at a usage limit
LATEST = 20  # the newest messages of a team the view carries


def live_label(raw: str) -> tuple[str, str, int]:
    """An open session's state from its raw live state: (words, tone, rank)."""
    return LIVE.get(raw.lower(), (raw or "open", "plain", 3, "open"))[:3]


def at_limit(session: Session) -> bool:
    """Stopped at a usage limit and still there: closed so, or open and idle since (a team member
    too, idle otherwise: it is the limit it waits for, not its lead)."""
    if session.resets is None:
        return False
    if session.exit is Exit.RUNNING:
        return live_label(session.live_state)[2] == 1 or session.live_state.lower() == "resting"
    return session.status is Status.LIMIT


def state_of(session: Session) -> tuple[str, str, int]:
    """What an open session is doing: (words, tone, rank). One idle at a usage limit waits for the
    limit, not for you."""
    if at_limit(session):
        return AT_LIMIT[:3]
    return live_label(session.live_state)


def state_id(session: Session) -> str:
    """The state as an id: needs-you, waiting, working, shell, limit or open for an open session;
    how it ended (Status' value: done, cut-off, limit ...) for a closed one."""
    if session.exit is not Exit.RUNNING:
        return session.status.value
    if at_limit(session):
        return AT_LIMIT[3]
    return LIVE.get(session.live_state.lower(), ("", "", 3, "open"))[3]


def lifts(moment: datetime) -> str:
    """16:00 today or tomorrow; Mon 06:00 further off."""
    return f"{moment:%H:%M}" if moment - datetime.now() < timedelta(hours=20) else f"{moment:%a %H:%M}"


def free_at(session: Session) -> datetime | None:
    """When a session stopped at a usage limit can go on: when the limit lifts, or now, signed in
    again in it since (/login: another account, whose limit it is not)."""
    return datetime.now() if session.signed_in_again else session.resets


def limit_words(session: Session) -> tuple[str, str]:
    """'◐ limit · 16:00' until the limit lifts, then '◐ limit lifted', loud: (words, tone)."""
    if session.signed_in_again:
        return "◐ signed in again", "to-continue!"
    if session.resets is not None and session.resets > datetime.now():
        return f"◐ limit · {lifts(session.resets)}", "to-continue"
    return "◐ limit lifted", "to-continue!"


def limit_help(session: Session) -> str:
    if session.signed_in_again:
        return "Stopped at a usage limit, then signed in again (/login): another account. c continues it now."
    if session.resets is not None and session.resets > datetime.now():
        left = duration((session.resets - datetime.now()).total_seconds())
        return f"Stopped at a usage limit, which lifts at {lifts(session.resets)} (in {left}). c continues it then."
    return "Stopped at a usage limit, which has lifted. c continues it now."


def label_of(session: Session) -> tuple[str, str]:
    """The words and tone the list's status column gives a session, open or closed."""
    if at_limit(session):
        return limit_words(session)
    if session.exit is not Exit.RUNNING:
        return STATUS[session.status]
    return state_of(session)[:2]


def by_project(sessions: list[Session]) -> list[str]:
    """'web-app ×2', 'api-server': the projects of these sessions, the busiest first."""
    tally: dict[str, int] = {}
    for session in sessions:
        tally[session.project] = tally.get(session.project, 0) + 1
    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0].casefold()))
    return [f"{name} ×{count}" if count > 1 else name for name, count in ranked]


def ago(moment: datetime) -> str:
    seconds = max(0.0, (datetime.now() - moment).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def project_hash(folder: str) -> str:
    """A project's stable id for anything shared: its folder never leaves the machine. The same
    folder gives the same id on every OS (not through folder_key, which follows this one's rules):
    separators, dots and case are evened out first."""
    plain = posixpath.normpath(folder.replace("\\", "/")).rstrip("/").casefold() if folder else ""
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()[:10]


# --- the list's groups --------------------------------------------------------------------------

Block = tuple[TeamState, list[tuple[str, Session]]]  # a team in the list, and its members' sessions


def block_open(block: Block) -> bool:
    """A team with a member open: it heads OPEN NOW, with its closed members too."""
    return any(s.exit is Exit.RUNNING for _, s in block[1])


@dataclass
class Groups:
    """The sessions shown, in the groups the list heads: open (the teams with a member open first,
    closed members too), pinned, the rest. A team with nobody open heads a row where its newest member
    would be. members: session id -> (team, member); placed: group -> the teams heading rows in it."""

    groups: list[tuple[str, list[Session]]]
    blocks: list[Block]
    members: dict[str, tuple[str, str]]
    placed: dict[str, list[str]] = field(default_factory=dict)


def team_blocks(shown: list[Session], team_states: dict[str, TeamState]) -> list[Block]:
    """The teams with a member among those shown, each with its members' sessions, the lead first."""
    by_id = {s.session_id: s for s in shown}
    blocks, taken = [], set()
    for state in team_states.values():
        members = []
        for member in state.team.ordered:
            found = state.sessions.get(member.name)
            if found is not None and found.session_id in by_id and found.session_id not in taken:
                members.append((member.name, by_id[found.session_id]))  # as the list has it now
                taken.add(found.session_id)
        if members:
            blocks.append((state, members))
    return blocks


def group(shown: list[Session], pinned: set[str], team_states: dict[str, TeamState], teams_too: bool = True) -> Groups:
    """Split the sessions shown (already sorted: open first) into the list's groups."""
    blocks = team_blocks(shown, team_states) if teams_too else []
    members = {s.session_id: (state.team.name, name) for state, team in blocks for name, s in team}
    in_teams = [s for block in blocks if block_open(block) for _, s in block[1]]
    taken = {s.session_id for s in in_teams}
    others = [s for s in shown if s.session_id not in taken]  # a closed team's members stay in place
    open_now = in_teams + [s for s in others if s.exit is Exit.RUNNING]
    pins = [s for s in others if s.exit is not Exit.RUNNING and s.session_id in pinned]
    rest = [s for s in others if s.exit is not Exit.RUNNING and s.session_id not in pinned]
    found = [(name, items) for name, items in (("open", open_now), ("pinned", pins), ("rest", rest)) if items]
    placed: dict[str, list[str]] = {}
    for name, items in found:  # each team heads a row once: where its first member comes
        for s in items:
            team = members.get(s.session_id, ("", ""))[0]
            if team and not any(team in names for names in placed.values()):
                placed.setdefault(name, []).append(team)
    return Groups(found, blocks, members, placed)


# --- the whole view -------------------------------------------------------------------------------

@dataclass
class SessionView:
    id: str
    project: str
    folder: str  # its folder as a key: local only, never shared (project_hash is for that)
    title: str
    cli: str
    open: bool
    kind: str  # terminal | background
    state: str  # state_id
    label: str  # the list's words for it
    tone: str
    rank: int  # an open session's place: 0 needs you ... 3
    resets_at: datetime | None
    context: float | None  # how full its context is, 0..1 (Claude only)
    crowded: bool
    last_active: datetime
    started: datetime | None
    hidden: bool
    pinned: bool
    team: str | None = None
    member: str | None = None
    lead: bool = False
    git: dict | None = None  # {"changed": bool, "ahead": bool}
    tokens: int | None = None  # over its whole life
    armed: datetime | None = None  # c: it continues when its limit lifts
    queued: str | None = None  # h on a busy one: "self" or "transcript", handed over once it finishes


@dataclass
class MemberView:
    name: str
    lead: bool
    focus: str
    session: str | None
    state: str
    label: str
    tone: str


@dataclass
class MessageView:
    at: datetime
    sender: str
    receiver: str
    text: str


@dataclass
class TeamView:
    name: str
    project: str
    goal: str
    open: int
    tokens: int
    members: list[MemberView]
    wires: list[dict]  # {"from", "to", "count"}: every message between two members
    latest: list[MessageView]  # the newest, newest last


@dataclass
class ProjectView:
    folder: str
    name: str
    last_active: datetime
    sessions: int
    open: int
    waiting: int
    git: dict | None


@dataclass
class View:
    at: datetime
    sessions: list[SessionView]
    groups: list[dict]  # {"name", "teams": [names], "sessions": [ids not in a team]}
    teams: list[TeamView]
    projects: list[ProjectView]
    cards: dict
    limits: list[dict] = field(default_factory=list)


def session_view(session: Session, *, hidden: bool, pinned: bool, member: tuple[str, str] | None,
                 lead: bool, git: dict | None, tokens: int | None, armed: datetime | None,
                 queued: str | None, cap: int = 0, default_cap: int = 0) -> SessionView:
    """One row. cap: the session's context cap in tokens (0: none); default_cap: the one new sessions
    get, past which a session without a cap is crowded."""
    label, tone = label_of(session)
    share = context.share(session, cap) if session.context and session.cli == "claude" else None
    return SessionView(
        id=session.session_id, project=session.project, folder=folder_key(session.cwd), title=session.title,
        cli=session.cli, open=session.exit is Exit.RUNNING,
        kind="background" if session.status is Status.BACKGROUND else "terminal", state=state_id(session),
        label=label, tone=tone, rank=state_of(session)[2] if session.exit is Exit.RUNNING else 9,
        resets_at=session.resets, context=share, crowded=bool(share is not None and context.crowded(session, cap, default_cap)),
        last_active=session.last_active, started=session.started, hidden=hidden, pinned=pinned,
        team=member[0] if member else None, member=member[1] if member else None, lead=lead, git=git,
        tokens=tokens, armed=armed, queued=queued)


def team_view(state: TeamState, tokens: dict, sessions: dict[str, Session]) -> TeamView:
    members = []
    for member in state.team.ordered:
        found = state.sessions.get(member.name)
        now =sessions.get(found.session_id, found) if found is not None else None
        if now is None:
            words, tone, state_name = "not started", "done", "not-started"
        else:
            (words, tone), state_name = label_of(now), state_id(now)
        members.append(MemberView(member.name, member.lead, member.focus, now.session_id if now else None,
                                  state_name, words, tone))
    wires = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(counts(state.between_members()).items())]
    latest = [MessageView(m.at, m.sender, m.receiver, " ".join((m.summary or m.text).split()))
              for m in state.said[-LATEST:]]
    return TeamView(state.team.name, state.team.project, state.team.goal, state.open_count(),
                    state.tokens(tokens), members, wires, latest)


def build(sessions: dict[str, Session], *, hidden: set[str], pinned: set[str], git: dict, team_states: dict,
          tokens: dict, limits: list, armed: dict, queued: dict, shown: list[Session] | None = None) -> View:
    """The whole view. sessions: every one known; hidden and pinned: their ids; git: folder key ->
    GitState; tokens: session id -> Tokens; limits: usage.Limit; armed and queued: as the dashboard
    keeps them. shown: the list's order, when there is one (else open first, newest first)."""
    order = shown if shown is not None else sorted(
        sessions.values(), key=lambda s: (s.exit is not Exit.RUNNING, -s.last_active.timestamp()))
    grouped = group(order, pinned, team_states)
    leads = {state.sessions[state.team.lead.name].session_id for state in team_states.values()
             if state.team.lead is not None and state.sessions.get(state.team.lead.name) is not None}

    def git_of(folder: str) -> dict | None:
        state = git.get(folder_key(folder))
        return {"changed": bool(state.changed), "ahead": bool(state.ahead)} if state is not None else None

    default_cap = picks.cap_tokens(picks.default_cap())

    def cap_of(session: Session) -> int:
        member = grouped.members.get(session.session_id)
        state = team_states.get(member[0]) if member else None
        return teams.cap_of(session.session_id, state.team if state is not None else None)

    views = [session_view(s, hidden=s.session_id in hidden, pinned=s.session_id in pinned,
                          member=grouped.members.get(s.session_id), lead=s.session_id in leads,
                          git=git_of(s.cwd), tokens=(use.total if (use := tokens.get(s.session_id)) else None),
                          armed=armed.get(s.session_id), queued=queued.get(s.session_id),
                          cap=cap_of(s), default_cap=default_cap)
             for s in sessions.values()]
    groups = [{"name": name, "teams": grouped.placed.get(name, []),
               "sessions": [s.session_id for s in items if s.session_id not in grouped.members]}
              for name, items in grouped.groups]
    projects: dict[str, ProjectView] = {}
    for s in sessions.values():
        key = folder_key(s.cwd)
        if not key:
            continue
        project = projects.setdefault(key, ProjectView(key, s.project, s.last_active, 0, 0, 0, git_of(s.cwd)))
        project.sessions += 1
        project.last_active = max(project.last_active, s.last_active)
        if s.exit is Exit.RUNNING:
            project.open += 1
            project.waiting += state_of(s)[2] < 2
    open_now = [s for s in sessions.values() if s.exit is Exit.RUNNING and s.session_id not in hidden]
    doing: dict[str, int] = {}
    for s in open_now:
        doing[state_id(s)] = doing.get(state_id(s), 0) + 1
    cards = {"open": doing,
             "need_you": by_project([s for s in open_now if state_of(s)[2] < 2]),
             "to_continue": by_project([s for s in sessions.values() if s.session_id not in hidden
                                        and s.exit is not Exit.RUNNING and s.status in INTERRUPTED])}
    return View(datetime.now(), views, groups, [team_view(state, tokens, sessions) for state, _ in grouped.blocks],
                sorted(projects.values(), key=lambda p: p.last_active, reverse=True), cards,
                [{"label": lim.label, "percent": lim.percent, "resets_at": lim.resets} for lim in limits])


def to_json(view: View) -> dict:
    """The view as JSON-ready data: datetimes as ISO text."""

    def plain(value):
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value

    return plain(asdict(view))
