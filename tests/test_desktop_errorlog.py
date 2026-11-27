"""What goes wrong on the desktop window's own threads is written to errors.log (errorlog.log_error), as the
terminal dashboard's workers write it (safework): the store and words readers, the look at the actor lock,
the Front's workers and the desktop notes. Without a window: each thread's function with stand-ins for the
relay. Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from types import SimpleNamespace

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from tabmancer import backgroundwords, diskclean, engine, errorlog, rename, search
from tabmancer.desktop import backgrounds, cleanup, front, main, monitor, naming, newsession, opening, signin
from tabmancer.desktop.summaries import Summarizer


@pytest.fixture
def logged(tmp_path, monkeypatch):
    """errors.log in a folder of the test's own: what it holds, read when asked."""
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path))
    return lambda: (tmp_path / errorlog.LOG).read_text(encoding="utf-8") if (tmp_path / errorlog.LOG).exists() else ""


def relay(*names: str) -> tuple[SimpleNamespace, dict[str, list[tuple]]]:
    """A stand-in for a window's Relay: what each signal was sent."""
    sent: dict[str, list[tuple]] = {name: [] for name in names}
    signals = {name: SimpleNamespace(emit=lambda *args, name=name: sent[name].append(args)) for name in names}
    return SimpleNamespace(**signals), sent


def test_a_store_read_that_fails_is_logged_and_said_in_the_window(logged):
    class Broken:
        def poll(self):
            raise ValueError("boom")

    stand_in, sent = relay("polled")
    store = Broken()
    main.read_store(store, stand_in)
    [(got, snap, first, error)] = sent["polled"]
    assert got is store and snap is None and not first and error == "ValueError: boom"
    assert "in desktop.read_store" in logged() and "ValueError: boom" in logged()


def test_a_look_at_the_lock_that_fails_is_logged_and_sends_nothing(logged, monkeypatch):
    def broken(monitor, acts):
        raise OSError("actor.json held open")

    monkeypatch.setattr(engine, "look", broken)
    stand_in, sent = relay("actor_seen")
    main.look_at_lock(True, stand_in)
    assert sent["actor_seen"] == []  # tick_actor looks again at its next tick: its thread has ended
    assert "in desktop.look_at_lock" in logged() and "actor.json held open" in logged()


def test_a_words_read_that_fails_is_logged_and_the_titles_still_found(logged, monkeypatch):
    def broken():
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(search.Index, "load", staticmethod(broken))
    stand_in, sent = relay("indexed")
    main.read_words(None, [], stand_in)
    assert sent["indexed"] == [(None, False)]
    assert "in desktop.read_words" in logged() and "index unreadable" in logged()


def test_a_desktop_note_that_fails_is_logged_never_raised(logged, monkeypatch):
    def broken(title, body, dashboard=""):
        raise OSError("powershell missing")

    monkeypatch.setattr(front.toast, "show", broken)
    front.show_toast("Limit lifted", "api continues")
    assert "in desktop.show_toast" in logged() and "powershell missing" in logged()


def test_a_front_worker_that_fails_is_logged_and_said(logged):
    said: list[tuple] = []
    stand_in = SimpleNamespace(handing={}, emit=lambda signal, *args: said.append((signal, *args)) or True)

    def write_handover(name):
        raise KeyError(name)

    front.WindowFront.work_on(stand_in, write_handover, ("api",), "api")
    (signal, note), ended = said
    assert signal == "noted" and note.severity == "error" and "(write_handover): 'api'" in note.words
    assert str(errorlog.config_dir() / errorlog.LOG) in note.words
    assert ended == ("ended",)  # a handover's thread is over, whatever came of it
    assert "write_handover" in logged() and "KeyError: 'api'" in logged()


def test_a_front_worker_that_does_its_work_logs_nothing(logged):
    said: list[tuple] = []
    stand_in = SimpleNamespace(handing={}, emit=lambda signal, *args: said.append((signal, *args)) or True)
    front.WindowFront.work_on(stand_in, lambda: None, (), "")
    assert said == [] and logged() == ""


# --- errorlog.guarded: one boundary for every thread of the window and its dialogs ------------------------

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def drained(qapp, worker) -> None:
    """worker's threads done, and what they sent delivered on this (the window's) thread."""
    for thread in worker.workers:  # as each worker's settle (the Summarizer has none of its own)
        thread.join(10)
    qapp.processEvents()


def test_guarded_logs_and_hands_the_error_on_with_where_it_is_written(logged):
    got = []

    def broken(a, b):
        raise ValueError(f"{a}+{b}")

    errorlog.guarded("desktop.test", broken, 1, 2, failed=lambda error, path: got.append((error, path)))
    [(error, path)] = got
    assert str(error) == "1+2" and path == errorlog.config_dir() / errorlog.LOG
    assert "in desktop.test" in logged() and "ValueError: 1+2" in logged()
    words = errorlog.failed_words("fetch", error, path)
    assert words == f"Something went wrong in the background (fetch): 1+2. The details are in {path}; please report it."


