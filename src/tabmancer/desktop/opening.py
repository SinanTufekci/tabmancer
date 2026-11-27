"""The window's keys that reach past it: Enter opens the ticked sessions, or the picked one, each in a
new tab (one running in the background is attached to), and on an open one brings its tab to the front,
as j does for the next open session waiting for you; m changes how the picked one opens; p opens a closed
one with a prompt (prompting.py); x twice on a team with members open closes their tabs.

The terminal dashboard's own steps (actions.py), each on a thread of its own: finding a tab, or today's
claude update before Claude Code sessions open (claudeupdate), takes a while, and the window never waits
on it. What opening keeps between presses (the ones still starting, and which never came up) is the
terminal dashboard's one copy too (openings.Openings). The modules are looked up when called
(launch.open_tabs, focus.focus_tab), so the demo's stand-ins (demo.stand_ins) stand in for them here too.
Nothing here claims the actor lock (the window's mount does): what a key asks for is done whichever
dashboard acts; what happens by itself (a limit lifting, today's claude update, through claudeupdate.due)
is the acting dashboard's.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from functools import partial

from PySide6.QtCore import QObject, Signal

from .. import actions, claudeupdate, errorlog, launch, openings, prompting, workspaces, workspacewords
from ..launch import Launch, Mode, default_mode
from ..sessions import Session, Status
from ..store import DashSnapshot, live_entries

CAME_UP_SECONDS = openings.CAME_UP_SECONDS  # by then one opened from here shows open, or it is said
ALREADY = replace(openings.ALREADY, seconds=4)
NO_TERMINAL = ("No terminal here opens tabs, so nothing opened: the commands are on the clipboard, to run "
               "in a terminal.")


def open_tabs(launches: list[Launch]) -> list[str]:
    """New tabs, by launch.open_tabs as it is when called (the demo stands in for it)."""
    return launch.open_tabs(launches)


def run_in_background(one: Launch) -> str:
    """claude --bg, by launch.run_in_background as it is when called (the demo stands in for it)."""
    return launch.run_in_background(one)


def live_ids(background: dict[str, dict]) -> set[str]:
    """The sessions a Claude process has open now: the pid files, and the background ones of the last
    full read (they have none), asked just before the tabs open, as the terminal dashboard asks."""
    return set(live_entries(False, background)[0])


def opened_note(opened: actions.Opened) -> actions.Note | None:
    """What an Enter came to, in one line for Bit: why nothing opened, or what did (and what was open
    already, so left alone)."""
    if opened.error:
        return actions.Note(opened.error, "error", 15)
    if opened.leftover:
        return actions.Note(NO_TERMINAL, "warning", 15)
    words = [opened.words] if opened.opened else []
    if opened.skipped:
        words.append(opened.skipped_words)
    if not words:
        return None
    return actions.Note(" ".join(words), "warning" if opened.skipped else "information", 10 if opened.skipped else 4)


class Opener(QObject):
    """What Enter, j and m keep between presses, and the threads they run on. said: words for Bit, for
    how long, and how loud ('information', 'warning', 'error', as actions.Note has them); opened: what
    came of an Enter (actions.Opened), for the window to read the sessions again. update: today's
    claude update before Claude Code sessions open, when it is on (never over the demo's made-up ones)."""

    said = Signal(str, float, str)
    opened = Signal(object)
    closed = Signal()
    prompted = Signal(object)  # what a p came to (prompting.Sent)

    def __init__(self, parent: QObject | None = None, clock: Callable[[], float] = time.monotonic,
                 update: bool = True) -> None:
        super().__init__(parent)
        self.update = update
        self.modes: dict[str, Mode] = {}  # how one opens, where m changed it
        self.openings = openings.Openings(clock=clock)  # the ones opened from here, and which came up
        self.turn = actions.Turn(None)  # where j goes on from
        self.workers: list[threading.Thread] = []

    # --- m ----------------------------------------------------------------------------------------

    def mode(self, session: Session) -> Mode:
        return self.modes.get(session.session_id) or default_mode(session.status)

    def cycle(self, session: Session) -> bool:
        """m: the next way a closed session opens (actions.next_mode); False for an open one."""
        if session.status is Status.RUNNING:
            return False
        self.modes[session.session_id] = actions.next_mode(session, self.mode(session))
        return True

    # --- Enter ------------------------------------------------------------------------------------

    def open(self, chosen: list[Session], snap: DashSnapshot) -> None:
        """These sessions in new tabs, each as m says (a team's lead last, so it finds its members up);
        not the ones opened from here a moment ago, still starting."""
        chosen = self.openings.choose(chosen)
        if not chosen:
            self.say(ALREADY)
            return
        self.start(self.open_now, self.openings.launches(chosen, self.mode, snap.background), dict(snap.background))

    def open_now(self, launches: list[Launch], background: dict[str, dict]) -> None:
        """On a thread of its own: today's claude update first, when it is due and one is Claude Code's;
        then the tabs, leaving out what a Claude process has open now (actions.open_launches)."""
        if self.update and any(one.cli == "claude" for one in launches):
            claudeupdate.before_opening(self.tell)
        done = actions.open_launches(launches, partial(live_ids, background), open_tabs)
        try:
            self.opened.emit(done)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def took(self, opened: actions.Opened) -> actions.Note | None:
        """Back on the window's thread: the ones opened kept (not opened again while they start; looked
        for a while later), and what to say."""
        self.openings.took(opened)
        return opened_note(opened)

    def seen(self, sessions: dict[str, Session]) -> actions.Note | None:
        """At each read: the ones opened from here that show open now; and, a while after they were
        opened, the ones that never did (Claude Code would not resume one: its tab says why)."""
        self.openings.seen(sessions)
        words = self.openings.due(sessions)
        return actions.Note(words, "warning", 10) if words else None

    # --- o: a workspace ------------------------------------------------------------------------------

    def open_workspace(self, workspace: workspaces.Workspace, sessions: list[Session],
                       background: dict[str, dict]) -> None:
        """o: a workspace's folders opened, each folder's latest sessions continued or new ones started
        (workspaces.resolve, given the sessions newest first), on a thread of its own: it looks at the folders
        and the teams first. Nothing to open: said why, folder by folder."""
        self.start(self.open_workspace_now, workspace, list(sessions), dict(background))

    def open_workspace_now(self, workspace: workspaces.Workspace, sessions: list[Session],
                           background: dict[str, dict]) -> None:
        launches, notes = workspaces.resolve(workspace, sessions)
        if not launches:
            self.say(workspacewords.nothing_note(notes))
            return
        self.open_now(launches, background)

    # --- p ----------------------------------------------------------------------------------------

    def write(self, session: Session, how: str, prompt: str, background: dict[str, dict]) -> None:
        """p: a closed session opened with the prompt (prompting.send), in a new tab or in the background, on
        a thread of its own; today's claude update first, when it is due and the session is Claude Code's."""
        self.start(self.write_now, session, how, prompt, dict(background))

    def write_now(self, session: Session, how: str, prompt: str, background: dict[str, dict]) -> None:
        if self.update and session.cli == "claude":
            claudeupdate.before_opening(self.tell)
        sent = prompting.send(session, how, prompt, partial(live_ids, background), open_tabs, run_in_background,
                              self.say)
        try:
            self.prompted.emit(sent)
        except RuntimeError:
            pass  # the window has gone meanwhile

    # --- an open session's tab, and j -------------------------------------------------------------

    def go_to(self, session: Session) -> None:
        """Its tab to the front (actions.go_to_tab), on a thread of its own: finding it takes a moment."""
        self.start(self.switch, session)

    def switch(self, session: Session) -> None:
        self.say(actions.go_to_tab(session))

    def next_waiting(self, sessions: Iterable[Session]) -> Session | None:
        """j: the next open session that needs you, else waiting for you (actions.next_waiting), and its
        tab to the front; None, said, when none is."""
        self.turn = actions.next_waiting(sessions, self.turn.group, self.turn.turn)
        if self.turn.session is None:
            self.say(actions.NONE_WAITING)
            return None
        self.go_to(self.turn.session)
        return self.turn.session

    # --- x twice on a team ---------------------------------------------------------------------------

    def close_team(self, names: list[str]) -> None:
        """These members' tabs closed, as on /exit (actions.closed_note), on a thread of its own: finding
        each tab takes a moment."""
        self.start(self.close_now, names)

    def close_now(self, names: list[str]) -> None:
        self.say(actions.closed_note(names))
        try:
            self.closed.emit()
        except RuntimeError:
            pass  # the window has gone meanwhile

    # --- threads and words ------------------------------------------------------------------------

    def start(self, work: Callable[..., None], *args: object) -> None:
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        name = getattr(work, "__name__", "open")
        thread = threading.Thread(target=errorlog.guarded, args=(f"desktop.opening.{name}", work, *args),
                                  kwargs={"failed": lambda error, path: self.say(actions.Note(
                                      errorlog.failed_words(name, error, path), "error", 15))},
                                  daemon=True, name="tabmancer-open")
        self.workers.append(thread)
        thread.start()

    def settle(self, seconds: float = 10) -> None:
        """Wait for what is under way (on closing, and in tests)."""
        for thread in list(self.workers):
            thread.join(seconds)

    def say(self, note: actions.Note) -> None:
        try:
            self.said.emit(note.words, float(note.seconds or 4), note.severity)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def tell(self, words: str) -> None:
        """claudeupdate's words while today's update runs, before the tabs open."""
        self.say(actions.Note(words, seconds=8))
