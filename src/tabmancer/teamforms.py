"""The new-team form's rules, without a screen (G3 slice 7).

What the boxes mean and what each key does with them: the line-ups to cycle through and to keep,
tabs or the background, worktrees, what is still missing, what will open and how it is said, the
help for each box, Ctrl+R's question and what its answer does to the boxes, a team at work to add
to or to tell, Ctrl+G's member, and the team (or the join, or the message) Ctrl+S makes. The
terminal form (teamview.NewTeamDialog) draws it; a desktop dialog can draw the same. The words are
returned as data, with a tone where the form colours them ('strong', 'muted', 'amber', 'green')."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import improve, picks, teams
from .actions import Note
from .config import UI_TRIES, Busy
from .newforms import CAP_SHORT

LEAD_LINE = f"{teams.LEAD}: {teams.LEAD_FOCUS}"
# One line, so the keys stay on screen in a 120x30 window; the examples show how a line is written.
MEMBERS_HELP = "name: area [@agy] @model @effort · lead @opus @xhigh · ui @agy @high · tests @sonnet @medium"
LEAD_HELP = ("@agy, @opus, @xhigh (CLI, model, effort) at the end: the lead's own; "
             "it picks the members' itself")
SAID_HERE = 160  # characters of the message to a team at work's lead the form shows: all of it is sent
TOWARD_HELP = {  # the box's help when Ctrl+R said to add to a team at work, or that it has the goal
    teams.ADD_TO: "The members to add, one a line as in a line-up: each opens in its tab and joins the team at work.",
    teams.HAS_IT: "The message for the lead, yours to change: Ctrl+S types it into its tab once it waits for you.",
}
GOAL_NOTE = "Ctrl+E improves the goal"  # on the Team card's border, as n's message card has its own
OWN = "your own"  # the line-up in the box, once edited
HELP = {
    "team-name": "One word: each member's tab is named after it, checkout-api.",
    "team-folder": "Type to find a project; ↑/↓ and Enter pick it. A folder path works too, → completes it.",
    "team-goal": "What the team is for: a plan pasted in keeps its lines, every member gets it. Ctrl+E improves it.",
    "team-most": "At most this many: the lead picks them from the goal, fewer for small work. Ctrl+R suggests one.",
}
NO_HELP = "Every member is a session of its own; with no lead line, they work as peers."
ASKING = ("Asking Claude for a line-up for the goal: your plan, your default model, "
          "about half a minute…")


@dataclass
class JoinTeam:
    """The new-team form's answer when the goal adds to a team at work: these members join it."""
    team: str
    members: str  # one a line, as in a line-up
    to: str       # the session told: its lead, or among peers the member the goal concerns most
    message: str
    goal: str     # what the team's goal grows by


@dataclass
class TellTeam:
    """The new-team form's answer when a team at work has the goal already: its lead is told."""
    team: str
    to: str
    message: str


@dataclass
class Fields:
    """The boxes as they read now: as typed (the folder with ~, the At most box's digits), the Context
    row's cap and the Advisor row's value (picks.NO_ADVISOR for none)."""

    name: str = ""
    folder: str = ""
    goal: str = ""
    members: str = ""
    most: str = ""
    cap: str = picks.NO_CAP
    advisor: str = picks.NO_ADVISOR

    def read(self) -> tuple[str, str, str, list[teams.Member]]:
        return (teams.clean(self.name),
                os.path.expanduser(self.folder.strip()),  # shown with ~
                teams.tidy(self.goal),  # its lines kept
                teams.parse_members(self.members, lead_ok=True))

    def most_n(self) -> int:
        try:
            return int(self.most)
        except ValueError:
            return 0

    def chosen_advisor(self) -> str:
        """The lead's advisor picked; empty for none."""
        return "" if self.advisor == picks.NO_ADVISOR else self.advisor


def first_lineup() -> str:
    """The box as the form opens: the lead line and the first ready-made line-up."""
    return f"{LEAD_LINE}\n{teams.TEMPLATES[next(iter(teams.TEMPLATES))]}"


def answer(text: str) -> str | None:
    """A one-line or goal dialog's answer: the words, or None for none."""
    return text.strip() or None


