"""The dashboard and one team as a screen shows them, read without a screen: the stores (G1).

A front end polls one from a worker thread every couple of seconds and draws what it hands back:
the desktop monitor a TeamStore, the desktop main window a DashboardStore. Neither imports a UI, and
both read what the terminal dashboard reads, about as often: the pid files at every poll and `claude
agents --json` at every eighth, the token counts and git every minute, the plan limits every five.
They keep nothing a dashboard keeps. They never record the plan limits (the dashboard's reading is
shown while it is fresh), never ask agy for its quota (the one the dashboard saved is shown), and
never claim the actor lock: they watch.

The reading itself is here once: the terminal dashboard (app.py) calls the same helpers.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from datetime import time as clock_time

from . import accounts, actor, agy, clis, gitstate, marks, teams, tokens, usage
from . import viewmodel as VM
from .config import read_json
from .sessions import (
    Exit,
    Session,
    _registry_sessions,
    find_sessions,
    folder_key,
    live_sessions,
    mark_live,
    other_sessions,
    read_session,
    transcript_path,
)

DAYS = 14  # the sessions read when the store starts, as the dashboard lists them
FULL_EVERY = 8  # every 8th poll asks `claude agents --json`; the others read the pid files
OTHERS_SECONDS = 60  # Codex's and agy's sessions have no live feed: read again each minute
TOKENS_SECONDS = 60
GIT_SECONDS = 60
GIT_RETRY_SECONDS = 10  # a folder git has just begun failing for is asked again this soon, once
USAGE_SECONDS = 300
ACCOUNTS_SECONDS = 5  # /login in any session switches who a CLI is signed in as
ACTOR_SECONDS = 10
CHART_DAYS = 14  # the token chart's days: tokens are counted for at least these (app.CHART_DAYS)
RESTING = ("idle", "done")  # a member other than the lead in one of these waits for its lead, not for you


# --- what the dashboard reads, as it reads it (app.py calls these) -----------------------------------

def live_entries(full: bool, background: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """(session id -> what its process says, the background sessions among them), as the dashboard's
    poll reads them. A full poll asks `claude agents --json`, and its background sessions are kept for
    the quick polls: those read the pid files, nearly free but blind to background sessions, and
    carry the kept ones over."""
    if full:
        live = live_sessions()
        return live, {sid: e for sid, e in live.items() if e.get("kind") == "background"}
    return {e["sessionId"]: e for e in _registry_sessions()} | background, background


def fresh_sessions(known: dict[str, Session], live: dict[str, dict], full: bool) -> dict[str, Session]:
    """The Claude sessions brought up to date, as the dashboard's poll does it (app.read_live).

    live: session id -> what its process says (live_entries). Only what is open is read, and of a
    transcript only what it gained since; a full poll also reads the ones known open that it no
    longer lists, which is how a session is found closed.
    """
    watched = set(live) | ({sid for sid, s in known.items() if s.exit is Exit.RUNNING} if full else set())
    updates: dict[str, Session] = {}
    for sid in watched:
        old = known.get(sid)
        if old is not None and old.cli != "claude":
            continue
        path = old.path if old else transcript_path(sid)
        if path is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if old is not None and old.size == size and sid in live:
            fresh = replace(old)  # transcript unchanged: only the live state can differ
        else:
            fresh = read_session(path, follow=True)  # new activity (read on from the last read), a new session, or one that closed
        if fresh is None:
            continue
        mark_live(fresh, live.get(sid))
        updates[sid] = fresh
    # agy keeps no record of what is open: its own db says.
    updates |= agy.live_updates({sid: s for sid, s in known.items() if sid not in updates})
    return updates


def resting_ids(team_states: dict[str, teams.TeamState]) -> set[str]:
    """The sessions of the teams' members other than their leads: idle, they wait for the lead's
    next message, not for you. A team of peers has none: each one, idle, waits for you."""
    return {s.session_id for state in team_states.values() for name, s in state.sessions.items()
            if s is not None and state.team.lead is not None and name != state.team.lead.name}


def rest(updates: Iterable[Session], resting: set[str]) -> None:
    """A member done for now waits for its lead, not for you: 'resting', in place."""
    for fresh in updates:
        if fresh.session_id in resting and fresh.live_state.lower() in RESTING:
            fresh.live_state = "resting"


def other_clis() -> list[str]:
    """The CLIs other than Claude Code whose sessions are listed: those turned on, and agy while a
    team has an agy member."""
    others = [name for name in clis.enabled() if name != "claude"]
    if "agy" not in others and teams.has_cli("agy"):
        others.append("agy")
    return others


def listed_others(days: float, include_all: bool) -> list[Session] | None:
    """Codex's and agy's sessions read again, as the list shows them; None when no such CLI is in
    use (then nothing of theirs is put in or taken out)."""
    others = other_clis()
    if not others:
        return None
    return [s for s in other_sessions(days, others) if include_all or s.entrypoint in ("", "cli")]


def take_others(sessions: dict[str, Session], fresh: list[Session]) -> set[str]:
    """Codex's and agy's sessions as read again (listed_others) in place of those known, in place:
    the ids that changed, came or went."""
    current = {sid: s for sid, s in sessions.items() if s.cli != "claude"}
    wanted = {s.session_id: s for s in fresh}
    if current == wanted:
        return set()
    gone = current.keys() - wanted.keys()
    for sid in gone:
        del sessions[sid]
    sessions.update(wanted)
    return {sid for sid in wanted if current.get(sid) != wanted[sid]} | gone


def read_team_states(listed: list[Session], days: float) -> tuple[dict[str, teams.TeamState], dict[str, Session]]:
    """(the teams with a member in the list, open or not, each member's session and what was said;
    the agy members' sessions as their team found them, to put in the list). Only what the
    transcripts gained since is read; a team whose sessions are all older than the list is left out."""
    saved = teams.load()
    if any(team.uses_bus for team in saved.values()):
        by_id = {s.session_id: s for s in listed}
        for found_agy in agy.find(days=min(days, 14)):
            by_id[found_agy.session_id] = found_agy
        listed = list(by_id.values())
    live = teams.live_names()
    states: dict[str, teams.TeamState] = {}
    agy_updates: dict[str, Session] = {}
    for name, team in saved.items():
        found = teams.member_sessions(team, listed, live)
        if any(s is not None for s in found.values()):
            states[name] = teams.TeamState(team, found, teams.conversation(team, found, live))
            for s in found.values():
                if s is not None and s.cli == "agy" and not s.session_id.startswith("agy:"):
                    agy_updates[s.session_id] = s
    return states, agy_updates


def git_folders(sessions: Iterable[Session], watch: str | None = None) -> list[str]:
    """The folders whose git the list shows: every session's; a monitor's (watch: its team), only
    the team's folder and its worktrees."""
    folders = [s.cwd for s in sessions]
    if watch is not None:
        team = teams.load().get(watch or "")
        folders = [f for f in folders if team is not None and teams.in_team_folder(team, f)]
    return folders


def kept_git(known: dict[str, gitstate.GitState], states: dict[str, gitstate.GitState],
             failed: set[str]) -> dict[str, gitstate.GitState]:
    """git as just read, where a folder git could not answer for keeps what was known."""
    return states | {key: state for key, state in known.items() if key in failed}


def watched_limits(account: accounts.Account | None, ask: bool = True) -> list[usage.Limit] | None:
    """The plan limits for a window that only watches: the acting dashboard's reading while it is
    fresh (one `claude -p /usage`, not two); else, with ask, Claude asked, never recorded (the
    dashboard records its own, or they would count twice). None: neither."""
    shared = usage.latest(account=account.key if account else None)
    if shared is not None:
        return shared
    return usage.read_all() if ask else None


def uses_agy(sessions: Iterable[Session], team_states: dict[str, teams.TeamState]) -> bool:
    """Whether agy is in use: an agy session in the list, or an agy member in a team."""
    return (any(s.cli == "agy" for s in sessions)
            or any(m.cli == "agy" for state in team_states.values() for m in state.team.members))


def read_armed() -> dict[str, datetime]:
    """The sessions to continue at their reset, as config.json keeps them."""
    saved = read_json("config.json")
    armed = saved.get("continue_at_reset") if isinstance(saved, dict) else None
    found = {}
    for sid, moment in (armed.items() if isinstance(armed, dict) else ()):
        try:
            found[sid] = datetime.fromisoformat(moment)
        except (TypeError, ValueError):
            pass
    return found


# --- one team -----------------------------------------------------------------------------------

@dataclass
class Snapshot:
    """What a team monitor draws, as the store read it last."""

    at: datetime
    view: VM.TeamView
    state: teams.TeamState
    limits: list[usage.Limit] = field(default_factory=list)  # Claude's plan limits; [] when not read
    agy_quota: dict = field(default_factory=dict)  # agy.quota_view(); {} for a team without agy members
    hours: list[int] = field(default_factory=list)  # the team's tokens per hour today, midnight first


def open_unsaid(team: teams.Team, found: dict[str, Session | None], live: dict[str, dict]) -> dict[str, Session | None]:
    """The members open but with nothing said yet (no transcript to list them by), as open sessions
    of their names, as the team view does (TeamScreen.poll)."""
    out = dict(found)
    for member in team.members:
        if member.cli != "claude" or out.get(member.name) is not None:
            continue
        info = live.get(team.session_name(member))
        if info:
            out[member.name] = Session(session_id=str(info.get("sessionId")), path=None, cwd=team.cwd,
                                       title=team.session_name(member), exit=Exit.RUNNING,
                                       live_state=str(info.get("status") or "idle"))
    return out


def resting(team: teams.Team, found: dict[str, Session | None]) -> dict[str, Session | None]:
    """A member other than the lead, open and done for now, waits for its lead's next message, not for
    you: 'resting', as the dashboard shows it (app.apply_live_updates). A team of peers has none."""
    if team.lead is None:
        return found
    out = dict(found)
    for name, session in found.items():
        if (session is not None and name != team.lead.name and session.cli == "claude"
                and session.exit is Exit.RUNNING and session.live_state.lower() in RESTING):
            out[name] = replace(session, live_state="resting")
    return out


def team_hours(report: tokens.Report, ids: set[str], now: datetime | None = None) -> list[int]:
    """Today's tokens per hour of the given sessions, from midnight up to the hour now."""
    now = now or datetime.now()
    midnight = datetime.combine(now.date(), clock_time.min).timestamp()
    out = [0] * (now.hour + 1)
    for event in reversed(report.events):  # oldest first: today's are at the end
        if event.at < midnight:
            break
        hour = datetime.fromtimestamp(event.at).hour
        if event.session in ids and hour < len(out):
            out[hour] += event.use.total
    return out


def git_words(state: gitstate.GitState | None) -> dict | None:
    """A folder's git as the view carries it (MemberView.git)."""
    return {"branch": state.branch, "changed": state.changed, "ahead": state.ahead} if state else None


class Polled:
    """When each thing a store reads is read again, by its clock (seconds, monotonic by default)."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.due: dict[str, float] = {}  # what -> when it is read again

    def is_due(self, what: str, every: float) -> bool:
        now = self.clock()
        if now < self.due.get(what, 0.0):
            return False
        self.due[what] = now + every
        return True


class TeamStore(Polled):
    """One team, read again at each poll(). Not for two threads at once: a front end polls it from one
    worker at a time and draws the Snapshot it returns on its own thread."""

    def __init__(self, name: str, *, days: float = DAYS, ask_limits: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(clock)
        self.name = name
        self.days = days
        self.ask_limits = ask_limits  # no fresh reading of the dashboard's: ask `claude -p /usage`, as a monitor does
        self.team: teams.Team | None = None
        self.sessions: dict[str, Session] = {}  # the team folder's sessions by id
        self.elsewhere: set[str] = set()  # open sessions read once and found outside the team's folder
        self.background: dict[str, dict] = {}  # background sessions, from the last full poll
        self.report: tokens.Report | None = None
        self.tokens: dict[str, tokens.Tokens] = {}
        self.git: dict[str, gitstate.GitState] = {}  # folder key -> its git
        self.git_folders: set[str] = set()  # the folder keys git was read for
        self.limits: list[usage.Limit] = []
        self.polls = 0

    def ours(self, session: Session) -> bool:
        return self.team is not None and teams.in_team_folder(self.team, session.cwd)

    # --- what it reads ------------------------------------------------------------------------

    def read_listed(self) -> None:
        """The team folder's sessions as the dashboard lists them when it starts (Codex's and agy's
        too, for a team with such members)."""
        self.sessions = {s.session_id: s for s in find_sessions(self.days, quick=True) if self.ours(s)}
        self.elsewhere.clear()
        self.git, self.git_folders = {}, set()

    def read_live(self, full: bool) -> None:
        """The open Claude sessions brought up to date: from the pid files, and on a full poll from
        `claude agents --json` (background sessions too, and the ones that closed). An open session
        found outside the team's folder is not read again till the next full poll."""
        live, self.background = live_entries(full, self.background)
        if full:
            self.elsewhere.clear()
        live = {sid: e for sid, e in live.items() if sid not in self.elsewhere}
        for sid, fresh in fresh_sessions(self.sessions, live, full).items():
            if self.ours(fresh):
                self.sessions[sid] = fresh
            else:
                self.elsewhere.add(sid)

    def read_others(self) -> None:
        """Codex's and agy's sessions in the team's folder, read again (they have no live feed)."""
        names = sorted({m.cli for m in self.team.members if m.cli != "claude"}) if self.team else []
        fresh = {s.session_id: s for s in other_sessions(self.days, names) if self.ours(s)} if names else {}
        self.sessions = {sid: s for sid, s in self.sessions.items() if s.cli == "claude"} | fresh

    def read_tokens(self) -> None:
        self.report = tokens.collect(self.days)
        self.tokens = self.report.per_session(whole_life=True)

    def read_git(self, folders: set[str]) -> None:
        """The members' folders in git (their worktrees, when they have them). One git could not
        answer for keeps what was known."""
        states, failed = gitstate.read_all(sorted(folders))
        self.git = kept_git(self.git, states, failed)
        self.git_folders = {folder_key(f) for f in folders}

    def read_limits(self) -> None:
        """The dashboard's reading while it is fresh; else, with ask_limits, Claude asked (one
        `claude -p /usage`), never recorded: the dashboard records its own."""
        limits = watched_limits(accounts.claude(), self.ask_limits)
        if limits is not None:
            self.limits = limits

    # --- the poll -----------------------------------------------------------------------------

    def poll(self) -> Snapshot | None:
        """Read what is due and the team as it stands; None when there is no such team (any more)."""
        team = teams.load().get(self.name)
        if team is None:
            self.team = None
            return None
        first = self.team is None or folder_key(self.team.cwd) != folder_key(team.cwd)
        self.team = team
        if first:
            self.read_listed()
            self.due["others"] = self.clock() + OTHERS_SECONDS  # find_sessions has just read them
        elif self.is_due("others", OTHERS_SECONDS):
            self.read_others()
        self.read_live(full=self.polls % FULL_EVERY == 0)  # the first poll asks `claude agents --json`
        self.polls += 1
        listed = list(self.sessions.values())
        if team.uses_bus:  # agy members' state moves faster than a minute: the team view reads them each time
            by_id = {s.session_id: s for s in listed}
            for found_agy in agy.find(days=2):
                if self.ours(found_agy):
                    by_id[found_agy.session_id] = found_agy
            listed = list(by_id.values())
        live = teams.live_names()
        found = resting(team, open_unsaid(team, teams.member_sessions(team, listed, live), live))
        members = [s for s in found.values() if s is not None]
        if self.is_due("tokens", TOKENS_SECONDS):
            self.read_tokens()
        folders = {s.cwd for s in members if s.cwd}
        if self.is_due("git", GIT_SECONDS) or {folder_key(f) for f in folders} - self.git_folders:
            self.read_git(folders)  # every minute, and at once for a member come up in a folder not read yet
        if self.is_due("limits", USAGE_SECONDS):
            self.read_limits()
        state = teams.TeamState(team, found, teams.conversation(team, found, live))
        quota = agy.load_quota()
        view = VM.team_view(state, self.tokens, {}, agy_usage=quota,
                            git_of=lambda folder: git_words(self.git.get(folder_key(folder))))
        hours = team_hours(self.report, {s.session_id for s in members}) if self.report is not None else []
        return Snapshot(datetime.now(), view, state, list(self.limits),
                        agy.quota_view(quota) if "agy" in view.clis else {}, hours)


