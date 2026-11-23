"""G4 in the dashboard: of two dashboards one acts (actor.py), the other watches and takes over when
the first is gone; a queued handover is handed over once, whichever window queued it."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tabmancer import accounts, actor, claudeupdate, config, toast, usage, wrapped
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit, Status

OTHER = {"pid": 4242, "started": "100", "command": "tabmancer.exe"}


@pytest.fixture
def another(monkeypatch):
    """Another dashboard holds the lock, and runs while `alive` says so."""
    alive = {"now": True}
    real = actor.process
    monkeypatch.setattr(actor, "process", lambda pid: (("100", "tabmancer.exe") if alive["now"] else None)
                        if pid == 4242 else real(pid))
    config.config_dir().mkdir(parents=True, exist_ok=True)
    (config.config_dir() / actor.LOCK).write_text(json.dumps(OTHER), encoding="utf-8")
    return alive


def stub_acting(monkeypatch) -> list[str]:
    done: list[str] = []
    monkeypatch.setattr(usage, "record", lambda limits: done.append("usage.record"))
    monkeypatch.setattr(accounts, "remember", lambda *a, **k: done.append("accounts.remember"))
    monkeypatch.setattr(accounts, "keep_limited", lambda kept: done.append("accounts.keep_limited"))
    monkeypatch.setattr(claudeupdate, "due", lambda *a, **k: not claudeupdate._elsewhere)
    monkeypatch.setattr(SessionPicker, "update_claude", lambda self: done.append("update_claude"))
    monkeypatch.setattr(SessionPicker, "check_updates", lambda self: done.append("check_updates"))
    monkeypatch.setattr(SessionPicker, "continue_due", lambda self: done.append("continue_due"))
    monkeypatch.setattr(wrapped, "due", lambda now=None: False)
    monkeypatch.setattr(toast, "show", lambda *a, **k: done.append("toast"))
    monkeypatch.setattr(claudeupdate, "_elsewhere", False)
    return done


async def test_the_first_dashboard_acts_and_lets_go_as_it_closes(fake_home, monkeypatch):
    done = stub_acting(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert app.acts and actor.holds()
        assert {"check_updates", "update_claude", "usage.record"} <= set(done)
    assert not (config.config_dir() / actor.LOCK).exists()  # the next one to look takes it


async def test_a_second_dashboard_watches_and_does_nothing_by_itself(fake_home, monkeypatch, another):
    done = stub_acting(monkeypatch)
    moment = (datetime.now() + timedelta(hours=1)).replace(microsecond=0)
    config.write_json("config.json", {"continue_at_reset": {"one": moment.isoformat()}})
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert not app.acts and app.watch is None
        app.check_limits()
        app.tell_desktop([app.sessions["one"]])
        await pilot.pause(0.2)
        assert app.armed == {"one": moment}  # shown: the other continues it
        assert any("Another tabmancer dashboard (pid 4242)" in str(n.message) for n in app._notifications)
        assert app.check_action("hide", ()) is not False  # the list's own keys work as ever
    assert done == []
    assert json.loads((config.config_dir() / actor.LOCK).read_text(encoding="utf-8")) == OTHER  # not its to free


async def test_it_takes_over_when_the_other_is_gone(fake_home, monkeypatch, another):
    done = stub_acting(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert not app.acts and done == []
        another["now"] = False  # closed (or its pid went to another process)
        await tick(app, pilot)
        assert app.acts and actor.holds()
        assert {"check_updates", "update_claude"} <= set(done) and not claudeupdate._elsewhere
        assert any("this one does now" in str(n.message) for n in app._notifications)
        # and should another judge it gone and take the lock, it steps back at once
        (config.config_dir() / actor.LOCK).write_text(json.dumps(OTHER), encoding="utf-8")
        another["now"] = True
        await tick(app, pilot)
        assert not app.acts and not app.actor_timers and claudeupdate._elsewhere


async def test_a_watching_dashboard_never_writes_the_sessions_to_continue(fake_home, monkeypatch, another):
    """Only the dashboard that acts writes continue_at_reset: c, the trim in check_limits and
    save_armed leave it as that one saved it."""
    stub_acting(monkeypatch)
    moment = (datetime.now() + timedelta(hours=1)).replace(microsecond=0)
    kept = {"continue_at_reset": {"gone": moment.isoformat()}}  # no longer at a limit here
    config.write_json("config.json", kept)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        session = replace(app.sessions["one"], status=Status.LIMIT, resets=moment, exit=Exit.CLEAN)
        app.sessions["one"] = session
        monkeypatch.setattr("tabmancer.app.at_limit", lambda s: s.session_id == "one")
        assert "dashboard that acts" in app.act("continue_at_reset", "one")
        app.check_limits()
        app.armed["one"] = moment
        app.save_armed()
        await pilot.pause(0.1)
    assert config.read_json("config.json") == kept


async def test_a_failed_save_of_the_sessions_to_continue_is_said_once(fake_home, monkeypatch):
    stub_acting(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert app.acts
        path = config.config_dir() / "config.json"
        path.write_text('{"theme": ', encoding="utf-8")
        app.armed["one"] = datetime.now()
        for _ in range(3):  # continue_due saves again every 10 s
            app.save_armed()
        await pilot.pause(0.1)
        said = [n for n in app._notifications if "could not be saved" in str(n.message)]
        assert len(said) == 1 and path.read_text(encoding="utf-8") == '{"theme": '
        path.write_text("{}", encoding="utf-8")
        app.save_armed()
        assert not app.armed_unsaved and "one" in config.read_json("config.json")["continue_at_reset"]


@pytest.mark.parametrize("first", [True, False])
async def test_a_queued_handover_is_handed_over_once(fake_home, monkeypatch, first):
    handed = []
    monkeypatch.setattr(SessionPicker, "hand_over", lambda self, session, how: handed.append(session.session_id))
    if not first:
        assert actor.first_to("handover-one")  # the other window queued it too, and got there first
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        app.queued["one"] = "self"
        app.apply_live_updates({"one": replace(app.sessions["one"], live_state="idle")})
        await pilot.pause(0.1)
        assert "one" not in app.queued
    assert handed == (["one"] if first else [])
    assert first or any("another tabmancer window hands it over" in str(n.message) for n in app._notifications)


def test_two_real_dashboards_never_both_hold_it(tmp_path):
    """The seam itself: a second process sees this one's lock as held."""
    import subprocess
    import sys

    assert actor.claim()
    script = "from tabmancer import actor; print(actor.claim())"
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False,
                         env={**os.environ}).stdout.strip()
    assert out == "False"
    actor.release()


