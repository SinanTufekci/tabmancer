"""Which keys the dashboard's footer shows, dimmed or not at all, and what a key does on the row at hand,
as plain rules with no UI behind them.

The row at hand (Row) is what the footer depends on: its kind ("team", "team-closed", "open",
"member-open", "closed", "member-closed", "none", or "ticked" while any are ticked), whether a team is
unfolded (how many are ticked, for "ticked"), and whether anyone waits for you. allowed() says
whether a key works (True), does nothing on this row but stays in the footer, dimmed (None), or is
not there at all (False). words() says what a key does here where it is not what its name says.
The terminal dashboard (app.SessionPicker) asks these; a desktop window's keys can ask the same."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from typing import NamedTuple

from . import viewmodel as VM
from .sessions import Exit, Session, Status

# The list's own keys: off in the live view (l), which lists processes, not sessions to pick.
PICKER_ONLY = {"toggle", "cycle_mode", "toggle_all", "save_workspace", "open", "hide", "toggle_hidden",
               "fold_teams"}
# Keys that do nothing on a kind of row (Row.kind): dimmed in the footer there, never hidden.
TEAM_ROWS = {"team", "team-closed"}
IDLE_ON = {
    "toggle": {"open", "member-open"},  # an open session cannot be ticked
    "cycle_mode": {"open", "member-open", *TEAM_ROWS},
    "hide": {"open", "member-open"},  # open sessions always show
    "pin": TEAM_ROWS,
    "prompt": TEAM_ROWS,
    "rename": {"member-closed", "member-open", *TEAM_ROWS},  # members keep the names the team finds them by
    "monitor": {"open", "closed", "none", "ticked"},  # a team's, from its row or a member's
}


class Row(NamedTuple):
    """What the footer's words and dimmed keys depend on (a tuple: the footer is made again only when
    it changes)."""

    kind: str
    unfolded: bool | int  # a team's row: shown with its members under it; "ticked": how many
    waiting: bool  # some open session waits for you (j)


NONE = Row("none", False, False)  # an empty list


def waiting(sessions: Iterable[Session]) -> bool:
    """Whether some open session waits for you (needs you, or is done and waiting)."""
    return any(s.exit is Exit.RUNNING and VM.state_of(s)[2] < 2 for s in sessions)


def row(sessions: Iterable[Session], team: tuple[str, bool, bool] | None, session: Session | None,
        ticked: int, members: Collection[str]) -> Row:
    """The row at hand. team: (its name, whether it is at work, whether it is unfolded) on a team's
    row; session: the highlighted one otherwise; ticked: how many are ticked; members: the ids of
    the sessions listed as a team's members."""
    anyone = waiting(sessions)
    if team is not None:
        _, at_work, unfolded = team
        return Row("team" if at_work else "team-closed", unfolded, anyone)
    if session is None:
        return Row("none", False, anyone)
    if ticked:  # with how many: the footer says "Open 3", and stood at "Open 1" from the first tick
        return Row("ticked", ticked, anyone)
    member = "member-" if session.session_id in members else ""
    return Row(member + ("open" if session.status is Status.RUNNING else "closed"), False, anyone)


def allowed(action: str, *, on_top: bool, monitor: bool, projects_focused: bool, live_mode: bool,
            own: Collection[str], sessions: Iterable[Session], at: Callable[[], Row]) -> bool | None:
    """Whether a key works now (True), is dimmed on this row (None), or is not there (False).
    on_top: a dialog or the team view is up, and the dashboard's own keys (own) are its; monitor:
    this window is a team's monitor, which never takes them; at: the row at hand, asked only when
    a rule needs it."""
    # Enter is a priority binding, so it would otherwise reach past a dialog or the project list.
    if action == "open" and (on_top or projects_focused):
        return False
    # Only the dashboard's own keys: the toolkit's (Tab between boxes, Ctrl+P) stay on. A monitor never
    # takes them, not even before its screen is up: x, f, space would hide, pin, tick in the list.
    if (on_top or monitor) and action in own:
        return False
    if monitor and action in ("toggle_mascot", "cycle_theme"):
        return False  # saved for the dashboard: a monitor leaves them as they are
    if action == "continue_at_reset":  # shown only when there is something to continue
        return any(VM.at_limit(s) for s in sessions)
    if live_mode and action in PICKER_ONLY:
        return False
    if not on_top and action in IDLE_ON and at()[0] in IDLE_ON[action]:
        return None  # nothing to do on this row: the key stays in the footer, dimmed
    if not on_top and action == "next_waiting" and not at()[2]:
        return None  # nobody waits for you
    return True


def words(action: str, at: Row, ticked: int) -> str | None:
    """What a key does on the row at hand, where it is not what its own name says."""
    kind = at[0]
    if action == "open":
        if ticked:
            return f"Open {ticked}"
        return {"team": "Team view", "team-closed": "Team view",
                "open": "Its tab", "member-open": "Its tab"}.get(kind)
    if action == "summarize":
        return {"team": "Start rest", "team-closed": "Start team"}.get(kind)
    if action == "hide":
        return {"team": "Close team", "team-closed": "Hide team"}.get(kind)
    if action == "toggle" and kind == "team-closed":
        return "Tick team"
    if action == "handover" and kind in TEAM_ROWS:
        return "Hand team over"
    return None
