"""The `n` form's rules without a screen (G3 slice 5): `tabmancer new`, the dashboard's dialog
(newsessions.NewSessions) and a desktop window's ask the same things here.

Which rows go with which CLI, agy's model row (its chain: never a Claude model through agy), the
model, effort, context and advisor picked, what the router suggests and how it is taken, when the
lines can be linked as a team of peers, what is checked, and the sessions it opens. Words come back
as plain strings, a line in several looks as (text, tone) parts: the front end draws them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from . import agy, clis, improve, picks, router, teams, workspaces
from .launch import Launch, Mode
from .sessions import Session, folder_key


@dataclass
class Project:
    cwd: str
    sessions: int = 0
    last: datetime | None = None
    used: set[str] = field(default_factory=set)  # the CLIs it was worked on with

    @property
    def name(self) -> str:
        return Path(self.cwd).name or self.cwd


def projects(sessions: list[Session]) -> list[Project]:
    """Every folder that has sessions or sits in a workspace, most recently used first."""
    found: dict[str, Project] = {}
    for session in sessions:
        if not session.cwd:
            continue
        project = found.setdefault(folder_key(session.cwd), Project(session.cwd))
        project.sessions += 1
        project.used.add(session.cli)
        if project.last is None or session.last_active > project.last:
            project.last = session.last_active
    for workspace in workspaces.load().values():
        for entry in workspace.entries:
            found.setdefault(folder_key(entry.cwd), Project(entry.cwd))
    alive = [p for p in found.values() if Path(p.cwd).is_dir()]
    return sorted(alive, key=lambda p: (p.last is None, -(p.last.timestamp() if p.last else 0), p.name.casefold()))


def match(name: str, known: list[Project]) -> Project | None:
    """A folder path, or the name of a recent project (its most recent folder of that name)."""
    path = Path(os.path.expanduser(name))
    if path.is_dir():
        return Project(str(path.resolve()))
    return next((p for p in known if p.name.casefold() == name.casefold()), None)


def topics(message: str | None) -> list[str]:
    """The first messages written, one a line: each line its own session."""
    return [" ".join(line.split()) for line in (message or "").split("\n") if line.strip()]


def launches(folders: list[str], cli: str, prompt: str | None, pick: picks.Pick | None = None) -> list[Launch]:
    """A fresh session in each folder for each line of the message (one, with no message); Claude
    Code ones with the model and effort picked (picks.fresh). A tab is titled by its line."""
    out = []
    for cwd in folders:
        for topic in topics(prompt) or [None]:
            about = topic if topic is None or len(topic) <= 32 else topic[:31] + "…"
            out.append(picks.fresh(Launch(title=f"{Path(cwd).name or cwd}: {about or 'new'}", cwd=cwd, mode=Mode.NEW,
                                          session_id="", prompt=topic, cli=cli), pick or picks.Pick()))
    return out


ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


def linked_team(folder: str, message: str, pick: picks.Pick, taken: set[str]) -> teams.Team:
    """The lines of the message as a team of peers in one folder: a line "name: topic" is a member of
    that name, any other line one named first, second... in its order."""
    members: list[teams.Member] = []
    for index, line in enumerate(topics(message)):
        named = teams.parse_members(line) if teams.MEMBER_LINE.match(line) else []
        member = named[0] if named else teams.Member(ORDINALS[index % len(ORDINALS)], line)
        while member.name in {m.name for m in members}:
            member = replace(member, name=f"{member.name}{index + 1}")
        members.append(replace(member, model=member.model or pick.model, effort=member.effort or pick.effort,
                               advisor=pick.advisor if picks.advisor_ok(member.model or pick.model, pick.advisor)
                               else ""))
    # Cleaned as new_team will clean it: cut to 18, "my-shop-frontends-" became "my-shop-frontends"
    # there, a name taken already, and the second linked team was saved over the first.
    base = teams.clean(teams.clean(Path(folder).name)[:18]) or "team"
    name = next(n for n in (teams.clean(base if k == 1 else f"{base}-{k}") for k in range(1, 100)) if n not in taken)
    goal = "; ".join(m.focus for m in members)
    return teams.new_team(name, folder, goal, members, peers=True, cap=pick.cap)


@dataclass
class Request:
    """What the dialog asks for: fresh sessions in these folders, or (linked) a team of peers."""
    folders: list[str]
    cli: str
    message: str = ""
    pick: picks.Pick = field(default_factory=picks.Pick)
    linked: bool = False


def default_cli() -> str:
    """The main CLI (clis.main_cli) if it is on, else Claude Code if it is on, else whichever is on."""
    on = clis.enabled()
    main = clis.main_cli()
    return main if main in on else "claude" if "claude" in on else on[0]


def next_cli(cli: str) -> str:
    """Ctrl+O: the next CLI that is on."""
    on = clis.enabled()
    return on[(on.index(cli) + 1) % len(on)] if cli in on else on[0]


# --- the rows: model, agy's model, effort, context, advisor --------------------------------------

SHORT = {picks.DEFAULT: "default"}  # "your default" as the Model and Effort rows show it
CAP_SHORT = {picks.NO_CAP: "Claude's own"}  # no cap, as the Context row shows it
ADVISOR_SHORT = {picks.NO_ADVISOR: "no advisor"}  # as the Advisor row shows it
AGY_CHAIN = "chain"  # agy's Model row: the chain decides by quota (agy.pick_model)
AGY_SHORT = {AGY_CHAIN: "chain", **{model: agy.name(model).lower() for model in agy.DEFAULT_CHAIN}}

ROWS = ("model", "agy-model", "effort", "cap", "advisor")


@dataclass
class Row:
    """A row of choices as the form draws it: its values, their labels and the one it starts at."""

    name: str
    values: list[str]
    labels: dict[str, str]
    value: str


def choices() -> list[Row]:
    """The rows: the model above, agy's own in its place (its chain, no Claude model: agy.resolve), the
    effort, the context cap and the advisor."""
    chain = agy.chain()
    return [Row("model", [picks.DEFAULT, *picks.MODELS], SHORT, picks.DEFAULT),
            Row("agy-model", [AGY_CHAIN, *chain], {**AGY_SHORT, **{m: agy.name(m).lower() for m in chain}}, AGY_CHAIN),
            Row("effort", [picks.DEFAULT, *picks.EFFORTS], SHORT, picks.DEFAULT),
            Row("cap", [*picks.CAPS, picks.NO_CAP], CAP_SHORT, picks.default_cap()),
            Row("advisor", [picks.NO_ADVISOR, *picks.ADVISORS], ADVISOR_SHORT, picks.NO_ADVISOR)]


def rows(cli: str) -> dict[str, tuple[bool, bool]]:
    """Each row for this CLI as (shown, on): Claude Code's are on for Claude Code only; agy's model row
    stands in the model's place for agy, on; for another CLI every row is off (its own settings)."""
    on_agy = cli == "agy"
    return {"model": (not on_agy, cli == "claude"), "agy-model": (on_agy, True),
            "effort": (True, cli == "claude"), "cap": (True, cli == "claude"), "advisor": (True, cli == "claude")}


