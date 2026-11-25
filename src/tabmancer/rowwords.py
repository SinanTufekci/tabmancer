"""The dashboard's list as words, with no UI behind them: each cell of a session's row (the picker's
columns, or the live view's), a team's row, a member's place in its team's tree, the headings over
the groups, and the rows in their order. Each cell comes as parts, (words, style), in sessionwords'
vocabulary, with one word more: 'on #rrggbb' paints behind the words (a loud state's badge). The
terminal dashboard (app) puts them into rich Text, the desktop window's table into its own; both say
the same. A front says what it has in a Listing and asks row() for each of keys().

A cell is its parts one after another, each in its own style; except a column in BASED, whose first
part's style is the whole cell's, the other parts drawn over it (as the terminal's Text with a style
of its own: a part with no colour takes the first one's).

What moves is not drawn here: a row says it (RowWords.motion) and each front moves it its own way. The
status of a session at work turns a star and sweeps a light across its words ('spin': the parts are
a still star in the accent and the words in bold blue), one that needs you breathes red ('pulse': the
parts are its middle shade). A folded team's row with a member at work turns that member's star
('spin': star is the frame to draw). A row lit after a change is the front's own.

A heading's row is set apart from what is above it: the terminal draws it two lines high, a blank
line over each of its cells.

Words are cut as the terminal cuts them: actions.clip where it clips (characters), fit() where it
truncates a Text (cells on the screen, '…' in the style of what it cuts)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from rich.cells import cell_len, set_cell_size

from . import context, sessionwords, teams, teamwords
from . import viewmodel as VM
from .actions import clip
from .launch import Mode, default_mode
from .palette import AMBER, BLUE, CYAN, GREEN, MUTED, RED, TEXT, mix
from .sessions import INTERRUPTED, Exit, Session, Status
from .sessionwords import TONE
from .teams import Message, TeamState
from .teamwords import Part
from .tokens import Tokens, human

Cell = list[Part]  # a cell's words; empty, an empty cell

COLUMNS = ("sel", "project", "title", "status", "model", "exit", "active", "mode")  # the keys, in order
LABELS = ("", "Project", "Title", "Status", "Model", "Context", "Last", "Open as")
LIVE_LABELS = ("", "Project", "Title", "State", "Model", "Kind", "Last activity", "ctx")
BASED = frozenset({"model"})  # the first part's style is the cell's (sessionwords.model_parts)
HEADING = "\x00"  # the keys of the rows that head the open sessions and the rest
TEAM_ROW = "\x01"  # ...and of the row that heads a team in OPEN NOW
PLACE = 18  # the Project column's words (a member's place in the tree too)
QUIET_SECONDS = 5 * 60  # busy this long with nothing written: the live view says 'quiet'
CONTEXT_BAR = 4  # cells in the bar that shows how full a context is
BADGE = 0.22  # a loud state's background: its own colour this much over black
SPINNING = {"busy", "working"}
STAR = "·✢✶✻✽✻✶✢"  # Claude Code's turning star, its frames; none of these are drawn as emoji
STILL_STAR = STAR[2]
PULSE = ("#3d0f0d", RED)  # what needs you breathes between these; still, it is halfway
UNTICKED, EMPTY_BAR = "#5a5a5a", "#2a2a2a"
CHIP_GAP = "  "
SHORTEST = 4  # a name on a team's row is never cut shorter; past that, "+2" stands for the last ones
STATUS_LABEL = {status: (label, TONE[tone]) for status, (label, tone) in VM.STATUS.items()}
# A team's members, counted on its row by what each does (VM.state_id), in this order.
TEAM_GLYPHS = {state: (glyph, TONE[tone]) for state, (glyph, tone) in VM.TEAM_GLYPHS.items()}


@dataclass(frozen=True)
class RowWords:
    """A row's cells, one a column in COLUMNS' order, and how it moves: 'spin', 'pulse' or ''."""

    cells: tuple[Cell, ...]
    motion: str = ""


def badge(style: str) -> str:
    """A loud state (a bold style) as a badge: its colour tinted in behind it. Quiet ones stay plain."""
    if not style.startswith("bold "):
        return style
    return f"{style} on {mix('#000000', style.split()[-1], BADGE)}"


def state_of(session: Session) -> tuple[str, str, int]:
    """What an open session is doing: (words, style, rank)."""
    words, tone, rank = VM.state_of(session)
    return words, TONE[tone], rank


def fit(parts: Iterable[Part], width: int) -> Cell:
    """Parts cut to a width on the screen as the terminal truncates a Text ('…' at the end): what is
    cut off goes, the '…' in the style of the part it cuts into."""
    parts = list(parts)
    plain = "".join(words for words, _ in parts)
    if cell_len(plain) <= width:
        return parts
    cut = set_cell_size(plain, width - 1) + "…"
    spans, at = [], 0
    for words, style in parts:  # where each styled part lies, as Text.assemble puts it
        if style and words:
            spans.append((at, at + len(words), style))
        at += len(words)
    end = len(cut)
    spans = [(start, min(stop, end) if stop >= end else stop, style) for start, stop, style in spans if start < end]
    out, at = [], 0
    for start, stop, style in spans:
        if start > at:
            out.append((cut[at:start], ""))
        out.append((cut[start:stop], style))
        at = stop
    if at < end:
        out.append((cut[at:], ""))
    return out


# --- a session's row -----------------------------------------------------------------------------


def tick(session: Session, selected: bool, accent: str) -> Cell:
    """The box Space ticks: none for one open in a terminal, nor for one in the background (open too,
    though Space ticks it to attach to it: a box would put it with the closed ones)."""
    if session.status is Status.RUNNING:
        return []
    if selected:
        return [("[x]", f"bold {accent}")]
    if session.status is Status.BACKGROUND:
        return []
    return [("[ ]", UNTICKED)]


def title(session: Session, *, pinned: bool, hidden: bool, width: int, style: str, accent: str) -> Cell:
    """Its title, a ★ before it pinned, 'hidden · ' while shown with X."""
    if pinned:
        return [("★ ", accent), (clip(session.title, width - 2), style)]
    if hidden:
        return [("hidden · ", MUTED), (clip(session.title, width - 9), f"{style} {MUTED}")]
    return [(clip(session.title, width), style)]


def status(session: Session, accent: str) -> tuple[Cell, str]:
    """The Status (or State) cell and how it moves: closed ones say how they ended, open ones what they
    are doing; loud ones as a badge."""
    if VM.at_limit(session):
        words, tone = VM.limit_words(session)
        return [(words, badge(TONE[tone]))], ""
    if session.exit is not Exit.RUNNING:
        label, style = STATUS_LABEL[session.status]
        return [(label, badge(style))], ""
    label, style, rank = state_of(session)
    kind = " · bg" if session.status is Status.BACKGROUND else ""
    if session.live_state.lower() in SPINNING:
        return [(f"{STILL_STAR} ", f"bold {accent}"), (f"{label}{kind}", f"bold {BLUE}")], "spin"
    if rank == 0:
        return [(f"! {label}{kind}", f"bold #ffffff on {mix(*PULSE, 0.5)}")], "pulse"
    return [(f"● {label}{kind}", style)], ""


def context_bar(session: Session, cap: int, default_cap: int) -> Cell:
    """'▰▰▰▱ 73%' (of its cap, for a session opened with one), amber from the point where a handover is
    worth it, red past 85%; nothing for one with no context to say (or not Claude)."""
    if not session.context or session.cli != "claude":
        return []
    share = context.share(session, cap)
    colour = RED if share >= 0.85 else AMBER if context.crowded(session, cap, default_cap) else MUTED
    filled = min(CONTEXT_BAR, round(share * CONTEXT_BAR))
    return [("▰" * filled, colour), ("▱" * (CONTEXT_BAR - filled), EMPTY_BAR), (f" {share:.0%}", colour)]


def process(session: Session, cap: int, default_cap: int) -> Cell:
    """The Context column: how full an open one's context is, how a closed one's process ended (the
    usual, clean, unsaid); other CLIs leave no record of that, so it names the CLI."""
    if session.cli != "claude":
        return [(session.cli, CYAN)]
    if session.exit is Exit.RUNNING:
        return context_bar(session, cap, default_cap)
    if session.exit is Exit.CLEAN:
        return []
    return [(session.exit.value, AMBER if session.exit is Exit.KILLED else MUTED)]


def model(session: Session, effort: str, shown: bool) -> Cell:
    """'opus 5.5·xhigh' (BASED); nothing where the screen has no room for the column."""
    return sessionwords.model_parts(session, effort, sessionwords.MODEL_ROOM) if shown else []


def session_row(session: Session, *, selected: bool, pinned: bool, hidden: bool, place: tuple[Cell, Cell] | None,
                effort: str, model_shown: bool, cap: int, default_cap: int, mode: Mode, armed: datetime | None,
                queued: bool, accent: str, width: int) -> RowWords:
    """A session's row in the picker's columns. place: a member's (place in its team's tree, its line)
    for the Project and Title cells (member_place, member_line); width: the Title column's room."""
    dim = "dim" if session.status is Status.RUNNING else ""  # open in a terminal: cannot be ticked
    if place is None:
        place = ([(clip(session.project, PLACE), dim)],
                 title(session, pinned=pinned, hidden=hidden, width=width, style=dim, accent=accent))
    said, motion = status(session, accent)
    return RowWords((tick(session, selected, accent), place[0], place[1], said, model(session, effort, model_shown),
                     process(session, cap, default_cap), [(VM.ago(session.last_active), MUTED)],
                     sessionwords.open_as(session, mode, armed=armed, queued=queued, accent=accent)), motion)


