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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import accounts, agy, context, picks, search, teambus, teams
from .sessions import INTERRUPTED, Exit, Session, Status, folder_key
from .teams import TeamState, counts
from .tokens import Report, duration
from .usage import until

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
    "shell": ("background command", "aside", 2, "shell"),  # its pid file's word (sessions.mark_live)
    # Its turn over, it waits for a command it left running in the background (sessions.mark_live).
    "aside": ("background command", "aside", 2, "shell"),
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


# A team's members, counted on its row by what each does (state_id), in this order: (glyph, tone).
# An idle one grey as on its chip, ◐ only for one stopped at a usage limit, as in the list.
TEAM_GLYPHS = {"needs-you": ("!", "your-turn!"), "waiting": ("●", "your-turn"), "working": ("✶", "working"),
               "limit": ("◐", "to-continue"), "idle": ("●", "done"), "shell": ("◦", "aside")}


def member_words(session: Session | None) -> tuple[str, str]:
    """What a team member is doing, in the list's words with its team glyph: (words, tone)."""
    if session is None:
        return "not started", "done"
    if session.exit is not Exit.RUNNING:
        return STATUS[session.status]
    label, tone, _ = state_of(session)
    return f"{TEAM_GLYPHS.get(state_id(session), ('●', ''))[0]} {label}", tone


def who(session: Session, members: Mapping[str, tuple[str, str]]) -> str:
    """A session as a note names it: a team member as 'team: member' (members: session id -> (team,
    member)), else its project."""
    found = members.get(session.session_id)
    return f"{found[0]}: {found[1]}" if found else session.project



def team_key_pairs(started: bool, unfolded: bool) -> tuple[tuple[str, str], ...]:
    """The keys a team's row offers, (key, what): a team not started yet, or one at work (its
    members unfolded under it or not)."""
    if not started:
        return (("Enter", "team view"), ("s", "start the team"), ("e", "members"), ("E", "all teams"),
                ("Space", "tick"), ("a", "add"), ("x", "hide"), ("T", "teams"), ("M", "monitor"))
    return (("Enter", "team view"), ("e", "fold" if unfolded else "unfold"), ("E", "all teams"),
            ("s", "start the rest"), ("a", "add"), ("x", "close"), ("T", "teams"), ("M", "monitor"))


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
class MessageView:
    at: datetime
    sender: str
    receiver: str
    text: str


@dataclass
class MemberView:
    name: str
    lead: bool
    focus: str
    session: str | None
    state: str
    label: str
    tone: str
    cli: str = "claude"
    model: str = ""  # the model that answers (its session's), else the one it starts with; "" for Claude's default
    effort: str = ""
    model_note: str = ""  # why an agy member gets another model than the one written for it
    fell_back: str = ""  # "opus 5.5 → sonnet 5.5 (overloaded)" while another model took over by itself
    context: float | None = None  # 0..1 of its cap or window (Claude only)
    cap: int = 0  # its context cap in tokens; 0: none
    tokens: int | None = None
    unread: int = 0  # bus messages to it not yet taken (a team on the bus)
    last: MessageView | None = None  # the newest message to or from it
    warm_until: datetime | None = None  # done for now, a keep-warm sleep keeping its cache until then
    needs_you: bool = False
    quota: dict | None = None  # an agy member's model: agy.standing() ({"left", "span", "back", ...})
    git: dict | None = None  # its folder's (its worktree's): {"branch", "changed": files, "ahead": commits}
    bus: dict | None = None  # {"runner": bool, "listening": bool, "since": datetime | None}; None off the bus


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
    clis: list[str] = field(default_factory=list)  # claude first, then the others by name
    uses_bus: bool = False
    cap: int = 0  # its members' context cap in tokens; 0: none
    peers: bool = False  # nobody leads
    background: bool = False
    worktrees: bool = False
    lead: str | None = None
    folder: str = ""


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
    agy_quota: dict = field(default_factory=dict)  # agy.quota_view(): {} without agy