def agy_model(value: str) -> tuple[str, str]:
    """The model agy's sessions get from its row, and why (agy.resolve: never a Claude one, Neon then
    Hydrogen by quota for the chain; none when both are out)."""
    return agy.resolve("" if value == AGY_CHAIN else value)


def pick_of(cli: str, values: dict[str, str]) -> picks.Pick:
    """What the rows say (`values` by row name); for agy its model row only; nothing for other CLIs."""
    if cli == "agy":
        return picks.Pick(agy_model(values["agy-model"])[0])
    if cli != "claude":
        return picks.Pick()
    model, effort, cap, advisor = (values[row] for row in ("model", "effort", "cap", "advisor"))
    model = "" if model == picks.DEFAULT else model
    advisor = advisor if advisor != picks.NO_ADVISOR and picks.advisor_ok(model, advisor) else ""
    return picks.Pick(model, "" if effort == picks.DEFAULT else effort, cap, advisor)


def pick_parts(pick: picks.Pick) -> list[str]:
    """The pick as the card of what opens says it: 'sonnet · high', '300k context'."""
    return [part for part in (pick.words(), f"{pick.cap} context" if picks.cap_tokens(pick.cap) else "",
                              f"{pick.advisor} advisor" if pick.advisor else "") if part]


def pick_words(cli: str, values: dict[str, str], focused: str | None = None) -> str:
    """A line on the row in focus (`focused`, a row's name), or on what is picked: what it means for
    the sessions."""
    if cli == "agy":
        chosen = values["agy-model"]
        model, why = agy_model(chosen)
        about = agy.chain_line() if chosen == AGY_CHAIN else f"{agy.name(chosen).lower()}, {agy.chain_words(chosen)}"
        now = f"Now: {agy.name(model)}" + (f" ({why})" if why else "") if model else f"Now: none, {why}"
        return f"{about}. {now}."
    if cli != "claude":
        return f"Model and effort are for Claude Code sessions; {clis.label(cli)} uses its own settings."
    model, effort, cap, advisor = (values[row] for row in ("model", "effort", "cap", "advisor"))
    shown_model = "" if model == picks.DEFAULT else model
    if advisor != picks.NO_ADVISOR and not picks.advisor_ok(shown_model, advisor):
        return f"{advisor} cannot advise {shown_model}: only a model as strong or stronger can, so none is given."
    if focused == "advisor":
        return f"{ADVISOR_SHORT.get(advisor, advisor)}: {picks.ADVISOR_WORDS[advisor]}. For these sessions only."
    if focused == "cap":
        return f"{CAP_SHORT.get(cap, cap)}: {picks.cap_words(cap)}"
    if focused == "effort" or (focused != "model" and effort != picks.DEFAULT and model == picks.DEFAULT):
        return f"{effort}: {picks.EFFORT_WORDS[effort]}"
    if focused == "model" or model != picks.DEFAULT:
        return f"{model}: {picks.MODEL_WORDS[model]}"
    return "default: your Claude Code settings pick them."


