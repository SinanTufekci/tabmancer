"""Teams: several sessions in one project, one of them the orchestrator, talking to each other.

Each member is an ordinary Claude Code session in its own tab, so it can be closed,
resumed and handed over like any other. It starts with a name (`--name <team>-<member>`),
a role (`--append-system-prompt`) and a setting that lets messages from other sessions in
(`--settings`, a file of tabmancer's own: your settings are not touched). The members talk
with Claude Code's own cross-session messaging: SendMessage to a member's name, and the
message arrives in that session as a new turn, idle or not.

tabmancer never carries the messages. It reads them back from the transcripts to show the
team at work: an outgoing message is a SendMessage tool call, an incoming one a user
record whose origin is a peer.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from . import picks
from .config import config_dir, read_json, write_json
from .launch import Launch, Mode
from .sessions import (
    Exit,
    Session,
    _is_real_prompt,
    _registry_sessions,
    _shorten,
    _text,
    folder_key,
    sanitize,
)

FILE = "teams.json"
SETTINGS_FILE = "team-settings.json"
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
DECIDE = "lead decides"  # the line-up the lead makes itself, with `tabmancer team add`
DECIDE_MOST = 4  # members it may add unless told otherwise
ADD = "tabmancer team add"  # the one command a deciding lead may run without asking


@dataclass
class Member:
    name: str
    focus: str = ""
    lead: bool = False
    model: str = ""  # its session's --model (an alias such as sonnet, or a full name); empty: the default
    effort: str = ""  # its session's --effort (picks.EFFORTS); empty: the default

    @property
    def pick(self) -> picks.Pick:
        return picks.Pick(self.model, self.effort)


@dataclass
class Team:
    name: str
    cwd: str
    goal: str
    members: list[Member] = field(default_factory=list)
    created: str = ""
    decide: int = 0  # the lead picks the members itself, at most this many; 0: the line-up given
    background: bool = False  # the members run as background sessions (claude --bg), the lead in a tab

    @property
    def lead(self) -> Member:
        return next((m for m in self.members if m.lead), self.members[0])

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


def parse_members(text: str, lead_ok: bool = False) -> list[Member]:
    """"api: endpoints @sonnet @high; ui: screens" (or one per line) to members. A name without a
    focus is fine, and so is one without a model or an effort (picks.split). With lead_ok, a "lead"
    line is the orchestrator's: its focus, model and effort; otherwise it is left out, as a team has
    one lead."""
    members, seen = [], set()
    for part in re.split(r"[;\n]", text):
        part, pick = picks.split(part)
        name, _, focus = part.partition(":")
        name, focus = clean(name), " ".join(focus.split())
        if not name or name in seen or (name == LEAD and not lead_ok):
            continue
        seen.add(name)
        members.append(Member(name, focus or (LEAD_FOCUS if name == LEAD else ""), lead=name == LEAD,
                              model=pick.model, effort=pick.effort))
    return members


def new_team(name: str, cwd: str, goal: str, members: list[Member], decide: int = 0,
             background: bool = False) -> Team:
    """The lead first: the one among members, or one with the usual focus. With decide, the lead
    picks the rest itself, at most that many; with background, the members run without tabs."""
    lead = next((m for m in members if m.lead), Member(LEAD, LEAD_FOCUS, lead=True))
    return Team(clean(name), cwd, " ".join(goal.split()), [lead, *(m for m in members if not m.lead)],
                datetime.now().isoformat(timespec="seconds"), decide, background)


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
    team.goal = " ".join(goal.split())
    save(saved)
    return team


def left_note(team: Team, gone: str) -> str:
    """What the lead is told when a member is taken off the team from the dashboard: it started
    with the old line-up in its role."""
    others = roster(team, team.lead)
    return (f"Team change: {team.name}-{gone} is no longer on the team, so send it no more work. "
            f"The members now: {others or 'none besides you'}.")


def goal_note(team: Team) -> str:
    """What the lead is told when the goal changes: it started with the old one in its role."""
    return f"Team change: the team goal is now: {team.goal} Plan again with the members where their work changes."


def joining(team: Team, members: list[Member]) -> list[Launch]:
    """Tabs for members who join a team already at work: each introduces itself to the lead."""
    return [launch(team, m, prompt=(f"You just joined the team {team.name}. Introduce yourself to "
                                    f"{team.session_name(team.lead)} with SendMessage and ask for work."))
            for m in members]


def from_tabmancer(team: Team, text: str) -> bool:
    """Whether a prompt is one tabmancer started a member with (joining(), launch(), in_background()),
    not yours."""
    return text.startswith((f"You just joined the team {team.name}.", f"Goal: {team.goal} ",
                            f"You are on the team {team.name}, in the background."))


def in_background(team: Team, launch: Launch) -> bool:
    """Whether this member's session starts in the background: a team's members do when it says so,
    its lead never (it is where you talk to the team)."""
    return team.background and launch.title != f"{team.name}: {team.lead.name}"


def background_start(team: Team, launch: Launch) -> Launch:
    """A member started in the background with nothing to say yet: claude --bg takes a first message,
    so it is told to wait for the lead's."""
    if launch.prompt:
        return launch
    return replace(launch, prompt=(f"You are on the team {team.name}, in the background. "
                                   f"{team.session_name(team.lead)} sends your first task as a message: wait for it."))