def effort_of(session: Session, member: bool) -> str:
    """The effort a session runs at: as its last reply says (Claude Code writes it there since
    2.1.319), else as tabmancer started it: a team member's own (member: it is one), or what it was
    opened with from tabmancer (picks). Others ran on Claude Code's own setting."""
    if session.effort in picks.EFFORTS:
        return session.effort
    found = teams.member_of(session) if member else None
    return found[1].effort if found else picks.recall(session.session_id).effort


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


def team_bus(team: teams.Team) -> tuple[dict[str, dict], dict[str, int]]:
    """For a team on the bus: (member -> live presence, member -> messages not yet taken); empty
    for a team without one. Reads the bus's files."""
    if not team.uses_bus:
        return {}, {}
    try:
        presence = teambus.read_presence(team.name)
        unread = {m.name: len(teambus.unread(team.name, team.session_name(m))) for m in team.members}
    except OSError:
        return {}, {}
    return presence, unread


def _presence_of(presence: dict[str, dict], team: teams.Team, member: teams.Member) -> dict | None:
    info = presence.get(member.name) or presence.get(team.session_name(member))
    if info is None:
        info = presence.get(teambus.normalize_member(team.name, member.name))
    if info is None:
        return None
    since = info.get("started")
    try:
        moment = datetime.fromisoformat(str(since)).astimezone().replace(tzinfo=None) if since else None
    except ValueError:
        moment = None
    return {"runner": bool(info.get("runner_alive")), "listening": bool(info.get("listening")), "since": moment}


def model_words(member: MemberView) -> str:
    """A member's model as the Model column and the team view say it (context.short_model), for every
    front end: 'opus 5.5 · high', and an agy one by its name, its level in it: 'Neon (High)'."""
    if not member.model:
        return "no model now" if member.cli == "agy" else "default model"
    words = context.short_model(member.model, most=24)
    if member.effort and not agy.label(member.model):
        words += f" · {member.effort}"
    return words


def member_model(member: teams.Member, session: Session | None) -> tuple[str, str]:
    """(model, note): the model a member answers with, its session's when known; else the one it
    starts with: for an agy member the picker's (agy's chain by quota, never Claude), with a note
    when the one written for it is replaced; ('', why) when all of the chain is out of quota."""
    if session is not None and session.model and not session.model.startswith("<"):
        return session.model, ""
    if member.cli == "agy":
        return agy.resolve(member.model)
    return member.model, ""


def members_open(state: TeamState, sessions: dict[str, Session]) -> int:
    """How many of a team's members are open, each as the list has it (sessions; else as the team's
    reader saw it, which can lag: a member it read closed can be open in the list)."""
    return sum(sessions.get(s.session_id, s).exit is Exit.RUNNING for s in state.sessions.values() if s is not None)


def tickable(session: Session) -> bool:
    """Whether Space ticks it: not while it is open in a terminal (one in the background is, to attach)."""
    return session.status is not Status.RUNNING


def members_closed(state: TeamState, sessions: dict[str, Session]) -> set[str]:
    """A closed team's members Space ticks as one (tickable), by their sessions' ids, each as the list has it."""
    now = (sessions.get(s.session_id, s) for s in state.sessions.values() if s is not None)
    return {s.session_id for s in now if tickable(s)}


def preselect(sessions: dict[str, Session], open_at_shutdown: Iterable[str],
              hidden: Callable[[Session], bool]) -> set[str]:
    """The sessions ticked when the list first shows, for Enter to open them again: those that looked
    open at the last shutdown (shutdowns.open_at_shutdown, read with the list), still listed, not hidden."""
    return {sid for sid in open_at_shutdown if sid in sessions and not hidden(sessions[sid])}


def untick_open(selected: set[str], preselected: set[str], updates: Iterable[Session]) -> tuple[set[str], set[str]]:
    """These sessions read again: one that turns out to be open was not left open at the shutdown, so it
    is unticked if it was ticked for that; one open in a terminal is never ticked (tickable). Returns the
    ticks and those ticked for the shutdown, as they are now."""
    updates = list(updates)
    now_open = {s.session_id for s in updates if s.exit is Exit.RUNNING}
    gone = (now_open & preselected) | {s.session_id for s in updates if not tickable(s)}
    return selected - gone, preselected - now_open