# --- the whole dashboard ------------------------------------------------------------------------

@dataclass
class DashSnapshot:
    """What the dashboard draws, as the store read it last. What only the window knows (its filter,
    the rows ticked, the l mode) is not in it: the window applies that (viewmodel.info_words)."""

    at: datetime
    view: VM.View
    sessions: dict[str, Session]  # every session known, by id: the store's own go on changing
    team_states: dict[str, teams.TeamState]
    hidden: set[str]  # session ids, hidden one by one or with their project (marks.json)
    pinned: set[str]
    report: tokens.Report | None  # tokens per day and hour, for the cards and the chart; None till counted
    accounts: list[accounts.Account] = field(default_factory=list)  # who each CLI is signed in as
    shared_emails: set[str] = field(default_factory=set)  # emails seen in more than one organisation (accounts.words' org)
    agy_bar: dict = field(default_factory=dict)  # agy.bar_view(): the chain in the top bar; {} while agy is not in use
    acting: dict | None = None  # the lock of the dashboard that acts, while it runs (actor.json); None: none does


class DashboardStore(Polled):
    """The whole dashboard, read again at each poll(): what the terminal dashboard reads, as often as
    it does (app.poll_tick and its timers), by the same helpers. It watches, as a second dashboard
    does: it never claims the actor lock, records neither the plan limits nor an account, never runs
    agy, and saves no marks (the token counts' cache, which every reader of them shares, aside). Not
    for two threads at once."""

    def __init__(self, *, days: float = DAYS, include_all: bool = False, ask_limits: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(clock)
        self.days = days
        self.include_all = include_all  # sessions started by tools too (claude -p), as `tabmancer --all`
        self.ask_limits = ask_limits  # no fresh reading of the dashboard's: ask `claude -p /usage`, as a second dashboard does
        self.sessions: dict[str, Session] = {}
        self.background: dict[str, dict] = {}  # background sessions, from the last full poll
        self.team_states: dict[str, teams.TeamState] = {}
        self.marks = marks.Marks()
        self.armed: dict[str, datetime] = {}
        self.report: tokens.Report | None = None
        self.tokens: dict[str, tokens.Tokens] = {}
        self.git: dict[str, gitstate.GitState] = {}  # folder key -> its git
        self.git_failed: set[str] = set()
        self.limits: list[usage.Limit] = []
        self.accounts: list[accounts.Account] = []
        self.shared_emails: set[str] = set()
        self.agy_bar: dict = {}
        self.acting: dict | None = None
        self.polls = 0

    # --- what it reads ------------------------------------------------------------------------

    def read_listed(self) -> None:
        """Every session of the last days, as the dashboard lists them when it starts."""
        self.sessions = {s.session_id: s for s in find_sessions(self.days, self.include_all, quick=True)}

    def read_live(self, full: bool) -> None:
        live, self.background = live_entries(full, self.background)
        updates = fresh_sessions(dict(self.sessions), live, full)
        rest(updates.values(), resting_ids(self.team_states))
        self.sessions.update(updates)

    def read_teams(self) -> None:
        self.team_states, agy_updates = read_team_states(list(self.sessions.values()), self.days)
        self.sessions.update(agy_updates)

    def read_others(self) -> None:
        fresh = listed_others(self.days, self.include_all)
        if fresh is not None:
            take_others(self.sessions, fresh)

    def read_tokens(self) -> None:
        self.report = tokens.collect(max(self.days, CHART_DAYS))
        self.tokens = self.report.per_session(whole_life=True)

    def read_git(self) -> None:
        states, failed = gitstate.read_all(git_folders(self.sessions.values()))
        self.git = kept_git(self.git, states, failed)
        newly, self.git_failed = failed - self.git_failed, set(failed)
        if newly:  # asked again soon, once: a folder git always refuses is not asked without end
            self.due["git"] = min(self.due.get("git", 0.0), self.clock() + GIT_RETRY_SECONDS)

    def read_limits(self) -> None:
        """The plan limits (watched_limits) and agy's chain from its saved quota, read together, as
        the dashboard reads them."""
        limits = watched_limits(accounts.claude(), self.ask_limits)
        if limits is not None:
            self.limits = limits
        self.agy_bar = agy.bar_view() if uses_agy(self.sessions.values(), self.team_states) else {}

    def read_accounts(self) -> None:
        self.accounts = accounts.signed_in()
        self.shared_emails = accounts.shared_emails()

    def read_acting(self) -> None:
        held = actor.holder()
        self.acting = held if actor.running(held) else None

    # --- the poll -----------------------------------------------------------------------------

    def poll(self) -> DashSnapshot:
        """Read what is due and the dashboard as it stands."""
        if self.polls == 0:
            self.read_listed()
            self.read_teams()  # who rests, before the first live read: the dashboard reads its teams with its list
            self.due["others"] = self.clock() + OTHERS_SECONDS  # find_sessions has just read them
        elif self.is_due("others", OTHERS_SECONDS):
            self.read_others()
        self.read_live(full=self.polls % FULL_EVERY == 0)  # the first poll asks `claude agents --json`
        self.polls += 1
        self.read_teams()
        if self.is_due("tokens", TOKENS_SECONDS):
            self.read_tokens()
        if self.is_due("git", GIT_SECONDS):
            self.read_git()
        if self.is_due("limits", USAGE_SECONDS):
            self.read_limits()
        if self.is_due("accounts", ACCOUNTS_SECONDS):
            self.read_accounts()
        if self.is_due("actor", ACTOR_SECONDS):
            self.read_acting()
        self.marks = marks.Marks.load()  # pinned and hidden in any dashboard show here at the next poll
        self.armed = read_armed()
        hidden = {sid for sid, s in self.sessions.items() if self.marks.is_hidden(s)}
        pinned = set(self.marks.pinned)
        view = VM.build(self.sessions, hidden=hidden, pinned=pinned, git=self.git, team_states=self.team_states,
                        tokens=self.tokens, limits=self.limits, armed=self.armed, queued={})
        return DashSnapshot(view.at, view, dict(self.sessions), dict(self.team_states), hidden, pinned, self.report,
                            list(self.accounts), set(self.shared_emails), dict(self.agy_bar), self.acting)
