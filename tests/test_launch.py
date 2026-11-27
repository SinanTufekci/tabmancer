from pathlib import Path

from tabmancer import launch as L
from tabmancer.launch import Launch, Mode, tmux_commands, windows_terminal_command


def launch(mode=Mode.RESUME, cwd=None, title="demo: fix bug"):
    return Launch(title=title, cwd=cwd or str(Path.home()), mode=mode, session_id="abc-123")


def test_modes_map_to_claude_flags():
    assert launch(Mode.RESUME).argv == ["claude", "--resume", "abc-123"]
    assert launch(Mode.FORK).argv == ["claude", "--resume", "abc-123", "--fork-session"]
    assert launch(Mode.NEW).argv == ["claude"]


def test_auto_continue_sends_the_prompt(monkeypatch):
    monkeypatch.delenv("TABMANCER_CONTINUE_PROMPT", raising=False)
    assert launch(Mode.AUTO).argv == ["claude", "--resume", "abc-123", L.DEFAULT_CONTINUE_PROMPT]
    monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", "kaldığın yerden devam et")
    assert launch(Mode.AUTO).argv[-1] == "kaldığın yerden devam et"


def test_default_mode_follows_how_the_session_ended():
    from tabmancer.sessions import Status

    assert L.default_mode(Status.CUT_OFF) is Mode.AUTO
    assert L.default_mode(Status.UNANSWERED) is Mode.AUTO
    assert L.default_mode(Status.ERROR) is Mode.AUTO
    assert L.default_mode(Status.STOPPED) is Mode.RESUME  # you stopped it on purpose
    assert L.default_mode(Status.DONE) is Mode.RESUME


def test_windows_terminal_quotes_the_prompt_for_powershell(monkeypatch):
    monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", "it's done; carry on")
    command = windows_terminal_command([launch(Mode.AUTO)], window="0")[-1]
    assert command == r"claude --resume abc-123 'it''s done\; carry on'"


def test_a_prompt_arrives_whole_quotes_and_percent_signs_too(monkeypatch):
    # Checked in a real tab, character for character: Windows Terminal wraps the command in
    # double quotes of its own and expands %NAME%; PowerShell ends '...' at curly quotes too.
    monkeypatch.setattr(L.shutil, "which", lambda name: "pwsh.exe" if name == "pwsh" else None)
    prompt = 'run "C:\\dir\\" and it’s 100% done'
    monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", prompt)
    command = windows_terminal_command([launch(Mode.AUTO)], window="0")[-1]
    assert command == r"""claude --resume abc-123 ('run \"C:\dir\\\" and it’’s 100'+[char]37+' done')"""


def test_windows_powershell_gets_single_quotes_for_double_ones(monkeypatch):
    # PowerShell 5.1 would pass the " on to claude unescaped, and the argument would fall apart.
    monkeypatch.setattr(L.shutil, "which", lambda name: None)
    monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", 'fix the "login" bug')
    args = windows_terminal_command([launch(Mode.AUTO)], window="0")
    assert "powershell.exe" in args and args[-1] == r"claude --resume abc-123 'fix the ''login'' bug'"


def test_tabs_do_not_inherit_claude_session_markers(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")  # a user setting: keep it
    env = L.clean_env()
    assert "CLAUDE_CODE_CHILD_SESSION" not in env and "CLAUDECODE" not in env
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"


def test_missing_folder_falls_back_to_home():
    assert launch(cwd="/no/such/folder").directory == str(Path.home())


def test_windows_terminal_one_tab_per_session():
    args = windows_terminal_command([launch(), launch(Mode.FORK)], window="0")
    assert args[:3] == ["wt.exe", "-w", "0"]
    assert args.count("new-tab") == 2
    assert args.count(";") == 1
    assert args[-1] == "claude --resume abc-123 --fork-session"
    assert "-NoExit" in args


def test_windows_terminal_escapes_semicolons():
    args = windows_terminal_command([launch(title="a; b")], window="new")
    assert args[args.index("--title") + 1] == r"a\; b"
    assert ";" not in args


def test_tmux_window_per_session():
    (command,) = tmux_commands([launch(Mode.FORK)])
    assert command[:2] == ["tmux", "new-window"]
    assert command[-1] == "claude --resume abc-123 --fork-session"


def test_posix_tabs_keep_a_shell_open_after_claude():
    for builder in (L.wezterm_commands, L.kitty_commands, L.gnome_terminal_commands, L.konsole_commands):
        (command,) = builder([launch()])
        assert command[-3:-1] == ["sh", "-c"]
        assert command[-1].startswith("claude --resume abc-123; exec ")


def test_applescript_quotes_paths_and_titles():
    tricky = launch(title='say "hi"')
    tricky.cwd = str(Path.home())
    script = L.iterm_script([tricky])
    assert 'write text "cd ' in script and "claude --resume abc-123" in script
    assert script.count("create tab") == 1
    assert L._applescript_string('a "b" \\c') == '"a \\"b\\" \\\\c"'
    assert L.apple_terminal_script([launch(), launch()]).count("do script") == 2


def test_terminal_detection(monkeypatch, real_launch):
    monkeypatch.setattr(L.sys, "platform", "linux")
    monkeypatch.setattr(L.shutil, "which", lambda name: f"/usr/bin/{name}")
    for var in ("TMUX", "WEZTERM_PANE", "KITTY_WINDOW_ID", "TERM_PROGRAM", "GNOME_TERMINAL_SCREEN",
                "GNOME_TERMINAL_SERVICE", "KONSOLE_VERSION"):
        monkeypatch.delenv(var, raising=False)
    assert L.terminal_kind() == "none"
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    assert L.terminal_kind() == "kitty"
    monkeypatch.setenv("TMUX", "/tmp/tmux")
    assert L.terminal_kind() == "tmux"  # inside tmux, tmux wins


def test_failed_launches_come_back_as_shell_lines(monkeypatch):
    monkeypatch.setattr(L, "terminal_kind", lambda: "kitty")

    class Result:
        returncode = 1

    monkeypatch.setattr(L.subprocess, "run", lambda *a, **k: Result())
    (line,) = L.open_tabs([launch()])
    assert line.endswith("&& claude --resume abc-123")


def test_many_long_tabs_go_in_several_command_lines_each_short_enough():
    import subprocess

    long = [Launch(title=f"t{i}", cwd=str(Path.home()), mode=Mode.NEW, session_id="", prompt="x" * 6000)
            for i in range(8)]
    commands = L.windows_terminal_commands(long, "new")
    assert len(commands) > 1 and all(len(subprocess.list2cmdline(c)) <= L.WT_MOST for c in commands)
    assert sum(c.count("new-tab") for c in commands) == 8  # every tab, once, in order
    window = commands[0][2]
    assert window.startswith("tabmancer-") and all(c[2] == window for c in commands)  # all in one window
    assert L.windows_terminal_commands([launch()], "new") == [windows_terminal_command([launch()], "new")]
    assert [c[2] for c in L.windows_terminal_commands(long, "0")] == ["0"] * len(commands)


# --- the desktop window's tabs: one named Windows Terminal window, a terminal found off Windows ---------------

def windows_terminal_started(monkeypatch) -> list[list[str]]:
    """Windows Terminal there, never started: each command it would run, kept here."""
    started: list[list[str]] = []

    class Started:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "terminal_kind", lambda: "windows-terminal")
    monkeypatch.setattr(L.claudeupdate, "before_opening", lambda: None)
    monkeypatch.setattr(L.subprocess, "Popen", lambda command, **kwargs: started.append(command) or Started())
    monkeypatch.delenv("WT_SESSION", raising=False)  # started from a shortcut, the tray or at login
    return started


