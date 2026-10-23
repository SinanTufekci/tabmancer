"""Shortcuts: the desktop picker, one per workspace (Windows), and start at login: a Startup
folder shortcut on Windows, a LaunchAgent on macOS, an XDG autostart entry on Linux."""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

PROG = "tabmancer"
PICKER_TITLE = "tabmancer"
STARTUP_NAME = "tabmancer at login"
LAUNCH_AGENT = "com.tabmancer.startup"  # macOS: ~/Library/LaunchAgents/<this>.plist
# Linux terminals that open a window running a command, the first found wins, and how each is told
# what to run.
LINUX_TERMINALS = [("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                   ("xfce4-terminal", ["-x"]), ("kitty", []), ("wezterm", ["start", "--"]),
                   ("alacritty", ["-e"]), ("xterm", ["-e"])]


def _windows_terminal() -> str | None:
    """wt.exe as a shortcut should call it: by its app alias, which outlives updates.

    Inside Windows Terminal, PATH also lists the app's own package folder, whose name
    carries the version (Microsoft.WindowsTerminal_1.24...): a shortcut to that breaks
    with the next update.
    """
    wt = shutil.which("wt")
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    alias = local / "Microsoft" / "WindowsApps" / "wt.exe"
    packaged = wt and "/windowsapps/microsoft.windowsterminal" in wt.lower().replace("\\", "/")
    if packaged and os.path.lexists(alias):
        return str(alias)
    return wt


def picker_argv(extra: list[str], title: str = PICKER_TITLE) -> list[str]:
    """Command that opens tabmancer (with extra arguments) in a new terminal window.

    In Windows Terminal it runs inside PowerShell 7 when that is installed, so the
    tab is a PowerShell tab with your profile loaded rather than a bare console.
    """
    exe = shutil.which(PROG)
    # Under the windowless login launcher sys.executable is pythonw; the picker needs a console.
    python = str(Path(sys.executable).with_name("python.exe")) if sys.platform == "win32" else sys.executable
    command = [exe, *extra] if exe else [python, "-m", PROG, *extra]
    wt = _windows_terminal()
    if not wt:
        return _terminal_window(command)
    pwsh = shutil.which("pwsh")
    if pwsh:
        from .launch import _ps_quote

        script = "& " + " ".join(_ps_quote(arg) for arg in command)  # & runs a quoted path
        command = [pwsh, "-NoLogo", "-Command", script]
    return [wt, "-w", "new", "new-tab", "--title", title, *command]


def _terminal_window(command: list[str]) -> list[str]:
    """Off Windows: a new terminal window running the command. Terminal on macOS; on Linux the first
    terminal found, when there is a display to show it on. Else the command itself."""
    if sys.platform == "darwin":
        line = shlex.join(command).replace("\\", "\\\\").replace('"', '\\"')  # an AppleScript string
        return ["osascript", "-e", 'tell application "Terminal" to activate', "-e",
                f'tell application "Terminal" to do script "{line}"']
    if sys.platform != "win32" and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        for name, run in LINUX_TERMINALS:
            found = shutil.which(name)
            if found:
                return [found, *run, *command]
    return command


def startup_file() -> Path:
    """Where start at login lives on this OS."""
    if sys.platform == "win32":
        return startup_shortcut()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT}.plist"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / f"{PROG}.desktop"


def enable_startup(argv: list[str]) -> Path:
    """Run argv at login: a Startup shortcut, a LaunchAgent or an autostart entry."""
    path = startup_file()
    about = "Open the tabmancer picker if sessions were left open"
    if sys.platform == "win32":
        return write_shortcut(path.parent, path.stem, argv, about)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path.write_bytes(plistlib.dumps({"Label": LAUNCH_AGENT, "ProgramArguments": argv, "RunAtLoad": True,
                                         "ProcessType": "Interactive"}))
    else:
        path.write_text("[Desktop Entry]\nType=Application\n"
                        f"Name={STARTUP_NAME}\nComment={about}\nExec={desktop_exec(argv)}\n"
                        "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    return path


def desktop_exec(argv: list[str]) -> str:
    """argv as a desktop entry's Exec line: an argument with a reserved character goes in double
    quotes, with " ` $ \\ escaped, and % doubled (the spec's field codes)."""
    reserved = set(" \t\n\"'\\><~|&;$*?#()`")
    out = []
    for arg in argv:
        arg = arg.replace("%", "%%")
        if reserved & set(arg) or not arg:
            arg = '"' + "".join("\\" + char if char in '"`$\\' else char for char in arg) + '"'
        out.append(arg)
    return " ".join(out)


def _powershell(script: str, **values: str) -> str:
    # Values travel in the environment, so no quoting can leak into the script.
    env = dict(os.environ, **{f"SC_{key.upper()}": value for key, value in values.items()})
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, env=env, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "PowerShell failed")
    return result.stdout.strip()


def special_folder(name: str) -> Path:
    """A Windows known folder such as Desktop or Startup (follows OneDrive redirection)."""
    if os.environ.get("TABMANCER_SHORTCUT_DIR"):  # tests
        return Path(os.environ["TABMANCER_SHORTCUT_DIR"])
    return Path(_powershell("[Environment]::GetFolderPath($env:SC_NAME)", name=name))


def write_shortcut(folder: Path, name: str, argv: list[str], description: str) -> Path:
    path = folder / f"{name}.lnk"
    _powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SC_PATH);"
        "$s.TargetPath = $env:SC_TARGET; $s.Arguments = $env:SC_ARGS;"
        "$s.WorkingDirectory = $env:USERPROFILE; $s.Description = $env:SC_DESC;"
        "if ($env:SC_ICON) { $s.IconLocation = $env:SC_ICON + ',0' };"
        "$s.Save()",
        path=str(path), target=argv[0], args=subprocess.list2cmdline(argv[1:]), desc=description,
        icon=shutil.which("claude") or "")
    return path


def startup_shortcut() -> Path:
    return special_folder("Startup") / f"{STARTUP_NAME}.lnk"


def login_argv() -> list[str] | None:
    """A windowless command for login: the gui-script launcher, or pythonw as a fallback (the
    interpreter itself off Windows, where nothing opens a console)."""
    launcher = shutil.which(f"{PROG}-startup")
    if launcher:
        return [launcher]
    if sys.platform != "win32":
        return [sys.executable, "-m", PROG, "startup-run"]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw), "-m", PROG, "startup-run"] if pythonw.exists() else None
