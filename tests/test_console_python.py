"""A window started windowless (the tabmancer-startup or tabmancer-desktop launcher, the monitor's window)
runs on pythonw.exe, which has no console. What it starts that needs one, or pipes of its own (a member's
tab, its MCP server, agy's plugin and hook, the sign-in tab's browser launcher, the typing helper), runs on
python.exe beside it (teambus.console_python). Nothing is started: the command lines are only read."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tabmancer import agyplugin, browsers, teambus, teams


@pytest.fixture
def windowless(monkeypatch, tmp_path) -> Path:
    """This process as the windowless launcher runs it: on Windows, sys.executable a pythonw.exe with a
    python.exe beside it, and no tabmancer on PATH. Gives the python.exe."""
    for name in ("python.exe", "pythonw.exe"):
        (tmp_path / name).write_text("")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setattr(sys, "_base_executable", str(tmp_path / "pythonw.exe"), raising=False)
    monkeypatch.setattr(teams.shutil, "which", lambda name: None)
    return tmp_path / "python.exe"


def test_pythonw_gives_the_python_beside_it(monkeypatch, tmp_path):
    quiet, loud = str(tmp_path / "pythonw.exe"), str(tmp_path / "python.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    assert teambus.console_python(quiet) == quiet  # none beside it: nothing better to give
    (tmp_path / "python.exe").write_text("")
    assert teambus.console_python(quiet) == loud
    assert teambus.console_python(str(tmp_path / "PYTHONW.EXE")) == loud  # as Windows names it, any case
    assert teambus.console_python(loud) == loud
    monkeypatch.setattr(sys, "executable", quiet)
    assert teambus.console_python() == loud  # sys.executable by default
    monkeypatch.setattr(sys, "platform", "linux")
    assert teambus.console_python(quiet) == quiet  # off Windows: as given


def test_a_members_tab_and_its_mcp_server_run_on_python(windowless, tmp_path):
    loud = str(windowless)
    assert teams._tabmancer_cmd() == (loud, "-m", "tabmancer")
    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x @codex\nrev: y @agy"))
    codex, agy = team.members[0], team.members[1]
    assert teams._agy_wrapper(team, agy)[0] == loud  # the agy member's tab: `tabmancer team run`
    flags = teams.codex_options(team, codex)
    command = next(flags[i + 1] for i, flag in enumerate(flags) if flag == "-c" and ".command=" in flags[i + 1])
    assert "pythonw" not in command.lower() and "python.exe" in command  # its MCP server
    assert loud in teambus.command("shop") and "pythonw" not in teambus.command("shop").lower()  # the bus CLI


def test_a_claude_members_mcp_config_runs_on_python(windowless, tmp_path):
    team = teams.new_team("mix", str(tmp_path), "Goal", teams.parse_members("api: x\nrev: y @agy"))
    path = teams.mcp_config(team, next(m for m in team.members if m.cli == "claude"))
    assert path is not None
    (server,) = json.loads(path.read_text(encoding="utf-8"))["mcpServers"].values()
    assert server["command"] == str(windowless)


def test_agys_plugin_runs_on_python(windowless):
    made = {name: json.loads(text) for name, text in agyplugin.files().items()}
    (server,) = made["mcp_config.json"]["mcpServers"].values()
    assert server["command"] == str(windowless)
    hook = made["hooks.json"]["tabmancer-stop"]["Stop"][0]["command"]
    assert "pythonw" not in hook.lower() and "python.exe" in hook


def test_the_sign_in_tabs_browser_launcher_runs_on_python(windowless):
    env = dict(browsers.login_environment("chrome:Default"))
    assert env[browsers.PYTHON] == str(windowless)


def test_the_typing_helper_runs_on_python(windowless, monkeypatch):
    """conftest stands in for typein.write_keys in every test: a copy of the module of its own, whose
    subprocess.run only records what it was asked to run."""
    spec = importlib.util.find_spec("tabmancer.typein")
    typein = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(typein)
    ran = []

    def run(argv, **kwargs):
        ran.append(argv)
        raise OSError("not run in tests")

    monkeypatch.setattr(typein.subprocess, "run", run)
    assert typein.write_keys(123, "hi") is False
    assert ran == [[str(windowless), "-m", "tabmancer.typein", "123"]]