def improve_refusal(goal: str) -> Note | None:
    """Ctrl+E with nothing worth improving in the Goal box."""
    if improve.worth_improving(goal):
        return None
    return Note("Ctrl+E needs a goal to improve: write what the team is for first.", "warning", 5)


IMPROVED = Note("Goal improved: Ctrl+Z in the box puts yours back.", seconds=6)

# The form's words, the terminal dialog's and the window's (one copy).
TITLE = "New team"
KEYS = [("Ctrl+S", "create"), ("Tab", "next"), ("Ctrl+T", "line-up"), ("Ctrl+L", "keep"), ("Ctrl+R", "suggest"),
        ("Ctrl+G", "role"), ("Esc", "cancel")]
IMPROVE_KEY = ("Ctrl+E", "improve")  # after suggest, where the key line has room (the window's always does)
KEEP_AS, KEEP_EXAMPLE = "Keep this line-up as", "e.g. api and screens"  # Ctrl+L's name prompt
DECIDE_ASIDE = "members, picked by the lead"  # beside the At most box
NAMED_NONE = "Claude's answer named no members."  # Ctrl+R's answer with no line-up in it

# a in a team's view (or on its row): new members, on one line
ADD_QUESTION = "Add members as name: area (several with ;, @agy picks Antigravity, @sonnet picks a model)"
ADD_EXAMPLE = "docs: the README and the user guide @agy"
ADD_ENTER = "add them"
ADD_KEYS = [("Enter", ADD_ENTER), ("Esc", "cancel")]
# g in a team's view: its goal, its lines kept
GOAL_TITLE = "The team's goal"
GOAL_KEYS = [("Ctrl+S", "save"), ("Enter", "a new line"), ("Esc", "cancel")]


