"""Teams: several sessions in one project, talking to each other.

Most teams have a lead, the orchestrator, which splits the goal and hands the work out. A team of
peers has none: each member owns a topic of its own, the user talks to each in its tab, and they
tell each other what touches the other's work (two related topics worked on side by side).

Each member is an ordinary Claude Code session in its own tab, so it can be closed,
resumed and handed over like any other. It starts with a name (`--name <team>-<member>`),
a role (`--append-system-prompt-file`, a file of tabmancer's own) and a setting that lets messages from other sessions in
(`--settings`, a file of tabmancer's own: your settings are not touched). The members talk
with Claude Code's own cross-session messaging: SendMessage to a member's name, and the
message arrives in that session as a new turn, idle or not.

tabmancer never carries the messages. It reads them back from the transcripts to show the
team at work: an outgoing message is a SendMessage tool call, an incoming one a user
record whose origin is a peer.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from . import clis, picks, router, teambus
from .config import config_dir, read_json, write_json
from .launch import Launch, Mode
from .sessions import (
    Exit,
    Session,
    Status,
    _is_real_prompt,
    _registry_sessions,
    _shorten,
    _text,
    folder_key,
    sanitize,
)

FILE = "teams.json"
SETTINGS_FILE = "team-settings.json"
TEAM_CLIS = ("claude", "agy", "codex")  # codex: only when a member asks for it (@codex), never picked
LEAD = "lead"
LEAD_FOCUS = "plans the work, hands it out and checks it"
YOU = "you"
TABMANCER = "tabmancer"  # ...and the prompts tabmancer itself starts members with, so they are not taken for yours
TEMPLATES = {  # one member a line, as the new-team dialog shows them
    "feature team": "api: the server side, data and endpoints\nui: the screens and how they behave\n"
                    "tests: tests for what the others build, and checking it end to end",
    "bug hunt": "reproduce: find a reliable way to reproduce the bug and write it down\n"
                "fix: find the cause and fix it\nverify: check the fix and look for the same bug elsewhere",
    "write and review": "writer: does the work\nreviewer: reviews every change the writer makes, "
                        "finds problems and asks for fixes",
}
# Line-ups with no lead: peers, each on a topic of its own, telling each other what touches the other's.
PEER_TEMPLATES = {
    "two topics": "first: the first topic, and what is done when it is done\n"
                  "second: the second, related topic, and what is done when it is done",
}
PEERS_MARK = "~peers\n"  # a kept line-up of peers begins with it (keep_lineup)
DECIDE = "lead decides"  # the line-up the lead makes itself, with `tabmancer team add`
DECIDE_MOST = 4  # members it may add unless told otherwise
ADD = "tabmancer team add"  # the one command a deciding lead may run without asking
START = "tabmancer team start"  # what a lead runs, without asking, to bring a closed member back
BUS_CMD = "tabmancer team"  # send / listen / who / start / add for mixed-CLI teams


@dataclass
class Member:
    name: str
    focus: str = ""
    lead: bool = False
    model: str = ""  # its session's --model (an alias such as sonnet, or a full name); empty: the default
    effort: str = ""  # its session's --effort (picks.EFFORTS); empty: the default
    owns: str = ""   # the folders or files that are its own ("src/api, docs/api.md"), written [...] in its line
    advisor: str = ""  # its session's --advisor (picks.ADVISORS): the lead's, picked in the new-team form
    cli: str = "claude"  # which coding CLI runs this member: "claude" or "agy"

    def line(self) -> str:
        """The member as a line of the members box: 'api: endpoints [src/api] @sonnet @high'."""
        owns = f" [{self.owns}]" if self.owns else ""
        # Read back, no tag is the main CLI: said always but where Claude Code is both, so a line read
        # after the main CLI changed still names the CLI it was written with.
        cli_tag = f" @{self.cli}" if self.cli != "claude" or clis.main_cli() != "claude" else ""
        return f"{self.name}: {self.focus}{owns}{cli_tag}{picks.tags(self.pick)}" if self.focus else \
            f"{self.name}{owns}{cli_tag}{picks.tags(self.pick)}"

    @property
    def pick(self) -> picks.Pick:
        return picks.Pick(self.model, self.effort, advisor=self.advisor)


@dataclass
class Team:
    name: str
    cwd: str
    goal: str
    members: list[Member] = field(default_factory=list)
    created: str = ""
    decide: int = 0  # the lead picks the members itself, at most this many; 0: the line-up given
    background: bool = False  # the members run as background sessions (claude --bg), the lead in a tab
    worktrees: bool = False  # each member (not the lead) in a git worktree of its own (claude --worktree)
    cap: str = ""  # its members' context cap (--autocompact, see picks); empty: none, as in teams made before caps

    @property
    def uses_bus(self) -> bool:
        """Whether this team uses tabmancer's cross-CLI message bus (any non-Claude member)."""
        return any(m.cli != "claude" for m in self.members)

    @property
    def clis(self) -> set[str]:
        return {m.cli for m in self.members}

    def has_worktree(self, member: Member) -> bool:
        """Whether this member works in a worktree of its own: every member but the lead, when the
        team says so (the lead stays in the project, where the branches come together)."""
        return self.worktrees and not member.lead

    def branch(self, member: Member) -> str:
        """The branch Claude Code gives a member's worktree."""
        return f"worktree-{self.session_name(member)}"

    @property
    def lead(self) -> Member | None:
        """The orchestrator; None for a team of peers, where nobody leads."""
        return next((m for m in self.members if m.lead), None)

    @property
    def peers(self) -> bool:
        return self.lead is None

    @property
    def ordered(self) -> list[Member]:
        """The lead first, when there is one, then the members as they were written."""
        return sorted(self.members, key=lambda m: not m.lead)

    @property
    def project(self) -> str:
        return Path(self.cwd).name or self.cwd

    def session_name(self, member: Member) -> str:
        return f"{self.name}-{member.name}"

    def member_of(self, session_name: str) -> str:
        """A session name back to the member's name; other sessions keep their own name."""
        prefix = f"{self.name}-"
        names = {m.name for m in self.members}
        if session_name.startswith(prefix) and session_name[len(prefix):] in names:
            return session_name[len(prefix):]
        return session_name


