import sys

import pytest

from tabmancer import cli, shortcuts


@pytest.fixture
def tools(monkeypatch):
    found = {"tabmancer": "C:/bin/tabmancer.exe", "wt": "C:/bin/wt.exe"}
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: found.get(name))
    return found


def test_picker_argv_wraps_in_windows_terminal(tools):
    argv = shortcuts.picker_argv(["open", "PCB work"], title="PCB work (Claude)")
    assert argv == ["C:/bin/wt.exe", "-w", "new", "new-tab", "--title", "PCB work (Claude)",
                    "C:/bin/tabmancer.exe", "open", "PCB work"]


def test_shortcuts_call_windows_terminal_by_its_alias_not_its_versioned_folder(tools, tmp_path, monkeypatch):
    # Inside Windows Terminal, PATH finds wt.exe in its package folder first. That
    # folder is named after the version, so a shortcut to it breaks on the next update.
    tools["wt"] = r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_1.24.12741.0_x64__8wekyb3d8bbwe\wt.exe"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert shortcuts.picker_argv([])[0] == tools["wt"]  # no alias to fall back on: keep what PATH found
    alias = tmp_path / "Microsoft" / "WindowsApps" / "wt.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    assert shortcuts.picker_argv([])[0] == str(alias)


def test_picker_argv_without_terminal_or_exe(tools):
    del tools["wt"], tools["tabmancer"]
    argv = shortcuts.picker_argv([])
    assert argv[1:] == ["-m", "tabmancer"]


def test_login_does_nothing_without_sessions_left_open(tools, monkeypatch):
    launched = []
    monkeypatch.setattr(cli, "find_sessions", lambda **kw: [])
    monkeypatch.setattr(cli, "recent_shutdowns", list)
    monkeypatch.setattr(cli, "open_at_shutdown", lambda sessions, shutdowns: set())
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: launched.append(a))
    cli.startup_main()
    assert launched == []


def test_login_opens_the_picker_when_sessions_were_left_open(tools, monkeypatch):
    launched = []
    monkeypatch.setattr(cli, "find_sessions", lambda **kw: [])
    monkeypatch.setattr(cli, "recent_shutdowns", list)
    monkeypatch.setattr(cli, "open_at_shutdown", lambda sessions, shutdowns: {"abc"})
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **k: launched.append(argv))
    cli.startup_main()
    (argv,) = launched
    assert argv[0] == "C:/bin/wt.exe" and argv[-1] == "C:/bin/tabmancer.exe"


def test_login_argv_prefers_the_windowless_launcher(monkeypatch):
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: "C:/bin/tabmancer-startup.exe"
                        if name == "tabmancer-startup" else None)
    assert shortcuts.login_argv() == ["C:/bin/tabmancer-startup.exe"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_startup_on_status_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TABMANCER_SHORTCUT_DIR", str(tmp_path))
    monkeypatch.setattr(shortcuts, "login_argv", lambda: [sys.executable, "-m", "tabmancer", "startup-run"])
    cli.manage_startup("on")
    assert (tmp_path / f"{shortcuts.STARTUP_NAME}.lnk").exists()
    cli.manage_startup("status")
    assert "On" in capsys.readouterr().out.splitlines()[-1]
    cli.manage_startup("off")
    assert not (tmp_path / f"{shortcuts.STARTUP_NAME}.lnk").exists()


def test_continue_prompt_command(capsys, monkeypatch):
    monkeypatch.delenv("TABMANCER_CONTINUE_PROMPT", raising=False)
    cli.main(["continue-prompt", "devam et"])
    assert capsys.readouterr().out.strip() == "Auto-continue sends: devam et"
    cli.main(["continue-prompt", "--reset"])
    assert "Continue where you left off" in capsys.readouterr().out


def test_picker_runs_inside_powershell_7_when_installed(tools):
    tools["pwsh"] = "C:/bin/pwsh.exe"
    argv = shortcuts.picker_argv(["open", "PCB work"], title="PCB work (Claude)")
    assert argv[6:9] == ["C:/bin/pwsh.exe", "-NoLogo", "-Command"]
    assert argv[9] == "& C:/bin/tabmancer.exe open 'PCB work'"