def ticked_words(selected: set[str], preselected: set[str]) -> str:
    """How many are ticked; when every one of them was ticked for the shutdown, that too."""
    shutdown = " · open at the last shutdown" if preselected and selected <= preselected else ""
    return f"{len(selected)} ticked{shutdown}"


def shutdown_hello(preselected: int) -> str:
    """What Bit says when the list first shows with sessions ticked for the shutdown."""
    return f"Hi! {preselected} were open when you shut down."


def team_view(state: TeamState, tokens: dict, sessions: dict[str, Session], *,
              git_of=None, agy_usage: dict | None = None, bus: tuple[dict, dict] | None = None) -> TeamView:
    """A team as the view carries it. git_of: folder -> its git as MemberView.git has it, or None; agy_usage: the
    saved agy quota (agy.load_quota()), read here when not given; bus: team_bus(), read here when not
    given."""
    team = state.team
    presence, unread = bus if bus is not None else team_bus(team)
    team_cap = picks.cap_tokens(team.cap)
    members = []
    for member in team.ordered:
        found = state.sessions.get(member.name)
        now = sessions.get(found.session_id, found) if found is not None else None
        if now is None:
            words, tone, state_name = "not started", "done", "not-started"
        else:
            (words, tone), state_name = label_of(now), state_id(now)
        model, note = member_model(member, now)
        view = MemberView(member.name, member.lead, member.focus, now.session_id if now else None, state_name,
                          words, tone, cli=member.cli, model=model,
                          effort=member.effort if member.cli == "claude" else "", model_note=note, cap=team_cap,
                          unread=unread.get(member.name, 0),
                          bus=_presence_of(presence, team, member) if team.uses_bus else None)
        if member.cli == "agy":
            if agy_usage is None:
                agy_usage = agy.load_quota()
            view.quota = agy.standing(model, agy_usage)
        said = next((m for m in reversed(state.said) if member.name in (m.sender, m.receiver)), None)
        if said is not None:
            view.last = MessageView(said.at, said.sender, said.receiver, " ".join((said.summary or said.text).split()))
        if now is not None:
            view.cap = teams.cap_of(now.session_id, team)
            if now.context and member.cli == "claude":
                view.context = context.share(now, view.cap)
            if (use := tokens.get(now.session_id)) is not None:
                view.tokens = use.total
            if git_of is not None and now.cwd:
                view.git = git_of(now.cwd)
            if now.exit is Exit.RUNNING:
                view.fell_back = now.fell_back or ""
                view.warm_until = now.warm_until if now.turn_over else None
                view.needs_you = not at_limit(now) and state_of(now)[2] < 2
        members.append(view)
    wires = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(counts(state.between_members()).items())]
    latest = [MessageView(m.at, m.sender, m.receiver, " ".join((m.summary or m.text).split()))
              for m in state.said[-LATEST:]]
    return TeamView(team.name, team.project, team.goal, members_open(state, sessions), state.tokens(tokens), members, wires,
                    latest, clis=sorted(team.clis, key=lambda cli: (cli != "claude", cli)), uses_bus=team.uses_bus,
                    cap=team_cap, peers=team.peers, background=team.background, worktrees=team.worktrees,
                    lead=team.lead.name if team.lead is not None else None, folder=team.cwd)


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

    def git_counts(folder: str) -> dict | None:
        """A member's folder (its worktree's): its branch and how much is changed and not pushed."""
        state = git.get(folder_key(folder))
        return ({"branch": state.branch, "changed": state.changed, "ahead": state.ahead}
                if state is not None else None)

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
    agy_usage = agy.load_quota()
    team_views = [team_view(state, tokens, sessions, git_of=git_counts, agy_usage=agy_usage) for state, _ in grouped.blocks]
    return View(datetime.now(), views, groups, team_views,
                sorted(projects.values(), key=lambda p: p.last_active, reverse=True), cards,
                [{"label": lim.label, "percent": lim.percent, "resets_at": lim.resets} for lim in limits],
                agy.quota_view(agy_usage))