def test_the_desktop_windows_tabs_go_into_its_named_window_not_a_new_one_each(monkeypatch):
    started = windows_terminal_started(monkeypatch)
    assert L.open_tabs([launch()]) == [] and started[-1][1:3] == ["-w", "new"]  # outside the window: as ever
    with L.gui_tabs("tabmancer"):
        L.open_tabs([launch()])
        L.open_tabs([launch(), launch(title="demo: two")])
        assert [command[1:3] for command in started[1:]] == [["-w", "tabmancer"]] * 2  # each Enter: the one window
        L.open_tabs([launch()], "0")  # a caller that says where: there
        assert started[-1][1:3] == ["-w", "0"]
    L.open_tabs([launch()])
    assert started[-1][1:3] == ["-w", "new"]  # put back once the window is gone


def test_off_windows_the_desktop_window_finds_a_terminal_where_a_shell_had_its_variables(monkeypatch, real_launch):
    """A GUI app has none of the terminals' variables: macOS's Terminal opens its tabs, on Linux GNOME Terminal
    with a display; else none (the commands given back). A terminal the window was started in still wins."""
    monkeypatch.setattr(L.shutil, "which", lambda name: f"/usr/bin/{name}")
    for var in ("TMUX", "WEZTERM_PANE", "KITTY_WINDOW_ID", "TERM_PROGRAM", "GNOME_TERMINAL_SCREEN",
                "GNOME_TERMINAL_SERVICE", "KONSOLE_VERSION", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(L.sys, "platform", "darwin")
    assert L.terminal_kind() == "none"  # a shell's command (tabmancer list…): as ever
    with L.gui_tabs("tabmancer"):
        assert L.terminal_kind() == "apple-terminal"
        monkeypatch.setattr(L.sys, "platform", "linux")
        assert L.terminal_kind() == "none"  # no display to show a terminal on
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert L.terminal_kind() == "gnome-terminal"
        monkeypatch.setattr(L.shutil, "which", lambda name: None if name == "gnome-terminal" else f"/usr/bin/{name}")
        assert L.terminal_kind() == "none"
        monkeypatch.setenv("TMUX", "/tmp/tmux")
        assert L.terminal_kind() == "tmux"


def test_tabmancer_desktop_runs_the_window_with_its_tabs_window(monkeypatch):
    """--tabs-window as the terminal dashboard takes it (the shortcut's), else the terminal dashboard's own window,
    for as long as the window runs: never after."""
    import sys
    import types

    import pytest

    from tabmancer import cli, desktop

    monkeypatch.setattr(desktop, "available", lambda: True)
    named = []
    window = types.ModuleType("tabmancer.desktop.main")
    window.run = lambda demo=False, days=14, include_all=False: named.append((L._gui, L._gui_window)) or 0
    monkeypatch.setitem(sys.modules, "tabmancer.desktop.main", window)
    for argv in (["desktop"], ["--tabs-window", "mine", "desktop"]):
        with pytest.raises(SystemExit) as out:
            cli.main(argv)
        assert out.value.code == 0
    assert named == [(True, "tabmancer"), (True, "mine")]
    assert (L._gui, L._gui_window) == (False, None)
    with pytest.raises(SystemExit) as out:  # bare tabmancer is the window, by the same path
        cli.main(["--tabs-window", "mine"])
    assert out.value.code == 0 and named[-1] == (True, "mine")