PLAIN = str.maketrans({"ı": "i", "ø": "o", "ł": "l", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe"})


def clean(name: str) -> str:
    """Names end up as session names and in command lines: lowercase letters, digits and dashes.
    A letter with a mark keeps its letter (ö o, ş s, ı i), so "takım" is "takim", not "tak-m"."""
    text = unicodedata.normalize("NFKD", name.strip().lower().translate(PLAIN))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:24].strip("-")


def _split_tags(text: str) -> tuple[str, picks.Pick, str]:
    """Strip trailing @cli, @model and @effort tags in any order. With no @cli, a Gemini model is
    agy's and a Claude one Claude Code's; with neither, the main CLI's (clis.main_cli)."""
    model = effort = cli = ""
    while found := picks.TAG.search(text):
        word = found[1]
        low = word.lower()
        if low in TEAM_CLIS:
            cli = cli or low
        elif low in picks.EFFORTS:
            effort = effort or low
        else:
            model = model or word
        text = text[:found.start()]
    if not cli and model.lower().startswith("gemini"):
        cli = "agy"
    elif not cli and picks.FAMILY.search(model):
        cli = "claude"
    return text, picks.Pick(model, effort), cli or clis.main_cli()


def parse_members(text: str, lead_ok: bool = False) -> list[Member]:
    """"api: endpoints @sonnet @high; ui: screens @agy @high" (or one per line) to members. A name
    without a focus is fine, and so is one without a CLI, model or effort. With lead_ok, a "lead"
    line is the orchestrator's: its focus, CLI, model and effort; otherwise it is left out, as a
    team has one lead."""
    members, seen = [], set()
    for part in (piece for line in text.split("\n") for piece in NEXT_MEMBER.split(line)):
        part, pick, cli = _split_tags(part)
        part = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", part)  # a list's bullet or number, as a model may write
        owned = OWNS.search(part)
        owns = " ".join(owned[1].split()) if owned else ""
        if owned:
            part = part[:owned.start()]
        name, _, focus = part.partition(":")
        name, focus = clean(name), " ".join(focus.split())
        if not name or name in seen or (name == LEAD and not lead_ok):
            continue
        seen.add(name)
        members.append(Member(name, focus or (LEAD_FOCUS if name == LEAD else ""), lead=name == LEAD,
                              model=pick.model, effort=pick.effort, owns=owns, cli=cli))
    return members


OWNS = re.compile(r"\s*\[([^\]]+)\]\s*$")  # "api: endpoints [src/api]"
# A ';' that starts another member: a name and a colon follow it ("; ui team: screens"), or a word
# alone, with an @model, its [files] or nothing after it ("; tests @haiku", "; docs"). Any other ';'
# is part of what the member does ("drafts first; icons, splash and the store pictures"): split
# there, the words after it became a member named after them.
NEXT_MEMBER = re.compile(r";(?=\s*(?:[-*\u2022]|\d+[.)])?\s*[^\W\d_][\w-]*"
                         r"(?:(?:\s+[\w-]+)*\s*:|\s*(?:[@\[;]|$)))")


def tidy(goal: str) -> str:
    """A goal as it was written, its lines kept (a plan pasted in stays readable in the roles), with
    the spaces at the ends of its lines and runs of blank lines taken out."""
    lines = [line.rstrip() for line in goal.strip().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


GOAL_IN_PROMPT = 300  # a first message names the goal on one line, this long at most
SENTENCE_END = re.compile(r"[.!?…][\"')\]”’]*$")  # "Done." "Done!)" "Is it?”"


def sentence(text: str) -> str:
    """Text that ends a sentence: with its own stop when it has one, else a period. A goal written
    with its period gave "Build checkout.." in the role."""
    text = text.rstrip()
    return text if not text or SENTENCE_END.search(text) else f"{text}."



def cut(text: str, most: int, more: str = "") -> str:
    """Text on one line, at most `most` characters: a longer one ends at a word, with "…" and `more`."""
    line = " ".join(text.split())
    if len(line) <= most:
        return line
    return line[:most].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…" + more


def goal_line(team: Team) -> str:
    """The goal as a first message names it: on one line, and a long one cut short, as the whole of it
    is in the member's role. Whole, a goal of a few pages went into every tab's command line."""
    return cut(team.goal, GOAL_IN_PROMPT, " (the whole goal is in your role)")


def new_team(name: str, cwd: str, goal: str, members: list[Member], decide: int = 0,
             background: bool = False, peers: bool = False, worktrees: bool = False, cap: str = "") -> Team:
    """The lead first: the one among members, or one with the usual focus. With decide, the lead
    picks the rest itself, at most that many; with background, the members run without tabs. With
    peers, nobody leads: the members as given, each in its tab (never in the background, as you talk
    to each of them). With worktrees, each member but the lead works in a git worktree of its own.
    Its members start with the context cap given, else the one new sessions start with."""
    cap = cap or picks.default_cap()
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")  # with its offset: a zone change cannot move it
    if peers:
        return Team(clean(name), cwd, tidy(goal), [replace(m, lead=False) for m in members], stamp,
                    worktrees=worktrees, cap=cap)
    lead = next((m for m in members if m.lead), Member(LEAD, LEAD_FOCUS, lead=True))
    return Team(clean(name), cwd, tidy(goal), [lead, *(m for m in members if not m.lead)],
                stamp, decide, background, worktrees, cap)


def add_members(team: Team, text: str) -> tuple[list[Member], str]:
    """Members from "name: area @model @effort; ..." joined to the team and saved: (who joined, a note on
    who did not). A team whose lead decides takes no more than its limit."""
    new = parse_members(text)
    taken = {m.name for m in team.members}
    note = []
    if dropped := [m.name for m in new if m.name in taken]:
        note.append(f"already on the team: {', '.join(dropped)}")
    new = [m for m in new if m.name not in taken]
    if team.decide:
        room = max(0, team.decide - sum(not m.lead for m in team.members))
        if len(new) > room:
            note.append(f"the team has room for {team.decide} members, so {', '.join(m.name for m in new[room:])} "
                        "did not join")
            new = new[:room]
    if new:
        saved = load()  # as it is now: the dashboard may have changed it meanwhile
        team = saved.get(team.name, team)
        team.members += new
        saved[team.name] = team
        save(saved)
    return new, "; ".join(note)


def remove_member(name: str, member: str) -> Team | None:
    """Take a member off a team, as saved now, and save it; never the lead. Its sessions stay: from
    now on they are ordinary ones. None: no such team."""
    saved = load()
    team = saved.get(name)
    if team is None:
        return None
    team.members = [m for m in team.members if m.lead or m.name != member]
    save(saved)
    return team


def set_goal(name: str, goal: str) -> Team | None:
    """A team's goal, changed and saved. Members started from now on get it in their role."""
    saved = load()
    team = saved.get(name)
    if team is None:
        return None
    team.goal = tidy(goal)  # its lines kept, as a new team's (and the "\n\nAdded" marks after it)
    save(saved)
    return team


def left_note(team: Team, gone: str) -> str:
    """What the lead (or, in a team of peers, each member) is told when a member is taken off the
    team from the dashboard: it started with the old line-up in its role."""
    if team.lead is None:
        return (f"Team change: {team.name}-{gone} is no longer on the team, so leave it out from now on. "
                f"The members now: {roster(team) or 'only you'}.")
    others = roster(team, team.lead)
    return (f"Team change: {team.name}-{gone} is no longer on the team, so send it no more work. "
            f"The members now: {others or 'none besides you'}.")


def goal_note(team: Team) -> str:
    """What the lead (or each peer) is told when the goal changes: it started with the old one in its role."""
    if team.lead is None:
        return (f"Team change: the team goal is now: {team.goal} Check your part against it and tell the "
                "others what changes for them.")
    return f"Team change: the team goal is now: {team.goal} Plan again with the members where their work changes."


def joining(team: Team, members: list[Member], adds: str = "") -> list[Launch]:
    """Tabs for members who join a team already at work: each introduces itself to the lead, or in a
    team of peers to the others. adds: what the goal grew by, that they join for."""
    why = f" for what its goal now adds: {cut(adds, GOAL_IN_PROMPT)}" if adds else ""
    if team.uses_bus:
        if team.lead is None:
            def hello_bus(member: Member) -> str:
                others = [m.name for m in team.members if m is not member]
                send_hint = (f'tabmancer team send {team.name} {others[0]} "..." --from {member.name}'
                             if others else f'tabmancer team send {team.name} <member> "..." --from {member.name}')
                return (f"You just joined the team {team.name}{why}. Introduce yourself to the others ("
                        f"{', '.join(others) or 'nobody yet'}) with `{send_hint}`, start "
                        f"`tabmancer team listen {team.name} {member.name}` in the background, then start on your part.")
            return [launch(team, m, prompt=hello_bus(m)) for m in members]
        return [launch(team, m, prompt=(
            f"You just joined the team {team.name}{why}. Introduce yourself to {team.lead.name} with "
            f'`tabmancer team send {team.name} {team.lead.name} "Joined and ready for work." --from {m.name}` '
            f"and start `tabmancer team listen {team.name} {m.name}` in the background to wait for your first task."))
            for m in members]
    if team.lead is None:
        def hello(member: Member) -> str:
            return (f"You just joined the team {team.name}{why}. Introduce yourself to the others ("
                    f"{', '.join(team.session_name(m) for m in team.members if m is not member) or 'nobody yet'}) "
                    "with SendMessage, then start on your part.")
        return [launch(team, m, prompt=hello(m)) for m in members]
    return [launch(team, m, prompt=(f"You just joined the team {team.name}{why}. Introduce yourself to "
                                    f"{team.session_name(team.lead)} with SendMessage and ask for work."))
            for m in members]


def _strip_team_tag(text: str) -> str:
    return re.sub(r"^\[tabmancer:[a-z0-9-]+\]\s*", "", text.strip(), flags=re.IGNORECASE)


def from_tabmancer(team: Team, text: str) -> bool:
    """Whether a prompt is one tabmancer started a member with (joining(), launch(), in_background()),
    not yours. A goal grown since (add_to_goal) still starts with the one its first members were given."""
    clean_text = _strip_team_tag(text)
    first = replace(team, goal=team.goal.split(ADDED)[0])
    prefixes = (
        f"You just joined the team {team.name}.",
        f"You just joined the team {team.name} for ",
        f"Goal: {goal_line(team)} ",
        f"Goal: {goal_line(first)} ",
        f"You are on the team {team.name}, in the background.",
        f"You are {team.name}-",
        "Read your full team role in ",
        f"You are resuming your role on team {team.name}.",
    )
    return clean_text.startswith(prefixes)


def in_background(team: Team, launch: Launch) -> bool:
    """Whether this member's session starts in the background: a team's Claude members do when it says so,
    its lead never (it is where you talk to the team), peers never, and non-Claude CLIs never."""
    return (launch.cli == "claude" and team.background and team.lead is not None
            and launch.title != f"{team.name}: {team.lead.name}")


def background_start(team: Team, launch: Launch) -> Launch:
    """A member started in the background with nothing to say yet: claude --bg takes a first message,
    so it is told to wait for the lead's."""
    if launch.prompt or team.lead is None:
        return launch
    return replace(launch, prompt=(f"You are on the team {team.name}, in the background. "
                                   f"{team.session_name(team.lead)} sends your first task as a message: wait for it."))


def has_cli(cli: str) -> bool:
    """Whether any saved team includes a member using this CLI."""
    return any(m.cli == cli for team in load().values() for m in team.members)


def load() -> dict[str, Team]:
    data = read_json(FILE)
    teams = {}
    for item in data.get("teams", []) if isinstance(data, dict) else []:
        try:
            members = [Member(m["name"], m.get("focus", ""), bool(m.get("lead")), str(m.get("model") or ""),
                              str(m.get("effort") or "") if m.get("effort") in picks.EFFORTS else "",
                              str(m.get("owns") or ""),
                              str(m.get("advisor") or "") if picks.family(str(m.get("advisor") or "")) else "",
                              str(m.get("cli") or "claude") if m.get("cli") in TEAM_CLIS else "claude")
                       for m in item["members"]]
            teams[item["name"]] = Team(item["name"], item["cwd"], item.get("goal", ""), members,
                                       item.get("created", ""), int(item.get("decide") or 0),
                                       bool(item.get("background")), bool(item.get("worktrees")),
                                       _cap(item.get("cap")))
        except (KeyError, TypeError):
            continue
    return teams


def _cap(saved: object) -> str:
    word = str(saved or "")
    return word if word == picks.NO_CAP or picks.cap_tokens(word) else ""


def save(teams: dict[str, Team]) -> None:
    write_json(FILE, {"teams": [asdict(t) for t in teams.values()]}, indent=2)


LINEUPS_FILE = "lineups.json"


def saved_lineups() -> dict[str, str]:
    """Line-ups of your own, kept with Ctrl+K in the new-team dialog: name -> one member a line."""
    data = read_json(LINEUPS_FILE)
    lineups = data.get("lineups") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in lineups.items()} if isinstance(lineups, dict) else {}


def keep_lineup(name: str, members: str | None) -> None:
    """Keep a line-up under a name, or (members None) forget it."""
    lineups = saved_lineups()
    if members is None:
        lineups.pop(name, None)
    else:
        lineups[name] = members
    write_json(LINEUPS_FILE, {"lineups": lineups}, indent=2)


def settings_file() -> Path:
    """Lets messages from other sessions in, for team members only."""
    path = config_dir() / SETTINGS_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"crossSessionInbound": "accept"}), encoding="utf-8")
    return path


def roster(team: Team, skip: Member | None = None) -> str:
    def _desc(m: Member) -> str:
        parts = []
        if team.uses_bus:
            parts.append(m.cli)
        parts.append("orchestrator" if m.lead else m.focus or m.name)
        if m.owns:
            parts.append(f"owns {m.owns}")
        return "; ".join(parts) if team.uses_bus else (
            f"{'orchestrator' if m.lead else m.focus or m.name}{f'; owns {m.owns}' if m.owns else ''}")
    return ", ".join(f"{team.session_name(m)} ({_desc(m)})" for m in team.members if m is not skip)