def info_words(listed: int, hidden: int, days: float, *, show_hidden: bool = False,
               card: tuple[str, str] | None = None, selected: int = 0, preselected: int = 0) -> tuple[str, str]:
    """The list's info line: (what the top bar shows, the whole line under it). listed: every session
    known; hidden: how many of them are hidden; card: the key and title of the card the list is
    narrowed to; selected: the rows ticked; preselected: those ticked for having looked open at the
    last shutdown."""
    info = f"{listed - (0 if show_hidden else hidden)} sessions, last {days:g} days"
    if hidden:
        info += f" · showing {hidden} hidden (x brings one back)" if show_hidden else f" · {hidden} hidden (X shows them)"
    bar = info
    if card:
        key, title = card
        info += f" · only {title} ({key} or Esc shows all)"
    if selected:
        info += f" · {selected} selected (Enter opens them)"
    if preselected:
        info += f" · {preselected} looked open at the last shutdown"
    return bar, info


def live_words(sessions: list[Session], every: float) -> str:
    """The info line in the l mode: how many are open, and how many of them wait for you."""
    open_now = [s for s in sessions if s.exit is Exit.RUNNING]
    waiting = sum(state_of(s)[2] < 2 for s in open_now)
    return (f"Live · {len(open_now)} open · {waiting} waiting for you · "
            f"refreshes every {every:g}s · l returns to the list")


# The top bar's parts are (words, tone): "" plain, "muted", "text", "empty" (a bar's unfilled
# cells), and "ok" (green), "low" (amber), "out" (red), each also "bold ...".
BAR_GAP = ("   ", "")


def usage_parts(limits: list, agy_parts: list[tuple[str, str]] | tuple = (), agy_first: bool = False,
                now: datetime | None = None) -> list[tuple[str, str]]:
    """The plan limits (usage.Limit) as small bars of 8: green, then amber from 60 %, red from 85 %,
    each with the time it lifts. Per-model weekly limits only once they are half full. agy_parts:
    where agy's chain stands (agy.bar_view), 'agy Neon 54% left · Hydrogen 31% left', after the
    plan limits, or before them with agy the main CLI (agy_first)."""
    plan: list[tuple[str, str]] = []
    for limit in limits:
        if limit.label not in ("session", "week") and not limit.label.startswith("codex ") and limit.percent < 50:
            continue  # per-model weekly limits only matter once they fill up
        tone = "ok" if limit.percent < 60 else "low" if limit.percent < 85 else "out"
        filled = round(limit.percent / 100 * 8)
        if plan:
            plan.append(BAR_GAP)
        plan += [(f"{limit.label} ", "muted"), ("▰" * filled, tone), ("▱" * (8 - filled), "empty"),
                 (f" {limit.percent}%", f"bold {tone}")]
        left = until(limit.resets, now)
        if left:
            # No ↻: some fonts draw it two cells wide, right over the time.
            plan.append((f" · {left}", "muted"))
    chain: list[tuple[str, str]] = []
    for i, (words, tone) in enumerate(agy_parts):
        chain += [(" · " if i else "agy ", "muted"), (words, {"ok": "ok", "low": "low", "out": "bold out"}.get(tone, "muted"))]
    if plan and chain:
        return [*chain, BAR_GAP, *plan] if agy_first else [*plan, BAR_GAP, *chain]
    return plan or chain


def account_parts(found: list, shared: set[str], room: int) -> tuple[list[tuple[str, str]], bool]:
    """Who the CLIs are signed in as (accounts.Account), in at most room cells: Codex's first, then
    Claude Code's, beside the plan limits it is about. Short of room, Codex's goes first, then Claude
    Code's plan, then its email is cut in the middle (accounts.words). shared: emails seen in more
    than one organisation, shown with it. Also says whether Claude Code's account is in."""
    parts: list[tuple[str, str]] = []
    width, claude_shown = 0, False
    for account in sorted(found, key=lambda a: a.cli != "codex"):  # Codex's first, Claude's last
        label = "codex " if account.cli == "codex" else ""
        gap = 3 if width else 0
        org = account.cli == "claude" and account.email in shared
        shown = accounts.words(account, room - gap - len(label) - width, org=org)
        if not shown:
            continue  # Codex's gone: Claude Code's, which matters more, may still fit
        email, _, plan = shown.partition(" · ")
        parts += [(" " * gap, ""), (label, "muted"), (email, "text")]
        if plan:
            parts.append((f" · {plan}", "muted"))
        width += gap + len(shown) + len(label)
        claude_shown = claude_shown or account.cli == "claude"
    return parts, claude_shown


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