def test_guarded_says_nothing_when_the_work_is_done_and_logs_a_failed_answer_too(logged):
    errorlog.guarded("desktop.test", lambda: None, failed=lambda error, path: 1 / 0)
    assert logged() == ""

    def answer(error, path):
        raise KeyError("no window")

    errorlog.guarded("desktop.test", lambda: 1 / 0, failed=answer)  # raises nothing
    assert "in desktop.test (failed)" in logged() and "KeyError: 'no window'" in logged()


def test_threads_failing_at_once_each_write_their_error_whole(logged):
    import threading

    threads = [threading.Thread(target=errorlog.guarded, args=(f"desktop.t{at}", lambda at=at: 1 / at if at else 1 / 0))
               for at in [0] * 8]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert logged().count("ZeroDivisionError") == 8


def test_background_jobs_that_fail_read_as_no_answer_and_ran_as_a_failure(qapp, logged, monkeypatch):
    def broken():
        raise OSError("claude agents --json crashed")

    monkeypatch.setattr(backgrounds.bgjobs, "jobs", broken)
    owner = QObject()
    jobs = backgrounds.Jobs(owner)
    read, ran = [], []
    jobs.read.connect(lambda why, found: read.append((why, found)))
    jobs.ran.connect(ran.append)
    jobs.fetch("open")
    drained(qapp, jobs)
    assert read == [("open", None)] and "in desktop.backgrounds.fetch_now" in logged()
    monkeypatch.setattr(backgroundwords, "run", lambda commands: 1 / 0)
    jobs.run([])
    drained(qapp, jobs)
    [done] = ran
    assert done.done == 0 and done.found is None and "(run): division by zero" in done.failures[0]


def test_a_clean_up_that_fails_is_said_and_never_left_measuring(qapp, logged, monkeypatch):
    def broken():
        raise PermissionError("projects folder locked")

    monkeypatch.setattr(diskclean, "collect", broken)
    shown = cleanup.CleanupDialog(None, "#7aa2f7")
    drained(qapp, shown.disk)
    assert not shown.busy and "projects folder locked" in shown.note.text()
    assert "in desktop.cleanup.collect" in logged()
    shown.close()


def test_a_rename_that_raises_what_it_does_not_catch_says_it_was_not_renamed(qapp, logged, monkeypatch):
    monkeypatch.setattr(rename, "rename", lambda session, name: 1 / 0)
    owner = QObject()  # kept: a parent let go of takes the renamer with it
    renamer = naming.Renamer(owner)
    failed = []
    renamer.failed.connect(failed.append)
    renamer.rename(SimpleNamespace(project="shop", path=None), "new name")
    drained(qapp, renamer)
    [note] = failed
    assert note.severity == "error" and note.words.startswith("shop was not renamed: Something went wrong")
    assert "in desktop.naming.rename" in logged()


def test_an_asked_work_that_raises_is_logged(qapp, logged):
    asker = newsession.Asker()
    answered = []
    asker.answered.connect(lambda *args: answered.append(args))
    asker.ask(None, "route", lambda: 1 / 0)
    drained(qapp, asker)
    assert answered == [] and "in desktop.asker.route" in logged()


@pytest.mark.parametrize("make", [lambda owner: opening.Opener(owner, update=False), signin.Accounts],
                         ids=["opening", "signin"])
def test_an_opening_or_sign_in_thread_that_fails_is_said(qapp, logged, make):
    owner = QObject()  # kept: a parent let go of takes the worker with it
    worker = make(owner)
    said = []
    worker.said.connect(lambda words, seconds, severity: said.append((words, severity)))

    def open_tabs_now():
        raise RuntimeError("wt.exe gone")

    worker.start(open_tabs_now)
    drained(qapp, worker)
    [(words, severity)] = said
    assert severity == "error" and "(open_tabs_now): wt.exe gone" in words
    assert "open_tabs_now" in logged()


def test_a_summary_that_raises_what_it_does_not_catch_is_said_and_can_be_asked_again(qapp, logged):
    def broken(session):
        raise TypeError("bad transcript")

    writer = Summarizer(run=broken)
    said = []
    writer.said.connect(said.append)
    session = SimpleNamespace(session_id="s1")
    assert writer.start(session)
    drained(qapp, writer)
    assert "s1" not in writer.summarizing and said and "bad transcript" in said[0].words
    assert "in desktop.summaries.write" in logged()


def test_the_monitors_reads_and_tab_switch_log_what_fails(logged):
    class Broken:
        def poll(self):
            raise ValueError("team file torn")

    stand_in, sent = relay("polled", "said")
    monitor.read_team(Broken(), stand_in)
    assert sent["polled"][0][3] == "ValueError: team file torn" and "in desktop.monitor.read_team" in logged()
    monitor.switch_failed(stand_in, OSError("no tab"), errorlog.config_dir() / errorlog.LOG)
    [(words, colour)] = sent["said"]
    assert "(switch): no tab" in words and colour == monitor.look.RED
