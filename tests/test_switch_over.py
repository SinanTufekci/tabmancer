"""The window is the dashboard: bare `tabmancer`, --demo, live and team watch open it with Textual installed
too (this suite's Python has it), the terminal dashboard is `tabmancer --terminal` while it is kept, and the
desktop shortcut and start at login run the window with no console behind it. Qt, the window and every
launch are stood in for: no window, tab, shortcut or registry entry is made."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tabmancer import cli, desktop, shortcuts


@pytest.fixture
def window(monkeypatch) -> list[tuple]:
    """Qt there, and the window stood in for: what desktop_window was asked to open."""
    opened: list[tuple] = []
    monkeypatch.setattr(desktop, "available", lambda: True)
    monkeypatch.setattr(cli, "desktop_window", lambda monitor, name, demo=False, days=14, include_all=False,
                        tabs_window="", view="": opened.append((monitor, name, demo, days, include_all, tabs_window,
                                                                view)) or 0)
    return opened


@pytest.fixture
def no_terminal_dashboard(monkeypatch):
    """The terminal dashboard must not start: its picker and its demo fail the test if they do."""
    from tabmancer import app, demo

    class Picker:
        def __init__(self, *args, **kwargs):
            pytest.fail("the terminal dashboard started")

    monkeypatch.setattr(app, "SessionPicker", Picker)
    monkeypatch.setattr(demo, "run", lambda days=14: pytest.fail("the terminal demo started"))


def exits(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as out:
        cli.main(argv)
    return out.value.code


@pytest.mark.parametrize(("argv", "asked"), [
    ([], (False, "", False, 14, False, "", "")),
    (["--days", "3", "--all"], (False, "", False, 3.0, True, "", "")),
    (["--tabs-window", "mine"], (False, "", False, 14, False, "mine", "")),
    (["--demo"], (False, "", True, 14, False, "", "")),
    (["live"], (False, "", False, 14, False, "", "live")),
    (["live", "--days", "2"], (False, "", False, 2.0, False, "", "live")),
    (["team", "watch", "shop"], (True, "shop", False, 14, False, "", "")),
    (["team", "watch"], (True, "", False, 14, False, "", "")),
])
def test_with_textual_too_the_dashboard_is_the_window(window, no_terminal_dashboard, capsys, argv, asked):
    assert cli.has_textual()  # this suite's Python has the terminal dashboard: it is passed over
    assert exits(argv) == 0 and window == [asked]
    err = capsys.readouterr().err
    if argv[:1] in (["live"], ["team"]):
        assert len(err.strip().splitlines()) == 1 and "--terminal" in err  # one line: what it is now
    else:
        assert err == ""


def test_terminal_is_the_terminal_dashboard(monkeypatch, window):
    from tabmancer import app, demo, sixel

    made = []

    class Picker:
        def __init__(self, days, include_all=False, live=False, tabs_window=""):
            made.append((days, include_all, live, tabs_window))

        def run(self):
            return None

    monkeypatch.setattr(app, "SessionPicker", Picker)
    monkeypatch.setattr(sixel, "probe", lambda: None)
    monkeypatch.setattr(demo, "run", lambda days=14: made.append(("demo", days)))
    watched = []
    monkeypatch.setattr(cli, "team_watch", lambda name, days, tabs_window="": watched.append((name, tabs_window)) or 0)
    cli.main(["--terminal"])
    cli.main(["--terminal", "--days", "3", "--all", "--tabs-window", "mine"])
    cli.main(["--terminal", "live"])
    cli.main(["--terminal", "--demo", "--days", "2"])
    assert exits(["--terminal", "--tabs-window", "mine", "team", "watch", "shop"]) == 0
    assert made == [(14, False, False, ""), (3.0, True, False, "mine"), (14, False, True, ""), ("demo", 2.0)]
    assert watched == [("shop", "mine")] and window == []  # the window never asked for


def test_terminal_goes_with_the_dashboard_alone(window, capsys):
    for argv in (["--terminal", "list"], ["--terminal", "team", "who", "shop"], ["--terminal", "desktop"]):
        assert exits(argv) == 2
        assert "--terminal is the terminal dashboard" in capsys.readouterr().err
    assert window == []


def test_live_opens_the_window_on_its_live_view(monkeypatch):
    """desktop_window hands the view to the window's run; the dashboard with none is run without it, so the
    window's run before it takes a view still opens."""
    from tabmancer import launch

    monkeypatch.setattr(desktop, "available", lambda: True)
    ran = []
    module = types.ModuleType("tabmancer.desktop.main")
    module.run = lambda demo=False, days=14, include_all=False, **view: ran.append((demo, view, launch._gui)) or 0
    monkeypatch.setitem(sys.modules, "tabmancer.desktop.main", module)
    assert exits(["live"]) == 0 and exits([]) == 0 and exits(["--demo"]) == 0
    assert ran == [(False, {"view": "live"}, True), (False, {}, True), (True, {}, True)]


def test_without_qt_live_says_how_to_get_it_and_offers_the_terminal_one(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: False)
    assert exits(["live"]) == 1  # it could not do what it was asked
    out = capsys.readouterr()
    assert cli.CORE_INSTALL in out.out and "tabmancer --terminal" in out.out  # Textual is here
    assert exits([]) == 0 and "doctor" in capsys.readouterr().out  # bare: the install line and the commands


def test_desktop_without_qt_names_the_terminal_ones_with_their_flag(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "available", lambda: False)
    assert exits(["desktop"]) == 1
    assert "tabmancer --terminal team watch [team]" in capsys.readouterr().err
    monkeypatch.setattr(cli, "has_textual", lambda: False)
    assert exits(["desktop"]) == 1
    assert "--terminal" not in capsys.readouterr().err  # nothing to offer