# --- a team's messages: the ones that fly, and the diagram's lanes --------------------------------

FLIGHT_SECONDS = 1.8  # how long a message takes to fly from card to card
OTHER = "other"  # the lane of sessions outside the team that wrote to it


def new_flights(seen: set | None, said: list, members: set[str],
                you: bool = False) -> tuple[set, list]:
    """The messages new since the last look (seen: (sender, receiver, text) of each; None before the
    first), and what is seen now. Those between two members fly; with you, your own to a member too.
    The first look only takes note: history stays put, nothing flies."""
    keys = {(m.sender, m.receiver, m.text) for m in said}
    if seen is None:
        return keys, []
    seen, flights = set(seen), []
    for message in said:
        key = (message.sender, message.receiver, message.text)
        if key in seen:
            continue
        seen.add(key)
        if message.receiver in members and (message.sender in members or (you and message.sender == teams.YOU)):
            flights.append(message)
    return seen, flights


def lanes_of(team: teams.Team, said: list) -> list[str]:
    """The diagram's lanes: you on the left when you (or tabmancer) said anything, then the lead and the
    members, then one for anyone else who wrote to them."""
    names = [m.name for m in team.ordered]
    people = {m.sender for m in said} | {m.receiver for m in said}
    left = [teams.YOU] if people & {teams.YOU, teams.TABMANCER} else []
    right = [OTHER] if people - set(names) - {teams.YOU, teams.TABMANCER} else []
    return [*left, *names, *right]


# --- the list's filters, pins and the row kept -----------------------------------------------------

ALL_PROJECTS = "*"  # the project filter's "every project"
CARDS = {"open": ("1", "OPEN NOW"), "waiting": ("2", "NEED YOU"), "continue": ("3", "TO CONTINUE")}  # key, title


def counted_by(session: Session, card: str) -> bool:
    """Whether a card counts this session: open; open and waiting for you; closed mid-work."""
    if card == "open":
        return session.exit is Exit.RUNNING
    if card == "waiting":
        return session.exit is Exit.RUNNING and state_of(session)[2] < 2
    return session.exit is not Exit.RUNNING and session.status in INTERRUPTED


def toggle_card(current: str | None, card: str) -> str | None:
    """1 2 3, or a click on a card: only what that card counts; the same again shows everything."""
    return None if current == card else card


def in_sight(sessions: Iterable[Session], is_hidden: Callable[[Session], bool], show_hidden: bool) -> list[Session]:
    """Every session but the hidden ones (X shows them too)."""
    if show_hidden:
        return list(sessions)
    return [s for s in sessions if not is_hidden(s)]


def visible(sessions: Iterable[Session], is_hidden: Callable[[Session], bool], *, show_hidden: bool = False,
            text: str = "", said_in: Callable[[], set[str]] = set, project: str = ALL_PROJECTS,
            card: str | None = None, live: bool = False) -> list[Session]:
    """The list's sessions, filtered and in order: the words (in its folder, title, prompts, or anywhere
    in the conversation: said_in, asked only with words), one project, what a card counts; live, the open
    ones by what they do. Else open sessions first in a fixed order, closed ones newest first: neither
    moves while a session merely keeps working, so rows stay put under the mouse. The pinned ones are
    set apart by group()."""
    needle = search.fold(text)
    said = said_in() if needle else set()
    shown = [s for s in in_sight(sessions, is_hidden, show_hidden)
             if not needle or s.session_id in said or search.matches(s, needle)]
    if project != ALL_PROJECTS:
        shown = [s for s in shown if folder_key(s.cwd) == project]
    if card:
        shown = [s for s in shown if counted_by(s, card)]
    if live:
        shown = [s for s in shown if s.exit is Exit.RUNNING]
        shown.sort(key=lambda s: (state_of(s)[2], s.project.casefold(), s.session_id))
        return shown
    shown.sort(key=lambda s: (0, s.project.casefold(), s.session_id) if s.exit is Exit.RUNNING
               else (1, -s.last_active.timestamp(), s.session_id))
    return shown


