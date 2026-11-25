"""What the dashboard says of one session, with no UI behind it: its Model and Opens-as columns, what
Enter does, how it came out, and its preview (status, what was said, the facts beside it). Each comes as
parts, (words, style), as in teamwords: the style a colour from palette.py with 'bold ' before it where
the words stand out, '' for none. A Cell is parts, or a str said as it is. A Row is (label, cell); a
row with the label '' goes on from the one above. The terminal dashboard (app) puts them into rich
Text, the desktop window into its own; both say the same.

A style is '' or words from a closed set, joined by spaces: one colour (from palette.py, or the accent
passed in) and any of bold, dim, italic, reverse ('bold reverse {accent}' marks the filter's words,
'dim italic' a summary being written). A front maps exactly these; a new word needs a line in each.

detail() is the whole preview of a session as one value (DetailView), from plain inputs (DetailInputs)
that read_inputs() reads off the state each front holds."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from . import actions, agy, clis, context, picks, summarize, teams, teamwords
from . import viewmodel as VM
from .actions import clip
from .gitstate import GitState
from .launch import Launch, Mode, default_mode
from .palette import AMBER, BLUE, CYAN, GREEN, MUTED, RED, TEXT
from .sessions import Exit, Session, Status, folder_key
from .teamwords import Part, message_parts
from .tokens import Tokens, duration, human

Cell = str | list[Part]
Row = tuple[str, Cell]

# The view model's tones as styles: quiet for what needs nothing from you, loud for what does. Amber
# is what TO CONTINUE counts, red what NEED YOU counts (viewmodel says it all).
TONE = {"your-turn!": f"bold {RED}", "your-turn": RED, "working": BLUE, "open": GREEN, "aside": CYAN,
        "to-continue!": f"bold {AMBER}", "to-continue": AMBER, "done": MUTED, "plain": ""}
MODE_LABEL = {Mode.RESUME: "resume", Mode.AUTO: "▶ auto", Mode.FORK: "fork", Mode.NEW: "new",
              Mode.CONTINUE: "continue"}
MODEL_ROOM = 16  # the Model column's words ('sonnet 5.5·xhigh')
EFFORT_SHORT = {"medium": "med"}  # as the Model column says an effort


def home_short(path: str) -> str:
    """A folder with the home folder written ~, so it fits beside the preview."""
    home = str(Path.home())
    return "~" + path[len(home):] if path and path.lower().startswith(home.lower()) else path


def agent(session: Session) -> str:
    """Claude, Codex, Antigravity: who answers in it."""
    return clis.label(session.cli).split()[0]


def model_parts(session: Session, effort: str, room: int = MODEL_ROOM) -> list[Part]:
    """'opus 5.5·xhigh': the model of its last reply, with its version, and the effort it ran at;
    quiet once closed. An amber ↓ after the model: another took over by itself (Session.fell_back), and
    the effort makes room for it where both do not fit. [] when there is no model to say. The first
    part's style is the whole cell's (the terminal makes it the Text's own)."""
    if not session.model or session.model.startswith("<"):
        return []
    # An agy model's name holds its level ('Hydrogen (High)'): no effort comes after it.
    model = context.short_model(session.model, room if agy.label(session.model) else room - 4)
    fell = bool(session.fell_back) and session.exit is Exit.RUNNING
    out: list[Part] = [(model, (AMBER if fell else TEXT) if session.exit is Exit.RUNNING else MUTED)]
    if fell:
        out.append(("↓", f"bold {AMBER}"))
    word = f"·{EFFORT_SHORT.get(effort, effort)}" if effort else ""
    if word and sum(len(words) for words, _ in out) + len(word) <= room:
        out.append((word, MUTED))
    return out


def open_as(session: Session, mode: Mode, *, armed: datetime | None, queued: bool, accent: str,
            plain: bool = False) -> list[Part]:
    """How it opens: armed (c) to go on when its limit lifts, queued (h on a busy one) to be handed
    over, else its mode; in the list only when not the plain resume (plain=True says it anyway)."""
    if armed is not None:
        return [(f"▶ at {VM.lifts(armed)}", f"bold {accent}")]
    if queued:
        return [("▶ hand over", f"bold {accent}")]
    if session.status is Status.RUNNING:
        return [("", "")]
    if mode is Mode.RESUME and not plain:
        return [("", "")]
    return [(MODE_LABEL[mode], f"bold {accent}" if mode is Mode.AUTO else MUTED)]


def enter_words(session: Session, launch: Callable[[], Launch], selected: Collection[str]) -> str:
    """What Enter does with the highlighted session, as it stands: ticked ones go first. launch: how
    it would open (only asked when that is what Enter does)."""
    if selected:
        count = len(selected)
        ticked = f"opens the {count} ticked session{'' if count == 1 else 's'}, each in a tab"
        return ticked if session.session_id in selected else ticked + " (not this one: Space ticks it)"
    if session.status is Status.BACKGROUND:
        return "attaches to it in a new tab"
    if session.exit is Exit.RUNNING:
        return "goes to its tab, to type there"
    how = launch()
    words = {Mode.RESUME: "resumes it in a new tab",
             Mode.AUTO: f"resumes it in a new tab and tells {agent(session)} to carry on",
             Mode.FORK: "forks it: a new session in a new tab, from a copy of its history",
             Mode.NEW: "starts a fresh session in its folder"}.get(how.mode, "opens it in a new tab")
    if how.options:
        team, _, member = how.title.partition(": ")
        words += f", as {member} of the team {team}"
    return words + " · m changes how"


def outcome(session: Session, summary: str | None) -> tuple[list[Part], str]:
    """A heading and a few words on what the session came to (shown when a row is opened). summary:
    the one written for it (s), if any."""
    if session.exit is Exit.RUNNING:
        heading, style = f"Open now · {VM.state_of(session)[0]}", GREEN
    else:
        heading, style = VM.OUTCOME.get(session.status, "Session"), TONE[VM.STATUS[session.status][1]]
    if session.status is Status.DONE or session.exit is Exit.RUNNING:
        body = summary or session.last_reply or session.last_prompt
    elif session.status in (Status.CUT_OFF, Status.UNANSWERED, Status.LIMIT, Status.DROPPED):
        body = f"Last asked: {session.last_prompt or session.first_prompt}"
    else:
        body = summary or session.last_reply or session.first_prompt
    title = [(heading, f"bold {style}"), (f"  ·  {VM.ago(session.last_active)} ago", MUTED)]
    return title, " ".join((body or "").split())


def status_row(session: Session) -> tuple[list[Part], str]:
    """The status as the list says it, and what it means beside it."""
    label, tone = VM.STATUS[session.status] if session.exit is not Exit.RUNNING else VM.state_of(session)[:2]
    if VM.at_limit(session):
        words, loud = VM.limit_words(session)
        return [(words, TONE[loud])], VM.limit_help(session)
    return [(label, TONE[tone])], VM.STATUS_HELP[session.status].replace("Claude", agent(session))


def messages(said: list[teams.Message], member: str, colours: dict[str, str]) -> list[Part]:
    """'3 in · 2 out  ·  2m ago  ← lead: Heads-up…' for a member's own preview. The first part's
    style is the whole line's."""
    mine = [m for m in said if member in (m.sender, m.receiver)]
    got = sum(m.receiver == member for m in mine)
    out: list[Part] = [(f"{got} in · {len(mine) - got} out", TEXT)]
    if mine:
        out.append((f"  ·  {VM.ago(mine[-1].at)} ago  ", MUTED))
        out.extend(message_parts(mine[-1], member, colours))
    return out


def said_rows(session: Session, *, messages: list[Part] | None = None,
              found: tuple[str, int, int] | None = None, summary: dict | None = None,
              summarizing: bool = False, summary_model: str = "", room: int = 60, more: int = 1,
              accent: str = "") -> list[Row]:
    """What was said in it, each cut to a few lines of the room there is: a member's messages, where
    the filter's words were found (found: (piece, start, end) of them), its summary, the first and last
    prompt, the last reply, the files edited. more: how many times as much of each (beside the list)."""
    rows: list[Row] = []
    if messages is not None:
        rows.append(("Messages", messages))
    if found is not None and found[0]:
        piece, start, end = found
        rows.append(("Found", [(piece[:start], ""), (piece[start:end], f"bold reverse {accent}"), (piece[end:], "")]))
    if summarizing:
        rows.append(("Summary", [(f"asking Claude ({summary_model})…", "dim italic")]))
    elif summary:
        words: list[Part] = [(summary.get("summary", ""), "")]
        if summary.get("size") != session.size:
            words.append(("  (written before the latest activity; s refreshes it)", "dim"))
        rows.append(("Summary", words))
    rows.append(("First prompt", clip(session.first_prompt, more * min(220, 3 * room))))
    if session.last_prompt and session.last_prompt != session.first_prompt:
        rows.append(("Last prompt", clip(session.last_prompt, more * min(220, 2 * room))))
    if session.last_reply:
        rows.append((agent(session), clip(session.last_reply, more * min(320, 4 * room))))
    if session.files:
        rows.append(("Files edited", clip(", ".join(session.files), more * 2 * room)))
    return rows


def fill_parts(session: Session, timed: bool = True, cap: int = 0, now: datetime | None = None) -> list[Part]:
    """'917K of 1M (92%) · going for 6h 12m', or '240K of a 300K cap (80%)' for a session opened with a
    context cap, coloured by how full it is (without the time, where it has a line of its own)."""
    if not session.context:
        return [("context size not known yet", MUTED)]
    share = context.share(session, cap)
    colour = GREEN if share < context.WARN_SHARE else AMBER if share < 0.85 else RED
    whole = f"a {human(cap)} cap" if cap else human(context.window(session.model, session.context))
    out: list[Part] = [(f"{human(session.context)} of {whole} ", TEXT), (f"({share:.0%})", f"bold {colour}")]
    if not timed:
        return out
    if session.started and session.exit is Exit.RUNNING:
        took = (now or datetime.now()) - session.started
        out.append((f" · going for {duration(took.total_seconds())}", MUTED))
    elif session.started:
        out.append((f" · ran for {duration((session.last_active - session.started).total_seconds())}", MUTED))
    return out


def fact_rows(session: Session, *, member: tuple[str, str] | None, effort: str, git: GitState | None,
              tokens: Tokens | None, cap: int, default_cap: int, launch: Callable[[], Launch], mode: Mode,
              armed: datetime | None, queued: bool, accent: str, now: datetime | None = None) -> list[Row]:
    """The facts beside the preview: its folder, team, CLI or model, branch and git, when it ran, its
    lines, tokens and context, how its process ended and how it opens (with the command line).
    member: (team, member) for a team member's session; launch: how it would open (asked when closed)."""
    rows: list[Row] = [("Folder", [(home_short(session.cwd) or "(none recorded)", TEXT)])]
    if member is not None:
        rows.append(("Team", [(member[0], f"bold {accent}"), (f" · {member[1]}", TEXT)]))
    if session.cli != "claude":
        rows.append(("CLI", [(clis.label(session.cli), CYAN)]))
    elif model := picks.model_name(session.model):
        rows.append(("Model", [(model, TEXT), (f" · {effort} effort" if effort else "", MUTED)]))
        if session.fell_back and session.exit is Exit.RUNNING:
            rows.append(("Fell back", [(f"{session.fell_back}: /model in its tab picks yours again", AMBER)]))
    if session.git_branch:
        rows.append(("Branch", [(f"⎇ {session.git_branch}", CYAN)]))
    if git is not None:  # the folder as it is now, whatever the session left
        rows.append(("Git", [(git.words(), AMBER if git.changed or git.ahead else MUTED)]))
    if session.started:
        rows.append(("Started", f"{session.started:%d %b %H:%M}"))
    rows.append(("Last active", f"{session.last_active:%d %b %H:%M}  ({VM.ago(session.last_active)} ago)"))
    if session.started:
        running = session.exit is Exit.RUNNING
        took: timedelta = ((now or datetime.now()) if running else session.last_active) - session.started
        rows.append(("Going for" if running else "Ran for", duration(took.total_seconds())))
    if session.lines_added is not None:
        rows.append(("Lines", [(f"+{session.lines_added}", GREEN), ("  ", ""), (f"−{session.lines_removed or 0}", RED)]))
    if tokens is not None and tokens.total:
        rows.append(("Tokens", [(human(tokens.total), TEXT), (f"  ·  {human(tokens.output)} out", MUTED)]))
    if session.context and session.cli == "claude":
        rows.append(("Context", fill_parts(session, timed=False, cap=cap)))
        if context.cold(session):  # the cache is gone: the next message pays for all of it again
            gone = f"cache gone after an hour: the next message writes all {human(session.context)} of it again"
            rows.append(("", [(gone, AMBER)]))
        if context.crowded(session, cap, default_cap):  # the two ways on: the same work, or a new phase
            rows.append(("", [("/compact", f"bold {AMBER}"), (": go on · ", AMBER), ("h", f"bold {AMBER}"),
                              (": new phase", AMBER)]))
    if session.exit in (Exit.CLEAN, Exit.KILLED):
        rows.append(("Process", [(session.exit.value, AMBER if session.exit is Exit.KILLED else TEXT),
                                 (f"  {VM.EXIT_HELP[session.exit]}", MUTED)]))
    if session.status is not Status.RUNNING:
        how = launch()
        rows.append(("Opens as", open_as(session, mode, armed=armed, queued=queued, accent=accent, plain=True)))
        try:
            argv = replace(how, options=()).argv  # a member's flags are long: said in words below
        except agy.NoModel as why:  # agy's chain all out of quota: it would not open
            rows.append(("", [(str(why), AMBER)]))
        else:
            if how.mode is Mode.AUTO:  # the prompt is long; its start is enough here
                argv = [*argv[:-1], clip(argv[-1], 28)]
            rows.append(("", [(subprocess.list2cmdline(argv), MUTED)]))
        if how.options:
            rows.append(("", [(f"as {how.title}, with its role and the team's messages let in", MUTED)]))
    return rows


# --- the whole preview, as one value ------------------------------------------------------------------


@dataclass(frozen=True)
class DetailInputs:
    """What a session's preview says beyond the session itself, as plain data: the terminal dashboard
    reads it off its own state (app.detail_inputs), the desktop window off its snapshot."""

    launch: Callable[[], Launch]  # how it would open, asked only where that is said (may raise agy.NoModel)
    mode: Mode  # how it opens as m set it
    member: tuple[str, str] | None = None  # (team, member) for a team member's session
    messages: list[Part] | None = None  # a member's (messages()), one line; None for a session in no team
    effort: str = ""  # the effort it runs at ('' for none known, or not Claude)
    git: GitState | None = None  # its folder's git now
    tokens: Tokens | None = None  # over its whole life
    cap: int = 0  # the context cap it runs under (0: none, or not Claude)
    default_cap: int = 0  # the one new sessions get
    armed: datetime | None = None  # c: goes on when its limit lifts
    queued: bool = False  # h on a busy one: handed over once it is free
    selected: Collection[str] = frozenset()  # the ticked sessions' ids: Enter opens them
    found: tuple[str, int, int] | None = None  # where the filter's words were said: (piece, start, end)
    summary: dict | None = None  # the one written for it (s)
    summarizing: bool = False  # one is being written
    summary_model: str = ""  # by this model
    room: int = 60  # the room for what was said, in characters: each is cut to a few lines of it
    more: int = 1  # how many times as much of each (beside the list: 3)
    accent: str = ""  # the theme's accent colour
    now: datetime | None = None  # the clock, for how long it has been going


@dataclass(frozen=True)
class DetailView:
    """A session's preview: its title (bold, in TEXT), its status with what that means beside it (in
    MUTED), what Enter does, what was said in it, and the facts beside them."""

    head: str
    status: list[Part]
    means: str
    enter: list[Part]  # '⏎ resumes it in a new tab · m changes how', in the accent
    said: list[Row]  # said_rows(): a 'Messages' row is one line, for the front to cut to its room
    facts: list[Row]  # fact_rows()


def read_inputs(session: Session, *, members: dict[str, tuple[str, str]], team_states: dict[str, teams.TeamState],
                git: dict[str, GitState], session_tokens: dict[str, Tokens], background: dict[str, dict],
                armed: dict[str, datetime], queued: Collection[str], modes: dict[str, Mode], default_cap: int,
                accent: str, selected: Collection[str] = frozenset(), summaries: dict[str, dict] | None = None,
                summarizing: Collection[str] = frozenset(), found: tuple[str, int, int] | None = None,
                room: int = 60, more: int = 1, said: bool = True) -> DetailInputs:
    """A session's preview inputs, read off what a front holds: members, session id -> (team, member)
    for the sessions listed as a team's; git by folder key; background, the background sessions' jobs
    (Enter attaches to one); modes, how m set each (else its status's default); found, where the
    filter's words were said (the front's search finds them). said=False: only what the facts need."""
    sid = session.session_id
    claude = session.cli == "claude"
    member = members.get(sid)
    mode = modes.get(sid) or default_mode(session.status)
    job = background.get(sid) if session.status is Status.BACKGROUND else None
    parts = None
    if said and member is not None:
        state = team_states[member[0]]
        parts = messages(state.said, member[1], teamwords.colours(state.team, accent))
    cap = 0
    if session.context and claude:
        state = team_states.get(member[0]) if member else None
        cap = teams.cap_of(sid, state.team if state is not None else None)
    return DetailInputs(
        launch=lambda: actions.launch_for(session, mode, job), mode=mode, member=member, messages=parts,
        effort=VM.effort_of(session, member is not None) if claude else "",
        git=git.get(folder_key(session.cwd)), tokens=session_tokens.get(sid), cap=cap, default_cap=default_cap,
        armed=armed.get(sid), queued=sid in queued, selected=selected, found=found if said else None,
        summary=(summaries or {}).get(sid) if said else None, summarizing=said and sid in summarizing,
        summary_model=summarize.MODEL, room=room, more=more, accent=accent)


def facts(session: Session, inputs: DetailInputs) -> list[Row]:
    """The facts beside the preview (fact_rows), from the inputs."""
    return fact_rows(session, member=inputs.member, effort=inputs.effort, git=inputs.git, tokens=inputs.tokens,
                     cap=inputs.cap, default_cap=inputs.default_cap, launch=inputs.launch, mode=inputs.mode,
                     armed=inputs.armed, queued=inputs.queued, accent=inputs.accent, now=inputs.now)


def detail(session: Session, inputs: DetailInputs) -> DetailView:
    """A session's whole preview, as both fronts show it."""
    status, means = status_row(session)
    enter = [("⏎ ", f"bold {inputs.accent}"),
             (enter_words(session, inputs.launch, inputs.selected), inputs.accent)]
    said = said_rows(session, messages=inputs.messages, found=inputs.found, summary=inputs.summary,
                     summarizing=inputs.summarizing, summary_model=inputs.summary_model, room=inputs.room,
                     more=inputs.more, accent=inputs.accent)
    return DetailView(session.title, status, means, enter, said, facts(session, inputs))
