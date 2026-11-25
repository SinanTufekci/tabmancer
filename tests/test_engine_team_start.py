"""Engine.team_start, the team handover dialog's answer, when the team has no lead by then (made a team of
peers since the lead's handover was written): refused with handover.next_team's words, nothing saved or
opened, the kept handover dropped."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from tabmancer import engine, handover, teams
from tabmancer.actions import Note
from tabmancer.sessions import Exit, Session, Status


class Front:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, *args))


def lead() -> Session:
    return Session(session_id="lead1", path=Path("lead1.jsonl"), cwd="C:/work/shop", title="task lead1",
                   status=Status.DONE, exit=Exit.CLEAN)


def test_a_team_made_peers_since_its_handover_is_refused_and_nothing_is_saved_or_opened(monkeypatch):
    written = teams.new_team("shop", "C:/work/shop", "sell", teams.parse_members("lead: steer\napi: a", lead_ok=True))
    peers = teams.new_team("shop", "C:/work/shop", "sell", teams.parse_members("api: a; ui: b"), peers=True)
    monkeypatch.setattr(teams, "load", lambda: {"shop": peers})
    saves: list = []
    monkeypatch.setattr(teams, "save", saves.append)
    front = Front()
    eng = engine.Engine(front, clock=lambda: datetime(2026, 11, 26, 12, 0), state=engine.EngineState())
    eng.state.kept_handovers["shop"] = (lead(), "## written", "api: carry on", True)

    eng.team_start(written, lead(), "## written", ("api: carry on", ""))

    with pytest.raises(handover.HandoverError) as refused:
        handover.next_team(peers, "api: carry on")
    assert front.calls == [("say_note", Note(f"shop is not handed over. {refused.value}", "warning", 10))]
    assert "H hands the picked member over" in front.calls[0][1].words
    assert saves == [] and eng.state.kept_handovers == {}
