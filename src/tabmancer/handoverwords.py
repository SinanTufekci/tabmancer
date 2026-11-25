"""What the dashboard says when a session or a team is handed over, with no UI behind it: the ways the
h dialog offers and what handing over does, the notes as it goes, and the team handover dialog's
words and line-up preview. Parts are (words, style) as in teamwords. The terminal dashboard (app,
handoverview, teamview) puts them into rich Text, the desktop window into its own; both say the same."""

from __future__ import annotations

from dataclasses import dataclass

from . import clis, handover, picks, teams
from .actions import Note, Told, writer_words
from .newforms import CAP_SHORT
from .palette import AMBER, CYAN, GREEN, MUTED
from .sessions import Exit, Session
from .teamwords import Part
from .tokens import human

TEAM_ABOUT = ("The lead wrote this for its successor. The team starts again under its name: a fresh lead "
              "with this handover, and the members on the right, fresh. The old sessions stay, resumable.")


@dataclass(frozen=True)
class Way:
    """A way h writes the handover: its id ("self", "transcript"), its title, why to pick it, and how
    it goes."""

    id: str
    title: str
    why: str
    how: str


def ways(session: Session) -> list[Way]:
    """The ways for this session, the most complete first: the session writes it itself (Claude, or
    agy on the chain's model with agy here to run it: write_itself resumes it), or Claude writes it
    from the transcript."""
    out = [Way("transcript", "Write it from the transcript", "cheaper; misses what was never written down",
               f"Claude ({handover.MODEL}) writes it in about a minute; the session is not resumed")]
    if session.cli == "claude" or (session.cli == "agy" and clis.installed("agy")):
        on = " on the chain's model (agy)" if session.cli == "agy" else ""
        out.insert(0, Way("self", "Let the session write it", "the most complete: it knows everything",
                          f"resumed once in the background{on}, without a tab; it writes the handover and exits"))
    return out


def about(session: Session) -> str:
    """What handing over does, for this one: closed first when open, after it finishes when busy."""
    is_open = session.exit is Exit.RUNNING
    busy = is_open and session.live_state not in handover.IDLE
    return ("A fresh session takes over, with a full handover as its first message: the goal, "
            "the decisions, what is done, what is open, the gotchas and the next steps."
            + (" This session is closed first (its tab closes); you can still resume it later." if is_open else "")
            + (" It is working now, so this happens as soon as it finishes." if busy else "")
            + (" To go on with the same work in the same session instead, /compact in its tab "
               "shrinks its context." if is_open else ""))


def started_note(session: Session, how: str) -> Told:
    """Said as the handover starts (session: as the list has it now)."""
    closing = "Closing it; " if session.exit is Exit.RUNNING else ""
    return Told(f"{closing}{writer_words(how)} in the background. The fresh session opens when it is done.",
                seconds=10, title=f"Handing over {session.project}")


def handed_note(session: Session, left_open: str = "") -> Told:
    """Said once the fresh session opens; left_open: why the old one could not be closed."""
    if left_open:
        return Told(f"Handed {session.project} over to a fresh session, but the old one is still open: "
                    f"{left_open}.", severity="warning", seconds=15, title="Handed over")
    return Told(f"Handed {session.project} over to a fresh session.", seconds=8, title="Handed over")


def team_handing_note(team: teams.Team) -> Told:
    return Told(f"{team.name}: its lead is writing the handover and the line-up for the rest. The new team is "
                "shown to you once it is done.", seconds=10, title="Handing the team over")


def team_not_started_note(team: teams.Team) -> Note:
    return Note(f"{team.name} is not started again: its members are closed. h in its view shows the "
                "handover again; s starts the team as it was.", seconds=10)


def team_handed_note(team: teams.Team, fresh: teams.Team) -> Told:
    count = sum(not m.lead for m in fresh.members)
    return Told(f"{team.name} handed over: a fresh lead with the handover and {count} fresh "
                f"member{'s' if count != 1 else ''}. The old sessions stay, resumable.",
                seconds=10, title="Team handed over")


def cap_words(chosen: str) -> str:
    """'300K: …' for the cap picked in the team handover dialog."""
    return f"{CAP_SHORT.get(chosen, chosen)}: {picks.CAP_WORDS[chosen]}"


def lineup_problems(team: teams.Team, members: list[teams.Member]) -> list[str]:
    """What the line-up still needs. A name kept from before goes on in its worktree, on its branch
    (teams.launch): the lead is asked to keep the name of a member whose part goes on."""
    return [problem for problem, wrong in (
        ("a member at least, one a line", not members and not team.decide),) if wrong]


def lineup_parts(members: list[teams.Member], cap: int, problems: list[str]) -> list[Part]:
    """'Opens 3 tabs: the lead, with the handover', each member with its CLI and pick, the cap, and
    what the line-up still needs. cap: in tokens, 0 for none."""
    out: list[Part] = [("Opens ", MUTED), (f"{len(members) + 1} tabs", f"bold {GREEN}"),
                       (": the lead, with the handover", MUTED)]
    for member in members:
        out.append(("\n● ", GREEN))
        out.append((member.name, f"bold {CYAN}"))
        cli_part = f"{member.cli} · " if member.cli != "claude" else ""
        out.append((f"  {cli_part}{member.pick.short() or 'your default model and effort'}", MUTED))
    out.append((f"\nEach under a {human(cap)} context cap." if cap else
                "\nEach on Claude Code's own window, with no cap.", MUTED))
    for problem in problems:
        out.append((f"\n▲ needs {problem}", AMBER))
    return out