def load() -> dict[str, Team]:
    data = read_json(FILE)
    teams = {}
    for item in data.get("teams", []) if isinstance(data, dict) else []:
        try:
            members = [Member(m["name"], m.get("focus", ""), bool(m.get("lead")), str(m.get("model") or ""),
                              str(m.get("effort") or "") if m.get("effort") in picks.EFFORTS else "")
                       for m in item["members"]]
            teams[item["name"]] = Team(item["name"], item["cwd"], item.get("goal", ""), members,
                                       item.get("created", ""), int(item.get("decide") or 0),
                                       bool(item.get("background")))
        except (KeyError, TypeError):
            continue
    return teams


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
    return ", ".join(f"{team.session_name(m)} ({'orchestrator' if m.lead else m.focus or m.name})"
                     for m in team.members if m is not skip)


def role(team: Team, member: Member) -> str:
    """The system prompt a member starts with. One line: it travels on a command line."""
    others = roster(team, member)
    common = (f"Team: {team.name}, working in {team.cwd}. Team goal: {team.goal}. "
              + (f"The other members, by the name to use with SendMessage: {others}. " if others else
                 "There are no other members yet. ")
              + "Messages from them arrive as new turns, so you never need to poll. Keep messages short "
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
                "Tell the user here when the goal is met or a decision is theirs.")
    if member.lead:
        return (f"You are {team.session_name(member)}, the orchestrator of this team. {common} Split the "
                "goal into tasks, send each member its first task with SendMessage, keep track of who does "
                "what, answer their questions, sort out overlaps and check the results. Do small glue work "
                "yourself and leave the members' areas to them. Tell the user here when the goal is met or "
                "a decision is theirs. The members start alongside you: if ListAgents does not show one yet, "
                "wait a moment and look again.")
    return (f"You are {team.session_name(member)}, a member of this team. Your area: {member.focus or member.name}. "
            f"{common} {team.session_name(team.lead)} is the orchestrator: it hands out the work, and you "
            "report to it with SendMessage when you finish something or get stuck. Ask another member directly "
            "when you need something from their area.")


def options(team: Team, member: Member) -> tuple[str, ...]:
    # --allowedTools=... in one piece: written apart it takes every word after it as a tool.
    # A deciding lead may add members without asking: that one command, and only it.
    tools = f"SendMessage,ListAgents,Bash({ADD}:*)" if member.lead and team.decide else "SendMessage,ListAgents"
    return ("--name", team.session_name(member), *member.pick.flags(), "--settings", str(settings_file()),
            f"--allowedTools={tools}", "--append-system-prompt", role(team, member))


def launch(team: Team, member: Member, resume: Session | None = None, prompt: str | None = None) -> Launch:
    """A member's tab: a fresh session, or its last one resumed with the same name and role."""
    if resume is not None:
        return Launch(title=f"{team.name}: {member.name}", cwd=team.cwd, mode=Mode.RESUME,
                      session_id=resume.session_id, prompt=prompt, options=options(team, member))
    if prompt is None and member.lead and team.decide and not any(not m.lead for m in team.members):
        prompt = (f"Goal: {team.goal} The team is yours to make: decide the members the work needs (at most "
                  f'{team.decide}), add them with {ADD} {team.name} "name: area; ...", then send each its first '
                  "task.")
    elif prompt is None and member.lead:
        prompt = (f"Goal: {team.goal} Your team is starting now. Plan the work and send each member its "
                  "first task.")
    return Launch(title=f"{team.name}: {member.name}", cwd=team.cwd, mode=Mode.NEW, session_id="",
                  prompt=prompt, options=options(team, member))


def member_of(session: Session, saved: dict[str, Team] | None = None) -> tuple[Team, Member] | None:
    """The team and member a session plays: it bears a member's session name, in the team's folder."""
    for team in (load() if saved is None else saved).values():
        if folder_key(session.cwd) != folder_key(team.cwd):
            continue
        for member in team.members:
            if session.title == team.session_name(member):
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
    return replace(plain, title=f"{team.name}: {member.name}", options=options(team, member))


def lead_last(sessions: list[Session], saved: dict[str, Team] | None = None) -> list[Session]:
    """The same sessions, with every team's orchestrator after the rest, so that its members are up
    when it starts handing out work (as start() does)."""
    saved = load() if saved is None else saved
    return sorted(sessions, key=lambda s: bool((found := member_of(s, saved)) and found[1].lead))


def start(team: Team, sessions: dict[str, Session | None], fresh: bool = False) -> list[Launch]:
    """Tabs for the members that are not open: closed ones resumed (unless fresh), missing ones new.
    The orchestrator goes last, so the others are up when it starts handing out work."""
    out = []
    for member in sorted(team.members, key=lambda m: m.lead):
        session = sessions.get(member.name)
        if session is not None and session.exit is Exit.RUNNING:
            continue
        out.append(launch(team, member, resume=None if fresh else session))
    return out


# --- who is who ----------------------------------------------------------------------------

def live_names() -> dict[str, dict]:
    """Session name -> pid file, for the sessions open right now."""
    return {str(info["name"]): info for info in _registry_sessions() if info.get("name")}


def member_sessions(team: Team, sessions: list[Session], live: dict[str, dict] | None = None) -> dict[str, Session | None]:
    """Each member's session: the open one with its name, else the newest one that had it."""
    live = live_names() if live is None else live
    by_id = {s.session_id: s for s in sessions}
    key = folder_key(team.cwd)
    out: dict[str, Session | None] = {}
    for member in team.members:
        name = team.session_name(member)
        info = live.get(name)
        found = by_id.get(str(info.get("sessionId"))) if info else None
        if found is None:
            named = [s for s in sessions if s.title == name and folder_key(s.cwd) == key]
            found = max(named, key=lambda s: s.last_active, default=None)
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
    return sorted(received + pending, key=lambda m: m.at)


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
