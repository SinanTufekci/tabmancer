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

from tabmancer import accounts, actions, actor, alerts, engine
from tabmancer.sessions import Exit, Session, Status

NOW = datetime(2026, 11, 25, 12, 0)


def s(sid, state="busy", exit=Exit.RUNNING, context=50_000, **kw) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=f"C:/work/{sid}", title=f"T {sid}",
                   exit=exit, status=kw.pop("status", Status.RUNNING if exit is Exit.RUNNING else Status.DONE),
                   live_state=state, last_active=NOW - timedelta(minutes=3), context=context, **kw)


class Front:
    """Records what the engine has it say and do, in order."""

    default_cap = 200_000
    account = None
    signed_as = "me"

    def __init__(self, monitor: bool = False) -> None:
        self.calls: list[tuple] = []
        self.monitor = monitor

    def who(self, session):
        return session.project

    def other_accounts_note(self, opens):
        return "", ""

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
                                "opening", "came_up", "full_poll_due", "full_polls", "armed", "armed_unsaved",
                                "told_limit", "actor_misses", "told_alone")
    for name in engine.FORWARDED:
        value = object()
        setattr(app, name, value)
        assert getattr(app.engine.state, name) is value and name not in vars(app), name
        other = object()
        setattr(app.engine.state, name, other)
        assert getattr(app, name) is other, name
    assert app.engine.front is app


# --- usage limits and the sessions to continue at their reset (E2) -------------------------------------

LIFTS = NOW + timedelta(hours=1)


def lim(sid, resets=LIFTS) -> Session:
    return s(sid, state="", exit=Exit.CLEAN, resets=resets, status=Status.LIMIT)


@pytest.fixture
def kept(monkeypatch):
    """What the accounts file and config.json are asked to keep."""
    saved = {"limited": [], "armed": [], "ok": True}
    monkeypatch.setattr(accounts, "limited_on", dict)
    monkeypatch.setattr(accounts, "keep_limited", lambda limited: saved["limited"].append(dict(limited)))
    monkeypatch.setattr(actions, "save_armed", lambda armed: saved["armed"].append(dict(armed)) or saved["ok"])
    return saved


def test_bit_says_once_which_a_limit_stopped_by_the_clock_given(kept):
    for at, words in ((NOW, "l1: limit until 13:00. c continues then"),
                      (LIFTS + timedelta(minutes=5), "l1: the limit has lifted. c continues now")):
        front = Front()
        eng = engine.Engine(front, clock=lambda at=at: at, state=engine.EngineState(acts=True, sessions={
            "l1": lim("l1"), "w": s("w")}))
        eng.limits()
        eng.limits()
        assert front.names() == ["limits_at", "show_bit", "limits_at"] and front.calls[1][1].words == words
        assert front.calls[0][1] == {"l1"} and eng.state.told_limit == {"l1"}
        assert kept["limited"][-1] == {"l1": "me"}  # stopped on the account signed in now


def test_one_gone_on_meanwhile_is_forgotten_by_the_one_that_acts(kept):
    for acts in (True, False):
        armed = {"l1": LIFTS, "gone": LIFTS}
        eng = made(Front(), acts=acts, sessions={"l1": lim("l1"), "gone": s("gone")}, armed=dict(armed),
                   told_limit={"l1", "gone"})
        eng.limits()
        assert eng.state.told_limit == {"l1"}
        assert eng.state.armed == ({"l1": LIFTS} if acts else armed)
    assert kept["armed"] == [{"l1": LIFTS}] and len(kept["limited"]) == 1  # only the one that acts saves


def test_a_save_that_fails_is_said_once(kept):
    front = Front()
    eng = made(front, acts=True)
    kept["ok"] = False
    eng.save_armed()
    eng.save_armed()
    assert front.names() == ["say_note"] and front.calls[0][1] is actions.ARMED_UNSAVED and eng.state.armed_unsaved
    kept["ok"] = True
    eng.save_armed()
    assert not eng.state.armed_unsaved
    watching = made(Front())
    watching.save_armed()
    assert len(kept["armed"]) == 3


def test_the_saved_armed_shown_only_where_it_watches():
    front = Front()
    eng = made(front, acts=True)
    eng.show_armed({"l1": LIFTS})
    assert eng.state.armed == {} and front.calls == []
    eng.state.acts = False
    eng.show_armed({"l1": LIFTS})
    eng.show_armed({"l1": LIFTS})
    assert eng.state.armed == {"l1": LIFTS} and front.names() == ["armed_changed"]