HELP = {
    "message": "Enter: a line more, a session each in every folder ticked. Ctrl+L links them; Ctrl+E improves them.",
    "choice": ("←/→ picks. Ctrl+R: the router's pick for the first message (write it first). Left at default: "
               "your Claude Code settings decide."),
    "folders": "Space ticks a folder. Type to filter by name or path (a / or a \\ alike); a folder path works too.",
}


def help_words(focus: str) -> str:
    """One line under the cards: how to use the box in focus ('message', 'choice', else the folders)."""
    return HELP.get(focus, HELP["folders"])


# --- what opens ----------------------------------------------------------------------------------

def can_link(cli: str, message: str, ticked: int) -> str:
    """Why the lines cannot be linked as a team of peers now; empty when they can."""
    if cli != "claude":
        return "linked sessions are Claude Code's"
    if len(topics(message)) < 2:
        return "write two lines or more"
    if ticked != 1:
        return "tick one folder"
    return ""


@dataclass
class Opens:
    """What will open: the card's title and a (folder, line) row for each tab ('' for no message)."""

    title: str
    rows: list[tuple[str, str]]


def opens(chosen: list[str], message: str, linked: bool) -> Opens:
    """The tabs that will open, a folder and its message each. `linked`: as a team of peers (can_link
    said yes)."""
    lines = topics(message)
    if not chosen:
        return Opens("Opens nothing yet", [])
    count = len(chosen) * max(1, len(lines))
    return Opens(f"Opens {count} tab{'s' * (count != 1)}" + (", linked" if linked else ""),
                 [(Path(cwd).name or cwd, line) for cwd in chosen for line in (lines or [""])])


def request(chosen: list[str], cli: str, message: str, pick: picks.Pick, linked: bool) -> Request:
    """What the form starts: its lines tidied, linked only when they can be."""
    return Request(chosen, cli, "\n".join(topics(message)), pick,
                   linked=linked and not can_link(cli, message, len(chosen)))


@dataclass
class Built:
    """The sessions a request opens; `team`, for linked lines, the team of peers saved for them."""

    launches: list[Launch]
    team: teams.Team | None = None


def build(asked: Request) -> Built:
    """A request's sessions: fresh ones in each folder for each line, or, linked, the lines as a team
    of peers in the one folder, saved (teams.save) and started."""
    if not asked.linked:
        return Built(launches(asked.folders, asked.cli, asked.message, asked.pick))
    saved = teams.load()
    team = linked_team(asked.folders[0], asked.message, asked.pick, set(saved))
    saved[team.name] = team
    teams.save(saved)
    return Built(teams.start(team, {}), team)


def fresh_problem(names: list[str], prompt: str | None, cli: str, pick: picks.Pick | None, linked: bool) -> str:
    """Why `tabmancer new` will not start these; empty when it will."""
    if pick and cli != "claude":
        return "--model, --effort, --autocompact and --advisor are for Claude Code sessions."
    if pick and not picks.advisor_ok(pick.model, pick.advisor):
        return f"{pick.advisor} cannot advise {pick.model}: only a model as strong or stronger can."
    if linked and (cli != "claude" or len(names) != 1 or len(topics(prompt)) < 2):
        return "--linked takes one folder, two --prompt or more, and Claude Code."
    return ""


