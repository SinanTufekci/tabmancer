"""A team's keys, in one copy: s starts it (a closed team whose cache is gone asks first), x twice closes
its members' tabs, a adds members, d twice takes one off, g changes the goal (the lead is told), H hands
the picked member alone over, Enter on a member opens it or goes to its tab; and the dashboard's own: a team's sessions opened (open_team), a
message for a member (tell_team), members joining a team at work (join_team). The terminal's team row and
its team screen both call these; each is a Surface, what the keys ask of the window they run in.

UI thread only. The first of two presses says what the second does; a second press counts within so
many seconds of the first, by the clock it is given (a timer that forgot the first would forget a newer
one too, when it went off after that one was made). Surfaces given the same presses share them: on the
dashboard a team's row and its view do (EngineState.team_presses), so x on one and x on the other closes it."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from . import actions, handover, teambus, teams
from .actions import Changed, Note
from .config import UI_TRIES, Busy
from .launch import Launch
from .sessions import Exit, Session, Status

if TYPE_CHECKING:
    from .teamforms import JoinTeam

CLOSE_AGAIN_SECONDS = 6  # x once on a team: x again this soon closes its members' tabs
COLD_AGAIN_SECONDS = 12  # a team whose cache is gone: a second press this soon starts it (cold_note)
REMOVE_AGAIN_SECONDS = 6  # d once on a member: d again this soon takes it off
DELETE_AGAIN_SECONDS = 6  # d once on a saved team (the terminal's T list, the window's Delete team…): again deletes it
NOBODY_OPEN = "No member is open."
PICK_FIRST = "Pick a member with ← → first."
NOTHING_TO_HAND_OVER = "This member has no session to hand over yet."
# A saved team deleted (TeamKeys.delete), in the terminal T list's words: its sessions stay, and so do the tabs
# of its members open now.
DELETE_KEY = "delete a saved team (d twice; the picked one first in the list): its sessions stay"
DELETE_TITLE = "Delete a team"
DELETE_BUTTON = "Delete team…"  # on a team's card: its menu, the drawer's button
NO_TEAM_TO_DELETE = "There is no saved team to delete."


def delete_again(team: str) -> str:
    return f"Press d again to delete the team {team}. Its sessions stay."


def deleted(team: str) -> str:
    return f"The team {team} is deleted. Its sessions stay."


class Surface(Protocol):
    """What the team keys ask of the window they run in."""

    def say(self, note: Note) -> None:
        ...

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """A team's sessions opened, as the dashboard opens them (TeamKeys.open_team)."""
        ...

    def open_sessions(self, launches: list[Launch]) -> None:
        ...

    def cold_start_ok(self, team: teams.Team, members: dict[str, Session | None]) -> bool:
        """Whether a closed team starts now (TeamKeys.cold_start_ok, the dashboard's)."""
        ...

    def recent(self) -> set[str] | None:
        """The sessions opened a moment ago, still starting (openings.Openings.recent); None: not known."""
        ...

    def kept_handovers(self) -> dict:
        """The teams' handovers written and not taken yet (EngineState.kept_handovers)."""
        ...

    def close_tabs(self, names: list[str]) -> None:
        """These members' tabs closed off the UI thread, and actions.closed_note said."""
        ...

    def adopt(self, team: teams.Team) -> None:
        """The team as saved now, in this view of it."""
        ...

    def go_to_tab(self, session: Session) -> None:
        ...

    def put_on_clipboard(self, text: str) -> None:
        ...

    def hand_over(self, session: Session) -> None:
        """H: this member's session handed over to a fresh one, as h hands a session over (the dialog asks
        how; a busy one is queued till it finishes)."""
        ...


class Dashboard(Surface, Protocol):
    """What the dashboard's own keys ask besides: how its sessions open, its members' messages."""

    def after_update(self, what: str, then: Callable[[], None]) -> bool:
        """While claude update runs: then, once it is done (True: it waits)."""
        ...

    def start_in_background(self, team: teams.Team, launches: list[Launch], then: list[Launch]) -> None:
        """claude --bg for each off the UI thread (actions.start_in_background), then the tabs."""
        ...

    def member_session(self, team: str, member: str) -> Session | None:
        """A member's session as the list has it now."""
        ...

    def notes(self) -> dict[str, tuple[str, str]]:
        """The messages waiting for members (EngineState.notes): id -> (name, message)."""
        ...

    def deliver_notes(self) -> None:
        ...

    def show_team(self, team: teams.Team) -> None:
        """The team's own view."""
        ...


