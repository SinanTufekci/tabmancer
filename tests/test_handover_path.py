"""Handing over without a screen (G3.5): Engine.handover and start_handover (h on a session),
handover_ready (written, the fresh one opens), team_handover (h twice on a team), team_handover_ready and
team_start (the team handover dialog's answer), told to a recording front."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tabmancer import actions, alerts, engine, handover, handoverwords, teams
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status


def s(sid: str, state: str = "", exit: Exit = Exit.CLEAN, status: Status | None = None) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), cwd="C:/work/shop", title=f"task {sid}",
                   status=status or (Status.RUNNING if exit is Exit.RUNNING else Status.DONE), exit=exit,
                   live_state=state)


class Front:
    """Records what the engine has it do; the dialogs' answers kept to give later."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.answers: list = []
        self.timers: list = []

    def monitoring(self):
        return False

    def live_ids(self):
        return set()

    def open_tabs(self, launches, window):
        self.calls.append(("open_tabs", [launch.title for launch in launches], window))
        return []

    def ask_handover(self, session, then):
        self.calls.append(("ask_handover", session.session_id))
        self.answers.append(then)

    def ask_team_start(self, team, written, lineup, suggested, then):
        self.calls.append(("ask_team_start", team.name, written, lineup, suggested))
        self.answers.append(then)

    def later(self, seconds, then):
        self.calls.append(("later", seconds))
        self.timers.append(then)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, *args))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def saved(monkeypatch):
    kept: dict = {"queued": [], "teams": []}
    monkeypatch.setattr(actions, "save_queued", lambda queued: kept["queued"].append(dict(queued)) or True)
    monkeypatch.setattr(teams, "save", lambda all_teams: kept["teams"].append(dict(all_teams)))
    return kept


def made(front: Front, **sessions: Session) -> engine.Engine:
    return engine.Engine(front, clock=lambda: datetime(2026, 11, 25, 12, 0),
                         state=engine.EngineState(sessions=dict(sessions)))


def test_h_asks_how_and_the_answer_starts_it(saved):
    front = Front()
    done = s("done")
    eng = made(front, done=done)
    eng.handover(None)
    assert front.calls == []
    eng.handover(done)
    assert front.calls == [("ask_handover", "done")]
    front.answers.pop()("transcript")
    assert front.names()[1:] == ["say_note", "hand_over"]
    assert front.calls[1][1] == handoverwords.started_note(done, "transcript")
    assert front.calls[2][1:] == (done, "transcript")


def test_a_background_one_is_refused():
    front = Front()
    background = s("bg", "busy", Exit.RUNNING, Status.BACKGROUND)
    made(front, bg=background).handover(background, "self")
    assert front.names() == ["say_note"] and front.calls[0][1].severity == "warning"


def test_a_busy_one_is_queued_and_h_again_takes_it_back(saved):
    front = Front()
    busy = s("busy", "busy", Exit.RUNNING)
    eng = made(front, busy=busy)
    eng.handover(busy, "self")
    assert eng.state.queued == {"busy": "self"} and saved["queued"] == [{"busy": "self"}]
    assert front.calls == [("say_note", actions.queued_note(busy, "self")), ("update_row", busy)]
    front.calls.clear()
    eng.handover(busy)
    assert eng.state.queued == {} and saved["queued"][-1] == {}
    assert front.calls == [("say_note", actions.unqueued_note(busy)), ("update_row", busy)]


def test_written_the_fresh_one_opens_as_a_key_opens_or_unasked_once_queued(monkeypatch):
    monkeypatch.setattr(handover, "fresh", lambda session, written: Launch("fresh", ".", Mode.NEW, ""))
    front = Front()
    done = s("done")
    eng = made(front, done=done)
    eng.handover_ready(done, "## handover")
    assert front.calls[0] == ("open_tabs", ["fresh"], None)
    assert front.calls[-2:] == [("say_note", handoverwords.handed_note(done, "")),
                                ("tell", alerts.handed_event("shop"))]
    front.calls.clear()
    eng.state.unasked.add("done")
    eng.handover_ready(done, "## handover", "its tab would not close")
    assert front.names() == ["open_unasked", "say_note", "tell"] and not eng.state.unasked


def shop() -> teams.Team:
    return teams.new_team("shop", "C:/work/shop", "sell", teams.parse_members("lead: steer\napi: a", lead_ok=True))


def test_h_twice_on_a_team_hands_it_over_and_a_press_elsewhere_starts_again():
    front = Front()
    eng = made(front)
    team = shop()
    other = teams.new_team("mill", "C:/work/mill", "grind", teams.parse_members("lead: steer", lead_ok=True))
    members = {"lead": s("lead1"), "api": s("api1", "idle", Exit.RUNNING)}
    eng.team_handover(team, members)
    assert eng.state.handing_team == "shop" and front.names() == ["say_note", "later"]
    assert front.calls[1] == ("later", 10)
    eng.team_handover(other, {"lead": s("mlead")})  # a press on another team never arms this one
    assert eng.state.handing_team == "mill"
    front.calls.clear()
    eng.team_handover(team, members)
    assert eng.state.handing_team == "shop" and front.names() == ["say_note", "later"]
    eng.team_handover(team, members)
    assert eng.state.handing_team is None
    assert front.calls[-2:] == [("say_note", handoverwords.team_handing_note(team)),
                                ("hand_over_team", team, members)]
    eng.state.handing_team = "shop"
    front.timers[0]()  # ten seconds on: a press is a first one again
    assert eng.state.handing_team is None


def test_the_leads_handover_is_kept_shown_again_and_started(saved, monkeypatch):
    front = Front()
    eng = made(front)
    team, lead = shop(), s("lead1")
    monkeypatch.setattr(teams, "load", lambda: {"shop": team})
    eng.team_handover_ready(team, lead, "## written", "api: carry on", True)
    assert front.calls == [("ask_team_start", "shop", "## written", "api: carry on", True)]
    front.answers.pop()(None)
    assert front.calls[-1] == ("say_note", handoverwords.team_not_started_note(team))
    assert eng.state.kept_handovers == {"shop": (lead, "## written", "api: carry on", True)}
    front.calls.clear()
    eng.team_handover(team, {"lead": lead, "api": None})  # closed still: shown again, not written again
    assert front.calls == [("ask_team_start", "shop", "## written", "api: carry on", True)]
    front.calls.clear()
    front.answers.pop()(("api: carry on\nqa: test", ""))
    assert front.names() == ["adopt_team", "open_team", "say_note", "tell"]
    fresh = front.calls[0][1]
    assert [m.name for m in fresh.members] == ["lead", "api", "qa"] and saved["teams"] == [{"shop": fresh}]
    assert front.calls[-1] == ("tell", alerts.handed_event("shop")) and eng.state.kept_handovers == {}


def test_opened_again_since_the_kept_handover_is_old():
    front = Front()
    eng = made(front)
    team = shop()
    eng.state.kept_handovers["shop"] = (s("lead1"), "old", "api: a", False)
    eng.team_handover(team, {"lead": s("lead1", "idle", Exit.RUNNING), "api": None})
    assert eng.state.kept_handovers == {} and front.names() == ["say_note", "later"]


def test_bit_says_who_was_handed_over_in_the_dashboards_colour():
    event = alerts.handed_event("shop")
    assert event.kind == alerts.HANDED and event.note is None and not event.bell
    assert event.bit == alerts.Bit("react", "shop: handed over", "handover", "accent")