def live_row(session: Session, *, effort: str, model_shown: bool, cap: int, default_cap: int, width: int,
             accent: str, now: datetime | None = None) -> RowWords:
    """A session's row in the live view: '!' where it needs you, its kind, and 'quiet' for one busy a
    while with nothing written (a long command, or a prompt nobody has answered)."""
    rank = state_of(session)[2]
    last = [(VM.ago(session.last_active), "dim")]
    if rank == 2 and ((now or datetime.now()) - session.last_active).total_seconds() > QUIET_SECONDS:
        last = [(f"{VM.ago(session.last_active)} · quiet", "yellow")]
    said, motion = status(session, accent)
    return RowWords(([(" ! ", "bold red")] if rank == 0 else [], [(clip(session.project, PLACE), "")],
                     [(clip(session.title, width), "")], said, model(session, effort, model_shown),
                     [("background" if session.status is Status.BACKGROUND else "terminal", "dim")], last,
                     context_bar(session, cap, default_cap)), motion)


# --- teams ---------------------------------------------------------------------------------------


def member_place(state: TeamState, member: str, last: bool, style: str, accent: str) -> Cell:
    """A member's place in its team's tree, where the project goes: ★ for the lead, the branch for the
    others (└ for the last one listed), its name in the team's colour for it."""
    colours = teamwords.colours(state.team, accent)
    lead = state.team.lead is not None and member == state.team.lead.name
    branch = "★ " if lead else "└ " if last else "├ "
    return fit([(branch, f"bold {colours[member]}" if lead else MUTED),
                (member, f"bold {colours.get(member, TEXT)} {style}".strip())], PLACE)


