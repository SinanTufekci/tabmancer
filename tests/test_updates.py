"""A newer tabmancer: PyPI asked once a day at most, the command that upgrades it the way it was
installed, said in the dashboard once, and never asked for when turned off, run from the source,
in the demo or in tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from tabmancer import cli, doctor, updates
from tabmancer.app import SessionPicker
from tabmancer.widgets import TopBar


def test_only_a_higher_release_is_newer():
    assert updates.newer("0.2.0", "0.1.0") and updates.newer("0.10.0", "0.9.1") and updates.newer("1.0", "0.9.9")
    assert not updates.newer("0.1.0", "0.1.0") and not updates.newer("0.0.9", "0.1.0")
    assert not updates.newer("0.2.0rc1", "0.1.0")  # a pre-release is never offered


def test_pypi_is_asked_once_a_day_and_a_failure_is_not_remembered():
    asked = []

    def ask(answer):
        def asking():
            asked.append(answer)
            return answer
        return asking

    morning = datetime(2026, 11, 2, 9, 0)
    assert updates.latest(morning, ask(None)) is None  # offline: nothing known yet
    assert updates.latest(morning + timedelta(minutes=5), ask("0.2.0")) == "0.2.0"  # asked again
    assert updates.latest(morning + timedelta(hours=20), ask("0.3.0")) == "0.2.0"  # remembered for a day
    assert len(asked) == 2
    assert updates.latest(morning + timedelta(days=1, minutes=6), ask(None)) == "0.2.0"  # offline: the last answer
    assert updates.latest(morning + timedelta(days=1, minutes=7), ask("0.3.0")) == "0.3.0"


def test_the_upgrade_command_follows_how_it_was_installed(monkeypatch):
    class Installed:
        def __init__(self, direct):
            self.direct = direct

        def read_text(self, name):
            return self.direct

    monkeypatch.setattr(updates.metadata, "distribution", lambda name: Installed(None))
    for prefix, command in (("C:\\Users\\a\\AppData\\Roaming\\uv\\tools\\tabmancer", "uv tool upgrade tabmancer"),
                            ("/home/a/.local/share/uv/tools/tabmancer", "uv tool upgrade tabmancer"),
                            ("/home/a/.local/share/pipx/venvs/tabmancer", "pipx upgrade tabmancer"),
                            ("C:\\Users\\a\\AppData\\Local\\uv\\cache\\archive-v0\\x1", "uvx tabmancer@latest"),
                            ("/home/a/venv", "pip install --upgrade tabmancer")):
        monkeypatch.setattr(updates.sys, "prefix", prefix)
        assert updates.upgrade_command() == command, prefix
    editable = json.dumps({"url": "file:///C:/src/tabmancer", "dir_info": {"editable": True}})
    monkeypatch.setattr(updates.metadata, "distribution", lambda name: Installed(editable))
    assert updates.upgrade_command() is None  # its source: git pull, not a note


def test_there_is_news_only_when_on_installed_and_newer(monkeypatch):
    monkeypatch.setattr(updates, "upgrade_command", lambda: "uv tool upgrade tabmancer")
    assert updates.news(ask=lambda: "9.0.0") == ("9.0.0", "uv tool upgrade tabmancer")
    updates.set_enabled(False)
    assert updates.news(ask=lambda: "9.0.0") is None and not updates.enabled()
    updates.set_enabled(True)
    monkeypatch.setenv("TABMANCER_UPDATE_CHECK", "0")
    assert updates.news(ask=lambda: "9.0.0") is None
    monkeypatch.delenv("TABMANCER_UPDATE_CHECK")
    monkeypatch.setattr(updates, "upgrade_command", lambda: None)
    assert updates.news(ask=lambda: "9.0.0") is None  # run from its source


def test_the_updates_command_and_doctor_say_where_it_stands(monkeypatch, capsys):
    monkeypatch.setattr(updates, "upgrade_command", lambda: "pipx upgrade tabmancer")
    monkeypatch.setattr(updates, "ask_pypi", lambda timeout=5: "9.0.0")
    cli.manage_updates(None)
    assert "9.0.0 is out. To upgrade: pipx upgrade tabmancer" in capsys.readouterr().out
    out = f"tabmancer {updates.__version__}; 9.0.0 is out. To upgrade: pipx upgrade tabmancer"
    assert doctor.update_check() == ("warn", out)
    cli.manage_updates("off")
    assert "The update check is off" in capsys.readouterr().out and not updates.enabled()
    assert "the update check is off" in doctor.update_check()[1]


async def test_the_dashboard_tells_of_a_newer_tabmancer_once(fake_home, monkeypatch):
    calls = []

    def news():
        calls.append(1)
        return "9.0.0", "uv tool upgrade tabmancer"

    monkeypatch.setattr(updates, "news", news)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if app.told_update:
                break
        brand = app.query_one(TopBar).query_one("#brand").render()
        assert "↑ 9.0.0" in str(brand)
        assert any("9.0.0 is out" in n.message and "uv tool upgrade tabmancer" in n.message
                   for n in app._notifications)
        app.check_updates()
        await pilot.pause(0.3)
        assert len(calls) == 2 and sum("9.0.0 is out" in n.message for n in app._notifications) == 1  # told once
