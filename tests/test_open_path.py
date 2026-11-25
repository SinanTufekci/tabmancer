"""The open path without a screen (G3.3): what Enter does (actions.enter_choice), which terminal window
the tabs go to (actions.window_for), and Engine.open's bookkeeping, told to a recording front."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tabmancer import actions, engine, openings
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status


def made(sid: str, status: Status = Status.DONE) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), cwd=f"C:/work/{sid}", title=f"task {sid}",
                   status=status, exit=Exit.RUNNING if status is Status.RUNNING else Exit.CLEAN)


def resumed(sid: str) -> Launch:
    return Launch(title=sid, cwd="C:/work", mode=Mode.RESUME, session_id=sid)


# --- Enter --------------------------------------------------------------------------------------------

def test_enter_opens_the_ticked_ones_else_the_picked_one():
    a, b = made("a"), made("b")
    assert actions.enter_choice([a, b], b).open == [a, b]  # ticked: the cursor does not count
    assert actions.enter_choice([], b) == actions.EnterChoice(open=[b])


def test_enter_on_an_open_one_goes_to_its_tab_and_on_a_team_shows_it():
    running = made("r", Status.RUNNING)
    assert actions.enter_choice([], running) == actions.EnterChoice(tab=running)
    team = object()
    assert actions.enter_choice([], None, team) == actions.EnterChoice(team=team)
    assert actions.enter_choice([], None) == actions.EnterChoice(note=actions.PICK_FIRST)
    assert actions.PICK_FIRST == actions.Note("Select sessions with Space first.", "warning")


# --- the window --------------------------------------------------------------------------------------

def asked_in_order(answers: dict[str, bool]):
    asked: list[str] = []

    def ask(name: str):
        def answer() -> bool:
            asked.append(name)
            return answers[name]
        return answer
    return asked, ask("monitor"), ask("bring")


@pytest.mark.parametrize(("tabs_window", "watching", "unasked", "answers", "window", "asked"), [
    ("named", True, True, {"monitor": True, "bring": True}, "named", []),  # its name known: nothing asked
    (None, False, False, {"monitor": True, "bring": True}, "0", []),  # the dashboard's, asked: the last used
    (None, False, True, {"monitor": False, "bring": True}, "0", ["monitor"]),
    (None, False, True, {"monitor": True, "bring": True}, "tabmancer", ["monitor"]),  # a monitor may be last
    (None, True, False, {"monitor": True, "bring": True}, "0", ["bring"]),  # a monitor's: the dashboard's tab
    (None, True, False, {"monitor": True, "bring": False}, "new", ["bring"]),
    (None, True, True, {"monitor": True, "bring": True}, "new", []),  # unasked: nothing comes to the front
])
def test_the_window_tabs_go_to(tabs_window, watching, unasked, answers, window, asked):
    order, monitor_open, bring = asked_in_order(answers)
    assert actions.window_for(tabs_window, watching, unasked, monitor_open, bring, "tabmancer") == window
    assert order == asked


# --- Engine.open --------------------------------------------------------------------------------------

class Front:
    """Records what Engine.open has it do; the tabs it opens and the ids open now as a test sets them."""

    def __init__(self, monitor: bool = False, open_now: set[str] | None = None, left: list[str] | None = None,
                 fails: bool = False) -> None:
        self.calls: list[tuple] = []
        self.monitor, self.open_now, self.left, self.fails = monitor, open_now or set(), left or [], fails
        self.timers: list = []

    def monitoring(self):
        return self.monitor

    def live_ids(self):
        self.calls.append(("live_ids",))
        return set(self.open_now)

    def open_tabs(self, launches, window):
        self.calls.append(("open_tabs", [launch.session_id for launch in launches], window))
        if self.fails:
            raise OSError("no wt")
        return list(self.left)

    def later(self, seconds, then):
        self.calls.append(("later", seconds))
        self.timers.append(then)

    def who(self, session):
        return session.project

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.calls.append((name, *args, *kwargs.values()))


def opener(front: Front, **state) -> engine.Engine:
    sessions = {sid: made(sid) for sid in ("a", "b", "c")}
    return engine.Engine(front, clock=lambda: datetime(2026, 11, 25, 12, 0),
                         state=engine.EngineState(sessions=sessions, **state))


def test_opening_keeps_what_opened_says_it_and_looks_for_it_later():
    clock = iter([100.0, 200.0]).__next__
    front = Front()
    eng = opener(front, openings=openings.Openings(clock=clock))
    fork = Launch(title="b", cwd="C:/work", mode=Mode.FORK, session_id="b")
    eng.open([resumed("a"), fork], updated=True)
    assert front.calls == [
        ("live_ids",), ("open_tabs", ["a", "b"], None), ("later", openings.CAME_UP_SECONDS), ("untick", {"a", "b"}),
        ("opened_rows", {"a", "b"}), ("say_note", actions.Note("Opened 2 sessions in new tabs.", seconds=4)),
        ("later", 5)]
    assert eng.state.openings.opening == {"a": 100.0, "b": 100.0} and eng.state.openings.checks == []
    front.calls.clear()
    front.timers[1]()  # five seconds on: a full poll shows them open
    assert front.calls == [("poll_live", True)]
    front.calls.clear()
    front.timers[0]()  # a while on: "a" never came up (a fork is never looked for)
    assert [call[0] for call in front.calls] == ["tell"]
    assert front.calls[0][1].note.words == "a did not come up: its tab says why."


def test_one_open_elsewhere_is_left_alone_and_said():
    front = Front(open_now={"a"})
    eng = opener(front)
    eng.open([resumed("a"), resumed("b")], window="0", updated=True)
    assert front.calls[:5] == [
        ("live_ids",), ("open_tabs", ["b"], "0"),
        ("say_note", actions.Note("Open already, so not opened again: a.", "warning", 10)), ("untick", {"a"}),
        ("poll_live",)]
    assert ("untick", {"b"}) in front.calls and eng.state.openings.opening.keys() == {"b"}


def test_no_terminal_that_opens_tabs_closes_with_the_commands_and_keeps_nothing():
    front = Front(left=["claude --resume a"])
    eng = opener(front)
    eng.open([resumed("a")], updated=True)
    assert front.calls[-1] == ("exit_with", ["claude --resume a"]) and not eng.state.openings.opening
    front = Front(fails=True)
    eng = opener(front)
    eng.open([resumed("a")], updated=True)
    assert front.calls[-1] == ("say_note", actions.Note("Could not open the tab: no wt", "error", 15))
    assert not eng.state.openings.opening


def test_a_monitor_finds_the_dashboards_window_first():
    front = Front(monitor=True)
    eng = opener(front)
    launches = [resumed("a")]
    eng.open(launches)
    assert front.calls == [("open_in_dashboard_window", launches)]
    front.calls.clear()
    eng.open(launches, window="0", updated=True)  # found: opened there
    assert ("open_tabs", ["a"], "0") in front.calls


def test_while_claude_update_runs_they_open_once_it_is_done(monkeypatch):
    front = Front()
    eng = opener(front, updating_claude=True)
    monkeypatch.setattr(engine.alerts, "update_due", lambda updating, clis: False)
    eng.open([resumed("a")])
    assert [call[0] for call in front.calls] == ["say_note"] and "open" in front.calls[0][1].words
    assert len(eng.state.after_claude_update) == 1
    front.calls.clear()
    eng.state.after_claude_update.pop()()
    assert ("open_tabs", ["a"], None) in front.calls


def test_a_poll_keeps_the_background_jobs_it_read():
    eng = opener(Front())
    jobs = {"bg": {"sessionId": "bg", "id": "7f3a"}}
    eng.live({}, True, set(), jobs)
    assert eng.state.background_live is jobs
    eng.live({}, False, set())  # nothing kept: as it was
    assert eng.state.background_live is jobs
