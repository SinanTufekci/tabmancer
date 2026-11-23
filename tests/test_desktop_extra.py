"""The desktop extra's edges, with or without Qt installed: only tabmancer.desktop imports Qt,
tabmancer runs as ever without it, and `tabmancer desktop` says how to add it."""

import ast
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


def test_the_package_and_its_cli_load_no_qt():
    code = ("import sys, tabmancer.cli, tabmancer.desktop, tabmancer.store; "
            "print(tabmancer.desktop.available() in (True, False), "
            "sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.split() == ["True", "[]"]


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


def test_desktop_names_the_monitor_until_there_is_more(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: True)
    with pytest.raises(SystemExit) as out:
        cli.main(["desktop"])
    assert out.value.code == 2
    assert "desktop --monitor" in capsys.readouterr().err


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


def test_the_extra_is_qt_essentials_alone():
    text = (SOURCE.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    extras = text[text.index("[project.optional-dependencies]"):]
    extras = extras[:extras.index("\n[", 1)]
    assert 'desktop = ["PySide6-Essentials>=6.8"]' in extras
    head = text[:text.index("[project.optional-dependencies]")]
    assert "PySide6" not in head.split("dependencies = [", 1)[1].split("]", 1)[0]  # never a core dependency