def last_word(state: TeamState, member: str) -> Message | None:
    """The latest message to or from this member."""
    return next((m for m in reversed(state.said) if member in (m.sender, m.receiver)), None)


def member_line(state: TeamState, member: str, width: int, accent: str) -> Cell:
    """Where a member's title goes: the latest message to or from it ('→ dash: Heads-up…'), else its
    role, while nothing is said."""
    said = last_word(state, member)
    if said is not None:
        return fit(teamwords.message_parts(said, member, teamwords.colours(state.team, accent)), width)
    return fit([(next((m.focus for m in state.team.members if m.name == member), ""), MUTED)], width)


def turning(session: Session | None) -> bool:
    """Whether its star turns on its team's row: open, at work, not held at a limit."""
    return (session is not None and session.exit is Exit.RUNNING and not VM.at_limit(session)
            and session.live_state.lower() in SPINNING)


def chip(session: Session | None, star: str = STILL_STAR) -> tuple[str, str, str, int]:
    """A member's sign on its team's row, the sign's style, its name's, and how much it needs you (lowest
    first): red when it waits for you, the turning star (this frame) while it works; a closed one is
    quiet, with ▲ when it was cut off."""
    if session is None or session.exit is not Exit.RUNNING:
        if session is not None and session.status in INTERRUPTED:
            label, style = STATUS_LABEL[session.status]
            return label.split()[0], style, MUTED, 4
        return "", "", MUTED, 5
    if VM.at_limit(session):
        return "◐", AMBER, AMBER, 3
    raw = session.live_state.lower()
    _, style, rank = state_of(session)
    if turning(session):
        return star, f"bold {BLUE}", TEXT, rank
    if rank == 0:
        return "!", f"bold {RED}", f"bold {RED}", rank
    if rank == 1:
        return "●", RED, RED, rank
    if raw in ("shell", "aside"):
        return "◦", CYAN, TEXT, 3
    return "●", style, TEXT, rank