def removal_on(team: str, member: str) -> str:
    """What a first d is held on: that team's member, never a member of that name on another team (a
    member's name has no '/'; a team's may)."""
    return f"{team}/{member}"


def at_work(members: dict[str, Session | None]) -> bool:
    """Whether a member of the team is open: a adds members that open at once, else they join it closed."""
    return any(session is not None and session.exit is Exit.RUNNING for session in members.values())


class TeamKeys:
    """A surface's team keys, with its presses: the team x was pressed on once (closing), the team a cold
    start was asked for (cold), the team and member d was pressed on (removing). clock: seconds, time.monotonic."""

    def __init__(self, surface: Surface, clock: Callable[[], float],
                 presses: dict[str, tuple[str, float]] | None = None) -> None:
        self.surface, self.clock = surface, clock
        # what was pressed once -> (on what, until when); shared when given
        self.presses: dict[str, tuple[str, float]] = presses if presses is not None else {}

    def again(self, what: str, on: str, seconds: float) -> bool:
        """A press of what on `on`: True when it is the second within seconds of the first (both are
        done with then); else it is a first press, held until seconds from now."""
        now = self.clock()
        held = self.presses.get(what)
        if held is not None and held[0] == on and now < held[1]:
            del self.presses[what]
            return True
        self.presses[what] = (on, now + seconds)
        return False

    def held(self, what: str) -> str | None:
        """What a press of `what` was made on, while a second one would still count."""
        held = self.presses.get(what)
        return held[0] if held is not None and self.clock() < held[1] else None

    @property
    def closing(self) -> str | None:
        return self.held("close")

    @property
    def cold(self) -> str | None:
        return self.held("cold")

    @property
    def removing(self) -> tuple[str, str] | None:
        """(team, member) d was pressed on once."""
        held = self.held("remove")
        if held is None:
            return None
        team, _, member = held.rpartition("/")
        return team, member

    def say(self, words: str, seconds: float, severity: str = "information") -> None:
        self.surface.say(Note(words, severity, seconds))

    # --- s, x, Enter ---------------------------------------------------------------------------------

    def start(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """s: every member not open, a closed one resumed as itself, one never started new, the lead last
        (actions.start_team); one opened a moment ago is left to come up. A handover written and kept is
        dropped: the team goes on as it was."""
        surface = self.surface
        surface.kept_handovers().pop(team.name, None)
        start = actions.start_team(team, members, surface.recent())
        if not start.launches:
            self.say(start.words, 3)
            return
        if not surface.cold_start_ok(team, members):
            return  # its cache is gone: said what going on costs, s again starts it
        surface.open_team(team, start.launches)

    def cold_start_ok(self, team: teams.Team, members: dict[str, Session | None]) -> bool:
        """Whether a closed team starts now: at once, unless its members' cache is gone; then the first
        press says what going on costs and what a handover saves (cold_note), and a second one within
        COLD_AGAIN_SECONDS starts it."""
        note = actions.cold_note(team, members)
        if note is None:
            self.presses.pop("cold", None)
            return True
        if self.again("cold", team.name, COLD_AGAIN_SECONDS):
            return True
        self.say(note, COLD_AGAIN_SECONDS)
        return False

    def open_team(self, team: teams.Team, launches: list[Launch]) -> None:
        """The dashboard's: a team's sessions in tabs, or for a team whose members run in the background,
        its members as background sessions first and then the lead's tab, once they are up. A member
        opened a moment ago, here or by `tabmancer team start`, is not opened again
        (actions.team_opening)."""
        surface = cast("Dashboard", self.surface)
        if any(launch.cli == "claude" for launch in launches) and surface.after_update(
                f"{team.name} starts", lambda: surface.open_team(team, launches)):
            return
        opening = actions.team_opening(team, launches, UI_TRIES)  # on a UI thread: no long wait for the lock
        if opening.words:
            self.say(opening.words, 3)
            return
        if opening.quiet:
            surface.start_in_background(team, opening.quiet, opening.tabs)
        elif opening.tabs:
            surface.open_sessions(opening.tabs)

    def close(self, team: teams.Team, members: dict[str, Session | None]) -> None:
        """x, twice: every open member's tab closes, as on /exit; the sessions stay (s starts them)."""
        note = actions.closing_note(team, members)
        if note is None:
            self.say(NOBODY_OPEN, 3)
            return
        if not self.again("close", team.name, CLOSE_AGAIN_SECONDS):
            self.say(note, CLOSE_AGAIN_SECONDS)
            return
        self.surface.close_tabs(actions.open_names(team, members))

    def open_member(self, team: teams.Team, member: teams.Member | None, session: Session | None) -> None:
        """Enter on a member: its tab when it is open, else it opens (resumed when it has a session); one
        in the background has no tab."""
        if member is None:
            self.say(PICK_FIRST, 3)
            return
        if session is not None and session.status is Status.BACKGROUND:
            self.say(f"{member.name} runs in the background, with no tab: B attaches to it.", 5)
            return
        if session is not None and session.exit is Exit.RUNNING:
            self.surface.go_to_tab(session)
            return
        self.surface.open_sessions([teams.launch(team, member, resume=session if session and session.path else None)])

    def hand_over(self, member: teams.Member | None, session: Session | None) -> None:
        """H: the picked member alone handed over to a fresh session with the same name and role; one with
        no session yet (or none picked) has nothing to hand over."""
        if member is None or session is None or session.path is None:
            self.surface.say(Note(NOTHING_TO_HAND_OVER, "warning"))
            return
        self.surface.hand_over(session)

    # --- a, d, g: the line-up and the goal -----------------------------------------------------------

    def add(self, team: teams.Team, text: str | None, at_work: bool) -> None:
        """a's answer: new members for the team. At work, each opens and introduces itself to the lead;
        closed, they join it and start with it."""
        added = actions.add_members(team, text or "", UI_TRIES)  # on a UI thread: no long wait for the lock
        if added.warning:
            self.say(added.warning, 8, "warning")
        if not added.new:
            return
        self.surface.adopt(added.team)
        if at_work:
            self.surface.open_team(added.team, added.launches)
        else:
            self.say(added.joined_words, 6)

    def remove(self, team: teams.Team, member: teams.Member | None, session: Session | None) -> Changed | None:
        """d, twice: the member taken off the team (actions.remove_member), for the caller to show and the
        lead to hear (changed). The first press says what the second does; none on the lead."""
        refusal = actions.removal_refusal(member)
        if refusal is not None or member is None:  # removal_refusal says why when none is picked
            if refusal is not None:
                self.surface.say(refusal)
            return None
        if not self.again("remove", removal_on(team.name, member.name), REMOVE_AGAIN_SECONDS):
            self.surface.say(actions.removal_ask(team, member, session))
            return None
        try:
            return actions.remove_member(team, member.name, UI_TRIES)
        except Busy as error:
            self.say(teams.not_saved_words(f"{member.name} is still on the team", error), 8, "warning")
            return None

    def delete(self, name: str) -> bool:
        """d, twice, on a saved team: deleted (teams.drop under the teams' lock, UI_TRIES), as the terminal's T
        list deletes one; its sessions stay, and so do its members' open tabs. The first press says what the
        second does; a handover its lead wrote for it, not taken yet, goes with it. True once deleted."""
        if not self.again("delete", name, DELETE_AGAIN_SECONDS):
            self.say(delete_again(name), DELETE_AGAIN_SECONDS)
            return False
        try:
            teams.drop(name, UI_TRIES)
        except Busy as error:
            self.say(teams.not_saved_words(f"{name} is not deleted", error), 8, "warning")
            return False
        self.surface.kept_handovers().pop(name, None)
        self.say(deleted(name), 5)
        return True

    def change_goal(self, team: teams.Team, goal: str, members: dict[str, Session | None]) -> None:
        """g's answer: the team's goal changed (actions.change_goal) and its lead told; said when the
        teams could not be changed now."""
        try:
            changed = actions.change_goal(team, goal, UI_TRIES)
        except Busy as error:
            self.say(teams.not_saved_words("The goal is not changed", error), 8, "warning")
            return
        self.changed(changed, members)

    def changed(self, changed: Changed | None, members: dict[str, Session | None]) -> None:
        """A member taken off, the goal changed: the team as saved now, and its lead told."""
        if changed is None:
            return
        self.surface.adopt(changed.team)
        self.tell_lead(changed.team, members, changed.note, changed.done)

    def tell_lead(self, team: teams.Team, members: dict[str, Session | None], note: str, done: str) -> None:
        """A change, for the lead to hear (actions.tell_lead): over the team bus when the team uses one,
        else on the clipboard and you in its tab, to paste it."""
        told = actions.tell_lead(team, members, note, done)
        if told.clipboard:
            self.surface.put_on_clipboard(told.clipboard)
        if told.go_to is not None:
            self.surface.go_to_tab(told.go_to)
        self.surface.say(told)

    # --- the dashboard's: a message for a member, members joining a team at work ----------------------

    def tell_team(self, name: str, to: str, message: str) -> None:
        """A message for a team's member (its lead, or the peer the goal concerns): typed into its tab once
        it waits for you, as c types into one; a closed one opens with it."""
        surface = cast("Dashboard", self.surface)
        team = teams.load().get(name)
        if team is None:
            return
        member = next((m for m in team.members if team.session_name(m).casefold() == to.casefold()), None)
        member = member or team.lead or (team.members[0] if team.members else None)
        if member is None:
            return
        who = team.session_name(member)
        session = surface.member_session(name, member.name)
        if session is None or session.exit is not Exit.RUNNING:
            if who in teams.opening():  # opened a moment ago (s, team start): open_team would leave it be
                surface.put_on_clipboard(message)
                self.say(f"{who} is opening already: the message is on the clipboard, to paste into its tab "
                         "once it is up (Ctrl+V, Enter).", 14)
                return
            # On the tab's command line: a goal of a few pages would not fit there, so its start, and all of
            # it on the clipboard.
            prompt = teams.cut(message, teams.MESSAGE_IN_PROMPT, " (the rest follows: the user pastes it)")
            surface.open_team(team, [teams.launch(team, member, resume=session if session is not None and session.path
                                                  else None, prompt=prompt)])
            if prompt != " ".join(message.split()):
                surface.put_on_clipboard(message)
                self.say(f"{who} was not open: it opens with the start of the message, all of which is on the "
                         "clipboard to paste in after it.", 14)
            else:
                self.say(f"{who} was not open: it opens with the message.", 8)
            return
        if session.status is Status.BACKGROUND:  # no tab to type into
            surface.put_on_clipboard(message)
            self.say(f"{who} runs in the background: the message is on the clipboard. Enter on its row "
                     "attaches to it.", 14)
            return
        if team.uses_bus:
            teambus.send(team.name, teams.YOU, member.name, message)
            self.say(f"Sent to {who} over the team bus.", 5)
            surface.adopt(team)
            return
        surface.notes()[session.session_id] = (who, message)
        if session.live_state not in handover.IDLE:
            self.say(f"{who} is at work: the message goes into its tab once it waits for you.", 8)
        surface.deliver_notes()

    def join_team(self, join: JoinTeam) -> None:
        """T's Ctrl+R found the goal adds to a team at work: the goal grows by it, the members join and
        open, each introducing itself, and the lead (or the peer it concerns) is told; then its view."""
        surface = cast("Dashboard", self.surface)
        team = teams.load().get(join.team)
        if team is None:
            self.say(f"There is no team {join.team} any more.", 6, "warning")
            return
        asked = [m.name for m in teams.parse_members(join.members)]
        try:
            new, note = teams.add_members(team, join.members, UI_TRIES)
        except Busy as error:
            self.say(teams.not_saved_words(f"Nobody joined {team.name}", error), 8, "warning")
            return
        if note:
            self.say(note[0].upper() + note[1:] + ".", 8, "warning")
        message = join.message
        if new:  # the goal grows only for members who joined; the lead hears who did not, after all
            try:
                teams.add_to_goal(team.name, join.goal, UI_TRIES)
            except Busy as error:  # they joined: they open and the lead hears of them all the same
                self.say(teams.not_saved_words(f"{team.name}'s goal did not grow", error), 8, "warning")
            team = teams.load().get(team.name, team)
            surface.open_team(team, teams.joining(team, new, adds=join.goal))
            if left := [name for name in asked if name not in {m.name for m in new}]:
                message += f" (Not joining after all: {', '.join(left)}.)"
        else:  # nobody could join: the lead gets the goal, not word of members who never come
            message = f"A request from the user for the team: {join.goal}"
        self.tell_team(team.name, join.to, message)
        surface.adopt(team)
        surface.show_team(team)