# --- start at login and the desktop shortcut ----------------------------------------------------------------


@pytest.fixture
def left_open(monkeypatch) -> list:
    """Sessions were open at the last shutdown; what startup_main starts as a process."""
    launched: list = []
    monkeypatch.setattr(cli, "find_sessions", lambda **kw: [])
    monkeypatch.setattr(cli, "recent_shutdowns", list)
    monkeypatch.setattr(cli, "open_at_shutdown", lambda sessions, shutdowns: {"abc"})
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **kw: launched.append(argv))
    return launched


def test_at_login_the_window_runs_in_the_windowless_launcher(window, left_open):
    cli.startup_main()
    assert window == [(False, "", False, 14, False, "", "")] and left_open == []  # in-process, no console


def test_at_login_with_neither_nothing_opens(monkeypatch, window, left_open):
    monkeypatch.setattr(desktop, "available", lambda: False)
    monkeypatch.setattr(cli, "has_textual", lambda: False)
    cli.startup_main()
    assert window == [] and left_open == []


def test_at_login_with_nothing_left_open_the_window_stays_shut(monkeypatch, window, left_open):
    monkeypatch.setattr(cli, "open_at_shutdown", lambda sessions, shutdowns: set())
    cli.startup_main()
    assert window == [] and left_open == []


@pytest.fixture
def written(monkeypatch, tmp_path) -> list:
    """Shortcuts as they would be written, none written: (name, argv)."""
    made: list = []
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("TABMANCER_SHORTCUT_DIR", str(tmp_path))
    monkeypatch.setattr(shortcuts, "write_shortcut",
                        lambda folder, name, argv, about: made.append((name, argv)) or folder / f"{name}.lnk")
    monkeypatch.setattr(shortcuts, "window_argv", lambda: ["C:/bin/tabmancer-desktop.exe"])
    monkeypatch.setattr(shortcuts, "picker_argv", lambda extra, title=shortcuts.PICKER_TITLE: ["wt", title, *extra])
    return made


def test_the_desktop_shortcut_opens_the_window(monkeypatch, written):
    monkeypatch.setattr(desktop, "available", lambda: True)
    cli.create_shortcut()
    assert written == [("tabmancer", ["C:/bin/tabmancer-desktop.exe"])]


def test_without_qt_the_shortcut_is_the_terminal_dashboard_while_it_is_kept(monkeypatch, written):
    monkeypatch.setattr(desktop, "available", lambda: False)
    cli.create_shortcut()
    assert written == [("tabmancer", ["wt", "tabmancer", "--terminal"])]
    monkeypatch.setattr(cli, "has_textual", lambda: False)
    with pytest.raises(SystemExit) as out:
        cli.create_shortcut()
    assert cli.CORE_INSTALL in str(out.value.code) and len(written) == 1


def test_a_workspaces_shortcut_opens_its_sessions_as_before(monkeypatch, written):
    monkeypatch.setattr(desktop, "available", lambda: True)
    monkeypatch.setattr(cli, "_get_workspace", lambda name: types.SimpleNamespace(name=name))
    cli.create_shortcut("pcb")
    assert written == [("pcb (Claude)", ["wt", "pcb (Claude)", "open", "pcb"])]


def test_window_argv_takes_the_launcher_then_pythonw(monkeypatch, tmp_path):
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: "C:/bin/tabmancer-desktop.exe"
                        if name == "tabmancer-desktop" else None)
    assert shortcuts.window_argv() == ["C:/bin/tabmancer-desktop.exe"]
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: None)
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    monkeypatch.setattr(shortcuts.sys, "executable", str(tmp_path / "python.exe"))
    assert shortcuts.window_argv() == [str(tmp_path / "python.exe"), "-m", "tabmancer", "desktop"]  # no pythonw
    (tmp_path / "pythonw.exe").write_text("")
    assert shortcuts.window_argv() == [str(tmp_path / "pythonw.exe"), "-m", "tabmancer", "desktop"]
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    assert shortcuts.window_argv()[0] == str(tmp_path / "python.exe")  # no console to hide


def test_the_terminal_dashboard_keeps_its_named_window(monkeypatch):
    monkeypatch.setattr(shortcuts, "_windows_terminal", lambda: "wt.exe")
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: {"tabmancer": "tabmancer.exe"}.get(name))
    assert shortcuts.picker_argv([shortcuts.TERMINAL]) == [
        "wt.exe", "-w", "tabmancer", "new-tab", "--title", "tabmancer", "tabmancer.exe", "--terminal",
        "--tabs-window", "tabmancer"]


def test_the_desktop_launcher_is_tabmancer_desktop(monkeypatch):
    given = []
    monkeypatch.setattr(cli, "main", given.append)
    monkeypatch.setattr(cli.sys, "argv", ["tabmancer-desktop.exe", "--days", "3"])
    cli.desktop_main()
    assert given == [["desktop", "--days", "3"]]


def test_the_launcher_is_a_gui_script():
    project = Path(cli.__file__).parents[2] / "pyproject.toml"
    if not project.is_file():
        pytest.skip("installed, not from its source")
    text = project.read_text(encoding="utf-8")
    gui = text[text.index("[project.gui-scripts]"):].split("\n[", 1)[0]  # that table alone (3.10: no tomllib)
    assert 'tabmancer-desktop = "tabmancer.cli:desktop_main"' in gui  # no console behind the window
