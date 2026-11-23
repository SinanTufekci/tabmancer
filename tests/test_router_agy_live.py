"""Live state for any agy conversation in the list, not only a team member's: at work or waiting for
your OK, read from its own db while that db moves. Fakes only: no agy is run."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tabmancer import agy
from tabmancer.sessions import Exit, Session, Status

SID = "6a2b4b6c-cf4c-4a60-a0ac-a1fa413c7d35"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path)
    (tmp_path / "conversations").mkdir()
    return tmp_path


def conversation(home: Path, statuses: list[int], sid: str = SID, minutes_ago: float = 0) -> Path:
    path = home / "conversations" / f"{sid}.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE steps (idx integer, step_type integer NOT NULL DEFAULT 0, "
                "status integer NOT NULL DEFAULT 0, step_payload blob, PRIMARY KEY (idx))")
    con.executemany("INSERT INTO steps (idx, step_type, status) VALUES (?, 132, ?)", list(enumerate(statuses)))
    con.commit()
    con.close()
    if minutes_ago:
        then = time.time() - minutes_ago * 60
        os.utime(path, (then, then))
    return path


def index(home: Path, sids: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.1234567+00:00")
    con = sqlite3.connect(home / agy.INDEX)
    con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, "
                "step_count integer, last_modified_time datetime, workspace_uris text, status text, "
                "nesting_depth integer, not_fully_idle numeric, killed numeric)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(sid, f"work {sid[:4]}", "", 3, now, '["file:///C:/work/parts"]', "CASCADE_RUN_STATUS_IDLE",
                      0, 0, 0) for sid in sids])
    con.commit()
    con.close()


def plain(sid: str = SID, **changes) -> Session:
    return replace(Session(session_id=sid, path=None, cwd="C:/work/parts", cli="agy", status=Status.DONE,
                           exit=Exit.UNKNOWN), **changes)


@pytest.mark.parametrize(("statuses", "state"), [
    ([3, 2], "busy"), ([3, 8], "busy"), ([3, 9, 1], "permission"), ([3, 3], ""), ([3, 7], ""),
])
def test_a_conversation_at_work_or_held_for_you_shows_as_open(home, statuses, state):
    conversation(home, statuses)
    fresh = agy.with_live_state(plain())
    assert fresh.live_state == state
    assert (fresh.exit, fresh.status) == ((Exit.RUNNING, Status.RUNNING) if state else (Exit.UNKNOWN, Status.DONE))


def test_the_list_carries_it_from_the_start(home):
    other = "7b3c5d7e-0000-4000-8000-000000000001"
    conversation(home, [3, 9])
    conversation(home, [3, 3], sid=other)
    index(home, [SID, other])
    found = {s.session_id: s for s in agy.find(14)}
    assert found[SID].live_state == "permission" and found[SID].exit is Exit.RUNNING
    assert found[other].live_state == "" and found[other].status is Status.DONE


def test_a_step_left_running_long_ago_is_not_believed(home):
    conversation(home, [3, 2], minutes_ago=agy.LIVE_WITHIN.total_seconds() / 60 + 5)  # an agy killed mid-step
    assert agy.live_state(SID) == ""
    assert agy.with_live_state(plain()).exit is Exit.UNKNOWN


def test_a_fresh_write_ahead_log_counts_as_written(home):
    path = conversation(home, [3, 2], minutes_ago=60)
    path.with_name(path.name + "-wal").write_bytes(b"")  # written just now
    assert agy.live_state(SID) == "busy"


def test_it_goes_back_to_done_when_the_work_ends(home):
    path = conversation(home, [3, 2])
    was = agy.with_live_state(plain())
    assert was.live_state == "busy"
    con = sqlite3.connect(path)
    con.execute("UPDATE steps SET status = 3")
    con.commit()
    con.close()
    after = agy.with_live_state(was)
    assert (after.exit, after.status, after.live_state) == (Exit.UNKNOWN, Status.DONE, "")


def test_nothing_new_returns_the_same_object(home):
    conversation(home, [3, 2])
    busy = agy.with_live_state(plain())
    assert agy.with_live_state(busy) is busy
    done = plain(session_id="7b3c5d7e-0000-4000-8000-000000000002")  # no db at all
    assert agy.with_live_state(done) is done


def test_a_session_open_for_another_reason_is_left_alone(home):
    resting = plain(exit=Exit.RUNNING, status=Status.RUNNING, live_state="resting")  # a team member listening
    assert agy.with_live_state(resting) is resting


def test_other_clis_are_not_read(home):
    conversation(home, [3, 2])
    claude = plain(cli="claude")
    assert agy.with_live_state(claude) is claude


def test_an_odd_id_reads_nothing(home):
    assert agy.live_state("agy:worker") == "" and agy.live_state("../../x") == "" and agy.live_state("") == ""


def test_now_can_be_given(home):
    conversation(home, [3, 2])
    assert agy.live_state(SID, now=datetime.now() + timedelta(hours=1)) == ""
    assert agy.live_state(SID, now=datetime.now()) == "busy"


def test_live_updates_gives_only_the_changed_ones(home):
    busy_id, quiet_id = SID, "7b3c5d7e-0000-4000-8000-000000000003"
    conversation(home, [3, 8], sid=busy_id)
    conversation(home, [3, 3], sid=quiet_id)
    known = {busy_id: plain(busy_id), quiet_id: plain(quiet_id), "c1": plain("c1", cli="claude")}
    changed = agy.live_updates(known)
    assert set(changed) == {busy_id} and changed[busy_id].live_state == "busy"
    assert agy.live_updates(known | changed) == {}  # polled again with nothing new: nothing


async def test_the_dashboard_shows_an_agy_conversation_at_work_and_then_done(home):
    from tabmancer import clis, viewmodel
    from tabmancer.app import SessionPicker

    clis.set_enabled(["claude", "agy"])
    path = conversation(home, [3, 2])
    index(home, [SID])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if SID in app.sessions:
                break
        assert viewmodel.state_id(app.sessions[SID]) == "working"
        con = sqlite3.connect(path)
        con.execute("INSERT INTO steps (idx, step_type, status) VALUES (2, 132, 9)")  # now held for your OK
        con.commit()
        con.close()
        app.poll_live(full=True)
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions[SID].live_state == "permission":
                break
        assert viewmodel.state_id(app.sessions[SID]) == "needs-you"
        con = sqlite3.connect(path)
        con.execute("UPDATE steps SET status = 3")
        con.commit()
        con.close()
        app.poll_live(full=True)
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions[SID].exit is not Exit.RUNNING:
                break
        assert app.sessions[SID].status is Status.DONE and app.sessions[SID].live_state == ""
