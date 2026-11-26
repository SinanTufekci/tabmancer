"""What the dashboard does by itself, through the engine and the real core behind it, with no UI: the
acting behaviours the terminal dashboard's tests (test_actor_app, test_limits, test_keep_warm,
test_open_guard) prove through Textual, here driven on engine.Engine with a front that only records. The
actor lock is a real file (in the test's own config folder), the sessions to continue are saved in the
real config.json, a queued handover is claimed for real (actor.first), opening goes through
actions.open_launches and Openings, the continue prompt through actions.type_continue, and a session's
state is read from a real transcript. test_engine pins the engine's own decisions with these stood in for.

Only the front is made up: what a front does off its UI thread (typing into a tab, the timers) runs here
at once, in the order the engine asks for it."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tabmancer import actions, actor, alerts, claudeupdate, config, engine, openings, typein
from tabmancer.launch import Launch, Mode, continue_prompt
from tabmancer.sessions import Exit, Session, Status, mark_live, read_session

NOW = datetime(2026, 11, 25, 12, 0)
LIFTS = NOW + timedelta(hours=1)
OTHER = {"pid": 4242, "started": "100", "command": "tabmancer.exe"}


def s(sid, state="busy", exit=Exit.RUNNING, **kw) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=f"C:/work/{sid}", title=f"T {sid}",
                   exit=exit, status=kw.pop("status", Status.RUNNING if exit is Exit.RUNNING else Status.DONE),
                   live_state=state, last_active=NOW - timedelta(minutes=3), **kw)


def lim(sid, resets=LIFTS, **kw) -> Session:
    """Stopped at a usage limit: closed so, or (state "idle") open and waiting since."""
    state = kw.pop("state", "")
    return s(sid, state=state, exit=Exit.RUNNING if state else Exit.CLEAN, resets=resets, status=Status.LIMIT, **kw)


class Front:
    """A front that records what the engine has it say and do, in order. What a real front does on a
    thread or a timer and then hands back to the engine (the continue prompt typed, the acting timers
    started or stopped, tabs no key asked for) it does at once; the timers it is asked for are kept."""

    default_cap = 200_000

    def __init__(self, monitor: bool = False, live: set[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.monitor = monitor
        self.live = live or set()  # what a Claude process has open now (Front.live_ids)
        self.tabs: list[Launch] = []
        self.timers: list[tuple[float, object]] = []
        self.engine: engine.Engine | None = None

    def who(self, session):
        return session.project

    def cap_of(self, session):
        return 0

    def monitoring(self):
        return self.monitor

    def other_accounts_note(self, opens):
        return "", ""

    def live_ids(self):
        return set(self.live)

    def launch_for(self, session):
        return actions.launch_for(session, Mode.RESUME)

    def open_tabs(self, launches, window):
        self.calls.append(("open_tabs", [launch.session_id for launch in launches], window))
        self.tabs.extend(launches)
        return []

    def open_unasked(self, launches):
        self.calls.append(("open_unasked", [launch.session_id for launch in launches]))
        self.engine.open(launches, "0")  # the dashboard's own window

    def continue_open(self, still_open, opened):
        self.calls.append(("continue_open", [x.session_id for x in still_open], opened))
        self.engine.continued(actions.type_continue(still_open, opened))  # off the UI thread, then back

    def start_acting(self):
        self.calls.append(("start_acting",))
        self.engine.started()

    def stop_acting(self):
        self.calls.append(("stop_acting",))
        self.engine.stopped()

    def later(self, seconds, then):
        self.calls.append(("later", seconds))
        self.timers.append((seconds, then))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, *args))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]

    def notes(self) -> list[str]:
        return [call[1].words for call in self.calls if call[0] == "say_note"]

    def told(self) -> list[alerts.Event]:
        return [call[1] for call in self.calls if call[0] == "tell"]


def made(front: Front, clock=lambda: NOW, **state) -> engine.Engine:
    state.setdefault("signed_as", "me")
    front.engine = engine.Engine(front, clock=clock, state=engine.EngineState(**state))
    return front.engine


def lock() -> Path:
    return config.config_dir() / actor.LOCK


def armed_saved() -> dict:
    return config.read_json("config.json").get("continue_at_reset", {})


# --- one acts: the actor lock, a real file (test_actor_app) -------------------------------------------------


@pytest.fixture
def another(monkeypatch):
    """Another dashboard holds the lock, and runs while `alive` says so (only its pid is made up)."""
    alive = {"now": True}
    real = actor.process
    monkeypatch.setattr(actor, "process", lambda pid: (("100", "tabmancer.exe") if alive["now"] else None)
                        if pid == OTHER["pid"] else real(pid))
    config.config_dir().mkdir(parents=True, exist_ok=True)
    lock().write_text(json.dumps(OTHER), encoding="utf-8")
    return alive


def test_the_first_dashboard_takes_the_lock_and_lets_it_go_at_its_end():
    eng = made(Front())
    eng.mount()
    assert eng.state.acts and actor.holds() and not claudeupdate._elsewhere  # claude update is its to run
    assert engine.look(False, True) == engine.Look("acting", held=actor.holder(), kept=True)
    eng.unmount()
    assert not lock().exists()  # the next one to look takes it


def test_a_second_dashboard_watches_and_does_nothing_by_itself(another):
    config.write_json("config.json", {"continue_at_reset": {"l1": LIFTS.isoformat()}})
    kept = lock().read_text(encoding="utf-8"), config.read_json("config.json")
    front = Front()
    eng = made(front, sessions={"l1": lim("l1"), "w": s("w")})
    eng.mount()
    assert not eng.state.acts and claudeupdate._elsewhere  # the one that acts runs claude update
    eng.seen(engine.look(False, eng.state.acts))
    assert eng.state.armed == {"l1": LIFTS} and front.names() == ["armed_changed"]  # shown: the other continues it
    eng.live({"w": s("w", state="idle")}, False, set())
    assert [e.kind for e in front.told()] == [alerts.DONE] and "tell_desktop" not in front.names()
    assert eng.arm(lim("l2")) == actions.ARM_ELSEWHERE
    assert not eng.choose("lift", ["l1"], [lim("l1")], "at 13:00")
    eng.state.armed = {"l1": NOW - timedelta(hours=1)}  # its limit lifted: still the other's to continue
    eng.due()
    eng.save_armed()
    eng.limits()
    eng.unmount()
    assert "open_tabs" not in front.names() and "continue_open" not in front.names()
    assert (lock().read_text(encoding="utf-8"), config.read_json("config.json")) == kept  # not its to write or free


def test_it_takes_over_when_the_other_is_gone_and_steps_back_when_taken_over(another):
    front = Front()
    eng = made(front)
    eng.mount()
    eng.seen(engine.look(False, eng.state.acts))
    assert not eng.state.acts and front.calls == []
    another["now"] = False  # closed (or its pid went to another process)
    eng.seen(engine.look(False, eng.state.acts))
    assert eng.state.acts and actor.holds() and not claudeupdate._elsewhere
    assert front.calls == [("start_acting",), ("start_wrapped",), ("say_note", engine.TOOK_OVER)]
    front.calls.clear()
    lock().write_text(json.dumps(OTHER), encoding="utf-8")  # another judged it gone and took the lock
    another["now"] = True
    eng.seen(engine.look(False, eng.state.acts))
    assert front.calls == [("stop_acting",), ("say_who_acts", OTHER)]  # at once: only one may act
    assert not eng.state.acts and claudeupdate._elsewhere


def test_a_lock_it_cannot_read_for_a_moment_does_not_stop_it(monkeypatch):
    """A lock file held open a moment (a virus scanner) reads as none: it keeps acting through one such
    look, and steps back only after two in a row."""
    front = Front()
    eng = made(front)
    eng.mount()
    assert eng.state.acts
    monkeypatch.setattr(actor, "holder", lambda: None)
    monkeypatch.setattr(actor, "claim", lambda: False)
    eng.seen(engine.look(False, eng.state.acts))
    assert eng.state.acts and eng.state.actor_misses == 1 and front.calls == []
    eng.seen(engine.look(False, eng.state.acts))
    assert not eng.state.acts and front.calls == [("stop_acting",), ("say_who_acts", {})]


def test_a_monitor_with_no_dashboard_says_so_once_and_never_takes_the_lock():
    front = Front(monitor=True)
    eng = made(front)
    eng.mount()
    for _ in range(3):
        eng.seen(engine.look(True, eng.state.acts))
    assert front.calls == [("say_note", engine.ALONE)] and not eng.state.acts and not lock().exists()


def test_a_dashboard_that_cannot_name_itself_only_watches(monkeypatch):
    """Its lock would be judged gone by the next to look: it never takes it."""
    monkeypatch.setattr(actor, "_mine", {"pid": 1, "started": "", "command": ""})
    eng = made(Front())
    eng.mount()
    eng.seen(engine.look(False, eng.state.acts))
    assert not eng.state.acts and not lock().exists()


def test_a_failed_save_of_the_sessions_to_continue_is_said_once_and_the_file_kept():
    front = Front()
    eng = made(front, acts=True, armed={"l1": LIFTS})
    path = config.config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"theme": ', encoding="utf-8")
    for _ in range(3):  # continue_due saves again every 10 s
        eng.save_armed()
    assert front.calls == [("say_note", actions.ARMED_UNSAVED)] and path.read_text(encoding="utf-8") == '{"theme": '
    path.write_text("{}", encoding="utf-8")
    eng.save_armed()
    assert not eng.state.armed_unsaved and armed_saved() == {"l1": LIFTS.isoformat()}


@pytest.mark.parametrize("first", [True, False])
def test_a_queued_handover_is_handed_over_once(first):
    if not first:
        assert actor.first_to("handover-q")  # the other window queued it too, and got there first
    front = Front()
    q = s("q")
    eng = made(front, acts=True, sessions={"q": q}, queued={"q": "self"})
    eng.live({"q": replace(q, live_state="idle")}, False, set())
    assert eng.state.queued == {} and ("hand_over" in front.names()) is first
    assert front.notes()[0] == ("q finished: handing it over now." if first else
                                "q finished: another tabmancer window hands it over.")


# --- continued at the limit: c, and when the limit lifts (test_limits) --------------------------------------


@pytest.fixture
def typed(monkeypatch):
    """What is typed into a tab (typein.type_into): kept, and taken while ok says so."""
    kept = {"ok": True, "typed": []}
    monkeypatch.setattr(typein, "type_into", lambda sid, text: kept["typed"].append((sid, text)) or kept["ok"])
    return kept


def test_lift_saves_them_and_they_go_on_once_the_limit_lifts(typed):
    clock = [NOW]
    closed, open_one = lim("lim"), lim("open", state="idle")
    front = Front()
    eng = made(front, clock=lambda: clock[0], acts=True, sessions={"lim": closed, "open": open_one})
    both = ["lim", "open"]
    assert eng.choose("lift", both, [closed, open_one], "at 13:00")
    assert set(armed_saved()) == {"lim", "open"}  # kept over a restart, and shown where it watches
    assert eng.choose("off", both, [closed, open_one], "at 13:00") and armed_saved() == {}
    eng.choose("lift", both, [closed, open_one], "at 13:00")
    eng.due()  # not yet: nothing opens
    assert not front.tabs and not typed["typed"] and set(eng.state.armed) == {"lim", "open"}
    clock[0] = LIFTS + actions.LIMIT_MARGIN
    eng.due()
    assert [launch.session_id for launch in front.tabs] == ["lim"]  # the closed one in a new tab
    assert front.tabs[0].mode is Mode.AUTO and front.tabs[0].argv[-1] == continue_prompt(Status.LIMIT)
    assert typed["typed"] == [("open", continue_prompt(Status.LIMIT))]  # the open one in its own tab
    assert ("open_unasked", ["lim"]) in front.calls  # by itself: the dashboard's window, keys left alone
    assert [e.bit.words for e in front.told() if e.kind == alerts.BACK] == ["2 back at work"]
    assert eng.state.armed == {} and armed_saved() == {}


def test_signed_in_again_one_waiting_for_its_limit_goes_on_now(typed):
    open_one = lim("open", state="idle")
    eng = made(Front(), acts=True, sessions={"open": open_one})
    eng.choose("lift", ["open"], [open_one], "at 13:00")
    eng.due()
    assert not typed["typed"] and eng.state.armed
    eng.state.sessions["open"] = replace(open_one, signed_in_again=True)  # /login in it, another account
    eng.due()
    assert typed["typed"] == [("open", continue_prompt(Status.LIMIT))] and eng.state.armed == {}


def test_one_gone_on_by_hand_meanwhile_is_left_alone(typed):
    front = Front()
    eng = made(front, acts=True, sessions={"lim": s("lim", exit=Exit.CLEAN)},  # continued by hand: no limit now
               armed={"lim": NOW - timedelta(minutes=2)})
    eng.due()
    assert not front.tabs and not typed["typed"] and eng.state.armed == {} and armed_saved() == {}


def test_now_opens_the_closed_and_types_into_the_open_one_whose_tab_is_never_closed(typed):
    closed, open_one = lim("lim"), lim("open", state="idle")
    front = Front()
    eng = made(front, acts=True, sessions={"lim": closed, "open": open_one})
    typed["ok"] = False  # its console would not take the keys (or not on Windows)
    eng.choose("now", ["lim", "open"], [closed, open_one], "at 13:00")
    assert [launch.session_id for launch in front.tabs] == ["lim"]  # as a key opens: no second tab for the open one
    assert "open_unasked" not in front.names() and "close_tab" not in front.names()
    assert "Could not write in the tab of open: type “continue” there yourself." in front.notes()
    assert [e.bit.words for e in front.told() if e.kind == alerts.BACK] == ["1 back at work"]


# --- the bell when a turn ends with a keep-warm sleep running (test_keep_warm) --------------------------------


def stamp(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def records(*, ended: bool) -> list[dict]:
    """A turn at work; ended: it ends with `sleep 3300` started in the background to keep its cache warm."""
    asked = [{"type": "user", "timestamp": stamp(0.2), "cwd": "C:/work/demo", "sessionId": "s",
              "message": {"role": "user", "content": "fix the bug"}}]
    if not ended:
        return asked
    return asked + [
        {"type": "assistant", "timestamp": stamp(0.1), "message": {"role": "assistant", "stop_reason": "tool_use",
         "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {
             "command": "sleep 3300", "description": "Wait", "run_in_background": True}}]}},
        {"type": "user", "timestamp": stamp(0.1), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "Command running in background with ID: b1."}]}},
        {"type": "assistant", "timestamp": stamp(0), "message": {"role": "assistant", "stop_reason": "end_turn",
         "content": [{"type": "text", "text": "Done: the fix is in."}]}}]


def polled(tmp_path, *, ended: bool) -> Session:
    """The session as a live poll reads it: its transcript, and Claude Code saying busy all along."""
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records(ended=ended)),
                    encoding="utf-8")
    session = read_session(path)
    mark_live(session, {"sessionId": "s", "kind": "interactive", "status": "busy"})
    return session


def test_a_turn_that_ends_with_a_keep_warm_sleep_running_is_said_to_wait_for_you(tmp_path):
    front = Front()
    working = polled(tmp_path, ended=False)
    assert working.live_state == "busy"
    eng = made(front, acts=True, sessions={"s": working})
    waiting = polled(tmp_path, ended=True)
    assert waiting.live_state == "idle"  # though Claude Code still says busy
    eng.live({"s": waiting}, False, set())
    assert [(e.kind, e.session) for e in front.told()] == [(alerts.DONE, "s")]  # the bell, j, the note
    assert [x.session_id for x in next(call[1] for call in front.calls if call[0] == "tell_desktop")] == ["s"]


# --- never opened twice; the ones that never came up (test_open_guard) -----------------------------------------


def demo(sid: str, title: str) -> Session:
    return replace(s(sid, state="", exit=Exit.CLEAN), cwd="C:/work/demo", title=title)


def test_one_opened_elsewhere_meanwhile_is_left_alone():
    front = Front(live={"two"})  # its pid file is there, the list not yet
    eng = made(front, sessions={"one": demo("one", "First task"), "two": demo("two", "Second task")})
    eng.open([Launch("demo: First task", "C:/work/demo", Mode.RESUME, "one"),
              Launch("demo: Second task", "C:/work/demo", Mode.RESUME, "two")])
    assert [launch.session_id for launch in front.tabs] == ["one"]
    assert front.notes()[0] == "Open already, so not opened again: demo: Second task."
    assert ("untick", {"two"}) in front.calls and ("poll_live",) in front.calls  # shown as open


def test_a_fresh_session_in_the_folder_of_one_open_elsewhere_still_opens():
    """Resuming it would make a second process on its transcript; a new session in its folder does not."""
    front = Front(live={"two"})
    eng = made(front)
    eng.open([Launch("t", "C:/work/demo", Mode.AUTO, "two"), Launch("t", "C:/work/demo", Mode.NEW, "two")])
    assert [launch.mode for launch in front.tabs] == [Mode.NEW]


def opened_one(front: Front) -> tuple[engine.Engine, object]:
    eng = made(front, sessions={"one": demo("one", "First task")})
    eng.open([Launch("demo: First task", "C:/work/demo", Mode.RESUME, "one")])
    (end,) = [then for seconds, then in front.timers if seconds == openings.CAME_UP_SECONDS]
    return eng, end


def test_a_session_that_never_comes_up_is_named():
    front = Front()
    _, end = opened_one(front)
    end()  # the window's end: no process ever showed up (Claude Code would not resume it)
    assert [e.note.words for e in front.told() if e.kind == alerts.NOT_UP] == [
        "demo did not come up: its tab says why."]


def test_a_session_that_comes_up_is_not_named():
    front = Front()
    eng, end = opened_one(front)
    eng.live({"one": replace(demo("one", "First task"), exit=Exit.RUNNING, live_state="idle")}, False, set())
    assert "one" in eng.state.openings.came_up
    end()
    assert not [e for e in front.told() if e.kind == alerts.NOT_UP]