def keys_with_improve(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """KEYS with Ctrl+E after suggest."""
    return [*pairs[:5], IMPROVE_KEY, *pairs[5:]]


def still_needed(missing: list[str], loud: bool) -> list[tuple[str, str]]:
    """What the form still needs, as (words, tone) parts: louder once Ctrl+S was refused for it."""
    tone = "amber" if loud else "muted"
    return [("Still needed: ", tone), (", ".join(missing), tone)]


@dataclass
class Keep:
    """What Ctrl+L comes to: a note, whether to ask for a name (then TeamForm.keep), and whether the
    line-ups changed (the form is drawn again)."""

    note: Note | None = None
    ask: bool = False
    changed: bool = False
    members: list[str] = field(default_factory=list)
    peers: bool = False


@dataclass
class Where:
    """Where the members run and where they work, as the form shows it: the ways to run them as
    (words, picked) pills, the key that switches them ('' for peers, each in a tab, with a note
    instead), and the own-worktrees box, ticked or not, with its key."""

    runs: list[tuple[str, bool]]
    key: str = ""
    note: str = ""
    worktrees: bool = False
    worktrees_words: str = "own git worktrees"
    worktrees_key: str = "Ctrl+O"


@dataclass
class Preview:
    """The right-hand card as the boxes say: its title (with whether it is the green 'what opens'), the
    ready mark, what is still missing (said louder once Ctrl+S was tried), and the line under it in
    parts with their tones."""

    title: str
    ready: bool = False
    missing: list[str] = field(default_factory=list)
    loud: bool = False
    tabs: str = ""
    head: list[tuple[str, str]] = field(default_factory=list)  # the whole line; the form shows it from tabs on
    asking: bool = False


@dataclass
class TowardView:
    """The cards when Ctrl+R said to add members to a team at work, or that it has the goal."""

    title: tuple[str, str]           # the form's title and its subject
    lineup_title: str                # the line-up card's
    team: teams.Team                 # drawn
    heading: tuple[str, str]         # the drawing card's title, and its tone
    how: list[tuple[str, str]]       # the line under it, in parts with their tones
    does: str                        # what Ctrl+S does, by the ready mark
    missing: list[str]
    loud: bool


@dataclass
class SuggestAsk:
    """Ctrl+R: why not (a note), or the prompt to send and the teams at work it weighs. box: the line-up
    to put back in the box first when a team at work had been taken up (None: leave it)."""

    refusal: Note | None = None
    prompt: str = ""
    here: list[teams.Team] = field(default_factory=list)
    goal: str = ""
    box: str | None = None


@dataclass
class Suggested:
    """What Ctrl+R's answer does: the note, the box's new text, the At most box's (None: as they are)."""

    note: Note
    box: str | None = None
    most: str | None = None


@dataclass
class RoleWords:
    """What Ctrl+G shows of a member: the title's words, its role word for word, its first message (''
    for none) and what is said instead of one."""

    head: str
    about: list[str]
    role: str
    first: str
    instead: str


def role_words(team: teams.Team, member: teams.Member) -> RoleWords:
    kind = teams.kind_of(member)
    about = [member.cli if member.cli != "claude" else "",
             f"a {kind}er" if kind in ("review", "research") else f"the {kind}s" if kind else "",
             member.pick.words(), f"owns {member.owns}" if member.owns else ""]
    first = teams.launch(team, member).prompt or ""
    instead = (f"Its first message comes from {team.session_name(team.lead)}." if team.lead and not member.lead
               else "It starts with no message.")
    return RoleWords(f"What {team.session_name(member)} is told", [part for part in about if part],
                     teams.role(team, member), first, instead)


@dataclass
class TeamForm:
    """The form's state beyond its boxes: the line-ups to cycle through (the ready-made ones, yours kept
    with Ctrl+L, lead decides), the one picked, tabs or the background (Ctrl+B), own worktrees (Ctrl+O),
    whether Ctrl+S was tried, and a team at work Ctrl+R took the goal toward (with the line-up the box
    held before). taken: the team names in use; working: the teams at work, each with its open members."""

    taken: set[str]
    working: list[tuple[teams.Team, set[str]]] = field(default_factory=list)
    saved: dict[str, str] = field(default_factory=teams.saved_lineups)  # line-ups of your own, kept with Ctrl+L
    lineups: list[str] = field(default_factory=list)
    template: int = 0
    background: bool = False  # Ctrl+B: the members run as background sessions, the lead in a tab
    worktrees: bool = False  # Ctrl+O: each member in a git worktree of its own
    tried: bool = False  # once Ctrl+S has been pressed, what is missing is said louder
    toward: teams.Suggestion | None = None
    before_toward: str | None = None  # the line-up in the box before Ctrl+R put another team's in it
    forgetting: str | None = None  # Ctrl+L once on a kept line-up: again forgets it
    repositories: dict[str, bool] = field(default_factory=dict)  # folder -> is a repository's top

    def __post_init__(self) -> None:
        if not self.lineups:
            self.lineups = [*teams.TEMPLATES, *teams.PEER_TEMPLATES, *self.saved, teams.DECIDE]

    # --- the line-up --------------------------------------------------------------------------------

    def picked(self) -> str:
        return self.lineups[self.template]

    def template_text(self, name: str, members: str) -> str:
        """A line-up with the lead line kept as it is in the box (its model, say). When the lead
        decides, the lead line is all there is."""
        lead = next((line for line in members.split("\n")
                     if teams.clean(line.partition(":")[0].partition("@")[0]) == teams.LEAD), LEAD_LINE)
        if name == teams.DECIDE:
            return lead
        if name in teams.PEER_TEMPLATES:  # peers: no lead line
            return teams.PEER_TEMPLATES[name]
        kept = self.saved.get(name, "")
        if kept.startswith(teams.PEERS_MARK):
            return kept.removeprefix(teams.PEERS_MARK)
        return f"{lead}\n{teams.TEMPLATES.get(name) or kept}"

    def shown_lineup(self, members: str) -> str:
        """The line-up marked among them: the one picked, or "your own" once the box was edited."""
        return OWN if members != self.template_text(self.picked(), members) else self.picked()

    def peers(self, members: str) -> bool:
        """No lead line in the box (and not the line-up where the lead decides): a team of peers."""
        return (self.picked() != teams.DECIDE
                and not any(m.lead for m in teams.parse_members(members, lead_ok=True)))

    def deciding(self, members: str) -> bool:
        """The lead picks the members: that line-up chosen, and no member written in."""
        return (self.picked() == teams.DECIDE
                and not any(not m.lead for m in teams.parse_members(members, lead_ok=True)))

    def next_lineup(self, members: str) -> str:
        """Ctrl+T: the next line-up into the box; from members for a team at work, back to a team of its own."""
        if self.toward is not None:
            return self.leave_toward(members)
        self.template = (self.template + 1) % len(self.lineups)
        return self.template_text(self.picked(), members)

    def toggle_background(self, members: str) -> Note | None:
        if self.peers(members):
            return Note("Peers each run in a tab: you talk to every one of them.", seconds=4)
        self.background = not self.background
        return None

    def toggle_worktrees(self) -> None:
        """Ctrl+O: each member (not the lead) in a git worktree of its own, on a branch of its own, so
        two members' half-done changes never mix in one checkout."""
        self.worktrees = not self.worktrees

    def where(self, peers: bool) -> Where:
        """Where the members run and work, as the form says it (peers: whether the box is a team of peers)."""
        if peers:
            return Where([("each in a tab", True)], note="(peers: you talk to each)", worktrees=self.worktrees)
        return Where([("each in a tab", not self.background), ("the members in the background", self.background)],
                     key="Ctrl+B", worktrees=self.worktrees)

    def keep_step(self, members: str) -> Keep:
        """Ctrl+L: keep the members in the box as a line-up of your own, under a name (asked for, then
        keep). On a kept one as it is, twice: forget it."""
        current = self.picked()
        if current in self.saved and members == self.template_text(current, members):
            if self.forgetting != current:
                self.forgetting = current
                return Keep(Note(f"Press Ctrl+L again to forget the line-up {current}.", seconds=4))
            try:
                teams.keep_lineup(current, None, UI_TRIES)
            except Busy as error:  # Ctrl+L again tries again
                return Keep(Note(teams.not_saved_words(f"The line-up {current} is still kept", error,
                                                       teams.LINEUPS_FILE), "warning", 8))
            self.forgetting = None
            del self.saved[current]
            self.lineups.remove(current)
            self.template = 0
            return Keep(Note(f"Forgot the line-up {current}.", seconds=4), changed=True)
        lines = [line.strip() for line in members.split("\n") if line.strip()
                 and teams.clean(line.partition(":")[0].partition("@")[0]) != teams.LEAD]
        if not lines:
            return Keep(Note("Write its members first; with the lead alone it is the line-up lead decides.",
                             seconds=5))
        return Keep(ask=True, members=lines, peers=self.peers(members))

    def keep(self, name: str | None, members: list[str], peers: bool) -> Keep:
        """The name given: kept (a line-up of peers stays one), and picked."""
        name = " ".join((name or "").split())[:30]
        if not name:
            return Keep()
        if name in teams.TEMPLATES or name in teams.PEER_TEMPLATES or name in (teams.DECIDE, OWN):
            return Keep(Note(f"{name} is a ready-made line-up: give yours another name.", "warning"))
        kept = (teams.PEERS_MARK if peers else "") + "\n".join(members)
        try:
            teams.keep_lineup(name, kept, UI_TRIES)
        except Busy as error:
            return Keep(Note(teams.not_saved_words(f"The line-up {name} is not kept", error, teams.LINEUPS_FILE),
                             "warning", 8))
        self.saved[name] = kept
        if name not in self.lineups:
            self.lineups.insert(len(self.lineups) - 1, name)  # before lead decides
        self.template = self.lineups.index(name)
        return Keep(Note(f"Kept the line-up {name}: Ctrl+T comes to it next time.", seconds=4), changed=True)

    # --- a team at work in the project --------------------------------------------------------------

    def teams_here(self, folder: str) -> list[tuple[teams.Team, set[str]]]:
        """The teams at work in the project (or a folder of it), each with its open members."""
        return [(team, names) for team, names in self.working if folder and teams.in_team_folder(team, folder)]

    def working_team(self, name: str) -> teams.Team | None:
        return next((team for team, _ in self.working if team.name == name), None)

    def at_work(self, folder: str) -> str:
        """The teams at work in this project, named for the line on the drawing's card ('' for none)."""
        return ", ".join(f"{team.name} ({len(open_names)} open)" for team, open_names in self.teams_here(folder))

    def toward_gone(self, folder: str) -> bool:
        """Another project now: the team Ctrl+R took the goal toward is not at work in it."""
        return self.toward is not None and not any(t.name == self.toward.team for t, _ in self.teams_here(folder))

    def leave_toward(self, members: str) -> str:
        """A team of its own after all: the line-up that was in the box before, as you left it."""
        self.toward = None
        before, self.before_toward = self.before_toward, None
        return before if before is not None else self.template_text(self.picked(), members)

    def toward_view(self, fields: Fields) -> TowardView | None:
        """None when that team is at work no more (the form leaves it)."""
        toward = self.toward
        team = self.working_team(toward.team) if toward is not None else None
        if team is None:
            return None
        missing = self.missing(fields)
        if toward.kind == teams.ADD_TO:
            new = [m for m in teams.parse_members(fields.members) if m.name not in {m.name for m in team.members}]
            told = team.session_name(team.lead) if team.lead is not None else "the others"
            # Its start only: a whole message (a long one, often) pushed the team out of the card. All of it is sent.
            said = teams.cut(toward.message, SAID_HERE)
            return TowardView(("Add to a team at work", toward.team), f"Members to add to {toward.team}",
                              replace(team, members=[*team.members, *new]),
                              (f"Adds {len(new)} to {toward.team}", "green"),
                              [(f"{toward.team} is at work here, and this goal adds to its work. ", "amber"),
                               ((f"Each one opens in its tab and introduces itself to {told}; {toward.to} is told: "
                                f"“{said}” Ctrl+N: a new team instead."), "muted")],
                              "Ctrl+S adds them", missing, self.tried)
        return TowardView(("Tell a team at work", toward.team), f"To {toward.to}", team,
                          (f"{toward.team} has this goal", "amber"),
                          [(f"{toward.team} is at work on this goal already: no new team needed. ", "amber"),
                           ((f"Ctrl+S sends the message on the left to {toward.to}: typed into its tab once it "
                            "waits for you. Ctrl+N: a new team anyway."), "muted")],
                          "Ctrl+S sends it", missing, self.tried)

    # --- what is missing, and what will open --------------------------------------------------------

    def is_repository(self, folder: str) -> bool:
        """Whether the folder is a git repository's top, asked of git once a folder."""
        if folder not in self.repositories:
            self.repositories[folder] = bool(folder) and Path(folder).is_dir() and teams.repository_root(folder)
        return self.repositories[folder]

    def missing(self, fields: Fields) -> list[str]:
        if self.toward is not None:  # joining a team at work, or telling it: no name, no line-up of its own
            text = fields.members.strip()
            if self.toward.kind == teams.ADD_TO:
                return [] if teams.parse_members(text) else ["a member to add"]
            return [] if text else ["a message for the lead"]
        name, folder, goal, members = fields.read()
        deciding, peers = self.deciding(fields.members), self.peers(fields.members)
        return [problem for problem, wrong in (
            ("a name", not name),
            (f"another name: {name} exists already", name in self.taken),
            ("a project folder that exists", not folder or not Path(folder).is_dir()),
            ("the goal", not goal),
            ("a member besides the lead", not deciding and not peers and not any(not m.lead for m in members)),
            ("two members at least, as peers (or a lead line)", peers and len(members) < 2),
            ("for worktrees, the top folder of a git repository", self.worktrees and folder
             and Path(folder).is_dir() and not self.is_repository(folder)),
            # A fresh member would start in the old one's worktree, on its branch, with its work in it.
            (f"another name: an earlier team {name} left its worktrees in this project", self.worktrees and name
             and folder and any(Path(folder, *teams.WORKTREES, f"{name}-{m.name}").exists() for m in members)),
            ("how many members at most, 1 to 9", deciding and not 1 <= fields.most_n() <= 9)) if wrong]

    def drawn(self, fields: Fields) -> teams.Team:
        """The team the boxes make, drawn as it stands, whatever is still missing."""
        name, folder, _, members = fields.read()
        return teams.new_team(name or "team", folder or ".", "", members, peers=self.peers(fields.members),
                              background=self.background)

    def preview(self, fields: Fields, asking: bool = False) -> Preview:
        """What opens and how, or what is still needed (asking: Ctrl+R's answer is awaited)."""
        if asking:
            return Preview("Asking Claude…", asking=True, head=[(ASKING, "muted")])
        missing = self.missing(fields)
        if missing:
            return Preview("Will open", missing=missing, loud=self.tried)
        team = self.drawn(fields)
        if team.peers:
            tabs = f"Opens {len(team.members)} tabs in {team.project}"
            head = [(tabs, "strong"), ((". Nobody leads: each starts on its part, you talk to each, and they tell "
                                       "each other what touches theirs."), "muted")]
        elif self.deciding(fields.members):
            tabs = f"Opens 1 tab in {team.project}"
            each = "each in the background" if self.background else "each in its own tab"
            head = [(tabs, "strong"), (f". It reads the goal and adds up to {fields.most_n()} members itself, {each}.",
                                       "muted")]
        elif self.background:
            bg = sum(not m.lead and m.cli == "claude" for m in team.members)
            extra_tabs = sum(not m.lead and m.cli != "claude" for m in team.members)
            if extra_tabs:
                tabs = f"Starts {bg} in the background and {extra_tabs + 1} in tabs in {team.project}"
            else:
                tabs = f"Starts {bg} in the background in {team.project}"
            head = [(f"{tabs}, then opens a tab for {team.session_name(team.lead)}", "strong"),
                    (", which hands out the work. B lists the background ones.", "muted")]
        else:
            tabs = f"Opens {len(team.members)} tabs in {team.project}"
            head = [(tabs, "strong"), (". The lead starts last and hands out the work.", "muted")]
        return Preview(tabs, ready=True, tabs=tabs, head=head)

    # --- the help line -----------------------------------------------------------------------------

    def help(self, focused: str, fields: Fields) -> str:
        """One line under the cards: how to fill the box in focus (its id)."""
        if focused == "team-members":
            return (TOWARD_HELP[self.toward.kind] if self.toward is not None
                    else LEAD_HELP if self.deciding(fields.members) else MEMBERS_HELP)
        if focused == "team-cap":
            return cap_help(fields.cap)
        if focused == "team-advisor":
            return self.advisor_help(fields)
        return HELP.get(focused, NO_HELP)

    def advisor_help(self, fields: Fields) -> str:
        """The Advisor row's help: whose advisor it is, and what it costs."""
        advisor = fields.chosen_advisor()
        if self.peers(fields.members):
            return "←/→ an advisor for the lead; a team of peers has none, so it is not given."
        if not advisor:
            return "←/→ an advisor for the lead (only the lead): a stronger model it asks at the turns that matter."
        lead = next((m for m in fields.read()[3] if m.lead), None)
        if lead is not None and lead.cli != "claude":
            return "←/→ --advisor is Claude Code only, so it is not given to an Antigravity lead."
        if lead is not None and not picks.advisor_ok(lead.model, advisor):
            return f"{advisor} cannot advise a {picks.family(lead.model)} lead: only a model as strong or stronger can."
        return f"←/→ the lead's advisor, for its session only. {advisor}: {picks.ADVISOR_WORDS[advisor]}."

    # --- Ctrl+R: a line-up suggested ---------------------------------------------------------------

    def suggest_ask(self, fields: Fields) -> SuggestAsk:
        """Claude reads the goal and the project's top level and suggests the team's size, and why, with
        a line-up (suggested then puts it in). One call on your plan; it is yours to change after."""
        _, folder, goal, _ = fields.read()
        if not goal or not folder or not Path(folder).is_dir():
            return SuggestAsk(Note("Write the goal and the project folder first: the line-up is suggested for them.",
                                   "warning", 5))
        here = self.teams_here(folder)
        prompt = teams.suggestion_prompt(goal, str(Path(folder).resolve()), working=here)
        box = self.leave_toward(fields.members) if self.toward is not None else None  # weighed afresh
        return SuggestAsk(None, prompt, [team for team, _ in here], goal, box)

    def suggested(self, suggestion: teams.Suggestion | None, error: str, fields: Fields) -> Suggested:
        """The answer into the boxes: the line-up; where the lead decides, the size into At most; for a
        team at work, the members to add to it or the message for its lead."""
        if error or suggestion is None:
            return Suggested(Note(f"No line-up suggested: {error}", "warning", 8))
        lines, count, lead = suggestion.lines, suggestion.count, suggestion.lead
        why = suggestion.why.rstrip(" .")
        why = f" — {why}" if why else ""  # "because ...", or in the language Claude answers in ("çünkü ...")
        if suggestion.kind == teams.ADD_TO:  # the goal adds to a team at work: members for it, its lead told
            self.toward, self.before_toward = suggestion, fields.members
            return Suggested(Note(f"{suggestion.team} is at work here, and this goal adds to it: Claude suggests "
                                  f"adding {count} member{'s' * (count != 1)}{why}. Ctrl+S adds them and tells "
                                  f"{suggestion.to}; Ctrl+N makes a new team instead.", seconds=15), box=lines)
        if suggestion.kind == teams.HAS_IT:  # a team at work has it: the goal, as a prompt for its lead
            self.toward, self.before_toward = suggestion, fields.members
            return Suggested(Note(f"{suggestion.team} is at work on this goal already. Ctrl+S sends it to "
                                  f"{suggestion.to}: the message is on the left, yours to change. Ctrl+N makes a new "
                                  "team anyway.", "warning", 15), box=suggestion.message)
        if self.deciding(fields.members):  # the lead picks the members: the size is what is asked for, its limit
            count = min(max(count, 1), teams.SUGGEST_MOST)  # the box takes one digit
            return Suggested(Note(f"Claude suggests at most {count} member{'s' if count > 1 else ''}{why}. The lead "
                                  "picks them; for the members written out, Ctrl+T to another line-up, then Ctrl+R.",
                                  seconds=12), most=str(count))
        whom = f"{count} members and a lead" if lead else f"{count} members, as peers"
        return Suggested(Note(f"Claude suggests {whom}{why}. Change what you like; Ctrl+G on a line shows what that "
                              "member is told.", seconds=12), box=lines)

    # --- Ctrl+G and Ctrl+S -------------------------------------------------------------------------

    def role_of(self, fields: Fields, line: str) -> tuple[teams.Team, teams.Member] | Note | None:
        """Ctrl+G: the member on the cursor's line, in the team the boxes make (None: not in it)."""
        found = teams.parse_members(line, lead_ok=True)
        if not found:
            return Note("Put the cursor on a member's line in the box first.", seconds=4)
        name, folder, goal, members = fields.read()
        team = teams.new_team(name or "team", folder or ".", goal or "(the goal)", members,
                              decide=fields.most_n() if self.deciding(fields.members) else 0,
                              peers=self.peers(fields.members))
        member = next((m for m in team.members if m.name == found[0].name), None)
        return None if member is None else (team, member)

    def create(self, fields: Fields) -> teams.Team | JoinTeam | TellTeam | None:
        """Ctrl+S: the team the boxes make (the lead's advisor given where it can advise the lead's model,
        Claude Code only), or the join or the message for a team at work. None while something is missing."""
        self.tried = True
        if self.missing(fields):
            return None
        toward = self.toward
        if toward is not None and toward.kind == teams.ADD_TO:
            return JoinTeam(toward.team, fields.members, toward.to, toward.message, fields.read()[2])
        if toward is not None:
            return TellTeam(toward.team, toward.to, " ".join(fields.members.split()))
        name, folder, goal, members = fields.read()
        peers = self.peers(fields.members)
        team = teams.new_team(name, str(Path(folder).resolve()), goal, members,
                              decide=fields.most_n() if self.deciding(fields.members) else 0,
                              background=self.background and not peers, peers=peers,
                              worktrees=self.worktrees, cap=fields.cap)
        advisor = fields.chosen_advisor()
        if advisor:
            team.members = [replace(m, advisor=advisor)
                            if m.lead and m.cli == "claude" and picks.advisor_ok(m.model, advisor) else m
                            for m in team.members]
        return team


def cap_help(cap: str) -> str:
    """The Context row's help: ←/→, and what the cap picked means."""
    return f"←/→ the context cap every member starts under. {CAP_SHORT.get(cap, cap)}: {picks.cap_words(cap)}."


CAPS = [*picks.CAPS, picks.NO_CAP]  # the Context row's values (CAP_SHORT names them)
ADVISOR_CHOICES = [picks.NO_ADVISOR, *picks.ADVISORS]  # the Advisor row's (ADVISOR_SHORT names them)