# Rules for the kinds of member most teams have, by the start of its name: what it does and what
# it leaves to the others. A member of another name gets its area alone.
KINDS = {
    "review": "You review: you do not change the code yourself. Read each change the others report, look for "
              "bugs, missing cases and unclear code, and send what you find to whoever made the change, with "
              "the file and the line; say plainly when a change is good to go.",
    "test": "You write and run the tests for what the others build. When a test finds a bug in another member's "
            "part, tell that member, with how to reproduce it, rather than fixing their code yourself.",
    "doc": "You write the documentation: read what the others build and describe it for the people who will use "
           "it. Ask the member who built something when it is unclear, rather than guessing.",
    "research": "You find things out and report them: read the code, the docs and the web, change no files, and "
                "send short findings with their sources to whoever needs them.",
}


def kind_of(member: Member) -> str | None:
    """The kind of member it is, by its name: review(er), test(s), doc(s), research(er)."""
    return next((kind for kind in KINDS if member.name.startswith(kind)), None)


def duties(member: Member) -> str:
    """What a member is told beyond its area: its kind's rule, and the files that are its own."""
    out = []
    kind = kind_of(member)
    if kind:
        out.append(KINDS[kind])
    if member.owns:
        out.append(f"Yours to edit: {member.owns}. The others' files are theirs: ask before you change one.")
    return " ".join(out)


def bus_cmd(team: Team, member: Member) -> str:
    """The command a member runs the team bus with: `tabmancer team` for Claude Code; for agy, whose
    sandbox cannot read the tabmancer package, the team's stdlib copy of the bus (teambus.install
    writes it when the member starts) run by the plain interpreter."""
    return teambus.command(team.name) if member.cli == "agy" else BUS_CMD


def reopen(team: Team, bus: str = BUS_CMD, tools: bool = False, codex: bool = False) -> str:
    """What a lead is told about a member that has closed: how to bring it back itself. With tools
    (a Codex lead, or an agy lead with tabmancer's plugin): the team_request tool, as a shell command
    from their sandbox may not reach tabmancer."""
    if tools:
        return ("Check who is online with the team_who tool. A member that has closed comes back as itself, "
                'where it left off, with the team_request tool (action: "start", members: ["<member>"]); with '
                "no member named, every closed one does. "
                + ("Codex may ask the user to approve it in your tab." if codex else
                   "The user approves each team_request in your tab."))
    if team.uses_bus:
        return (f"Check who is online with `{bus} who {team.name}`. A member that has closed comes back as "
                f"itself, where it left off, with `{START} {team.name} <member>`; with no member named, every "
                "closed one does.")
    return (f"A member that has closed (ListAgents no longer shows it, and it is not just starting) comes back as "
            f"itself, where it left off, with the shell command {START} {team.name} <member>; with no member "
            "named, every closed one does.")


def _agy_plugin_installed() -> bool:
    """Whether agy has tabmancer's plugin (agyplugin: the bus as MCP tools), as installed or older."""
    try:
        from . import agyplugin

        return agyplugin.status() != "missing"
    except Exception:  # noqa: BLE001  (a role without the sentence beats no role)
        return False


def _agy_tools_asked() -> bool:
    """Whether agy still asks before one of the team's tools (not all in its allow list)."""
    try:
        from . import agyplugin

        return bool(agyplugin.asked_tools())
    except Exception:  # noqa: BLE001  (the sentence about asking, rather than no role)
        return True


def _sends_by_tool(member: Member) -> bool:
    """Whether a member sends with the team_send tool rather than its bus command: Codex always (its
    sandbox may not reach the bus), agy with tabmancer's plugin (its terminal sandbox may refuse)."""
    return member.cli == "codex" or (member.cli == "agy" and _agy_plugin_installed())


def send_how(team: Team, member: Member, to: str = "<member>") -> str:
    """How this member sends to `to`, as the role says it."""
    if _sends_by_tool(member):
        return f'the team_send tool (to: "{to}", message: "...")'
    return f'`{bus_cmd(team, member)} send {team.name} {to} "..." --from {member.name}`'


def _bus_role(team: Team, member: Member) -> str:
    """Role prompt for a member of a mixed-CLI (Claude + Antigravity) or Antigravity team."""
    others = roster(team, member)
    goal = f"Team goal:\n\n{team.goal}\n\n" if "\n" in team.goal else f"Team goal: {sentence(team.goal)} "
    bus = bus_cmd(team, member)
    listen_cmd = f"{bus} listen {team.name} {member.name}"
    # The bus and team_request as MCP tools: Codex always, agy with tabmancer's plugin. Their shell
    # commands may not reach the bus or tabmancer from either one's sandbox.
    tools = _sends_by_tool(member)
    codex = member.cli == "codex"
    if codex:
        listen_how = (
            "Nothing to run to receive messages: tabmancer queues each message for you into this conversation, "
            "where it arrives as a new turn. When you have nothing else to do, end your turn. team_inbox "
            "(ack: true) reads what is waiting, and team_who and team_status show the team: use the team_* "
            "tools rather than shell commands, which may not reach the bus from Codex's sandbox."
        )
    elif member.cli == "agy":
        listen_how = (
            f"To receive messages without polling, run `{listen_cmd}` via run_command with "
            "WaitMsBeforeAsync: 500 (and IsDaemon: false) so it runs in the background as a task, then end your "
            "turn (call no more tools). The system automatically wakes you with the message the instant one arrives. "
            "Never poll in a loop or use timers. After handling a message and replying, start "
            f"`{listen_cmd}` in the background again before ending your turn."
        )
        if tools:
            # The tools run outside agy's terminal sandbox; a shell send from inside it may be refused.
            listen_how += (
                " The bus is also yours as tools (team_send, team_inbox, team_who, team_status): send and look "
                "with them, not with shell commands, and keep waiting with the background listen above. "
                + ("Your first team_* call may ask for approval in your tab: make it alone, and no other team_* "
                   "call until it is answered. " if _agy_tools_asked() else "")
                + "If the listen says [acks off], call team_inbox with ack: true once you have read its "
                "messages, or they come again.")
        else:
            listen_how += (
                " The bus commands write to the bus folder, which agy's terminal sandbox may refuse: if one "
                "fails with a permission error or the listen says [acks off], run it with BypassSandbox: true.")
    else:
        listen_how = (
            f"To receive messages without polling, run `{listen_cmd}` in the background (run_in_background: true) "
            "whenever you wait with nothing else to do. It exits automatically the instant a message arrives for "
            "you, printing it to stdout and waking you up. If it times out after 55 minutes with no messages "
            "(which also keeps your prompt cache warm), run it again up to three times if still waiting. Never "
            "poll in a loop."
        )
    common = (
        f"Team: {team.name}, working in {team.cwd}. You run on {member.cli}. {goal}"
        + (f"The other members (address by member name or session name): {others}. " if others else
           "There are no other members yet. ")
        + "This team communicates through tabmancer's cross-CLI team bus (Claude and Antigravity members share it). "
        + (f'Send a message with {send_how(team, member)}, and optionally summary: "short gist". '
           "See who is online with the team_who tool. " if tools else
           f"Send a message with {send_how(team, member)} (optional `--summary \"short gist\"`). "
           f"See who is online with `{bus} who {team.name}`. ")
        + f"{listen_how} "
        + ("The same bus is also yours as tools (mcp__tabmancer__team_send, team_inbox, team_status, team_who; "
           "team_wait blocks your turn, so the background listen above is the way to wait). "
           if member.cli == "claude" else "")
        + "Keep messages short and concrete: what you did, which files you changed, what you need. "
        "Coordinate before you touch another member's area."
    )
    if member.lead and team.decide:
        return (
            f"You are {team.session_name(member)}, the orchestrator of this team, and you decide who is on it. "
            f"{common} First read the goal and look at the project, then decide which members the work needs: "
            f"at most {team.decide}, fewer when the work is small, none if you can do it well alone. "
            "Give each a short name (a word) and an area, and optionally @agy (for Antigravity) or @claude, "
            "a model and an effort. "
            + (f'Add them all with one shell command: {ADD} {team.name} "name: area @model @effort; name: area @agy". '
               if not tools else 'Add them all with one call of the team_request tool (action: "add", lineup: '
               '"name: area @model @effort; name: area @agy"), '
               + ("which Codex may ask the user to approve in your tab. " if codex else
                  "which the user approves in your tab. "))
            + "Each opens and introduces itself on the team bus. Then send each its first task with "
            + ("the team_send tool" if tools else f"`{bus} send`")
            + ("; their reports arrive here as new turns. Keep track of " if codex else
               f", start `{listen_cmd}` in the background while waiting for reports, keep track of ")
            + "who does what, answer their questions, sort out overlaps and check the results. Do small glue work "
            "yourself. Tell the user here when the goal is met or a decision is theirs. "
            + reopen(team, bus, tools, codex)
        )
    if member.lead:
        return (
            f"You are {team.session_name(member)}, the orchestrator of this team. {common} Split the goal into "
            "tasks, send each member its first task with "
            + ("the team_send tool" if tools else f"`{bus} send {team.name} <member> \"...\" --from {member.name}`")
            + " (messages queue safely even if a member is still starting)"
            + ("; their reports arrive here as new turns. " if codex else
               f", then start `{listen_cmd}` in the background while waiting for their reports. ")
            + "Keep track of who does what, "
            "answer their questions, sort out overlaps and check the results. Do small glue work yourself and "
            "leave the members' areas to them. Tell the user here when the goal is met or a decision is theirs. "
            + reopen(team, bus, tools, codex)
        )
    if team.lead is None:
        text = (
            f"You are {team.session_name(member)}, one of the peers on this team. Your part: "
            f"{member.focus or member.name}. {common} Nobody leads this team: your part is yours to plan and "
            "decide, and the user talks to each of you in your own tab. Before you change something another "
            f"member's work depends on (a shared file, an interface, a schema, the build), tell that member with "
            f"{'the team_send tool' if tools else f'`{bus} send`'} and agree on it first; when you finish "
            "something the others need, tell them. "
            "Tell the user here when your part is done or a decision is theirs."
        )
    else:
        text = (
            f"You are {team.session_name(member)}, a member of this team. Your area: "
            f"{member.focus or member.name}. {common} {team.session_name(team.lead)} ({team.lead.name}) is the "
            f"orchestrator: it hands out the work, and you report to it with {send_how(team, member, team.lead.name)} "
            "when you finish something or get stuck. "
            "Ask another member directly when you need something from their area."
        )
    extra = duties(member)
    return f"{text} {extra}" if extra else text