def toggle_pins(pinned: set[str], ids: set[str]) -> tuple[set[str], bool]:
    """f: these pinned above the rest, or unpinned when all of them were; and whether they are pinned now."""
    if ids <= pinned:
        return pinned - ids, False
    return pinned | ids, True


def kept_row(keys: list[str], current: str | None, row: int, keep_place: bool) -> int | None:
    """Where the cursor goes when the rows are made again: on its row (current: its key) if still
    listed; else, keep_place, on the row that took its place (it was on row), not the newest, where
    the next x or Enter would act on a session never picked; else None (the top)."""
    if current is not None and current in keys:
        return keys.index(current)
    if keep_place and current is not None and keys:
        return min(row, len(keys) - 1)
    return None


def back_step(search_open: bool, card: str | None) -> str:
    """Esc: the words' filter cleared ("search"), else a card's ("card"), else "quit"."""
    return "search" if search_open else "card" if card else "quit"


# --- the projects' lines (the terminal's sidebar, the desktop window's rail) -----------------------

@dataclass(frozen=True)
class ProjectRow:
    key: str  # its folder key; ALL_PROJECTS for the first line
    name: str
    count: int  # its sessions in sight
    mark: str = ""  # its most urgent session: "waiting" (for you), else "open", else "continue", else ""
    git: str = ""  # where its folder stands in git: "±" changes not committed, "↑" commits not pushed


def git_signs(changed: object, ahead: object) -> str:
    """A folder's git as the projects' lines show it: ± changes not committed, ↑ commits not pushed."""
    return ("±" if changed else "") + ("↑" if ahead else "")


def project_signs(projects: Iterable[ProjectView]) -> dict[str, str]:
    """Folder key -> its git signs, from the view's projects."""
    return {project.folder: git_signs(project.git.get("changed"), project.git.get("ahead"))
            for project in projects if project.git}


def project_rows(sessions: list[Session], waiting: set[str] | None = None,
                 git: dict[str, str] | None = None) -> list[ProjectRow]:
    """All projects first, then a line per project in a stable alphabetical order (a live update never
    reshuffles them under the mouse), each marked by its most urgent session: waiting for you (the ids
    in waiting; by default the ones NEED YOU counts), else open, else cut off or unanswered. git: folder
    key -> its signs (git_signs)."""
    if waiting is None:
        waiting = {s.session_id for s in sessions if counted_by(s, "waiting")}
    groups: dict[str, list[Session]] = {}
    for session in sessions:
        groups.setdefault(folder_key(session.cwd) or "?", []).append(session)
    rows = [ProjectRow(ALL_PROJECTS, "All projects", len(sessions))]
    for key, items in sorted(groups.items(), key=lambda item: item[1][0].project.casefold()):
        if any(s.session_id in waiting for s in items):
            mark = "waiting"
        elif any(s.exit is Exit.RUNNING for s in items):
            mark = "open"
        elif any(s.status in INTERRUPTED for s in items):
            mark = "continue"
        else:
            mark = ""
        rows.append(ProjectRow(key, items[0].project, len(items), mark, (git or {}).get(key, "")))
    return rows


# --- the tokens a day (the terminal's sidebar chart, the desktop window's rail) -------------------

@dataclass(frozen=True)
class DayTokens:
    day: date
    total: int


def token_ids(report: Report, sessions: dict[str, Session], project: str) -> set[str] | None:
    """The sessions a project's tokens are counted over: None for every project; else those whose
    folder is the project's. Where the session list knows a session, its folder decides, as in the
    list; for the others (gone from it), the folder the report saw them in last."""
    if project == ALL_PROJECTS:
        return None
    folders = report.folders | {sid: s.cwd for sid, s in sessions.items()}
    return {sid for sid, folder in folders.items() if folder_key(folder) == project}


