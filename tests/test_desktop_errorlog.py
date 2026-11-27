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

from tabmancer import engine, errorlog, search
from tabmancer.desktop import front, main


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