def chips(members: list[tuple[str, bool, str, str, str, int]], width: int, lead_style: str) -> Cell:
    """A team's members on its one row, each a sign of what it is doing and its name: '● ★lead  ✶ api
    ◦ ui'. members: (name, lead, sign, sign's style, name's style, rank: 0 needs you, 1 waits for you,
    then the quieter ones). Long names are cut until the line fits; when even that is not enough, the
    quietest are counted instead ('+2'), so the ones waiting for you stay named."""

    def drawn(most: int, keep: int) -> Cell:
        kept = sorted(range(len(members)), key=lambda i: (not members[i][1], members[i][5], i))[:keep]
        line: Cell = []
        for index, (name, lead, sign, sign_style, name_style, _) in enumerate(members):
            if index not in kept:
                continue
            if line:
                line.append((CHIP_GAP, ""))
            if sign:
                line += [(sign, sign_style), (" ", "")]
            if lead:
                line.append(("★", lead_style))
            line.append((name if len(name) <= most else name[: most - 1] + "…", name_style))
        if keep < len(members):  # red when one of those counted waits for you too
            waiting = any(m[5] <= 1 for i, m in enumerate(members) if i not in kept)
            line.append((f"{CHIP_GAP}+{len(members) - keep}", f"bold {RED}" if waiting else MUTED))
        return line

    def wide(line: Cell) -> int:
        return cell_len("".join(words for words, _ in line))

    longest = max((len(m[0]) for m in members), default=0)
    for most in range(max(longest, SHORTEST), SHORTEST - 1, -1):
        line = drawn(most, len(members))
        if wide(line) <= width:
            return line
    for keep in range(len(members) - 1, 0, -1):
        line = drawn(SHORTEST, keep)
        if wide(line) <= width:
            return line
    return drawn(SHORTEST, 1)