def folders_for(names: list[str], known: list[Project]) -> tuple[list[str], list[str]]:
    """The folders named (a path, or a recent project's name), and the names that are neither."""
    folders, missing = [], []
    for name in names:
        project = match(name, known)
        if project:
            folders.append(project.cwd)
        else:
            missing.append(name)
    return folders, missing


# --- the router and Ctrl+E -----------------------------------------------------------------------

def route_key(message: str) -> str:
    """The first message as the router is asked about it: its lines, each one tidied."""
    return "\n".join(topics(message))


def route_refusal(cli: str, message: str) -> tuple[str, float | None] | None:
    """Why Ctrl+R asks the router nothing, as (words, seconds); None when it asks."""
    if cli not in router.CLIS:
        return (f"The router picks for Claude Code and Antigravity sessions, not {clis.label(cli)}: Ctrl+O to "
                "another CLI first."), None
    if not router.worth_routing(route_key(message)):
        return ("Ctrl+R needs the first message: write what the session is to do (3 words or more), and the "
                "router picks for it."), 6
    return None


def route_take(route: router.Route) -> dict[str, str]:
    """The router's pick as row values: what it named, the rest as it was. An agy pick goes into agy's
    row (Claude Code's stay as they were): as the chain when it is what the chain picks now, so the
    sessions still follow the quota when they open; another CLI's pick is only said."""
    if route.cli == "agy":
        if route.pick.model in agy.chain():
            return {"agy-model": AGY_CHAIN if route.pick.model == agy.resolve("")[0] else route.pick.model}
        return {}
    if route.cli != "claude":
        return {}
    return {row: value for row, value in (("model", route.pick.model), ("effort", route.pick.effort),
                                          ("cap", route.pick.cap)) if value}


def taken(route: router.Route, pick: picks.Pick) -> bool:
    """Whether the rows say what the router picked."""
    return all(not want or have == want for want, have in
               ((route.pick.model, pick.model), (route.pick.effort, pick.effort), (route.pick.cap, pick.cap)))


def route_line(cli: str, message: str, asking: bool, found: router.Route | str | None, take_route: bool,
               pick: picks.Pick) -> list[tuple[str, str]] | None:
    """The router's line under the rows, as (text, tone) parts (tones: muted, muted-bold, warn,
    warn-bold, pick, ok, ok-bold): what it suggests and why, that it is asking, or why not. None:
    no line, for a CLI the router does not pick for. `found`: its answer for this message, a string
    when there is none."""
    if cli not in router.CLIS:
        return None
    if not router.worth_routing(route_key(message)):
        return [("Router: ", "muted"), ("Ctrl+R", "muted-bold"), (" suggests a pick once there is a ", "muted"),
                ("first message", "warn-bold"), (" (3 words or more).", "muted")]
    if asking:
        doing = "reading agy's quota…" if cli == "agy" else f"asking {router.MODEL} what this needs…"
        return [("Router: ", "muted"), (doing, "muted"), ("  it goes in when it comes" if take_route else "", "muted")]
    if isinstance(found, router.Route):
        why = f" — {found.why}" if found.why else ""
        if found.cli != "claude":
            return [("Router: ", "muted"), (found.words(), "pick"), (why, "muted")]
        if taken(found, pick):
            return ([("✓ ", "ok-bold"), (found.why, "warn"), ("  Ctrl+R asks the router again", "muted")]
                    if found.guessed else [("✓ ", "ok-bold"), ("the router's pick", "ok"), (why, "muted")])
        return [("Router suggests ", "muted"), (found.words(), "pick"), (why, "muted"), ("  Ctrl+R", "warn-bold"),
                (" takes it", "muted")]
    if isinstance(found, str):
        return [("Router: no suggestion (", "muted"), (found[:80], "warn"), ("). Ctrl+R asks again.", "muted")]
    return [("Router: ", "muted"), ("Ctrl+R", "warn-bold"),
            (" picks a model, effort and context for this message.", "muted")]


# --- the form's state ----------------------------------------------------------------------------

TICK_FIRST = "Tick the folders first (Space)."
TICK, START, REFUSE = "tick", "start", "refuse"  # what Enter does (NewForm.enter)


@dataclass(frozen=True)
class RouteAsk:
    """What Ctrl+R (or a look at the router) comes to: the router to ask in the background about
    `slot` (CLI, message), or the row values to take now from an answer already there (empty: none)."""

    slot: tuple[str, str] | None = None
    take: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Enter:
    """What Enter does: tick a folder typed in the filter box (and start nothing yet), start (ticking
    `key` first, the highlighted folder when none was ticked), or refuse with a note."""

    kind: str
    key: str | None = None
    note: str = ""


