"""claude update before sessions open (claudeupdate.py): once a day when it is on, the sessions the
dashboard is asked for meanwhile opened once it is done, and tabmancer new waiting for it."""

from __future__ import annotations

import subprocess
import threading
from datetime import date

from tabmancer import app as app_module
from tabmancer import claudeupdate, launch
from tabmancer.app import SessionPicker


def fake_claude(monkeypatch, versions=("2.1.322", "2.1.323"), update_code=0, said=""):
    """claude --version answers the first version until claude update has run, then the second."""
    calls = []

    def quiet(claude, *args, timeout):
        calls.append(args)
        if args == ("--version",):
            updated = ("update",) in calls
            return subprocess.CompletedProcess(args, 0, f"{versions[1] if updated else versions[0]} (Claude Code)\n", "")
        return subprocess.CompletedProcess(args, update_code, said, "")

    monkeypatch.setattr(claudeupdate, "_quiet", quiet)
    return calls


def test_it_is_off_until_turned_on_and_then_runs_once_a_day(monkeypatch):
    calls = fake_claude(monkeypatch)
    assert not claudeupdate.due()
    claudeupdate.set_enabled(True)
    assert claudeupdate.due()
    outcome = claudeupdate.run("claude")
    assert outcome.ok and outcome.changed and outcome.words() == "Claude Code updated: 2.1.322 → 2.1.323."
    assert calls == [("--version",), ("update",), ("--version",)]
    assert claudeupdate.last()["day"] == date.today().isoformat() and claudeupdate.last()["after"] == "2.1.323"
    monkeypatch.setattr(claudeupdate, "_tried", False)  # another process, the same day
    assert not claudeupdate.due()
    assert claudeupdate.due(date(2099, 1, 1))  # and the next day again
    monkeypatch.setenv("TABMANCER_CLAUDE_UPDATE", "0")
    assert not claudeupdate.due(date(2099, 1, 1))


def test_a_failed_update_is_said_and_not_kept_as_done(monkeypatch):
    fake_claude(monkeypatch, versions=("2.1.322", "2.1.322"), update_code=1, said="Network error\n")
    claudeupdate.set_enabled(True)
    outcome = claudeupdate.run("claude")
    assert not outcome.ok and outcome.words() == ("Claude Code could not update: Network error. "
                                                  "Sessions open on 2.1.322.")
    assert claudeupdate.last() == {} and not claudeupdate.due()  # not again in this process
    monkeypatch.setattr(claudeupdate, "_tried", False)
    assert claudeupdate.due()  # the next time the dashboard opens


def test_up_to_date_says_so():
    assert claudeupdate.Outcome(True, "2.1.322", "2.1.322").words() == "Claude Code is up to date (2.1.322)."


def test_a_tab_opens_only_after_the_update(monkeypatch):
    order = []
    claudeupdate.set_enabled(True)
    monkeypatch.setattr(claudeupdate, "run", lambda claude=None, timeout=0: order.append("update") or
                        claudeupdate.Outcome(True, "1.0.0", "1.0.1"))
    monkeypatch.setattr(launch, "terminal_kind", lambda: "none")
    monkeypatch.setattr(launch, "_note", lambda launches: order.append("open"))
    session = launch.Launch("one", ".", launch.Mode.RESUME, "one")
    launch.open_tabs([session])
    assert order == ["update", "open"]
    monkeypatch.setattr(claudeupdate, "_tried", True)
    launch.open_tabs([session])
    assert order == ["update", "open", "open"]  # once


def test_tabmancer_new_updates_first(monkeypatch, capsys, tmp_path):
    from tabmancer import cli

    claudeupdate.set_enabled(True)
    monkeypatch.setattr(claudeupdate, "run", lambda claude=None, timeout=0: claudeupdate.Outcome(True, "1.0.0", "1.0.1"))
    opened = []
    monkeypatch.setattr(cli, "start_fresh", lambda *args: opened.append(capsys.readouterr().out))
    cli.main(["new", str(tmp_path)])
    assert opened and "Updating Claude Code first" in opened[0] and "1.0.0 → 1.0.1" in opened[0]


async def test_the_dashboard_opens_sessions_once_claude_is_updated(fake_home, monkeypatch):
    claudeupdate.set_enabled(True)
    release = threading.Event()

    def slow_update(claude=None, timeout=0):
        release.wait(10)
        return claudeupdate.Outcome(True, "2.1.322", "2.1.323")

    monkeypatch.setattr(claudeupdate, "run", slow_update)
    monkeypatch.setattr("tabmancer.doctor.missing_from_cli", lambda claude: [])
    tabs = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: tabs.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        for _ in range(50):
            await pilot.pause(0.05)
            if app.sessions:
                break
        assert app.updating_claude
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert not tabs and len(app.after_claude_update) == 1
        assert any("Claude Code is updating: the session opens" in n.message for n in app._notifications)
        release.set()
        for _ in range(50):
            await pilot.pause(0.05)
            if tabs:
                break
        assert len(tabs) == 1 and not app.updating_claude
        assert any("2.1.322 → 2.1.323" in n.message for n in app._notifications)
