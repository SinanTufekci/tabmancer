"""s in the window (desktop/summaries.py): a session's summary written on a thread as the terminal's s
writes it, once at a time per session; what it came to said in the terminal's words; one not saved
kept in memory and shown; summaries.json read again when another process changed it. Fakes only: no
claude or agy is run. Offscreen. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
import threading
from pathlib import Path

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tabmancer import summarize
from tabmancer.config import config_dir
from tabmancer.desktop.summaries import Summarizer, stamp
from tabmancer.sessions import Session


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def a_session(sid: str = "s") -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd="C:/work/shop", title="Fix it")


def settled(qapp, summarizer: Summarizer, seconds: float = 5) -> None:
    """The threads over and their answers taken on the window's thread."""
    for thread in list(summarizer.workers):
        thread.join(seconds)
    for _ in range(20):
        qapp.processEvents()


def heard(summarizer: Summarizer) -> tuple[list, list]:
    done, said = [], []
    summarizer.done.connect(done.append)
    summarizer.said.connect(said.append)
    return done, said


def test_s_writes_it_on_a_thread_once_at_a_time_and_shows_it(qapp):
    go, ran = threading.Event(), []

    def run(session):
        ran.append(threading.current_thread().name)
        go.wait(5)
        return summarize.Summary({"summary": "Did the thing.", "size": 1}, True)

    summarizer = Summarizer(run=run)
    done, said = heard(summarizer)
    assert summarizer.start(a_session()) and summarizer.summarizing == {"s"}
    assert not summarizer.start(a_session())  # being written: not again
    go.set()
    settled(qapp, summarizer)
    assert ran == ["tabmancer-summary"]  # never the window's thread
    assert summarizer.summarizing == set() and summarizer.summaries["s"]["summary"] == "Did the thing."
    assert done == ["s"] and said == []  # saved: nothing to say


def test_a_failure_is_said_in_the_terminals_words(qapp):
    def run(session):
        raise summarize.SummaryError("no quota")

    summarizer = Summarizer(run=run)
    done, said = heard(summarizer)
    summarizer.start(a_session())
    settled(qapp, summarizer)
    (note,) = said
    assert (note.words, note.severity, note.seconds) == ("Summary failed: no quota", "error", 8)
    assert done == ["s"] and "s" not in summarizer.summaries and summarizer.summarizing == set()


def test_one_not_saved_is_shown_and_said_and_kept_over_a_reread(qapp):
    summarizer = Summarizer(run=lambda session: summarize.Summary({"summary": "Mine."}, False))
    _, said = heard(summarizer)
    summarizer.start(a_session())
    settled(qapp, summarizer)
    assert summarizer.summaries["s"] == {"summary": "Mine."} and said[0].words == summarize.UNSAVED
    assert summarize._store("t", {"summary": "The terminal's."})  # another process writes one
    assert summarizer.reread()
    assert summarizer.summaries == {"s": {"summary": "Mine."}, "t": {"summary": "The terminal's."}}
    assert not summarizer.reread()  # unchanged since: not read again
    assert summarize._store("s", {"summary": "Saved there."})  # the file has its own for it now
    assert summarizer.reread() and summarizer.summaries["s"] == {"summary": "Saved there."}
    assert summarizer.unsaved == {}


def test_a_summary_the_terminal_writes_shows_after_a_reread(qapp):
    summarizer = Summarizer(run=lambda session: summarize.Summary({}, True))
    assert summarizer.summaries == {} and not summarizer.reread()
    assert summarize._store("x", {"summary": "From the terminal."})
    assert summarizer.reread() and summarizer.summaries == {"x": {"summary": "From the terminal."}}


def test_a_write_inside_one_tick_of_the_clock_is_read_all_the_same(qapp):
    """Two writes the file system dates alike (a busy machine): the second is still seen, by its size or,
    the same size too, by the file each write puts in place."""
    path = config_dir() / summarize.FILE
    assert summarize._store("t", {"summary": "AAA"})
    summarizer = Summarizer(run=lambda session: summarize.Summary({}, True))
    first = path.stat().st_mtime_ns
    assert summarize._store("t", {"summary": "BBB"})  # the same size
    os.utime(path, ns=(first, first))  # and dated as the first
    assert summarizer.reread() and summarizer.summaries["t"] == {"summary": "BBB"}
    assert summarize._store("t", {"summary": "CCCC"})
    os.utime(path, ns=(first, first))
    assert summarizer.reread() and summarizer.summaries["t"] == {"summary": "CCCC"}
    assert not summarizer.reread()
    assert stamp() == summarizer.seen and stamp()[0] == first  # type: ignore[index]


def test_by_default_it_runs_summarize_as_the_terminal_does(qapp):
    assert Summarizer().run is summarize.summarize