@dataclass
class NewForm:
    """The n form's state beyond its boxes: the projects found and the folders typed in (kept once
    ticked), those ticked (by folder key), those the filter shows, the CLI, Ctrl+L's linking, whether
    agy's quota was read, and the router: its answers by (CLI, message), a string when there is none,
    the messages it is being asked about, and whether an answer goes into the rows when it comes. The
    terminal dialog (newsessions.NewSessions) draws it; a desktop dialog can draw the same."""

    found: list[Project] = field(default_factory=list)
    ticked: set[str] = field(default_factory=set)  # folder keys
    extra: list[Project] = field(default_factory=list)  # folders typed in by hand
    shown: dict[str, Project] = field(default_factory=dict)  # what the filter shows, by folder key
    cli: str = field(default_factory=default_cli)
    linked: bool = False  # Ctrl+L: the lines as a team of peers in the one folder ticked
    routes: dict[tuple[str, str], router.Route | str] = field(default_factory=dict)
    routing: set[tuple[str, str]] = field(default_factory=set)
    take_route: bool = False
    quota_read: bool = False  # agy's quota is read once, when agy is first picked

    # --- the folders -------------------------------------------------------------------------------

    def everything(self) -> list[Project]:
        """The folders typed in, then the projects found."""
        return self.extra + self.found

    def listed(self, typed: str) -> dict[str, Project]:
        """What the filter shows for what is typed in its box (newforms.find), kept as `shown`."""
        self.shown = {folder_key(p.cwd): p for p in find(typed, self.everything())}
        return self.shown

    def toggle(self, key: str | None) -> bool:
        """Space on a folder shown: ticked or not. A folder typed in stays listed once it is ticked.
        False when there is no such folder shown (nothing changes)."""
        if key is None or key not in self.shown:
            return False
        self.ticked ^= {key}
        if key in self.ticked and key not in {folder_key(p.cwd) for p in self.everything()}:
            self.extra.insert(0, self.shown[key])
        return True

    def known(self) -> list[Project]:
        """The ticked folders' projects, as the list knows them."""
        known = {folder_key(p.cwd): p for p in self.everything()} | self.shown
        return [known[key] for key in self.ticked if key in known]

    def chosen(self) -> list[str]:
        return [project.cwd for project in self.known()]

    def chosen_names(self) -> list[str]:
        return [project.name for project in self.known()]

    # --- the CLI and the lines ---------------------------------------------------------------------

    def next_cli(self) -> None:
        """Ctrl+O: the next CLI turned on."""
        self.cli = next_cli(self.cli)

    def wants_quota(self) -> bool:
        """Whether agy's quota is to be read now (once a dialog, when agy is picked): for the chain's line."""
        if self.cli == "agy" and not self.quota_read:
            self.quota_read = True
            return True
        return False

    def can_link(self, message: str) -> str:
        """Why the lines cannot be linked now; empty when they can (newforms.can_link)."""
        return can_link(self.cli, message, len(self.ticked))

    def toggle_link(self) -> None:
        """Ctrl+L: the lines apart, or linked as a team of peers."""
        self.linked = not self.linked

    def opens(self, message: str) -> Opens:
        """What will open, for the card on the right."""
        return opens(self.chosen(), message, self.linked and not self.can_link(message))

    def request(self, message: str, pick: picks.Pick) -> Request:
        """What Ctrl+S (or Enter) asks for."""
        return request(self.chosen(), self.cli, message, pick, self.linked)

    # --- the router --------------------------------------------------------------------------------

    def slot(self, message: str) -> tuple[str, str]:
        """The router's answers are kept by this: each CLI has picks of its own for the same message."""
        return self.cli, route_key(message)

    def ask(self, message: str, take: bool = False) -> RouteAsk:
        """Ask the router about the first message as it is now, unless it was asked already. With take,
        its pick goes into the rows when it comes, or now when it is there. A guess made when the router
        was not reached is asked again with take; an answer being asked for is not asked again."""
        key = route_key(message)
        if self.cli not in router.CLIS or not router.worth_routing(key):
            return RouteAsk()
        slot = (self.cli, key)
        self.take_route = take or (self.take_route and slot in self.routing)
        found = self.routes.get(slot)
        if isinstance(found, router.Route) and not (take and found.guessed):
            return RouteAsk(take=self.taking(found) if take else {})
        if slot not in self.routing:
            self.routing.add(slot)
            self.routes.pop(slot, None)  # asked again after it failed
            return RouteAsk(slot=slot)
        return RouteAsk()

    def taking(self, route: router.Route) -> dict[str, str]:
        """The router's pick as row values to put in (newforms.route_take); no answer waits to be taken now."""
        self.take_route = False
        return route_take(route)

    def routed(self, slot: tuple[str, str], found: router.Route | str, now: tuple[str, str] | None) -> dict | None:
        """The router's answer for `slot` is in: kept. `now`: the slot as the form stands (None once it
        has closed). None when the message or the CLI has changed since (kept, should it come back);
        else the row values to take (empty: none)."""
        self.routing.discard(slot)
        self.routes[slot] = found
        if slot != now:
            return None
        values = self.taking(found) if isinstance(found, router.Route) and self.take_route else {}
        self.take_route = False
        return values

    def route_line(self, message: str, pick: picks.Pick) -> list[tuple[str, str]] | None:
        """The router's line under the rows (newforms.route_line) for the message and the rows as they are."""
        slot = self.slot(message)
        return route_line(self.cli, message, slot in self.routing, self.routes.get(slot), self.take_route, pick)

    # --- Enter -------------------------------------------------------------------------------------

    def enter(self, typed: str | None, highlighted: str | None, listed: bool) -> Enter:
        """What Enter does. typed: the filter box's text when Enter is pressed in it (None elsewhere);
        highlighted: the highlighted row's folder key; listed: whether a row is highlighted at all.
        A folder typed in the box is ticked (the next Enter starts); with nothing ticked, the
        highlighted folder is ticked and started; with nothing to tick, a note."""
        path = Path(os.path.expanduser(typed.strip())) if typed and typed.strip() else None
        if path is not None and path.is_dir():
            key = folder_key(str(path))
            if key not in self.ticked:
                return Enter(TICK, key)
        if not self.ticked:
            if not listed:
                return Enter(REFUSE, note=TICK_FIRST)
            return Enter(START, highlighted)
        return Enter(START)