def _role(team: Team, member: Member) -> str:
    """A member's role without the worktree note (role adds it)."""
    if team.uses_bus:
        return _bus_role(team, member)
    others = roster(team, member)
    goal = f"Team goal:\n\n{team.goal}\n\n" if "\n" in team.goal else f"Team goal: {sentence(team.goal)} "
    common = (f"Team: {team.name}, working in {team.cwd}. {goal}"
              + (f"The other members, by the name to use with SendMessage: {others}. " if others else
                 "There are no other members yet. ")
              + "Messages from them arrive as new turns, so you never need to poll. A command you run in "
              "the background tells you when it finishes, so never wait for one with sleep or a loop that "
              "checks its output: each check reads the whole conversation again. The one sleep worth "
              "running: the cache forgets this conversation an hour after your last reply, and the next "
              "message then writes all of it again, some 20 times what this costs; so when you wait "
              "with nothing to do (for your next task, or for members still at work), run sleep 3300 in the "
              "background, and when it says it finished and you still wait, run it again, three times in a "
              "row at most, then let it go. Once new work has come, ignore it. Keep messages short "
              "and concrete: what you did, which files you changed, what you need. Coordinate before you "
              "touch another member's area.")
    if member.lead and team.decide:
        return (f"You are {team.session_name(member)}, the orchestrator of this team, and you decide who is on "
                f"it. {common} First read the goal and look at the project, then decide which members the work "
                f"needs: at most {team.decide}, fewer when the work is small, none if you can do it well alone. "
                "Give each a short name (a word) and an area, and a model and an effort when they matter (@sonnet "
                "@medium for routine work, @opus @high for hard design; leave them out for the user's default). "
                f'Add them all with one shell command: {ADD} {team.name} "name: area @model @effort; name: area". Each '
                + ("starts as a background session, with no tab, and introduces " if team.background
                   else "opens in its own tab and introduces ")
                + "itself to you. Then send each its first task with SendMessage, keep track of who does what, "
                "answer their questions, sort out overlaps and check the results. Do small glue work yourself. "
                "Tell the user here when the goal is met or a decision is theirs. " + reopen(team))
    if member.lead:
        return (f"You are {team.session_name(member)}, the orchestrator of this team. {common} Split the "
                "goal into tasks, send each member its first task with SendMessage, keep track of who does "
                "what, answer their questions, sort out overlaps and check the results. Do small glue work "
                "yourself and leave the members' areas to them. Tell the user here when the goal is met or "
                "a decision is theirs. The members start alongside you: if ListAgents does not show one yet, "
                "wait a moment and look again. " + reopen(team))
    if team.lead is None:
        text = (f"You are {team.session_name(member)}, one of the peers on this team. Your part: "
                f"{member.focus or member.name}. {common} Nobody leads this team: your part is yours to plan and "
                "decide, and the user talks to each of you in your own tab. Before you change something another "
                "member's work depends on (a shared file, an interface, a schema, the build), tell that member "
                "with SendMessage and agree on it first; when you finish something the others need, tell them. "
                "Tell the user here when your part is done or a decision is theirs.")
    else:
        text = (f"You are {team.session_name(member)}, a member of this team. Your area: "
                f"{member.focus or member.name}. {common} {team.session_name(team.lead)} is the orchestrator: it "
                "hands out the work, and you report to it with SendMessage when you finish something or get stuck. "
                "Ask another member directly when you need something from their area.")
    extra = duties(member)
    return f"{text} {extra}" if extra else text


def worktree_note(team: Team, member: Member) -> str:
    """What a team with worktrees adds to a role: where the member works and who merges."""
    if not team.worktrees:
        return ""
    if member.lead:
        return ("Each member works in a git worktree of its own, on its own branch (worktree-<its session "
                "name>): when one reports a part done, check it on that branch and merge it into the project "
                "here, where you work.")
    who = team.session_name(team.lead) if team.lead is not None else "the user"
    return (f"You work in a git worktree of your own, on the branch {team.branch(member)}, so the others' "
            f"changes never mix with yours mid-way: commit your work there as you go, and tell {who} when the "
            "branch is ready to merge.")


def role(team: Team, member: Member) -> str:
    """The system prompt a member starts with (from a file of its own: role_file)."""
    text = _role(team, member)
    note = worktree_note(team, member)
    return f"{text} {note}" if note else text


# --- a line-up suggested for a goal -------------------------------------------------------------

SUGGEST_MOST = 8  # members besides a lead: the size is Claude's to suggest, as few as the work needs
SUGGEST_EFFORT = "medium"  # one answer, a short one: your default model at a medium effort


