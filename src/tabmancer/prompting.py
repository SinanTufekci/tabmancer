"""p: a prompt written to a closed session, which opens with it, in a new tab or in the background.

Nothing types into an open session from outside, so p on one brings its tab to the front instead; one
running in the background is attached to (Enter), not prompted. What p says and the steps it takes, with
no UI behind them, are here: the terminal dashboard and the desktop window each ask for the prompt in
their own way, then send it (send, on a thread: a terminal can take seconds to start).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import actions, clis, picks, teams
from .actions import Note, clip
from .launch import Launch, Mode
from .sessions import Session, Status

TAB, BACKGROUND = "tab", "background"  # where a prompt sends its session
TITLE_CHARS = 50  # the session's title in the prompt dialog's heading, clipped
TAB_TITLE_CHARS = 48  # the tab's title, as a resumed session's
PLACEHOLDER = "What should Claude do next?"
ATTACH = "This session runs in the background: Enter attaches to it."
FAILED_SECONDS = 15.0
ELSEWHERE_SECONDS = 10.0
STARTED_SECONDS = 8.0


def refusal(session: Session) -> str | None:
    """Why p asks nothing for this one (said as a warning), or None. An open one has its tab brought
    forward instead, by the front end, saying nothing more."""
    return ATTACH if session.status is Status.BACKGROUND else None


def heading(session: Session) -> str:
    return f"Prompt {session.project} · {clip(session.title, TITLE_CHARS)}"


def placeholder(session: Session) -> str:
    """The empty line's words: another CLI's session by its name."""
    return PLACEHOLDER if session.cli == "claude" else f"What should {clis.label(session.cli)} do next?"


def keys(session: Session) -> list[tuple[str, str]]:
    """The dialog's keys: Ctrl+B only where the CLI runs sessions in the background."""
    out = [("Enter", "open in a new tab")]
    if clis.CLIS[session.cli].can_background:
        out.append(("Ctrl+B", "run in the background"))
    return [*out, ("Esc", "cancel")]


def launch_for(session: Session, prompt: str) -> Launch:
    """The session resumed with the prompt as its first message: a team's member as that member, one opened
    with an effort at that effort again (picks.resumed)."""
    return picks.resumed(teams.as_member(session, Launch(
        title=clip(f"{session.project}: {session.title}", TAB_TITLE_CHARS), cwd=session.cwd, mode=Mode.RESUME,
        session_id=session.session_id, prompt=prompt, cli=session.cli)), context=session.context or 0)


@dataclass
class Sent:
    """What a prompt came to: 'opened' (a new tab), 'started' (in the background), 'elsewhere' (open in a
    tab now, so nothing opened), 'leftover' (no terminal opens tabs: the command to run is in it) or
    'failed'; and the note to say."""

    outcome: str
    note: Note
    leftover: list[str] = field(default_factory=list)


def send(session: Session, how: str, prompt: str, live: Callable[[], set[str]],
         open_tabs: Callable[[list[Launch]], list[str]], start: Callable[[Launch], str],
         say: Callable[[Note], None]) -> Sent:
    """On a thread: the session opened with the prompt, in a new tab (open_tabs: launch.open_tabs) or in the
    background (start: launch.run_in_background, which says what it started); a CLI with no background
    sessions opens a tab instead, saying so. Not when a process has it open now (live, asked afresh): two
    would write one transcript. say: words before a slow step (the background start takes seconds)."""
    launch = launch_for(session, prompt)
    instead = ""
    if how == BACKGROUND and not clis.CLIS[session.cli].can_background:
        instead = f"{clis.label(session.cli)} has no background sessions: opening a tab instead. "
        how = TAB
    if actions.open_elsewhere([launch], live):
        return Sent("elsewhere", Note(f"{session.project} is open in another tab now: write to it there.",
                                      "warning", ELSEWHERE_SECONDS))
    if how == TAB:
        try:
            leftover = open_tabs([launch])
        except OSError as error:
            return Sent("failed", Note(f"{instead}Could not open the tab: {error}", "error", FAILED_SECONDS))
        if leftover:
            return Sent("leftover", Note(f"{instead}No supported terminal here. Run: {leftover[0]}", "warning",
                                         FAILED_SECONDS), leftover)
        return Sent("opened", Note(f"{instead}Opened {session.project} in a new tab with your prompt.",
                                   "warning" if instead else "information", ELSEWHERE_SECONDS if instead else None))
    say(Note(f"Starting {session.project} in the background…"))
    try:
        return Sent("started", Note(start(launch), seconds=STARTED_SECONDS))
    except OSError as error:
        return Sent("failed", Note(str(error), "error", STARTED_SECONDS))