def test_due_once_the_limit_lifted_by_the_clock(kept):
    clock = [NOW]
    front = Front()
    eng = engine.Engine(front, clock=lambda: clock[0], state=engine.EngineState(
        acts=True, sessions={"l1": lim("l1"), "w": s("w")}, armed={"l1": LIFTS, "w": LIFTS}))
    eng.due()
    assert front.calls == [] and kept["armed"] == []
    clock[0] = LIFTS + actions.LIMIT_MARGIN
    eng.due()
    assert front.calls == [("continue_these", [eng.state.sessions["l1"]], True)]  # w went on by hand: left alone
    assert eng.state.armed == {} and kept["armed"] == [{}]


def test_one_set_to_continue_at_its_reset(kept):
    watching = made(Front())
    assert watching.arm(lim("l1")) == actions.ARM_ELSEWHERE and watching.state.armed == {}
    eng = made(Front(), acts=True)
    assert eng.arm(s("w")) == "not at a limit"
    assert eng.arm(lim("l1")) is None and eng.arm(lim("l2", resets=None)) == "not at a limit"
    assert eng.state.armed == {"l1": LIFTS} and kept["armed"] == [{"l1": LIFTS}]


def test_the_c_dialogs_answers(kept):
    stopped = [lim("l1"), lim("l2", NOW + timedelta(hours=2))]
    front = Front()
    watching = made(front)
    assert not watching.choose("lift", ["l1"], stopped, "at 13:00")
    assert front.calls == [("say_note", actions.ACTS_ELSEWHERE)]
    front = Front()
    eng = made(front, acts=True, armed={"l1": LIFTS})
    assert not eng.choose("handover", ["l1"], stopped, "at 13:00")
    assert not eng.choose("now", ["l2"], stopped, "at 13:00")
    assert eng.choose("lift", ["l1", "l2"], stopped, "at 13:00")
    assert not eng.choose("later", ["l1"], stopped, "at 13:00")
    assert front.names() == ["open_handover", "continue_these", "say_note"]
    assert front.calls[0][1:] == (stopped[0], "transcript") and front.calls[1][1:] == ([stopped[1]], False)
    assert front.calls[2][1].words == "2 sessions continue at 13:00, when the limit lifts. Leave the dashboard open."
    assert eng.state.armed == {"l1": LIFTS, "l2": NOW + timedelta(hours=2)} and len(kept["armed"]) == 3


# --- one acts: the actor lock, and the front's timers (E3) ---------------------------------------------

OTHER = {"pid": 42}


def test_a_look_at_the_lock_keeps_no_state(monkeypatch):
    calls = []
    lock = {"holder": None, "claim": True, "running": True}
    monkeypatch.setattr(actor, "holder", lambda: lock["holder"])
    monkeypatch.setattr(actor, "claim", lambda: calls.append("claim") or lock["claim"])
    monkeypatch.setattr(actor, "is_me", lambda held: held == "me")
    monkeypatch.setattr(actor, "running", lambda held: lock["running"])
    monkeypatch.setattr(engine.store, "read_armed", lambda: {"l1": LIFTS})
    assert engine.look(True, False) == engine.Look("monitor", alone=False) and calls == []  # a monitor never claims
    lock["holder"] = "me"
    assert engine.look(False, True) == engine.Look("acting", held="me", kept=True) and calls == []
    lock["holder"] = None
    assert engine.look(False, True).kept and calls == ["claim"]  # none to read: taken again
    assert engine.look(False, False) == engine.Look("took")
    lock["claim"] = False
    assert engine.look(False, False) == engine.Look("watching", armed={"l1": LIFTS})
    assert list(inspect.signature(engine.look).parameters) == ["monitor", "acts"]  # no engine, no state


