"""The desktop extra's edges, with or without Qt installed: only tabmancer.desktop imports Qt,
tabmancer runs as ever without it, and `tabmancer desktop` says how to add it."""

import ast
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import tabmancer
from tabmancer import cli, desktop

SOURCE = Path(tabmancer.__file__).parent
QT = ("PySide6", "shiboken6", "PyQt5", "PyQt6", "PySide2")


def qt_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
        found += [name for name in names if name.split(".")[0] in QT]
    return found


def test_only_the_desktop_package_imports_qt():
    outside = {str(path.relative_to(SOURCE)): qt_imports(path) for path in SOURCE.rglob("*.py")
               if "desktop" not in path.relative_to(SOURCE).parts}
    assert len(outside) > 30  # the whole package was read
    assert {name: found for name, found in outside.items() if found} == {}
    assert qt_imports(SOURCE / "desktop" / "monitor.py")  # and the scan finds Qt where it is
    assert qt_imports(SOURCE / "desktop" / "main.py")


def test_the_package_and_its_cli_load_no_qt():
    code = ("import sys, tabmancer.cli, tabmancer.desktop, tabmancer.store; "
            "print(tabmancer.desktop.available() in (True, False), "
            "sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.split() == ["True", "[]"]


BLOCKED = ("textual", "PySide6", "shiboken6")
# The core works headless, with neither Textual nor Qt importable: what the MCP server, the bus, the
# agy plugin, teams and the CLI's twins stand on, and what the desktop window reads. Each is used a
# little on made-up data, in folders of the test's own.
HEADLESS = """
import importlib, json, sys

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + name)

sys.meta_path.insert(0, Blocked())
for name in MODULES:
    importlib.import_module("tabmancer." + name)
from tabmancer import agyplugin, handover, keys, mcpserver, router, store, teams

assert keys.KEYS and keys.MONITOR_KEYS
assert all(len(row) == 3 for _, rows in keys.KEYS + keys.MONITOR_KEYS for row in rows)
assert {tool["name"] for tool in mcpserver.TOOLS}
shop = teams.new_team("shop", FOLDER, "Add a checkout", teams.parse_members("api: endpoints"))
teams.save({"shop": shop})
assert teams.load()["shop"].goal == "Add a checkout"
assert "api" in handover.team_request(teams.load()["shop"])
assert router.guess("rename the design doc").pick.model == "haiku"
assert json.loads(agyplugin.files()["plugin.json"])["name"]
assert store.TeamStore("shop").name == "shop"
from tabmancer import sessions, usage
sessions._agents_json = lambda include_finished=False: []  # never the real `claude agents`
usage.read_all = list  # nor `claude -p /usage`
snap = store.DashboardStore().poll()
assert snap.view.sessions == [] and snap.view.teams == [] and snap.acting is None
print(sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED))
"""
CORE = ("keys", "store", "viewmodel", "actions", "actor", "marks", "search", "teams", "teambus", "codexbus",
        "mcpserver", "agyplugin", "summarize", "router", "handover", "teamwatch", "launch", "picks", "agy")


def test_the_core_runs_without_textual_or_qt(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(SOURCE.parent)}
    for name in ("TABMANCER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        env[name] = str(tmp_path / name.lower())
    given = {"BLOCKED": BLOCKED, "MODULES": CORE, "FOLDER": str(tmp_path / "shop")}
    code = "".join(f"{name} = {value!r}\n" for name, value in given.items()) + HEADLESS
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env,
                         check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == ["[]"]


def test_the_key_lists_import_nothing():
    tree = ast.parse((SOURCE / "keys.py").read_text(encoding="utf-8"))
    imported = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [getattr(node, "module", None) for node in imported] == ["__future__"]


def test_available_asks_without_importing(monkeypatch):
    monkeypatch.setattr(desktop.importlib.util, "find_spec", lambda name: None)
    assert desktop.available() is False

    def broken(name):
        raise ValueError("PySide6.__spec__ is None")

    monkeypatch.setattr(desktop.importlib.util, "find_spec", broken)
    assert desktop.available() is False


def test_desktop_without_qt_says_how_to_add_it(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: False)
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop", "--monitor", "shop"])
    assert out.value.code == 1
    said = capsys.readouterr().err
    assert desktop.INSTALL in said
    assert "team watch" in said  # the terminal monitor is there either way


def test_desktop_opens_the_dashboards_window(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: True)
    opened = []
    window = types.ModuleType("tabmancer.desktop.main")
    window.run = lambda demo=False: opened.append(demo) or 0
    monkeypatch.setitem(sys.modules, "tabmancer.desktop.main", window)
    for argv in (["desktop"], ["desktop", "--demo"]):
        with pytest.raises(SystemExit) as out:
            cli.main(argv)
        assert out.value.code == 0
    assert opened == [False, True]
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop", "shop"])  # a team, without --monitor
    assert out.value.code == 2
    assert "desktop --monitor shop" in capsys.readouterr().err
    assert opened == [False, True]


def test_desktop_monitor_of_no_team_says_so_before_any_window(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: True)
    window = types.ModuleType("tabmancer.desktop.monitor")

    def run(team, demo=False):
        raise AssertionError("no window for a team that is not there")

    window.run = run
    monkeypatch.setitem(sys.modules, "tabmancer.desktop.monitor", window)
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop", "--monitor", "nope"])
    assert out.value.code == 1
    assert "There is no team yet" in capsys.readouterr().err


def test_desktop_monitor_hands_the_team_to_the_window(monkeypatch):
    from tabmancer import teams

    monkeypatch.setattr(desktop, "available", lambda: True)
    teams.save({"shop": teams.new_team("shop", "C:/work/shop", "Add a checkout", teams.parse_members("api: endpoints"))})
    opened = []
    window = types.ModuleType("tabmancer.desktop.monitor")
    window.run = lambda team, demo=False: opened.append((team, demo)) or 0
    monkeypatch.setitem(sys.modules, "tabmancer.desktop.monitor", window)
    for argv, team in ((["desktop", "--monitor", "SHOP"], "shop"), (["desktop", "--monitor", "--demo"], "")):
        with pytest.raises(SystemExit) as out:
            cli.main(argv)
        assert out.value.code == 0
        assert opened.pop() == (team, argv[-1] == "--demo")  # the demo's team is made up when it starts


def m_pressed(monkeypatch, choice, qt):
    """What M in the dashboard starts (teamwatch.open_monitor) with this choice saved: the
    processes it starts, the tabs it looks for, and what it says."""
    from tabmancer import config, teamwatch

    if choice is not None:
        config.merge_json("config.json", monitor=choice)
    monkeypatch.setattr(desktop, "available", lambda: qt)
    started, looked = [], []
    monkeypatch.setattr(teamwatch.subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    monkeypatch.setattr(teamwatch.focus, "focus_tab", lambda title: looked.append(title) or True)
    return teamwatch.open_monitor("shop", "C:/work/shop"), started, looked


def test_m_opens_the_terminal_monitor_unless_told_otherwise(monkeypatch):
    from tabmancer import teamwatch

    assert teamwatch.monitor_choice() == "terminal"
    for choice in (None, "terminal", "window", 3):
        said, started, looked = m_pressed(monkeypatch, choice, qt=True)
        assert said == "The monitor of shop is open already: brought to the front."
        assert started == [] and looked == [teamwatch.title("shop")]


def test_m_opens_the_desktop_window_when_chosen(monkeypatch):
    from tabmancer import config, teamwatch

    said, started, looked = m_pressed(monkeypatch, "desktop", qt=True)
    assert said == "The monitor of shop opens in a desktop window." and looked == []
    [(args, kw)] = started
    assert args[1:] == ["-m", "tabmancer", "desktop", "--monitor", "shop"]
    assert Path(args[0]).parent == Path(sys.executable).parent  # the Python Qt was found in
    assert kw["cwd"] == config.config_dir()  # never the team's folder: -m imports from where it starts
    assert kw["stdin"] == subprocess.DEVNULL
    assert (config.config_dir() / teamwatch.DESKTOP_LOG).exists()  # where a window that fails says why


def test_m_chosen_for_the_desktop_without_qt_opens_the_terminal_one(monkeypatch):
    from tabmancer import teamwatch

    said, started, looked = m_pressed(monkeypatch, "desktop", qt=False)
    assert said.startswith("The monitor of shop is open already") and desktop.INSTALL in said
    assert started == [] and looked == [teamwatch.title("shop")]


def test_the_desktop_window_runs_without_a_console(monkeypatch, tmp_path):
    from tabmancer import teamwatch

    for name in ("python.exe", "pythonw.exe"):
        (tmp_path / name).write_text("")
    monkeypatch.setattr(teamwatch.sys, "executable", str(tmp_path / "python.exe"))
    quiet = "pythonw.exe" if sys.platform == "win32" else "python.exe"
    assert teamwatch.desktop_command("shop")[0] == str(tmp_path / quiet)


def test_the_cli_says_what_m_opens(monkeypatch, capsys):
    from tabmancer import config, teamwatch

    monkeypatch.setattr(desktop, "available", lambda: False)
    for choice, words in (("desktop", "once Qt is installed"), ("terminal", "in a terminal window")):
        with pytest.raises(SystemExit) as out:
            cli.main(["desktop", "--m-opens", choice])
        assert out.value.code == 0 and words in capsys.readouterr().out
        assert config.read_json("config.json")["monitor"] == choice == teamwatch.monitor_choice()
    monkeypatch.setattr(desktop, "available", lambda: True)
    with pytest.raises(SystemExit):
        cli.main(["desktop", "shop"])  # a team, without --monitor: what M opens is said with it
    assert "M in the dashboard opens the terminal monitor" in capsys.readouterr().err


def test_the_extra_is_qt_essentials_alone():
    text = (SOURCE.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    extras = text[text.index("[project.optional-dependencies]"):]
    extras = extras[:extras.index("\n[", 1)]
    assert 'desktop = ["PySide6-Essentials>=6.8"]' in extras
    head = text[:text.index("[project.optional-dependencies]")]
    assert "PySide6" not in head.split("dependencies = [", 1)[1].split("]", 1)[0]  # never a core dependency