def team_row(state: TeamState, members: list[tuple[str, Session]], *, unfolded: bool, selected: Collection[str],
             tokens: int, width: int, accent: str, star: str = STILL_STAR) -> RowWords:
    """A team's row. Folded (as it starts): its members as chips, each with a sign of what it is doing.
    Unfolded, its members are listed under it, and the row says how many are open and how many messages
    went between them. Then what the open members are doing (or that it is closed), its tokens, and
    when it last spoke. members: its members in the list, each with its session as the list has it
    now (the lead first); tokens: what the team spent (TeamState.tokens)."""
    name = state.team.name
    opened = any(s.exit is Exit.RUNNING for _, s in members)
    if opened:
        mark = [("▾" if unfolded else "▸", f"bold {accent}")]
    else:  # closed: it opens as a team, so it is ticked as one
        closed = {s.session_id for _, s in members}
        ticked = closed & set(selected)
        mark = ([("[x]", f"bold {accent}")] if ticked == closed else [("[-]", f"bold {accent}")] if ticked
                else [("[ ]", UNTICKED)])
    heading = fit([("TEAM ", f"bold {MUTED}"), (name, f"bold {accent}")], PLACE)
    spinning = False
    if unfolded:
        said = len(state.between_members())
        messages = f"{said} message{'' if said == 1 else 's'}"
        opened_now, everyone = sum(s.exit is Exit.RUNNING for _, s in members), len(state.team.members)
        what = next((words for words in (f"{messages} · {opened_now} of {everyone} open",
                                         f"{messages} · {opened_now}/{everyone} open", messages)
                     if len(words) <= width), messages)
        line = [(clip(what, width), MUTED)]
    else:
        listed = dict(members)  # every member, in the team's order (the lead first)
        shown = [(member, listed.get(member.name) or state.sessions.get(member.name)) for member in state.team.ordered]
        line = chips([(member.name, member.lead, *chip(session, star)) for member, session in shown], width,
                     f"bold {accent}")
        spinning = any(turning(session) for _, session in shown)
    if opened:
        doing = Counter(VM.state_id(s) for _, s in members if s.exit is Exit.RUNNING)
        said_now: Cell = []
        order = list(TEAM_GLYPHS)
        for what in sorted(doing, key=lambda s: order.index(s) if s in TEAM_GLYPHS else len(order)):
            glyph, style = TEAM_GLYPHS.get(what, ("●", MUTED))
            said_now.append((f"{'  ' if said_now else ''}{glyph} {doing[what]}", style))
        last = VM.ago(state.said[-1].at) if state.said else ""
    else:
        stopped = sum(s.status in INTERRUPTED for _, s in members)
        said_now = [(f"▲ {stopped} to continue", badge(f"bold {AMBER}"))] if stopped else [("closed", MUTED)]
        last = VM.ago(max(s.last_active for _, s in members)) if members else ""
    return RowWords((mark, heading, line, said_now, [], [(human(tokens), MUTED)] if tokens else [],
                     [(last, MUTED)], []), "spin" if spinning else "")


# --- the rows ------------------------------------------------------------------------------------


def heading(group: str, items: list[Session], accent: str) -> RowWords:
    """The row over a group: OPEN NOW (counting the open ones: a team shows its closed members there
    too), ★ PINNED, EARLIER. Where the group's sessions are closed, the Context column says what it
    shows for them: how the process ended."""
    count = sum(s.exit is Exit.RUNNING for s in items) if group == "open" else len(items)
    words, style = {"open": ("OPEN NOW", f"bold {GREEN}"), "pinned": ("★ PINNED", f"bold {accent}"),
                    "rest": ("EARLIER", f"bold {TEXT}")}[group]
    closed = group == "rest" or (bool(items) and all(s.exit is not Exit.RUNNING for s in items))
    return RowWords(([], [(words, style), (f" · {count}", MUTED)], [], [], [],
                     [("process", MUTED)] if closed else [], [], []))


def rows(groups: list[tuple[str, list[Session]]], members: dict[str, tuple[str, str]],
         blocks: list[VM.Block], unfolded: Collection[str], live: bool) -> list[tuple[str, Session | None]]:
    """(key, session) of the list's rows (viewmodel.group's groups, members and blocks): the sessions
    shown, under a heading for each group when there is more than one (HEADING + the group), and each
    team in OPEN NOW under a row of its own (TEAM_ROW + its name) with its members (unless folded).
    Neither kind of heading has a session. The live view: the sessions alone."""
    if live:
        return [(s.session_id, s) for _, items in groups for s in items]
    out: list[tuple[str, Session | None]] = []
    in_blocks = {state.team.name: listed for state, listed in blocks}
    placed: set[str] = set()
    for name, items in groups:
        if len(groups) > 1:
            out.append((f"{HEADING}{name}", None))
        for session in items:
            team = members.get(session.session_id, ("", ""))[0]
            if not team:
                out.append((session.session_id, session))
            elif team not in placed:  # the team's row where its first member comes, once
                placed.add(team)
                out.append((f"{TEAM_ROW}{team}", None))
                if team in unfolded:
                    out += [(s.session_id, s) for _, s in in_blocks[team]]
    return out


# --- the whole list ------------------------------------------------------------------------------


def nobody(session: Session) -> bool:
    return False