def test_the_one_that_acts_steps_back_after_two_unreadable_looks_or_anothers_lock():
    front = Front()
    eng = made(front, acts=True)
    eng.seen(engine.Look("acting", held=None))
    assert eng.state.acts and eng.state.actor_misses == 1 and front.calls == []
    eng.seen(engine.Look("acting", held=None, kept=True))
    assert eng.state.actor_misses == 0
    eng.seen(engine.Look("acting", held=None))
    eng.seen(engine.Look("acting", held=None))
    assert front.calls == [("stop_acting",), ("say_who_acts", {})]
    front.calls.clear()
    eng = made(front, acts=True)
    eng.seen(engine.Look("acting", held=OTHER))
    assert front.calls == [("stop_acting",), ("say_who_acts", OTHER)]  # another's: at once


def test_one_that_watches_takes_over_once_or_shows_the_saved_armed():
    front = Front()
    eng = made(front)
    eng.seen(engine.Look("watching", armed={"l1": LIFTS}))
    assert front.calls == [("armed_changed",)] and eng.state.armed == {"l1": LIFTS} and not eng.state.acts
    front.calls.clear()
    eng.state.actor_misses = 1
    eng.seen(engine.Look("took"))
    eng.seen(engine.Look("took"))
    assert front.calls == [("start_acting",), ("start_wrapped",), ("say_note", engine.TOOK_OVER)]
    assert eng.state.acts and eng.state.actor_misses == 0


def test_a_monitor_says_once_each_time_that_no_dashboard_is_open():
    front = Front(monitor=True)
    eng = made(front)
    for alone in (True, True, False, True):
        eng.seen(engine.Look("monitor", alone=alone))
    assert front.calls == [("say_note", engine.ALONE)] * 2 and eng.state.told_alone


def test_only_a_dashboard_takes_the_lock_and_only_the_one_that_acts_lets_it_go(monkeypatch):
    done = []
    monkeypatch.setattr(actor, "claim", lambda: done.append("claim") or True)
    monkeypatch.setattr(actor, "release", lambda: done.append("release"))
    monitor = made(Front(monitor=True))
    monitor.mount()
    monitor.unmount()
    assert done == [] and not monitor.state.acts
    dashboard = made(Front())
    dashboard.mount()
    dashboard.unmount()
    assert done == ["claim", "release"] and dashboard.state.acts


async def test_the_dashboards_timers_are_the_schedule(fake_home, monkeypatch):
    """engine.SCHEDULE is what the terminal dashboard runs: each timer at its seconds, the acting ones
    only while it acts."""
    from tabmancer import claudeupdate, wrapped
    from tabmancer.app import SessionPicker

    started: list[tuple[str, float, str]] = []
    for kind in ("set_interval", "set_timer"):
        real = getattr(SessionPicker, kind)

        def timed(self, seconds, callback=None, *a, kind=kind, real=real, **k):
            started.append((kind, seconds, getattr(callback, "__name__", "")))
            return real(self, seconds, callback, *a, **k)
        monkeypatch.setattr(SessionPicker, kind, timed)
    for name in ("poll_tick", "tick_actor", "refresh_usage", "refresh_tokens", "show_accounts", "refresh_git",
                 "check_updates", "continue_due", "tell_wrapped", "update_claude"):
        def quiet(self, *a, **k):
            return None
        quiet.__name__ = name
        monkeypatch.setattr(SessionPicker, name, quiet)
    monkeypatch.setattr(claudeupdate, "due", lambda *a, **k: False)
    monkeypatch.setattr(wrapped, "due", lambda now=None: False)
    monkeypatch.setattr(actor, "release", lambda: None)
    for acts in (True, False):
        started.clear()
        monkeypatch.setattr(actor, "claim", lambda acts=acts: acts)
        app = SessionPicker(days=14)
        async with app.run_test(size=(120, 30)) as pilot:
            for _ in range(8):
                await pilot.pause(0.05)
            assert app.acts is acts
            for every in engine.SCHEDULE:
                runs = every.acting <= acts
                assert (("set_interval", every.seconds, every.name) in started) is runs, every
                if every.first is not None:
                    assert (("set_timer", every.first, every.name) in started) is runs, every
            assert len(app.actor_timers) == (sum(1 + (e.first is not None) for e in engine.SCHEDULE if e.acting)
                                             if acts else 0)


def test_one_that_watches_continues_nothing_whatever_runs_due(kept):
    front = Front()
    eng = made(front, sessions={"l1": lim("l1")}, armed={"l1": LIFTS - timedelta(hours=2)})
    eng.due()
    assert front.calls == [] and eng.state.armed == {"l1": LIFTS - timedelta(hours=2)} and kept["armed"] == []