async def tick(app, pilot) -> None:
    app.tick_actor()
    await app.workers.wait_for_complete()
    await pilot.pause(0.1)


async def test_a_lock_it_cannot_read_for_a_moment_does_not_stop_it(fake_home, monkeypatch):
    """A lock file held open a moment (a virus scanner) reads as none: the dashboard that acts keeps
    acting through one such look, and steps back only after two in a row."""
    import threading

    stub_acting(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert app.acts
        threads = []
        monkeypatch.setattr(actor, "holder", lambda: threads.append(threading.current_thread()) or None)
        monkeypatch.setattr(actor, "claim", lambda: False)
        await tick(app, pilot)
        assert app.acts and app.actor_misses == 1
        await tick(app, pilot)
        assert not app.acts
        assert threading.main_thread() not in threads  # looked for off the screen's thread


async def test_a_monitor_with_no_dashboard_says_so_once(fake_home, monkeypatch):
    from tabmancer import teams

    teams.save({"shop": teams.new_team("shop", str(fake_home), "Ship", teams.parse_members("api: the API"))})
    monkeypatch.setattr(actor, "running", lambda record: False)
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        for _ in range(3):
            await tick(app, pilot)
        said = [n for n in app._notifications if "No tabmancer dashboard is open" in str(n.message)]
        assert len(said) == 1 and not app.acts
        assert not (config.config_dir() / actor.LOCK).exists()  # a monitor never takes the lock


async def test_a_queued_handover_that_failed_can_be_queued_again(fake_home, monkeypatch):
    from tabmancer import handover

    def refuse(session):
        raise handover.HandoverError("no answer")

    monkeypatch.setattr(handover, "write_itself", refuse)
    monkeypatch.setattr(handover, "close", lambda now, open_ones: None)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        app.queued["one"] = "self"
        app.apply_live_updates({"one": replace(app.sessions["one"], live_state="idle")})
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert any("was not handed over" in str(n.message) for n in app._notifications)
    assert actor.first_to("handover-one")  # given back: h again would hand it over


async def test_a_dashboard_that_cannot_name_itself_only_watches_and_says_why(fake_home, monkeypatch):
    monkeypatch.setattr(actor, "_mine", {"pid": 1, "started": "", "command": ""})
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.4)
        assert not app.acts
        assert any("could not read its own start time" in str(n.message) for n in app._notifications)
        await tick(app, pilot)
        assert not app.acts and not (config.config_dir() / actor.LOCK).exists()
