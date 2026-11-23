"""One team as a screen shows it, read without a screen: the one-team store (G1a).

The desktop monitor (tabmancer.desktop) polls it from a worker thread every couple of seconds and
draws what it hands back. It imports no UI, and it reads what the dashboard reads, about as often:
the pid files at every poll and `claude agents --json` at every eighth, the token counts and git
every minute, the plan limits every five. It keeps nothing a dashboard keeps. It never records the
plan limits (the dashboard's reading is shown while it is fresh), never asks agy for its quota (the
one the dashboard saved is shown), and never claims the actor lock: it watches.

G1 grows it into the whole dashboard's store and moves the dashboard's polling onto it;
`fresh_sessions` is app.read_live's loop, kept the same until then.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from datetime import time as clock_time

from . import accounts, agy, gitstate, teams, tokens, usage
from . import viewmodel as VM
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
USAGE_SECONDS = 300
RESTING = ("idle", "done")  # a member other than the lead in one of these waits for its lead, not for you


@dataclass
class Snapshot:
    """What a team monitor draws, as the store read it last."""

    at: datetime
    view: VM.TeamView
    state: teams.TeamState
    limits: list[usage.Limit] = field(default_factory=list)  # Claude's plan limits; [] when not read
    agy_quota: dict = field(default_factory=dict)  # agy.quota_view(); {} for a team without agy members
    hours: list[int] = field(default_factory=list)  # the team's tokens per hour today, midnight first


def fresh_sessions(known: dict[str, Session], live: dict[str, dict], full: bool) -> dict[str, Session]:
    """The Claude sessions brought up to date, as the dashboard's poll does it (app.read_live).

    live: session id -> what its process says (live_sessions() for a full poll, the pid files for a
    quick one). Only what is open is read, and of a transcript only what it gained since; a full poll
    also reads the ones known open that it no longer lists, which is how a session is found closed.
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
            fresh = read_session(path, follow=True)
        if fresh is None:
            continue
        mark_live(fresh, live.get(sid))
        updates[sid] = fresh
    return updates


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


class TeamStore:
    """One team, read again at each poll(). Not for two threads at once: a front end polls it from one
    worker at a time and draws the Snapshot it returns on its own thread."""

    def __init__(self, name: str, *, days: float = DAYS, ask_limits: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.days = days
        self.ask_limits = ask_limits  # no fresh reading of the dashboard's: ask `claude -p /usage`, as a monitor does
        self.clock = clock
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
        self.due: dict[str, float] = {}  # what -> when it is read again (the clock's seconds)

    def is_due(self, what: str, every: float) -> bool:
        now = self.clock()
        if now < self.due.get(what, 0.0):
            return False
        self.due[what] = now + every
        return True

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
        if full:
            live = live_sessions()
            self.background = {sid: e for sid, e in live.items() if e.get("kind") == "background"}
            self.elsewhere.clear()
        else:
            live = {e["sessionId"]: e for e in _registry_sessions()} | self.background
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
        self.git = states | {key: state for key, state in self.git.items() if key in failed}
        self.git_folders = {folder_key(f) for f in folders}

    def read_limits(self) -> None:
        """The dashboard's reading while it is fresh; else, with ask_limits, Claude asked (one
        `claude -p /usage`), never recorded: the dashboard records its own."""
        account = accounts.claude()
        shared = usage.latest(account=account.key if account else None)
        if shared is not None:
            self.limits = shared
        elif self.ask_limits:
            self.limits = usage.read_all()

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