def project_files(folder: Path, most_folders: int = 40, per_folder: int = 12) -> str:
    """The project's files folder by folder, from git when it is a repository ('src/app/: main.py,
    ui.py (+3 more)'), so the files a member is given are ones that exist; else '' (not one)."""
    try:
        listed = subprocess.run(["git", "-C", str(folder), "ls-files"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    if listed.returncode != 0:
        return ""
    folders: dict[str, list[str]] = {}
    for name in listed.stdout.splitlines():
        parent, _, leaf = name.rpartition("/")
        folders.setdefault(parent + "/" if parent else "./", []).append(leaf)
    lines = []
    for parent, leaves in sorted(folders.items())[:most_folders]:
        more = f" (+{len(leaves) - per_folder} more)" if len(leaves) > per_folder else ""
        lines.append(f"{parent}: {', '.join(leaves[:per_folder])}{more}")
    if len(folders) > most_folders:
        lines.append(f"(+{len(folders) - most_folders} more folders)")
    return "\n".join(lines)


def at_work(team: Team, open_names: set[str]) -> str:
    """A team at work in the project, as a suggestion is told of it: who leads, its goal, each member's
    part, its files, and whether it is open."""
    who = f"led by {team.session_name(team.lead)}" if team.lead is not None else "peers: nobody leads"
    goal = cut(team.goal, AT_WORK_GOAL)
    members = "\n".join(
        f"- {team.session_name(m)}: {'plans the work, hands it out and checks it' if m.lead else m.focus or m.name}"
        f"{f' [{m.owns}]' if m.owns else ''} ({'open' if m.name in open_names else 'closed'})" for m in team.members)
    return f"Team {team.name} ({who}). Its goal: {goal}\nIts members:\n{members}"


AT_WORK_GOAL = 800  # characters of a team at work's goal a suggestion is told
AGY_ASKED = ("If the goal asks to use Antigravity (agy) or mix CLIs, put @agy on those members (with an optional "
             "effort, e.g. 'ui: screens [src/ui] @agy @high'; never a Claude or GPT model with @agy)")


def agy_bullet() -> str:
    """suggestion_prompt's word on @agy members, with what agy can run now: the model its chain gives
    from the quota saved last (agy.pick_model: no agy is run, nothing is read afresh), or that it
    has none, so no member is put on agy only to stand there."""
    from . import agy

    if not agy.installed():
        return "agy (Antigravity) is not installed here: put no @agy member; leave the CLI out (Claude Code)."
    model, why = agy.pick_model()
    if not model or agy.barred(model):
        said = (why or "none is usable").removesuffix(agy.PICK_OR_WAIT).rstrip(". ")
        return (f"agy has no model now ({said}): put no @agy member, even if the goal asks for agy, and say so "
                "in the size line's why; leave the CLI out (Claude Code).")
    return f"{AGY_ASKED}; its members run {agy.name(model)} now. Otherwise leave the CLI out (Claude Code)."


def suggestion_prompt(goal: str, folder: str, most: int = SUGGEST_MOST,
                      working: list[tuple[Team, set[str]]] | None = None) -> str:
    """What Claude is asked for a line-up: the goal, the project's files (from git, or its top level)
    and its README's start. The answer is the team's size and why (suggested_size), then the members
    box's lines (parse_members reads them). With teams at work in the project (each with its open
    members' names), it decides first how the goal stands to them (read_suggestion): a team of its
    own, members for one of them, or that one has it already, and then what that team is told."""
    path = Path(folder)
    try:
        entries = sorted((p for p in path.iterdir() if not p.name.startswith(".")),
                         key=lambda p: (not p.is_dir(), p.name.casefold()))
    except OSError:
        entries = []
    files = project_files(path)
    listing = (f"\n{files}" if files else
               ", ".join(p.name + ("/" if p.is_dir() else "") for p in entries[:60]) or "(nothing listed)")
    readme = next((p for p in (path / name for name in ("README.md", "README.rst", "README.txt", "README"))
                   if p.is_file()), None)
    try:
        start = readme.read_text(encoding="utf-8", errors="replace")[:1500] if readme else ""
    except OSError:
        start = ""
    working = working or []
    weigh = ""
    if working:
        weigh = (
            "\nAlready at work in this project now:\n" + "\n\n".join(at_work(t, names) for t, names in working)
            + "\n\nFirst decide how the goal stands to the team at work, and say it on the first line, as one of:\n"
            "Decision: new team\nDecision: add to <team>\nDecision: <team> has it\n\n"
            "- new team: the goal is other work. Answer then as for any team (below), giving its members parts and "
            "files apart from the team at work's.\n"
            "- add to <team>: the goal adds to that team's work and needs people it does not have. Then the size "
            "line (N: the members to add), the lines of the members to add only, no lead line, and last a line\n"
            "To <session name>: a message for that team's lead (in a team of peers, the member the goal concerns "
            "most) saying what the goal adds and who joins for it.\n"
            "- <team> has it: that team is on the goal already, or its members can do it as they are. Then no "
            "line-up, only a line\nTo <session name>: the goal as a prompt for its lead (in a team of peers, the "
            "member it concerns most): what is wanted, in the user's words where they are clear.\n"
            "Write the message on that one line. Add to a team, or leave the goal to it, only when the goal is its "
            "own work; otherwise a new team.\n\nFor a new team, or the members to add: ")
    return (
        "You set up a team of Claude Code sessions for one goal in one project. Each member is a session of "
        "its own, working in the same folder at the same time, and they message each other.\n\n"
        f"Goal: {goal}\nProject folder: {path.name}\n{'Its files, by folder' if files else 'At its top level'}: "
        f"{listing}\n"
        + (f"Its README begins:\n{start}\n" if start else "")
        + (weigh or "\n")
        + ("answer with" if working else "Answer with") + " the team's size and why on a "
        + ("line after the decision" if working else "first line")
        + ", then the line-up, one member a line" + ("" if working else ", nothing else")
        + ":\nTeam size: N, because <one short sentence>\n"
        "name: what it works on [the folders or files that are its own] @model @effort\n\n"
        f"- N is the number of members besides a lead: 2 to {most}, as few as the work needs. Each member is a "
        "session of its own, using the user's plan, so a part gets a member of its own only when it can go on "
        "at the same time as the others; small work takes 2. Names: one short lowercase word.\n"
        "- Inside a line, use commas, not semicolons: a semicolon starts another member.\n"
        "- Give each a part that can go on at the same time as the others, and the folders or files that are "
        "its own, so that no two edit the same file.\n"
        "- Put a first line 'lead: plans the work, hands it out and checks it @opus @xhigh' only when the parts "
        "depend on each other so much that someone must hand out and check the work. Leave it out when they "
        "are separate topics that only need to tell each other what touches the other: they then work as peers.\n"
        f"- {router.GUIDE}\n"
        f"- {agy_bullet()}\n"
        "- Name a member who reviews review, one who writes tests tests, one who writes documentation docs, one "
        "who finds things out research.")


MEMBER_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*[^\W\d_][\w-]*\s*:\s*\S")  # "- api: ...", not "Here you go:"


def suggested_lines(answer: str) -> str:
    """The members box's text from Claude's answer: each member it named (a one-word name, a colon
    and its part), as a clean line; anything said around them is left out (its size line too: two
    words before the colon are no member's name)."""
    named = "\n".join(line for line in answer.splitlines() if MEMBER_LINE.match(line) and not DECISION_LINE.match(line))
    return "\n".join(member.line() for member in parse_members(named, lead_ok=True))


OWN_TEAM, ADD_TO, HAS_IT = "new", "add", "has"  # what a suggestion says of a goal, with a team at work in the project
DECISION_LINE = re.compile(r"^[\s*_#>-]*decision[\s*_]*:[\s*_]*(.*?)[\s*_.]*$", re.IGNORECASE)
TO_LINE = re.compile(r"^[\s*_#>-]*to\s+[`'\"*_]*([\w.-]+)[`'\"*_]*\s*:[\s*_]*(.*)$", re.IGNORECASE)


@dataclass
class Suggestion:
    """What Ctrl+R came back with. OWN_TEAM: a line-up for a team of its own (lines). ADD_TO: members for a team
    at work (lines, no lead), whose `to` (its lead, or among peers the member the goal concerns most)
    is told `message`. HAS_IT: that team is on the goal already: `to` gets the goal as `message`."""
    lines: str = ""
    count: int = 0      # members in lines besides a lead
    lead: bool = False  # a lead line among them
    why: str = ""       # why that many, in Claude's words
    kind: str = OWN_TEAM
    team: str = ""      # the team at work it is about (ADD_TO, HAS_IT)
    to: str = ""        # the session name told
    message: str = ""


def read_suggestion(answer: str, working: list[Team] | None = None, goal: str = "") -> Suggestion:
    """Claude's answer to suggestion_prompt as a Suggestion. A decision for a team that is not at work
    in the project, or one to add members with none named, is taken for what can be done: a new team,
    or the goal left to that team. A message missing, or for no member of it, goes to its lead (or
    first member) and says the goal."""
    rows = [row for row in answer.splitlines() if not row.strip().startswith("```")]
    at = {t.name: t for t in working or []}
    kind, team = OWN_TEAM, None
    decided = next((m.group(1).strip(" `'\"*_") for m in map(DECISION_LINE.match, rows) if m), "")
    said = decided.casefold()
    if said.startswith("add to "):
        team, kind = at.get(clean(decided[len("add to "):].strip(" `'\"*_"))), ADD_TO
    elif said.endswith(" has it"):
        team, kind = at.get(clean(decided[:-len(" has it")].strip(" `'\"*_"))), HAS_IT
    told = next((i for i, row in enumerate(rows) if TO_LINE.match(row)), None)
    # The message: its line, and the lines after it up to one that is the answer's own again (a member,
    # the size, the decision), wherever in the answer it was written.
    end = told
    if told is not None:
        end = told + 1
        while end < len(rows) and not any(p.match(rows[end]) for p in (MEMBER_LINE, SIZE_LINE, DECISION_LINE, TO_LINE)):
            end += 1
    lines = suggested_lines("\n".join(rows if told is None else rows[:told] + rows[end:]))
    count, lead, why = suggested_size(answer, lines)
    if team is None:
        return Suggestion(lines, count, lead, why)
    if kind == ADD_TO:
        lines = "\n".join(m.line() for m in parse_members(lines))  # the members to add: no lead line
        count, lead = len(parse_members(lines)), False
        if not lines:
            kind = HAS_IT
    to, message = "", ""
    if told is not None:
        matched = TO_LINE.match(rows[told])
        to = matched.group(1)
        message = " ".join([matched.group(2), *(row.strip() for row in rows[told + 1:end])]).strip()
    member = next((m for m in team.members if to.casefold() in (team.session_name(m).casefold(), m.name.casefold())),
                  None) or team.lead or (team.members[0] if team.members else None)
    to = team.session_name(member) if member is not None else ""
    if not message:
        added = ", ".join(m.name for m in parse_members(lines))
        message = (f"The user's goal now adds this to the team's work: {sentence(goal)} Joining for it: {added}."
                   if kind == ADD_TO else f"A request from the user for the team: {goal}")
    return Suggestion(lines if kind == ADD_TO else "", count if kind == ADD_TO else 0, False, why, kind, team.name,
                      to, message)


def add_to_goal(name: str, more: str) -> Team | None:
    """A team at work's goal, with what a new goal adds kept under it, dated: members who start from now
    on read both in their role. None: no such team."""
    saved = load()
    team = saved.get(name)
    if team is None:
        return None
    team.goal = tidy(f"{team.goal}{ADDED}{datetime.now():%d %b}: {tidy(more)}")
    save(saved)
    return team


ADDED = "\n\nAdded "  # what add_to_goal puts between a goal and what it grew by
MESSAGE_IN_PROMPT = 6000  # a message a closed member opens with (on the tab's command line), at most


# "Team size: 3, because ...", in Markdown's bold or not
SIZE_LINE = re.compile(r"^[\s*_#>-]*team\s+size[\s*_]*:[\s*_]*(\d+)[\s*_]*[,.;:–—-]?\s*(.*?)[\s*_]*$", re.IGNORECASE)


def suggested_size(answer: str, lines: str) -> tuple[int, bool, str]:
    """The suggestion's size: how many members besides a lead its lines name (what is written counts,
    whatever number the answer said), whether one of them leads, and why that many, in its words."""
    members = parse_members(lines, lead_ok=True)
    why = next((m.group(2) for m in map(SIZE_LINE.match, answer.splitlines()) if m), "")
    return sum(1 for m in members if not m.lead), any(m.lead for m in members), why


ROLES = "roles"  # in tabmancer's folder: each member's role, <session name>.md


def role_file(team: Team, member: Member) -> Path | None:
    """The member's role in a file of its own, for --append-system-prompt-file; None when it cannot
    be written. On the command line itself a team's goal went once for each member: with a plan of a
    few pages pasted in as the goal, six tabs came to 38000 characters and none opened (Windows takes
    32767 at most)."""
    path = config_dir() / ROLES / f"{team.session_name(member)}.md"
    text = role(team, member)
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def _tabmancer_cmd() -> tuple[str, ...]:
    """How a tab invokes tabmancer itself (e.g. `tabmancer team run`)."""
    return ("tabmancer",) if shutil.which("tabmancer") else (sys.executable, "-m", "tabmancer")


def bus_env(team: Team, member: Member) -> tuple[tuple[str, str], ...]:
    """Environment variables passed to members of a mixed/agy team."""
    if not team.uses_bus:
        return ()
    return (("TABMANCER_TEAM", team.name), ("TABMANCER_MEMBER", member.name),
            (teambus.BUS_ENV, str(teambus.bus_root())))


MCP_SERVER = "tabmancer"  # its tools reach Claude Code as mcp__tabmancer__team_send, ...
# The tools a Claude member runs unasked. Not team_request: what it starts or adds is asked of you.
MCP_ALLOWED = ("team_send", "team_inbox", "team_wait", "team_who", "team_status")


def mcp_config(team: Team, member: Member) -> Path | None:
    """The --mcp-config file that gives a Claude member of a bus team the bus as tools (`tabmancer
    mcp`, mcpserver), named for it: written when it changed, None when it cannot be."""
    if not team.uses_bus or member.cli != "claude":
        return None
    command, *args = _tabmancer_cmd()
    entry = {"type": "stdio", "command": command,
             "args": [*args, "mcp", "--team", team.name, "--member", member.name]}
    text = json.dumps({"mcpServers": {MCP_SERVER: entry}}, indent=2)
    path = teambus.team_dir(team.name) / "mcp" / f"{team.session_name(member)}.json"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def agy_notes(team: Team, only: set[str] | None = None) -> list[str]:
    """'ui: opus never runs through agy: Neon instead (...)', for each agy member (of only, by name,
    when given) whose model was asked for and is not the one it gets: said where the team starts."""
    from . import agy

    notes = []
    for member in team.members:
        if member.cli != "agy" or not member.model or (only is not None and member.name not in only):
            continue
        model, note = agy.resolve(member.model)
        if note and model != member.model:
            notes.append(f"{member.name}: {note}")
    return notes


# A model Codex runs (OpenAI's): only one of these goes to it with -m. Not "auto", an effort, or a
# Claude or Gemini model: Codex then runs its default.
CODEX_MODEL = re.compile(r"(?i)^(gpt-|o\d|codex-)[\w.\-]*$")


def _toml_literal(text: str) -> str:
    """A TOML literal string ('...': backslashes as written) for Codex's -c, or a basic one when
    the text holds a single quote."""
    return f"'{text}'" if "'" not in text else json.dumps(text)


def codex_options(team: Team, member: Member) -> tuple[str, ...]:
    """A Codex member's -c overrides, for its session alone (never ~/.codex): tabmancer's MCP server
    for this member, with a tool timeout longer than a team_wait. Its model with -m only when one was
    asked for that Codex runs (CODEX_MODEL)."""
    teambus.install(team.name, team.created, [m.name for m in team.members])  # its team file: when it began
    command, *args = _tabmancer_cmd()
    served = [*args, "mcp", "--team", team.name, "--member", member.name]
    key = f"mcp_servers.{MCP_SERVER}"
    flags = ["-c", f"{key}.command={_toml_literal(command)}",
             "-c", f"{key}.args=[{', '.join(_toml_literal(a) for a in served)}]",
             "-c", f"{key}.tool_timeout_sec=3600"]
    model = member.model.strip()
    if CODEX_MODEL.match(model):
        flags += ["-m", model]
    return tuple(flags)


def options(team: Team, member: Member) -> tuple[str, ...]:
    if member.cli == "codex":
        return codex_options(team, member)
    if member.cli == "agy":
        bdir = teambus.team_dir(team.name)
        # The bus copy and team.json it runs from (in the folder --add-dir lets agy read): the
        # role text names its command (bus_cmd).
        teambus.install(team.name, team.created, [m.name for m in team.members])
        from . import agy

        # Always a model, from agy's chain by quota and never a Claude one (agy.resolve): with
        # none, agy runs its own default, which may be neither. With the whole chain out there is
        # none: no --model here, and the launch refuses to open the tab and says why
        # (agy.model_flags raises NoModel) rather than agy running a model nobody picked. No
        # --effort: agy's model ids carry theirs (gemini-4-neon-high).
        model = agy.resolve(member.model)[0]
        return ("--mode", "auto", "--add-dir", str(bdir), *(("--model", model) if model else ()))
    # --allowedTools=... in one piece: written apart it takes every word after it as a tool.
    served = mcp_config(team, member)
    rules = ",".join(f"Bash({rule}:*)" for rule in bash_rules(team, member))
    if team.uses_bus:
        # The bus as shell commands, and as tools when its MCP server is there (mcp_config).
        tools = rules + ("".join(f",mcp__{MCP_SERVER}__{t}" for t in MCP_ALLOWED) if served is not None else "")
    else:
        tools = "SendMessage,ListAgents" + (f",{rules}" if rules else "")
    path = role_file(team, member)
    told = (("--append-system-prompt-file", str(path)) if path is not None else
            ("--append-system-prompt", " ".join(role(team, member).split())))
    return ("--name", team.session_name(member), *member.pick.flags(), *picks.cap_flags(team.cap),
            "--settings", str(settings_file()),
            *((f"--mcp-config={served}",) if served is not None else ()),  # one piece: it takes several
            f"--allowedTools={tools}", *told)


def bash_rules(team: Team, member: Member) -> list[str]:
    """The tabmancer commands a Claude member runs without asking, for its own team only: a member
    of shop could otherwise send on, listen to, start or add to any other team unasked (with
    `tabmancer team:*`). Claude Code matches `<rule>:*` as the command and any arguments after a
    space (shop's rules never match shop2), and checks each part of a command joined by && ; |.
    - A bus member: send and who for its team, listen as itself.
    - A lead: start for its team (its closed members back); a deciding lead: add too."""
    rules = ([f"{BUS_CMD} send {team.name}", f"{BUS_CMD} listen {team.name} {member.name}",
              f"{BUS_CMD} who {team.name}"] if team.uses_bus else [])
    if member.lead:
        rules.append(f"{START} {team.name}")
        if team.decide:
            rules.append(f"{ADD} {team.name}")
    return rules


def ensure_worktree(team: Team, member: Member) -> Path | None:
    """Create a git worktree for a non-Claude member when the team uses worktrees."""
    kept = existing_worktree(team, member)
    if kept is not None:
        return kept
    target = Path(team.cwd, *WORKTREES, team.session_name(member))
    branch = team.branch(member)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Reuse existing branch if a previous generation left it, otherwise create it.
        check = subprocess.run(["git", "-C", team.cwd, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                               capture_output=True, timeout=10, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        cmd = (["git", "-C", team.cwd, "worktree", "add", str(target), branch] if check.returncode == 0 else
               ["git", "-C", team.cwd, "worktree", "add", "-b", branch, str(target)])
        made = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=30, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if made.returncode == 0 and (target / ".git").exists():
            return target
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _agy_first_prompt(team: Team, member: Member, bus_role: Path | None, prompt: str | None,
                      resuming: bool = False) -> str:
    """Initial -i prompt for an agy team member: embeds its team tag and points to its role file."""
    tag = f"[tabmancer:{team.session_name(member)}]"
    bus = bus_cmd(team, member)
    tools = _sends_by_tool(member)
    sends = "the team_send tool" if tools else f"`{bus} send`"
    role_hint = (f"Read your full team role in {bus_role} and follow it strictly." if bus_role is not None
                 else " ".join(role(team, member).split()))
    if resuming:
        if prompt:
            return f"{tag} You are resuming your role on team {team.name}. {role_hint} {prompt}"
        return (f"{tag} You are resuming your role on team {team.name}. {role_hint} "
                f"Start `{bus} listen {team.name} {member.name}` in the background (WaitMsBeforeAsync: 500) "
                "and continue your work.")
    if prompt:
        return f"{tag} {role_hint} {prompt}"
    if member.lead and team.decide and not any(not m.lead for m in team.members):
        adds = ('the team_request tool (action: "add", lineup: "name: area; ...")' if tools else
                f'{ADD} {team.name} "name: area; ..."')
        body = (f"Goal: {goal_line(team)} The team is yours to make: decide the members the work needs (at most "
                f"{team.decide}), add them with {adds}, send each its first task with "
                f"{sends}, then start `{bus} listen {team.name} {member.name}` in the background.")
    elif member.lead:
        body = (f"Goal: {goal_line(team)} Your team is starting now. Plan the work, send each member its first task "
                f"with {send_how(team, member)}, then start "
                f"`{bus} listen {team.name} {member.name}` in the background.")
    elif team.lead is None:
        body = (f"Goal: {goal_line(team)} Your part: {member.focus or member.name}. The others start alongside you: "
                f"say hello to them with {sends}, start `{bus} listen {team.name} {member.name}` in the "
                "background, agree where your parts meet, then start on yours.")
    else:
        body = (f"Goal: {goal_line(team)} Your area: {member.focus or member.name}. Wait for your first task from "
                f"{team.lead.name} by running `{bus} listen {team.name} {member.name}` in the background "
                "(WaitMsBeforeAsync: 500) now, then end your turn.")
    return f"{tag} {role_hint} {body}"


def _codex_first_prompt(team: Team, member: Member, bus_role: Path | None, prompt: str | None,
                        resuming: bool = False) -> str:
    """Initial prompt for a Codex member: its team tag (how its thread is found) and its role."""
    tag = f"[tabmancer:{team.session_name(member)}]"
    role_hint = (f"Read your full team role in {bus_role} and follow it strictly." if bus_role is not None
                 else " ".join(role(team, member).split()))
    if resuming:
        return f"{tag} You are resuming your role on team {team.name}. {role_hint} {prompt or 'Continue your work.'}"
    if prompt:
        return f"{tag} {role_hint} {prompt}"
    if member.lead and team.decide and not any(not m.lead for m in team.members):
        body = (f"Goal: {goal_line(team)} The team is yours to make: decide the members the work needs (at most "
                f'{team.decide}), add them with the team_request tool (action: "add", lineup: "name: area; ..."), '
                "send each its first task with the team_send tool; their reports arrive here as new turns.")
    elif member.lead:
        body = (f"Goal: {goal_line(team)} Your team is starting now. Plan the work and send each member its first "
                "task with the team_send tool; their reports arrive here as new turns.")
    elif team.lead is None:
        body = (f"Goal: {goal_line(team)} Your part: {member.focus or member.name}. The others start alongside you: "
                "say hello to them with team_send, agree where your parts meet, then start on yours.")
    else:
        body = (f"Goal: {goal_line(team)} Your area: {member.focus or member.name}. Your first task comes from "
                f"{team.lead.name} as a new turn here: end your turn now.")
    return f"{tag} {role_hint} {body}"


def _first_prompt(team: Team, member: Member, bus_role: Path | None, prompt: str | None,
                  resuming: bool = False) -> str:
    made = _codex_first_prompt if member.cli == "codex" else _agy_first_prompt
    return made(team, member, bus_role, prompt, resuming=resuming)


def _agy_wrapper(team: Team, member: Member) -> tuple[str, ...]:
    return (*_tabmancer_cmd(), "team", "run", team.name, member.name, "--")


def launch(team: Team, member: Member, resume: Session | None = None, prompt: str | None = None) -> Launch:
    """A member's tab: a fresh session, or its last one resumed with the same name and role."""
    env = bus_env(team, member)
    if member.cli in ("agy", "codex"):  # each under `team run`, its role in the bus folder
        role_file(team, member)
        bus_role = teambus.write_role_file(team.name, team.session_name(member), role(team, member))
        if resume is not None:
            return Launch(
                title=f"{team.name}: {member.name}",
                cwd=home_of(team, resume.cwd),
                mode=Mode.RESUME,
                session_id=resume.session_id,
                prompt=_first_prompt(team, member, bus_role, prompt, resuming=True),
                cli=member.cli,
                options=options(team, member),
                env=env,
                wrapper=_agy_wrapper(team, member),
            )
        cwd = team.cwd
        if team.has_worktree(member):
            made = ensure_worktree(team, member)
            if made is not None:
                cwd = str(made)
        return Launch(
            title=f"{team.name}: {member.name}",
            cwd=cwd,
            mode=Mode.NEW,
            session_id="",
            prompt=_first_prompt(team, member, bus_role, prompt, resuming=False),
            cli=member.cli,
            options=options(team, member),
            env=env,
            wrapper=_agy_wrapper(team, member),
        )

    if resume is not None:  # in its own folder: a member with a worktree goes on there
        if team.uses_bus and prompt is None and not member.lead and team.lead is not None:
            prompt = (f"You are resuming your role on team {team.name}. Start `{BUS_CMD} listen {team.name} "
                      f"{member.name}` in the background (run_in_background: true) and continue waiting for messages.")
        return Launch(title=f"{team.name}: {member.name}", cwd=home_of(team, resume.cwd), mode=Mode.RESUME,
                      session_id=resume.session_id, prompt=prompt, options=options(team, member), env=env)
    if prompt is None and member.lead and team.decide and not any(not m.lead for m in team.members):
        prompt = (f"Goal: {goal_line(team)} The team is yours to make: decide the members the work needs (at most "
                  f'{team.decide}), add them with {ADD} {team.name} "name: area; ...", then send each its first '
                  "task.")
    elif prompt is None and member.lead:
        prompt = (f"Goal: {goal_line(team)} Your team is starting now. Plan the work and send each member its "
                  "first task.")
    elif prompt is None and team.lead is None:  # peers: nobody hands out work, so each starts on its own part
        if team.uses_bus:
            prompt = (f"Goal: {goal_line(team)} Your part: {member.focus or member.name}. The others start alongside "
                      f"you: say hello to them with `{BUS_CMD} send`, start `{BUS_CMD} listen {team.name} "
                      f"{member.name}` in the background, agree where your parts meet, then start on yours.")
        else:
            prompt = (f"Goal: {goal_line(team)} Your part: {member.focus or member.name}. The others start alongside "
                      "you: say hello to them with SendMessage, agree where your parts meet, then start on yours.")
    elif prompt is None and team.uses_bus and team.lead is not None:
        # In a bus team, Claude workers start a background bus listener so they wake up on agy/Claude messages.
        prompt = (f"You are {team.session_name(member)} on team {team.name}. Start listening for your first task from "
                  f"{team.lead.name} by running `{BUS_CMD} listen {team.name} {member.name}` in the background "
                  "(run_in_background: true), then wait for it.")
    # A fresh member with a worktree of its own: Claude Code makes it, .claude/worktrees/<session name>
    # on the branch worktree-<session name> (tried with 2.1.317), and the session works there. One
    # there already (a handover keeps the name of a member whose part goes on, and its worktree
    # stays) is where it goes on, on its branch with what is not merged yet: the session starts in
    # it, as a resume does, and asks Claude Code for none.
    cwd, worktree = team.cwd, ()
    if team.has_worktree(member):
        kept = existing_worktree(team, member)
        cwd, worktree = (str(kept), ()) if kept is not None else (team.cwd, ("--worktree", team.session_name(member)))
    return Launch(title=f"{team.name}: {member.name}", cwd=cwd, mode=Mode.NEW, session_id="",
                  prompt=prompt, options=(*options(team, member), *worktree), env=env)


def existing_worktree(team: Team, member: Member) -> Path | None:
    """The member's worktree from before, when git still has it (its .git file is there)."""
    there = Path(team.cwd, *WORKTREES, team.session_name(member))
    return there if (there / ".git").exists() else None


WORKTREES = (".claude", "worktrees")  # where `claude --worktree <name>` puts a worktree, in the repository


def repository_root(folder: str) -> bool:
    """Whether the folder is a git repository's top: where Claude Code puts its worktrees, and so
    where a team with worktrees has to be."""
    try:
        found = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=10, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return found.returncode == 0 and folder_key(found.stdout.strip()) == folder_key(str(Path(folder).resolve()))


def in_team_folder(team: Team, cwd: str) -> bool:
    """Whether a session's folder is the team's: the project or any folder in it. A transcript keeps
    the folder its session was in last, so a member that had gone into src/ was lost to its team
    (shown as never started, and started afresh). A worktree Claude Code made for a member
    (<project>/.claude/worktrees/<name>) is in the project too."""
    key, here = folder_key(team.cwd), folder_key(cwd)
    return bool(key) and (here == key or here.startswith(key.rstrip("\\/") + os.sep))


def home_of(team: Team, cwd: str) -> str:
    """Where a member's session goes on: in its worktree (<project>/.claude/worktrees/<name>, wherever
    in it the member was last), else in the project, though the member was last in a folder of it."""
    if cwd and in_team_folder(team, cwd) and folder_key(cwd) != folder_key(team.cwd):
        inside = Path(os.path.relpath(folder_key(cwd), folder_key(team.cwd))).parts
        if len(inside) >= 3 and tuple(part.casefold() for part in inside[:2]) == WORKTREES:
            return str(Path(team.cwd, *inside[:3]))
    return team.cwd


SKEW = timedelta(minutes=2)  # a member's session starts this long before its team at the most (clocks)


@functools.lru_cache(maxsize=64)
def _made(created: str) -> datetime | None:
    """When a team was made, as a moment: one saved without its offset is the local time it was saved in."""
    try:
        made = datetime.fromisoformat(created)
    except (TypeError, ValueError):
        return None  # a team saved before teams kept the time they were made
    return made if made.tzinfo else made.astimezone()


def since_made(team: Team, session: Session) -> bool:
    """Whether the session began once the team was made: a session of an earlier team of the same name,
    in the same folder, is not this one's member. A new team is a fresh one: the old team's sessions
    were taken for its members, and s (or tabmancer team start) brought them back instead. Compared as
    moments, so a change of time zone or the clocks going back cannot drop a team's own members."""
    made = _made(team.created)
    began = session.started or session.last_active
    if made is None or began is None:
        return True
    return (began if began.tzinfo else began.astimezone()) >= made - SKEW


def session_name_of(team: Team, launch: Launch) -> str:
    """The session name a member's launch opens under: its tab is titled '<team>: <member>'."""
    return f"{team.name}-{launch.title.partition(': ')[2]}"


OPENING_FILE = "opening.json"  # members tabmancer team start opened a moment ago: session name -> time
OPENING_SECONDS = 45  # long enough for a tab to start claude and its session to be listed as open


def opening(now: float | None = None) -> set[str]:
    """The session names team start opened in the last OPENING_SECONDS: a second start of the same
    member while its tab is still coming up would have two processes write one transcript."""
    data = read_json(OPENING_FILE)
    now = time.time() if now is None else now
    return {name for name, when in (data.items() if isinstance(data, dict) else ())
            if isinstance(when, (int, float)) and 0 <= now - when < OPENING_SECONDS}


def note_opening(names: list[str], now: float | None = None) -> None:
    now = time.time() if now is None else now
    data = read_json(OPENING_FILE)
    kept = {name: when for name, when in (data.items() if isinstance(data, dict) else ())
            if isinstance(when, (int, float)) and now - when < OPENING_SECONDS}
    write_json(OPENING_FILE, {**kept, **dict.fromkeys(names, now)})


def member_of(session: Session, saved: dict[str, Team] | None = None) -> tuple[Team, Member] | None:
    """The team and member a session plays: it bears a member's session name, in the team's folder
    (or a worktree of it), and began once the team was made."""
    for team in (load() if saved is None else saved).values():
        if not in_team_folder(team, session.cwd) or not since_made(team, session):
            continue
        bindings = teambus.read_bindings(team.name) if session.cli != "claude" else {}
        for member in team.members:
            if session.title == team.session_name(member):
                return team, member
            if member.cli == session.cli and bindings.get(member.name, {}).get("session_id") == session.session_id:
                return team, member
    return None


def as_member(session: Session, plain: Launch, saved: dict[str, Team] | None = None) -> Launch:
    """A session resumed from the list, as the member it plays when it plays one: with its name, its
    role and the setting that lets the team's messages in, all of which `claude --resume` alone leaves
    out. A fork or a new session is a session of its own, not the member."""
    found = member_of(session, saved) if plain.mode in (Mode.RESUME, Mode.AUTO) else None
    if found is None:
        return plain
    team, member = found
    if member.cli in ("agy", "codex"):
        role_file(team, member)
        bus_role = teambus.write_role_file(team.name, team.session_name(member), role(team, member))
        return replace(plain, title=f"{team.name}: {member.name}", options=options(team, member),
                       env=bus_env(team, member), wrapper=_agy_wrapper(team, member),
                       prompt=_first_prompt(team, member, bus_role, plain.prompt, resuming=True))
    return replace(plain, title=f"{team.name}: {member.name}", options=options(team, member),
                   env=bus_env(team, member))


def lead_last(sessions: list[Session], saved: dict[str, Team] | None = None) -> list[Session]:
    """The same sessions, with every team's orchestrator after the rest, so that its members are up
    when it starts handing out work (as start() does)."""
    saved = load() if saved is None else saved
    return sorted(sessions, key=lambda s: bool((found := member_of(s, saved)) and found[1].lead))


def start(team: Team, sessions: dict[str, Session | None], fresh: bool = False,
          only: set[str] | None = None) -> list[Launch]:
    """Tabs for the members that are not open (of only, the members named, when given): closed ones
    resumed (unless fresh), missing ones new. The orchestrator goes last, so the others are up when
    it starts handing out work."""
    out = []
    for member in sorted(team.members, key=lambda m: m.lead):
        if only is not None and member.name not in only:
            continue
        session = sessions.get(member.name)
        if session is not None and session.exit is Exit.RUNNING:
            continue
        out.append(launch(team, member, resume=None if fresh else session))
    return out


# --- who is who ----------------------------------------------------------------------------

def live_names() -> dict[str, dict]:
    """Session name -> pid file, for the sessions open right now."""
    return {str(info["name"]): info for info in _registry_sessions() if info.get("name")}


def _agy_live_state(session: Session | None, presence: dict) -> str:
    """Live state for a running agy team member: needs your OK when a step waits for it (a permission
    prompt in its tab: it looks busy otherwise, and its turn never ends), resting when listening, busy
    when active, else idle."""
    from . import agy

    if session is not None and agy.waiting_for_you(session.session_id):
        return "permission"
    if presence.get("listening"):
        return "resting"
    if session is not None and session.path is not None and session.path.suffix == ".jsonl":
        meta = agy._read_transcript(session.path)
        if meta.get("busy"):
            return "busy"
    if session is not None and session.status is Status.CUT_OFF:
        return "busy"
    return "idle"


def member_sessions(team: Team, sessions: list[Session], live: dict[str, dict] | None = None) -> dict[str, Session | None]:
    """Each member's session: the open one with its name, else the newest one that had it."""
    live = live_names() if live is None else live
    by_id = {s.session_id: s for s in sessions}
    presence = teambus.read_presence(team.name) if team.uses_bus else {}
    bindings = teambus.read_bindings(team.name) if team.uses_bus else {}
    out: dict[str, Session | None] = {}
    for member in team.members:
        name = team.session_name(member)
        if member.cli in ("agy", "codex"):  # found by binding or the tag of its first prompt
            pinfo = presence.get(member.name)
            binfo = bindings.get(member.name)
            found: Session | None = None
            for sid in (pinfo.get("session_id") if pinfo else "", binfo.get("session_id") if binfo else ""):
                cand = by_id.get(str(sid)) if sid else None
                if cand is not None and since_made(team, cand):
                    found = cand
                    break
            if found is None:
                tag = f"[tabmancer:{name}]".lower()
                named = [s for s in sessions if s.cli == member.cli and in_team_folder(team, s.cwd) and since_made(team, s)
                         and (s.title.lower() == name.lower() or tag in (s.first_prompt or "").lower())]
                found = max(named, key=lambda s: s.last_active, default=None)
                if found is not None and (not binfo or binfo.get("session_id") != found.session_id):
                    teambus.save_binding(team.name, member.name, found.session_id, cli=member.cli,
                                         created=team.created)
            if pinfo and pinfo.get("runner_alive"):
                state = (_agy_live_state(found, pinfo) if member.cli == "agy"  # codex.read gives no live state yet
                         else (found.live_state if found is not None and found.live_state else "idle"))
                if found is not None:
                    found = replace(found, title=name, exit=Exit.RUNNING, status=Status.RUNNING, live_state=state)
                else:
                    found = Session(session_id=str(pinfo.get("session_id") or f"{member.cli}:{name}"), path=None,
                                    cwd=team.cwd, title=name, cli=member.cli, exit=Exit.RUNNING,
                                    status=Status.RUNNING, live_state=state)
            elif found is not None and found.title != name:
                found = replace(found, title=name)
            out[member.name] = found
            continue

        info = live.get(name)
        found = by_id.get(str(info.get("sessionId"))) if info else None
        if found is not None and not since_made(team, found):
            found = None  # an earlier team's member of that name, open still
        if found is None:
            named = [s for s in sessions if s.title == name and in_team_folder(team, s.cwd) and since_made(team, s)]
            found = max(named, key=lambda s: s.last_active, default=None)
        if (found is not None and team.uses_bus and presence.get(member.name, {}).get("listening")
                and found.exit is Exit.RUNNING and found.live_state.lower() in ("idle", "done", "busy", "shell")):
            found = replace(found, live_state="resting")
        out[member.name] = found
    return out


# --- what they said ------------------------------------------------------------------------

@dataclass
class Message:
    at: datetime
    sender: str     # a member's name, YOU, or another session's name
    receiver: str
    text: str
    summary: str = ""
    delivered: bool = True  # False: sent, not read by the receiver yet


_cache: dict[str, tuple[int, list[dict]]] = {}  # transcript -> (bytes read, events found)


def _events(path: Path) -> list[dict]:
    """Sends, receipts and your prompts in a transcript, read incrementally."""
    offset, found = _cache.get(str(path), (0, []))
    try:
        size = path.stat().st_size
        if size < offset:
            offset, found = 0, []
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return found
    found = list(found)
    taken = {event.get("id") for event in found if event.get("id")}  # messages received, by id
    end = data.rfind(b"\n") + 1  # a line still being written waits for next time
    for raw in data[:end].split(b"\n"):
        # Only what can matter: messages from peers, SendMessage calls, and your own prompts
        # (user records that are not tool results, which are most of a transcript's bulk).
        wanted = (b'"peer"' in raw or (b'"SendMessage"' in raw and b'"type":"assistant"' in raw)
                  or (b'"type":"user"' in raw and b'"tool_use_id"' not in raw))
        if not raw or not wanted:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("isSidechain"):
            continue
        record = sanitize(record)
        at = _moment(record.get("timestamp"))
        origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
        attached = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
        if record.get("type") == "attachment" and attached.get("type") == "queued_command":
            # A message that came while the session was busy is taken in mid-turn: no user
            # record, only this attachment. Without it, it stayed "on its way" for good.
            origin = attached.get("origin") if isinstance(attached.get("origin"), dict) else {}
        if record.get("type") in ("user", "attachment") and origin.get("kind") == "peer":
            if origin.get("senderTaskId"):  # a subagent of this session handing its report back: not the team
                continue
            if origin.get("msg_id") and origin["msg_id"] in taken:  # recorded both ways: once
                continue
            taken.add(origin.get("msg_id"))
            found.append({"kind": "in", "at": at, "from": str(origin.get("name") or origin.get("from") or "?"),
                          "text": str(origin.get("body") or ""), "id": origin.get("msg_id")})
        elif record.get("type") == "user" and _is_real_prompt(record):
            found.append({"kind": "you", "at": at, "text": _text((record.get("message") or {}).get("content"))})
        elif record.get("type") == "assistant":
            content = (record.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SendMessage":
                    given = block.get("input") if isinstance(block.get("input"), dict) else {}
                    found.append({"kind": "out", "at": at, "to": str(given.get("to") or "?"),
                                  "text": str(given.get("message") or ""), "summary": str(given.get("summary") or "")})
    _cache[str(path)] = (offset + end, found)
    return found


def _moment(stamp) -> datetime:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return datetime.now()


def conversation(team: Team, sessions: dict[str, Session | None], live: dict[str, dict] | None = None) -> list[Message]:
    """Everything said in the team, oldest first: your prompts, messages read, messages on their way."""
    live = live_names() if live is None else live
    pipes = {str(info.get("messagingSocketPath")): name for name, info in live.items()
             if info.get("messagingSocketPath")}
    received: list[Message] = []
    sent: list[Message] = []
    for member, session in sessions.items():
        if session is None or session.path is None:
            continue
        if session.cli == "codex":
            continue  # not a Claude transcript: what it was sent is on the bus, read below
        if session.cli == "agy":
            from . import agy

            for event in agy.user_events(session.path):
                sender = TABMANCER if from_tabmancer(team, event["text"]) else YOU
                received.append(Message(event["at"], sender, member, _shorten(_strip_team_tag(event["text"]), 400)))
            continue
        for event in _events(session.path):
            if event["kind"] == "in":
                received.append(Message(event["at"], team.member_of(event["from"]), member, event["text"]))
            elif event["kind"] == "you":
                sender = TABMANCER if from_tabmancer(team, event["text"]) else YOU
                received.append(Message(event["at"], sender, member, _shorten(event["text"], 400)))
            else:
                target = event["to"].removeprefix("uds:")
                to = team.member_of(pipes.get(target, pipes.get(event["to"], event["to"])))
                sent.append(Message(event["at"], member, to, event["text"], event["summary"], delivered=False))
    # A message read by its receiver is shown once, as read; the rest are still on their way.
    read = {(m.sender, m.receiver, m.text) for m in received}
    pending = [m for m in sent if (m.sender, m.receiver, m.text) not in read]
    said = {(m.sender, m.receiver, m.text): m for m in sent}
    for message in received:  # in the order things were said, not read
        original = said.get((message.sender, message.receiver, message.text))
        if original is not None:
            message.summary, message.at = original.summary, original.at
    combined = received + pending
    if team.uses_bus or (teambus.team_dir(team.name) / "messages.jsonl").is_file():
        seen = {(m.sender, m.receiver, m.text) for m in combined}
        for row in teambus.messages(team.name, since=_made(team.created)):
            sender = team.member_of(row["from"])
            receiver = team.member_of(row["to"])
            if (sender, receiver, row["text"]) in seen:
                continue
            seen.add((sender, receiver, row["text"]))
            combined.append(Message(row["at"], sender, receiver, row["text"], row["summary"],
                                    delivered=bool(row["delivered"])))
    return sorted(combined, key=lambda m: m.at)


def cap_of(session_id: str, team: Team | None = None) -> int:
    """The context cap a session runs under, as far as tabmancer knows, in tokens: its team's for a
    member of a team with one, else the one tabmancer last opened it with (picks); 0 for none."""
    if team is not None and (tokens := picks.cap_tokens(team.cap)):
        return tokens
    return picks.cap_of(session_id)


@dataclass
class TeamState:
    """A team as the dashboard last read it: each member's session and everything said."""

    team: Team
    sessions: dict[str, Session | None]
    said: list[Message]

    def open_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s is not None and s.exit is Exit.RUNNING)

    def between_members(self) -> list[Message]:
        """What the members said to each other (your prompts and other sessions left out)."""
        names = {m.name for m in self.team.members}
        return [m for m in self.said if m.sender in names and m.receiver in names]

    def tokens(self, counted: dict) -> int:
        """Every member's session over its whole life, as the dashboard counts it."""
        return sum(use.total for s in self.sessions.values()
                   if s is not None and (use := counted.get(s.session_id)) is not None)


def counts(said: list[Message]) -> dict[tuple[str, str], int]:
    """(sender, receiver) -> how many messages went that way."""
    out: dict[tuple[str, str], int] = {}
    for message in said:
        out[(message.sender, message.receiver)] = out.get((message.sender, message.receiver), 0) + 1
    return out