def week_tokens(report: Report, ids: set[str] | None = None, days: int = 7) -> list[DayTokens]:
    """Tokens a day over the report's last days, oldest first: the last is the day it was counted
    (today, till midnight passes), as the TOKENS TODAY card has it."""
    daily = report.daily(ids)[-days:]
    first = report.last - timedelta(days=len(daily) - 1)
    return [DayTokens(first + timedelta(days=n), use.total) for n, use in enumerate(daily)]


# --- the board's lanes (the desktop window's Board) ------------------------------------------------

# A lane for each card, by its rule (counted_by), and one for the rest: (key, title, tone, what it
# holds, said under its title while it is empty). An open session that needs you is in NEED YOU only.
LANES = (("waiting", CARDS["waiting"][1], "your-turn!", "waiting for an answer"),
         ("open", CARDS["open"][1], "working", "at work or idle"),
         ("continue", CARDS["continue"][1], "to-continue!", "cut off or unanswered"),
         ("done", "DONE", "done", "closed, nothing left to do"))
TEAM_CARD = "team:"  # a team's one card on the board: TEAM_CARD + its name


@dataclass
class Lane:
    """One of the board's lanes. items: its cards in order, a session's by its id and a team's by
    TEAM_CARD + its name; count: the sessions in it by their own state, a team's members each in
    their own (NEED YOU's and TO CONTINUE's are the cards' numbers); words: what they are, as on the
    card (the projects; the open ones by what they do); away: those of them on a team's card in
    another lane, by team; folded: shown as a strip with its count."""

    key: str
    title: str
    tone: str
    caption: str
    items: list[str] = field(default_factory=list)
    count: int = 0
    words: list[str] = field(default_factory=list)
    away: dict[str, int] = field(default_factory=dict)
    folded: bool = False


def lane_of(session: Session) -> str:
    """Its lane: needs you (card 2's rule), else open (card 1's), else to continue (card 3's), else done."""
    for card in ("waiting", "open", "continue"):
        if counted_by(session, card):
            return card
    return "done"


def board(shown: list[Session], team_states: dict[str, TeamState], pinned: set[str]) -> list[Lane]:
    """The board's lanes over the sessions shown (visible(): already filtered and in the list's order).
    Each session is in the lane of its own state, but a team is one card, in the lane of its most
    urgent member, where that member comes. Pinned cards first (a team's when a member is pinned),
    then the list's order. DONE is folded."""
    lanes = {key: Lane(key, title, tone, caption, folded=key == "done") for key, title, tone, caption in LANES}
    rank = {key: place for place, (key, *_rest) in enumerate(LANES)}
    own = {s.session_id: lane_of(s) for s in shown}
    blocks = team_blocks(shown, team_states)
    team_of = {s.session_id: state.team.name for state, members in blocks for _, s in members}
    team_lane = {state.team.name: min((own[s.session_id] for _, s in members), key=rank.__getitem__)
                 for state, members in blocks}
    stuck = {state.team.name for state, members in blocks if any(s.session_id in pinned for _, s in members)}
    placed: set[str] = set()
    for s in shown:
        lane = lanes[own[s.session_id]]
        lane.count += 1
        team = team_of.get(s.session_id)
        if team is None:
            lane.items.append(s.session_id)
        elif own[s.session_id] != team_lane[team]:
            lane.away[team] = lane.away.get(team, 0) + 1
        elif team not in placed:
            placed.add(team)
            lane.items.append(TEAM_CARD + team)
    for lane in lanes.values():
        lane.items.sort(key=lambda item: not (item[len(TEAM_CARD):] in stuck if item.startswith(TEAM_CARD)
                                              else item in pinned))
        mine = [s for s in shown if own[s.session_id] == lane.key]
        if lane.key == "open":
            doing: dict[tuple[str, str, int], int] = {}
            for s in mine:
                doing[state_of(s)] = doing.get(state_of(s), 0) + 1
            lane.words = [f"{count} {label}" for (label, _tone, _rank), count in sorted(doing.items(), key=lambda item: item[0][2])]
        elif lane.key != "done":
            lane.words = by_project(mine)
    return list(lanes.values())
