"""A handover queued in another open tabmancer window (h on a busy session there): that window hands it over
as it finishes, so h here says so and does nothing; once that window has closed, h here asks as ever. The
terminal's rows show the other window's queued mark too, as the desktop window shows them all."""

from __future__ import annotations

import pytest
from test_engine import Front, made, s

from tabmancer import actions, actor, config, engine
from tabmancer.sessions import Exit, Session, Status

OTHER = {"pid": 20, "started": "200", "command": "tabmancer monitor"}


@pytest.fixture
def processes(monkeypatch):
    """Fake processes: pid -> (start time, command line); this one is pid 10, another window pid 20."""
    table = {10: ("100", "tabmancer"), 20: ("200", "tabmancer monitor")}
    monkeypatch.setattr(actor, "process", lambda pid: table.get(pid))
    monkeypatch.setattr(actor, "_mine", {"pid": 10, "started": "100", "command": "tabmancer"})
    return table


def queue_elsewhere(sid: str, how: str = "self") -> None:
    saved = config.read_json(actions.QUEUE) or {}
    config.write_json(actions.QUEUE, {**saved, sid: {"how": how, "by": OTHER}})


def test_read_queued_others_leaves_out_this_windows_own(processes):
    assert actions.save_queued({"a": "self"})
    queue_elsewhere("b", "transcript")
    assert actions.read_queued() == {"a": "self", "b": "transcript"}
    assert actions.read_queued(others=True) == {"b": "transcript"}
    del processes[20]  # that window closed
    assert actions.read_queued(others=True) == {}


def test_h_on_one_queued_in_another_window_says_so_and_does_nothing(processes):
    queue_elsewhere("a")
    front = Front()
    made(front, sessions={"a": s("a")}).handover(s("a"))
    assert front.calls == [("say_note", actions.Note(
        "a's handover is queued in another tabmancer window: h there cancels it.", "warning", 10))]


def test_h_asks_as_ever_once_that_window_has_closed(processes):
    queue_elsewhere("a")
    del processes[20]
    front = Front()
    made(front, sessions={"a": s("a")}).handover(s("a"))
    assert front.names() == ["ask_handover"]


def test_h_on_one_queued_here_and_elsewhere_still_cancels_it_here(processes, monkeypatch):
    monkeypatch.setattr(actions, "save_queued", lambda queued, *args, **kwargs: True)
    queue_elsewhere("a")
    front = Front()
    eng = made(front, sessions={"a": s("a")}, queued={"a": "self"})
    eng.handover(s("a"))
    assert eng.state.queued == {} and front.names() == ["say_note", "update_row"]


def test_a_poll_takes_the_other_windows_queue_and_redraws_the_rows_it_marks(processes):
    front = Front()
    eng = made(front, acts=True, sessions={"a": s("a"), "b": s("b")})
    eng.live({}, False, set(), None, {"a": "self"})
    assert eng.state.queued_elsewhere == {"a": "self"}
    assert ("update_row", eng.state.sessions["a"]) in front.calls
    front.calls.clear()
    eng.live({}, False, set(), None, {"a": "self"})  # nothing changed: nothing redrawn
    assert "update_row" not in front.names()
    eng.live({}, False, set(), None, {})
    assert eng.state.queued_elsewhere == {} and ("update_row", eng.state.sessions["a"]) in front.calls


def test_the_terminals_rows_show_both_and_only_this_windows_own_go_with_it():
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    app.queued_elsewhere = {"a": "self", "b": "self"}
    app.queued = {"b": "transcript"}
    assert app.shown_queued == {"a": "self", "b": "transcript"}
    assert app.engine.state.queued == {"b": "transcript"}  # q counts and saves only these
    assert isinstance(app.engine.state, engine.EngineState)


def test_one_closed_meanwhile_is_not_refused():
    closed = Session(session_id="z", path=None, cwd="C:/work/z", title="z", status=Status.DONE, exit=Exit.CLEAN)
    assert actions.handover_refusal(closed, [], queued=False, queued_elsewhere=False) is None
