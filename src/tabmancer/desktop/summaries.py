"""s on a session in the window: its short summary written as the terminal dashboard's s writes it
(summarize.summarize: one `claude -p` on a small model, or `agy -p` on the chain's model, never a
Claude one through agy), on a thread of its own, the window never waiting. What it came to is said in
the terminal's words (sessionwords.summary_note); the summaries shown are summaries.json's, read again
when another process (the terminal dashboard) changed it, and one written here but not saved is kept
in memory and shown all the same.

A keypress, not the window acting on its own: nothing here runs unless s asks for it, so it is allowed
in a window that does not hold the actor lock, as h's handover and Ctrl+R's suggestion are."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from .. import actions, errorlog, sessionwords, summarize
from ..config import config_dir
from ..sessions import Session

Summarize = Callable[[Session], summarize.Summary]


def stamp() -> tuple[int, int, int] | None:
    """summaries.json as it is now (its last change in ns, its size, its file id), or None when there is none
    to read. Not the time alone: two writes inside one tick of the file system's clock (a busy machine) keep
    it, and the second would be missed; each write replaces the file (config.write_json), so the id changes.
    """
    try:
        found = (config_dir() / summarize.FILE).stat()
        return found.st_mtime_ns, found.st_size, found.st_ino
    except OSError:
        return None


class Summarizer(QObject):
    """The summaries shown (summaries: session id -> its cache entry), the sessions being summarized
    (summarizing), and s's threads. done: a session's summary came, or did not (its id): its preview
    is drawn again; said: what to say of it (an actions.Note)."""

    done = Signal(str)
    said = Signal(object)
    came = Signal(str, object, object)  # from a thread: the session id, its Summary or None, the error

    def __init__(self, parent: QObject | None = None, run: Summarize | None = None) -> None:
        super().__init__(parent)
        self.run = run or summarize.summarize
        self.summaries: dict[str, dict] = summarize.load_cache()
        self.unsaved: dict[str, dict] = {}  # written here, not taken by summaries.json: kept in memory
        self.summarizing: set[str] = set()
        self.seen = stamp()
        self.workers: list[threading.Thread] = []
        self.came.connect(self.finished)

    def start(self, session: Session) -> bool:
        """s on a session: its summary written on a thread. False, nothing run, while one is being
        written for it already."""
        sid = session.session_id
        if sid in self.summarizing:
            return False
        self.summarizing.add(sid)
        self.workers = [thread for thread in self.workers if thread.is_alive()]
        thread = threading.Thread(target=errorlog.guarded, args=("desktop.summaries.write", self.write, session),
                                  kwargs={"failed": lambda error, path: self.write_failed(
                                      sid, errorlog.failed_words("summary", error, path))},
                                  daemon=True, name="tabmancer-summary")
        self.workers.append(thread)
        thread.start()
        return True

    def write(self, session: Session) -> None:
        try:
            summary, error = self.run(session), None
        except (summarize.SummaryError, OSError) as failure:
            summary, error = None, str(failure)
        try:
            self.came.emit(session.session_id, summary, error)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def write_failed(self, sid: str, words: str) -> None:
        """A write that raised what it does not answer itself: said as its error, so s can ask again."""
        try:
            self.came.emit(sid, None, words)
        except RuntimeError:
            pass  # the window has gone meanwhile

    def finished(self, sid: str, summary: summarize.Summary | None, error: str | None) -> None:
        """Back on the window's thread: the summary shown (kept in memory when it was not saved), what
        it came to said, its preview drawn again."""
        self.summarizing.discard(sid)
        if summary is not None:
            self.summaries[sid] = summary.entry
            if summary.saved:
                self.unsaved.pop(sid, None)  # the next reread takes it, and whatever else changed, from the file
            else:
                self.unsaved[sid] = summary.entry
        note: actions.Note | None = sessionwords.summary_note(summary, error)
        if note is not None:
            self.said.emit(note)
        self.done.emit(sid)

    def reread(self) -> bool:
        """summaries.json read again when it changed since last seen (the terminal dashboard wrote one):
        True when it was. One written here and not saved stays shown, unless the file has one of its
        own for that session now."""
        now = stamp()
        if now == self.seen:
            return False
        self.seen = now
        read = summarize.load_cache()
        self.summaries = {**{sid: entry for sid, entry in self.unsaved.items() if sid not in read}, **read}
        self.unsaved = {sid: entry for sid, entry in self.unsaved.items() if sid not in read}
        return True
