"""Continue without a screen (G3.4): Engine.continue_these (c now, or the limit lifted) and
Engine.continued (the prompt typed off the UI thread, back), told to a recording front."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tabmancer import actions, alerts, engine
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status


def lim(sid: str, open_now: bool = False) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), cwd=f"C:/work/{sid}", title=f"task {sid}",
                   status=Status.LIMIT, exit=Exit.RUNNING if open_now else Exit.CLEAN,
                   live_state="idle" if open_now else "")


class Front:
    """Records what the engine has it do."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def launch_for(self, session):
        return Launch(title=session.title, cwd=session.cwd, mode=Mode.RESUME, session_id=session.session_id)

    def monitoring(self):
        return False

    def live_ids(self):
        return set()

    def open_tabs(self, launches, window):
        self.calls.append(("open_tabs", [(launch.session_id, launch.mode) for launch in launches], window))
        return []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, *args))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def made(front: Front) -> engine.Engine:
    return engine.Engine(front, clock=lambda: datetime(2026, 11, 25, 12, 0),
                         state=engine.EngineState(updating_claude=False))


def test_c_now_opens_the_closed_ones_and_types_into_the_open_ones():
    front = Front()
    closed, open_one = lim("c1"), lim("o1", open_now=True)
    made(front).continue_these([closed, open_one])
    assert front.calls[0] == ("open_tabs", [("c1", Mode.AUTO)], None)  # as a key opens: Engine.open
    assert front.calls[-1] == ("continue_open", [open_one], 1)  # Bit waits for the typing
    assert "tell" not in front.names()


def test_the_limit_lifted_opens_them_unasked():
    front = Front()
    made(front).continue_these([lim("c1")], unasked=True)
    assert front.names() == ["open_unasked", "tell"]
    launches = front.calls[0][1]
    assert [(launch.session_id, launch.mode) for launch in launches] == [("c1", Mode.AUTO)]
    assert launches[0].prompt == actions.continue_prompt(Status.LIMIT)
    assert front.calls[1][1] == alerts.back_event(1)  # nothing to type: Bit says it at once


def test_how_the_typing_went():
    front = Front()
    eng = made(front)
    missed = actions.Note("Could not write in the tab of o4: type “continue” there yourself.", "warning", 15)
    eng.continued(actions.Continued(2, missed))
    assert front.calls == [("tell", alerts.back_event(2)), ("say_note", missed)]
    front.calls.clear()
    eng.continued(actions.Continued(0))
    assert front.calls == []


def test_bit_says_how_many_are_back_in_the_dashboards_colour():
    event = alerts.back_event(3)
    assert event.kind == alerts.BACK and event.note is None and not event.bell
    assert event.bit == alerts.Bit("react", "3 back at work", "back", "accent")