@dataclass(frozen=True)
class Listing:
    """What the list is drawn from, as a front has it now: the sessions shown, grouped (viewmodel.group
    over them, teams_too unless live), every session known, the teams; and the front's own state."""

    grouped: VM.Groups
    sessions: Mapping[str, Session]  # every session known, as it is now (a team's row says its members' now)
    team_states: Mapping[str, TeamState]
    accent: str
    width: int  # the Title column's room
    model_shown: bool = True  # room for the Model column
    live: bool = False  # the live view: the open sessions alone, in its columns
    unfolded: Collection[str] = frozenset()  # teams whose members are listed under them
    selected: Collection[str] = frozenset()  # ticked session ids
    pinned: Collection[str] = frozenset()
    is_hidden: Callable[[Session], bool] = nobody  # out of sight (shown only while X shows them)
    modes: Mapping[str, Mode] = field(default_factory=dict)  # how one opens, where m changed it
    armed: Mapping[str, datetime] = field(default_factory=dict)  # c: continues when its limit lifts
    queued: Collection[str] = frozenset()  # h asked while it worked
    tokens: Mapping[str, Tokens] = field(default_factory=dict)  # each session over its whole life
    default_cap: int = 0  # the context cap new sessions get, in tokens
    star: str = STILL_STAR  # the frame of the turning star a folded team's row draws for a member at work


def keys(listing: Listing) -> list[tuple[str, Session | None]]:
    """(key, session) of the list's rows, in order (rows())."""
    grouped = listing.grouped
    return rows(grouped.groups, grouped.members, grouped.blocks, listing.unfolded, listing.live)


def team_members(listing: Listing, name: str) -> list[tuple[str, Session]]:
    """A team's members in the list, the lead first, each with its session as it is now."""
    listed = next((members for state, members in listing.grouped.blocks if state.team.name == name), [])
    return [(member, listing.sessions.get(s.session_id, s)) for member, s in listed]


def cap_of(listing: Listing, session: Session) -> int:
    """The context cap it runs under (teams.cap_of): its team's for a member, else what it was opened with."""
    found = listing.grouped.members.get(session.session_id)
    state = listing.team_states.get(found[0]) if found else None
    return teams.cap_of(session.session_id, state.team if state is not None else None)


def row(listing: Listing, key: str, session: Session | None) -> RowWords:
    """The words of one of the list's rows (keys()): a session's, a team's or a heading's."""
    accent, width = listing.accent, listing.width
    if session is not None:
        sid = session.session_id
        effort = VM.effort_of(session, sid in listing.grouped.members)
        cap = cap_of(listing, session)
        if listing.live:
            return live_row(session, effort=effort, model_shown=listing.model_shown, cap=cap,
                            default_cap=listing.default_cap, width=width, accent=accent)
        place = None
        if sid in listing.grouped.members:  # in a team's tree
            team, member = listing.grouped.members[sid]
            state = listing.team_states[team]
            listed = team_members(listing, team)
            style = "" if session.exit is Exit.RUNNING else "dim"
            place = (member_place(state, member, bool(listed) and listed[-1][0] == member, style, accent),
                     member_line(state, member, width, accent))
        return session_row(session, selected=sid in listing.selected, pinned=sid in listing.pinned,
                           hidden=listing.is_hidden(session), place=place, effort=effort,
                           model_shown=listing.model_shown, cap=cap, default_cap=listing.default_cap,
                           mode=listing.modes.get(sid) or default_mode(session.status), armed=listing.armed.get(sid),
                           queued=sid in listing.queued, accent=accent, width=width)
    if key.startswith(TEAM_ROW):
        name = key.removeprefix(TEAM_ROW)
        state = listing.team_states.get(name)
        if state is None:  # gone since the rows were made
            return RowWords(tuple([] for _ in COLUMNS))
        return team_row(state, team_members(listing, name), unfolded=name in listing.unfolded,
                        selected=listing.selected, tokens=state.tokens(dict(listing.tokens)), width=width,
                        accent=accent, star=listing.star)
    group = key.removeprefix(HEADING)
    return heading(group, dict(listing.grouped.groups).get(group, []), accent)
