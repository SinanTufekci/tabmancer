"""B: the background sessions (`claude --bg`), listed to attach to, stop, remove and clean up, in words and
steps with no UI behind them. The terminal dashboard's B screen (background.py) and the desktop window
each show the list in their own way; what they say, when a key waits for a second press, and what the
commands do is here, in one copy. What acts on a session (stop, remove) is bgjobs' alone, looked up
when called.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import bgjobs
from .actions import clip
from .bgjobs import Job, kind
from .launch import Launch, Mode
from .palette import MUTED

CONFIRM_SECONDS = 4  # a second press within this long confirms
REFRESH_SECONDS = 5  # the list read again this often while it is shown
TITLE = "Background sessions"
NO_ANSWER = "`claude agents --json` did not answer, so background sessions cannot be listed."
EMPTY = "No background sessions. Ctrl+B in a prompt starts one."
NOTHING_TO_CLEAN = "Nothing to clean up: every background session is working or waiting for you."
WORKING = "Working…"
KEYS = (("Enter", "attach"), ("x", "stop"), ("d", "remove"), ("c", "clean up idle and stopped"), ("Esc", "close"))
COLUMNS = (("state", "State"), ("project", "Project"), ("name", "Name"), ("started", "Started"), ("id", "Id"))
PROJECT_CHARS, NAME_CHARS = 18, 44
TAB_TITLE_CHARS = 48


def heading(found: list[Job]) -> list[str]:
    """The title, how many, and how many could be cleaned up (the front end sets them apart)."""
    tidy = sum(job.tidy for job in found)
    return [TITLE, str(len(found)), *([f"{tidy} idle or stopped"] if tidy else [])]


def row(job: Job) -> list[tuple[str, str]]:
    """A job's cells in COLUMNS' order, each with its style ('' for the plain text colour): its state as
    bgjobs.LABELS says it, when it started and its id muted, as the B screen shows them."""
    label, style = bgjobs.LABELS[kind(job)]
    return [(label, style), (job.project[:PROJECT_CHARS], ""), (job.name[:NAME_CHARS] or job.session_id[:8], ""),
            (bgjobs.ago(job.started), MUTED), (job.id, MUTED)]


def attach_launch(job: Job) -> Launch:
    """Enter: a tab attached to it (`claude attach`)."""
    return Launch(title=clip(f"{job.project}: {job.name}", TAB_TITLE_CHARS), cwd=job.cwd, mode=Mode.ATTACH,
                  session_id=job.id)


class Confirm:
    """x, d and c ask twice: the first press says what a second does (and arms it), the second, soon
    after, on the same one, does it. Moving to another row disarms it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.armed: tuple[str, str] | None = None  # (action, job id or '*') waiting for a second press
        self.armed_at = 0.0

    def confirmed(self, action: str, target: str) -> bool:
        """True on the second press of the same key for the same target; arms it on the first."""
        if self.armed == (action, target) and self.clock() - self.armed_at < CONFIRM_SECONDS:
            self.armed = None
            return True
        self.armed, self.armed_at = (action, target), self.clock()
        return False

    def disarm(self) -> bool:
        """Forget a first press; True when one was waiting."""
        was, self.armed = self.armed is not None, None
        return was


@dataclass
class Asked:
    """What a key on a job came to: commands to run now, or words to say (a warning when a second press
    would do it)."""

    commands: list[tuple[str, Job]] = field(default_factory=list)
    words: str = ""
    warning: bool = False


def stop(job: Job | None, confirm: Confirm) -> Asked:
    """x: an idle one stops at once; one working or waiting for you on a second press."""
    if job is None:
        return Asked()
    if not job.running:
        return Asked(words=f"{job.id} is not running.")
    if kind(job) != "idle" and not confirm.confirmed("stop", job.id):
        return Asked(words=f"{job.project} is {kind(job).replace('needs', 'waiting for you')}: press x again to "
                           "stop it. Its conversation is kept.", warning=True)
    return Asked([("stop", job)])


def remove(job: Job | None, confirm: Confirm) -> Asked:
    """d: on a second press, stopped first when it runs."""
    if job is None:
        return Asked()
    if not confirm.confirmed("remove", job.id):
        stopping = "stop and remove" if job.running else "remove"
        return Asked(words=f"Press d again to {stopping} {job.id}. Its transcript stays, so the session can still "
                           "be resumed from the list.", warning=True)
    return Asked([("remove", job)])


def clean_up(found: list[Job], confirm: Confirm) -> Asked:
    """c: every idle or stopped one removed, on a second press."""
    tidy = [job for job in found if job.tidy]
    if not tidy:
        return Asked(words=NOTHING_TO_CLEAN)
    if not confirm.confirmed("clean", "*"):
        return Asked(words=f"Press c again to remove {len(tidy)} idle or stopped background sessions. Their "
                           "transcripts stay.", warning=True)
    return Asked([("remove", job) for job in tidy])


@dataclass
class Ran:
    """What the commands came to: how many were done, what failed, and the list read again (None: the CLI
    did not answer)."""

    commands: list[tuple[str, Job]]
    done: int
    failures: list[str]
    found: list[Job] | None

    @property
    def words(self) -> str:
        verb = "Stopped" if self.commands[0][0] == "stop" else "Removed"
        if self.failures:
            return f"{verb} {self.done} of {len(self.commands)}. " + " ".join(self.failures)
        what = self.commands[0][1].project if len(self.commands) == 1 else f"{self.done} background sessions"
        return f"{verb} {what}."


def run(commands: list[tuple[str, Job]]) -> Ran:
    """On a thread (each `claude stop` or `rm` takes a moment): a running session stopped before it is
    removed; then the list read again."""
    done, failures = 0, []
    for action, job in commands:
        try:
            if job.running:
                bgjobs.stop(job.id)
            if action == "remove":
                bgjobs.remove(job.id)
            done += 1
        except OSError as error:
            failures.append(f"{job.id}: {error}")
    return Ran(commands, done, failures, bgjobs.jobs())
