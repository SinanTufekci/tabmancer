"""An agy member stopped at a step held for your OK (a permission prompt in its tab) shows as needing
you, as a Claude session at a permission prompt does: it looked busy, and its turn never ended."""

from __future__ import annotations

import sqlite3

import pytest

from tabmancer import agy, teams, viewmodel
from tabmancer.sessions import Session

SID = "6a2b4b6c-cf4c-4a60-a0ac-a1fa413c7d35"


@pytest.fixture
def agy_home(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path)
    (tmp_path / "conversations").mkdir()
    return tmp_path


def conversation(home, statuses: list[int], sid: str = SID) -> None:
    """A conversation db as agy writes it: table steps, one row per step with its status."""
    con = sqlite3.connect(home / "conversations" / f"{sid}.db")
    con.execute("CREATE TABLE `steps` (`idx` integer,`step_type` integer NOT NULL DEFAULT 0,"
                "`status` integer NOT NULL DEFAULT 0,`step_payload` blob,PRIMARY KEY (`idx`))")
    con.executemany("INSERT INTO steps (idx, step_type, status) VALUES (?, 132, ?)", list(enumerate(statuses)))
    con.commit()
    con.close()


@pytest.mark.parametrize(("statuses", "waits"), [
    ([3, 3, 3, 9, 1], True),  # the seen case: team_who held (9), team_status behind it pending (1)
    ([3, 3, 9], True),
    ([3, 3, 9, 10, 0], True),  # queued and unspecified are not reached either
    ([3, 3, 3], False),  # done
    ([3, 9, 3, 2], False),  # held once, allowed: it went on
    ([3, 7, 1], False),  # denied: an error, its turn goes on
    ([3, 2], False),  # running
    ([3, 8], False),  # generating: seen briefly in a live run (2026-11-23), then gone; no prompt
    ([3, 9, 8], False),  # held once, then on to generating
    ([], False),
])
def test_the_newest_step_reached_decides(agy_home, statuses, waits):
    conversation(agy_home, statuses)
    assert agy.waiting_for_you(SID) is waits


def test_nothing_to_read_is_not_waiting(agy_home):
    assert not agy.waiting_for_you(SID)  # no db
    assert not agy.waiting_for_you("agy:worker")  # its conversation not known yet
    assert not agy.waiting_for_you("../../escape")
    (agy_home / "conversations" / f"{SID}.db").write_text("not a database", encoding="utf-8")
    assert not agy.waiting_for_you(SID)


def test_the_db_is_only_read(agy_home):
    conversation(agy_home, [3, 9, 1])
    path = agy_home / "conversations" / f"{SID}.db"
    before = path.read_bytes()
    assert agy.waiting_for_you(SID)
    assert path.read_bytes() == before


@pytest.mark.parametrize("presence", [{}, {"listening": True}])
def test_a_held_member_needs_you_even_while_listening(agy_home, presence):
    conversation(agy_home, [3, 3, 9, 1])
    session = Session(session_id=SID, path=None, cwd="", cli="agy")
    state = teams._agy_live_state(session, presence)
    assert state == "permission"
    words, _, rank, kind = viewmodel.LIVE[state]
    assert (words, rank, kind) == ("needs your OK", 0, "needs-you")  # as a Claude session at a prompt


def test_a_member_that_went_on_is_not_held(agy_home):
    conversation(agy_home, [3, 9, 3])
    session = Session(session_id=SID, path=None, cwd="", cli="agy")
    assert teams._agy_live_state(session, {"listening": True}) == "resting"
    assert teams._agy_live_state(session, {}) == "idle"


def test_the_team_row_and_the_view_say_it_needs_you(agy_home, tmp_path, monkeypatch):
    """The seam: a running agy member found by its presence, its live state, the team view."""
    from datetime import datetime, timedelta

    from tabmancer import teambus

    conversation(agy_home, [3, 3, 9, 1])
    team = teams.new_team("seam", str(tmp_path), "Ping", teams.parse_members("worker: pings @agy"))
    team.created = (datetime.now().astimezone() - timedelta(hours=1)).isoformat(timespec="seconds")
    found = Session(session_id=SID, path=None, cwd=str(tmp_path), cli="agy", started=datetime.now(),
                    last_active=datetime.now())
    monkeypatch.setattr(teambus, "read_presence", lambda name: {
        "worker": {"member": "worker", "runner_alive": True, "listening": True, "session_id": SID}})
    monkeypatch.setattr(teambus, "read_bindings", lambda name: {})
    member = teams.member_sessions(team, [found], live={})["worker"]
    assert member is not None and member.live_state == "permission"
    view = viewmodel.team_view(teams.TeamState(team, {"worker": member}, []), {}, {}, bus=({}, {}))
    worker = next(m for m in view.members if m.name == "worker")  # a lead comes with the team
    assert worker.needs_you and worker.state == "needs-you"
