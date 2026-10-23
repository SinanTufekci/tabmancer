"""The view model: the dashboard as plain data, the same the list draws."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta

import pytest
from test_team_tree import ready, shop

from tabmancer import app as app_module
from tabmancer import viewmodel as VM
from tabmancer.app import TEAM_ROW, SessionPicker
from tabmancer.sessions import Exit, Session, Status


def session(status=Status.DONE, exit=Exit.CLEAN, live="", resets=None, **more):
    return Session(session_id="s", path=None, cwd="C:/work/demo", status=status, exit=exit, live_state=live,
                   resets=resets, **more)


def test_every_state_has_an_id_words_and_a_tone():
    for status, (label, tone) in VM.STATUS.items():
        closed = session(status)
        assert VM.state_id(closed) == status.value and VM.label_of(closed) == (label, tone) and tone in VM.TONES
    assert VM.state_id(session(Status.RUNNING, Exit.RUNNING, "idle")) == "waiting"
    assert VM.state_id(session(Status.RUNNING, Exit.RUNNING, "blocked")) == "needs-you"
    assert VM.state_id(session(Status.RUNNING, Exit.RUNNING, "busy")) == "working"
    assert VM.state_id(session(Status.BACKGROUND, Exit.RUNNING, "shell")) == "shell"
    assert VM.state_id(session(Status.RUNNING, Exit.RUNNING, "something new")) == "open"
    assert VM.label_of(session(Status.RUNNING, Exit.RUNNING, "busy")) == ("working", "working")
    soon = datetime.now() + timedelta(hours=2)
    stopped = session(Status.RUNNING, Exit.RUNNING, "idle", resets=soon)  # open, idle at a limit
    assert VM.state_id(stopped) == "limit" and VM.label_of(stopped) == (f"◐ limit · {soon:%H:%M}", "to-continue")
    assert VM.label_of(session(Status.LIMIT, resets=datetime.now() - timedelta(minutes=5))) == \
        ("◐ limit lifted", "to-continue!")
    assert all(tone in VM.TONES for _, tone, _, _ in VM.LIVE.values())


def test_the_list_uses_the_view_models_words_and_colours():
    # The list's own tables are made from the view model's: one place says how a state reads.
    for status, (label, style) in app_module.STATUS_LABEL.items():
        assert (label, style) == (VM.STATUS[status][0], app_module.TONE[VM.STATUS[status][1]])
    assert app_module.LIVE_STATES["blocked"][:2] == ("needs you", app_module.TONE["your-turn!"])


def test_the_view_model_needs_no_textual():
    code = ("import sys, tabmancer.viewmodel; "
            "sys.exit(any(m == 'textual' or m.startswith('textual.') for m in sys.modules))")
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_a_project_hash_is_the_same_on_every_os_and_says_nothing_of_the_path():
    # The colony places galleries by it and shares may compare it: one folder, one id, wherever it
    # is computed (a pinned value, so a Linux run and a Windows run must agree).
    for written in ("C:/Users/someone/clients/secret", "C:\\Users\\Someone\\clients\\secret\\",
                    "c:/users/./someone/clients/secret"):
        assert VM.project_hash(written) == "d7658feb1d", written
    assert VM.project_hash("/home/someone/clients/secret") == "42839c0c06"
    assert VM.project_hash("C:/Users/someone/other") != "d7658feb1d"


async def test_the_whole_view_matches_the_list_and_goes_out_as_json(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        view = app.view()
        data = json.loads(json.dumps(VM.to_json(view)))  # all of it goes out as JSON
        by_id = {s["id"]: s for s in data["sessions"]}
        assert {"lead-1", "api-1", "ui-1", "one", "two"} <= by_id.keys()
        assert by_id["lead-1"]["team"] == "shop" and by_id["lead-1"]["lead"] is True
        assert by_id["api-1"]["member"] == "api" and by_id["api-1"]["state"] == "working"
        assert by_id["ui-1"]["open"] is False and by_id["ui-1"]["team"] == "shop"  # a closed member
        assert by_id["two"]["team"] is None and by_id["two"]["state"] == "done"
        opened = next(g for g in data["groups"] if g["name"] == "open")
        assert opened["teams"] == ["shop"] and opened["sessions"] == ["one"]  # in no team
        (team,) = data["teams"]
        assert [m["name"] for m in team["members"]] == ["lead", "api", "ui"] and team["open"] == 2
        assert {"from": "lead", "to": "api", "count": 1} in team["wires"]
        assert team["latest"][-1]["text"] == "endpoint done"
        assert data["cards"]["open"] == {"waiting": 1, "working": 2}
        # the list says the same as the view, row by row (open ones animate, so closed ones here)
        app.toggle_folded("shop")  # the closed member shows under its team's row
        for sid in ("ui-1", "two"):
            assert app.table.get_cell(sid, "status").plain == by_id[sid]["label"]


async def test_actions_by_id_go_the_way_of_the_keys(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    opened, focused, handed = [], [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: focused.append(title) or True)
    app = SessionPicker(days=14)
    monkeypatch.setattr(app, "hand_over", lambda session, how: handed.append((session.session_id, how)))
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        assert app.act("open", "two") == "opening" and [launch.session_id for launch in opened] == ["two"]
        assert app.act("open", "two") == "already opening"  # as Enter twice
        assert app.act("focus", "api-1") == "going to its tab"
        assert app.act("focus", "two") == "not open"
        await pilot.pause(0.3)
        assert focused == ["shop-api"]
        assert app.act("handover", "two", how="transcript") == "handing over"
        await pilot.pause(0.1)
        assert handed == [("two", "transcript")]
        assert app.act("continue_at_reset", "two") == "not at a limit"
        with pytest.raises(KeyError):
            app.act("explode", "two")
        with pytest.raises(KeyError):
            app.act("open", "nobody")
        assert f"{TEAM_ROW}shop" in app.table.rows  # nothing changed the list's shape