def improve_refusal(text: str) -> str:
    """Why Ctrl+E rewrites nothing; empty when it does."""
    if improve.worth_improving(text):
        return ""
    return "Ctrl+E needs a first message to improve: write what the session is to do first."


# --- the folder list -------------------------------------------------------------------------------

def _seps(text: str) -> str:
    """Folders written with / or \\ alike: on Windows a path is written either way."""
    return text.replace("\\", "/")


def find(typed: str, projects: list[Project]) -> list[Project]:
    """The projects a filter's words find (n's list, and the new-team form's): every one for no words;
    else those with every word in their name or folder (/ and \\ alike), the ones with them in the
    name first ("app" is in every folder under AppData); and a folder written out in full first of
    all, the project it is when it is listed (else a newer one that matches too went first, and
    Enter took it)."""
    needle = os.path.expanduser(typed.strip())
    if not needle:
        return list(projects)
    words = [_seps(w).casefold() for w in needle.split()]
    hits = [p for p in projects if matches(needle, p)]
    hits.sort(key=lambda p: not all(w in _seps(p.name).casefold() for w in words))  # stable: newest first still
    path = Path(needle)
    if path.is_dir():
        key = folder_key(str(path))
        same = next((p for p in projects if folder_key(p.cwd) == key), None) or Project(str(path.resolve()))
        hits = [same, *(p for p in hits if folder_key(p.cwd) != key)]
    return hits


def matches(needle: str, project: Project) -> bool:
    """Whether a project is what the filter asks for: every word of it in its name or its folder,
    whatever the case and whichever way a / or a \\ is written ("desktop/shop" finds C:\\...\\Desktop\\shop)."""
    where = _seps(f"{project.name} {project.cwd}").casefold()
    return all(_seps(term).casefold() in where for term in needle.split())


def term_pattern(term: str) -> str:
    """A filter word as a pattern that finds it in a row, its / and \\ either way, in any case."""
    return "(?i)" + "".join(r"[\\/]" if char in "/\\" else re.escape(char) for char in term)


def home_as_tilde(path: str) -> str:
    home = str(Path.home())
    inside = folder_key(path).startswith(folder_key(home)) and path[len(home):][:1] in ("", "\\", "/")
    return "~" + path[len(home):] if inside else path


def fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def ago(moment: datetime) -> str:
    seconds = max(0.0, (datetime.now() - moment).total_seconds())
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"
