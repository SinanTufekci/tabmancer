"""Desktop notifications: a session that wants you back is told outside the dashboard too,
unless you are looking at the dashboard."""

from __future__ import annotations

import subprocess

from tabmancer import app as app_module
from tabmancer import cli, sessions, toast
from tabmancer.app import SessionPicker
from tabmancer.config import read_json, write_json
from tabmancer.toast import show as real_show  # taken before conftest stubs it out


async def wait_for(pilot, condition, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


async def test_a_session_that_stops_working_is_told_on_the_desktop(fake_home, monkeypatch):
    state = {"value": [{"sessionId": "one", "kind": "interactive", "status": "busy"},
                       {"sessionId": "two", "kind": "interactive", "status": "busy"}]}
    monkeypatch.setattr(sessions, "_agents_json", lambda: state["value"])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)
    shown = []
    monkeypatch.setattr(toast, "show", lambda title, body, dashboard: shown.append((title, body, dashboard)))
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.sessions.get("one") and app.sessions["one"].live_state == "busy")
        app.app_focus = False  # the terminal says you are elsewhere
        state["value"] = [{"sessionId": "one", "kind": "interactive", "status": "idle"},
                          {"sessionId": "two", "kind": "interactive", "status": "busy"}]
        await wait_for(pilot, lambda: shown)
        assert shown[0] == ("demo: waiting for you", "First task", "tabmancer")
        state["value"] = [{"sessionId": "one", "kind": "interactive", "status": "busy"},
                          {"sessionId": "two", "kind": "interactive", "status": "busy"}]
        await wait_for(pilot, lambda: app.sessions["one"].live_state == "busy")
        state["value"] = [{"sessionId": "one", "kind": "interactive", "status": "idle"},
                          {"sessionId": "two", "kind": "interactive", "status": "blocked"}]
        await wait_for(pilot, lambda: len(shown) == 2)  # two at once: one notification
        assert shown[1][0] == "2 sessions want you back" and "needs you" in shown[1][1]
        write_json("config.json", {"desktop_notifications": False})  # tabmancer notifications off
        app.tell_desktop([app.sessions["one"]])
        await pilot.pause(0.2)
        assert len(shown) == 2


async def test_nothing_is_shown_while_you_look_at_the_dashboard_off_windows(fake_home, monkeypatch):
    shown = []
    monkeypatch.setattr(toast, "show", lambda title, body, dashboard: shown.append(title))
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.sessions)
        app.app_focus = True
        app.tell_desktop([app.sessions["one"]])
        await pilot.pause(0.2)
        assert not shown
        app.app_focus = False
        app.tell_desktop([app.sessions["one"]])
        await wait_for(pilot, lambda: shown)


def test_windows_shows_it_unless_the_dashboard_tab_is_in_front(monkeypatch):
    runs = []

    class Result:
        returncode = 4

    def run(argv, **kw):
        runs.append((argv, kw["env"]))
        return Result()

    monkeypatch.setattr(toast.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", run)
    assert real_show("web-app: waiting for you", 'Fix the "login" <loop> & more', "tabmancer") == toast.LOOKING
    argv, env = runs[0]
    assert argv[0] == "powershell.exe"  # Windows PowerShell: pwsh cannot load the WinRT toast types
    assert env["CCR_TOAST_BODY"] == 'Fix the "login" <loop> & more'  # as is: no quoting, no escaping
    assert env["CCR_DASHBOARD"] == "tabmancer"
    Result.returncode = 0
    assert real_show("t", "b", "tabmancer") == toast.SHOWN
    Result.returncode = 1
    assert real_show("t", "b", "tabmancer") == toast.FAILED


def test_notifications_can_be_turned_off(fake_home, capsys):
    assert toast.enabled()
    cli.main(["notifications", "off"])
    assert read_json("config.json")["desktop_notifications"] is False and not toast.enabled()
    assert "off" in capsys.readouterr().out
    cli.main(["notifications", "on"])
    assert toast.enabled()
