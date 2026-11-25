"""What the dashboard does by itself, with no UI (engine.py): a live poll's words in order, a queued
handover that finished, team notes typed or left on the clipboard, Bit on idle background sessions, the
sessions that never came up; that it keeps no thread, lock or clock of its own; and that the terminal
dashboard's state is the engine's (one copy). The dashboard's golden runs prove the terminal says the
same as before; test_desktop_extra proves the module loads with neither Textual nor Qt."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tabmancer import actions, actor, alerts, engine
from tabmancer.sessions import Exit, Session, Status

NOW = datetime(2026, 11, 25, 12, 0)


def s(sid, state="busy", exit=Exit.RUNNING, context=50_000, **kw) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=f"C:/work/{sid}", title=f"T {sid}",
                   exit=exit, status=Status.RUNNING if exit is Exit.RUNNING else Status.DONE, live_state=state,
                   last_active=NOW - timedelta(minutes=3), context=context, **kw)


class Front:
    """Records what the engine has it say and do, in order."""

    default_cap = 200_000

    def __init__(self, monitor: bool = False) -> None:
        self.calls: list[tuple] = []
        self.monitor = monitor

    def who(self, session):
        return session.project

    def cap_of(self, session):
        return 0

    def monitoring(self):
        return self.monitor

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, *args))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def claims(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(actions, "save_queued", lambda queued: saved.append(dict(queued)) or True)
    monkeypatch.setattr(actor, "first_to", lambda key: True)
    return saved


def made(front, **state) -> engine.Engine:
    return engine.Engine(front, clock=lambda: NOW, state=engine.EngineState(**state))


def test_a_poll_says_what_turned_hands_over_and_delivers_in_order(claims):
    front = Front()
    a, q, m = s("a"), s("q"), s("m")
    eng = made(front, acts=True, sessions={"a": a, "q": q, "m": m}, queued={"q": "self"},
               notes={"m": ("api", "build the login")}, full_poll_due=True)
    updates = {"a": replace(a, live_state="idle"), "q": replace(q, live_state="idle"),
               "m": replace(m, live_state="idle")}
    eng.live(updates, True, set())
    assert front.names() == ["tell", "say_note", "hand_over", "tell", "tell_desktop", "flash_changed",
                             "untick_open", "type_note", "rows_changed"]
    told = [call[1] for call in front.calls if call[0] == "tell"]
    assert [(e.kind, e.session) for e in told] == [(alerts.DONE, "a"), (alerts.DONE, "m")]
    assert front.calls[1][1].words == "q finished: handing it over now."
    assert front.calls[2][1:] == (updates["q"], "self")
    assert [x.session_id for x in front.calls[4][1]] == ["a", "m"]
    assert front.calls[7][1:] == (updates["m"], "api", "build the login")
    assert front.calls[8][1] == {"a", "q", "m"}
    st = eng.state
    assert (st.queued, st.unasked, st.notes, claims) == ({}, {"q"}, {}, [{}])
    assert (st.full_polls, st.full_poll_due, st.sessions) == (1, False, updates)


def test_the_one_that_watches_sends_no_desktop_note(claims):
    front = Front()
    eng = made(front, sessions={"a": s("a")})
    eng.live({"a": s("a", state="permission")}, False, set())
    assert "tell_desktop" not in front.names() and eng.state.full_polls == 0
    assert front.calls[0][1].kind == alerts.NEEDS_YOU


def test_a_monitor_leaves_a_closed_queued_one(claims):
    front = Front(monitor=True)
    eng = made(front, sessions={"q": s("q")}, queued={"q": "self"})
    eng.live({"q": s("q", exit=Exit.CLEAN, state="")}, False, set())
    assert "hand_over" not in front.names() and eng.state.unasked == set()
    assert "closed before it finished" in front.calls[0][1].words


def test_a_member_resting_for_its_lead_is_not_said_to_wait(claims):
    front = Front()
    eng = made(front, sessions={"m": s("m")})
    eng.live({"m": s("m", state="idle")}, False, {"m"})
    assert "tell" not in front.names() and eng.state.sessions["m"].live_state == "resting"


def test_a_closed_members_message_goes_on_the_clipboard():
    front = Front()
    eng = made(front, notes={"x": ("ui", "the screens"), "w": ("api", "later")}, sessions={"w": s("w")})
    eng.deliver()
    assert front.names() == ["put_on_clipboard", "say_note"] and front.calls[0][1] == "the screens"
    assert eng.state.notes == {"w": ("api", "later")}  # at work: it waits


def test_a_message_not_typed_takes_you_to_its_tab():
    front = Front()
    eng = made(front)
    eng.typed(actions.Told("Sent to api.", seconds=5))
    by_hand = actions.Told("Could not type it.", "warning", 14, clipboard="hi", go_to=s("m"))
    eng.typed(by_hand)
    assert front.names() == ["say_note", "put_on_clipboard", "go_to_tab", "say_note"]
    assert front.calls[1][1] == "hi" and front.calls[2][1].session_id == "m" and front.calls[3][1] is by_hand


def test_bit_points_out_idle_background_sessions_once():
    front = Front()
    eng = made(front)
    eng.idle(0)
    eng.idle(2)
    eng.idle(3)
    assert front.names() == ["show_bit"] and front.calls[0][1].words.startswith("2 background sessions sit idle")
    assert eng.state.told_idle


def test_the_ones_that_never_came_up(claims):
    front = Front()
    eng = made(front, opening={"up": 1.0, "nv": 1.0})
    eng.live({"up": s("up", state="idle"), "nv": s("nv", exit=Exit.CLEAN, state="")}, False, set())
    assert eng.state.came_up == {"up"}
    front.calls.clear()
    eng.came_up_check({"up", "nv"})
    assert front.calls[0][1].note.words == "nv did not come up: its tab says why."


def test_crowded_once_and_by_the_clock_given():
    big = s("c", context=900_000, started=NOW - timedelta(hours=2))
    said = {}
    for at in (NOW, NOW + timedelta(hours=1)):
        front = Front()
        eng = engine.Engine(front, clock=lambda at=at: at)
        eng.crowded([big])
        eng.crowded([big])
        assert front.names() == ["tell"] and eng.state.told_crowded == {"c"}
        said[at] = front.calls[0][1].note.words
        assert said[at] == alerts.crowded_event([big], front.cap_of, front.default_cap, set(), now=at).note.words
    assert said[NOW] != said[NOW + timedelta(hours=1)]


def test_no_thread_lock_or_clock_of_its_own():
    """Engine methods run on the front's UI thread only; the time is the clock it is given."""
    imported, named = set(), set()
    for node in ast.walk(ast.parse(inspect.getsource(engine))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Name):
            named.add(node.id)
        elif isinstance(node, ast.Attribute):
            named.add(node.attr)
    assert not imported & {"threading", "asyncio", "concurrent", "multiprocessing", "queue", "time", "_thread"}
    assert not named & {"Lock", "RLock", "Thread", "Timer", "Semaphore", "Condition", "now", "today", "utcnow",
                        "monotonic", "perf_counter", "time"}
    eng = engine.Engine(Front(), clock=lambda: NOW)
    assert set(vars(eng)) == {"front", "clock", "state"}


def test_the_dashboards_state_is_the_engines():
    """Each forwarded name set on the dashboard is the engine's, and back: one copy, never two."""
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    assert engine.FORWARDED == ("acts", "sessions", "queued", "unasked", "notes", "told_crowded", "told_idle",
                                "opening", "came_up", "full_poll_due", "full_polls")
    for name in engine.FORWARDED:
        value = object()
        setattr(app, name, value)
        assert getattr(app.engine.state, name) is value and name not in vars(app), name
        other = object()
        setattr(app.engine.state, name, other)
        assert getattr(app, name) is other, name
    assert app.engine.front is app
