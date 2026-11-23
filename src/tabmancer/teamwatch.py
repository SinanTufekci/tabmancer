"""The team monitor's own window: `tabmancer team watch <team>`, opened by M on a team.

The window is made for a second screen: in Windows Terminal it opens maximized on the first screen
that is not the main one (one screen: maximized there), in a window named after the team, so M on a
team whose monitor is open brings that one to the front instead of opening a second.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

from . import focus
from .launch import _wt_escape, terminal_kind

OFFSET = 40  # pixels in from a screen's corner: Windows Terminal places a window there, then maximizes it


def title(team: str) -> str:
    """The monitor's tab title, which M looks for before it opens another."""
    return f"tabmancer · {team}"


def monitor_open() -> bool:
    """Whether any team's monitor has a tab open in Windows Terminal: only looked for (focus.tab_open),
    no tab or window comes to the front."""
    return focus.tab_open(title(""))


def window_name(team: str) -> str:
    """Windows Terminal's name for the monitor's window: letters, digits and dashes."""
    return "tabmancer-watch-" + (re.sub(r"[^A-Za-z0-9]+", "-", team).strip("-").lower() or "team")


def command(team: str, tabs_window: str = "") -> list[str]:
    """The monitor's command line: tabmancer as a member's tab runs it (teams._tabmancer_cmd).
    tabs_window: the dashboard's window, when the dashboard knows its name: the monitor's tabs go
    there by name, with no tab brought to the front first."""
    from .teams import _tabmancer_cmd

    told = ["--tabs-window", tabs_window] if tabs_window else []
    return [*_tabmancer_cmd(), *told, "team", "watch", team]


def second_screen() -> tuple[int, int] | None:
    """The top-left corner of the work area of the first screen that is not the main one, in
    pixels on the desktop (left of the main screen they are negative); None with one screen, or off
    Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        found: list[tuple[int, int]] = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)

        def each(monitor, dc, rect, data) -> int:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)) and not info.dwFlags & 1:
                found.append((info.rcWork.left, info.rcWork.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(each), 0)
    except (OSError, AttributeError, ValueError):
        return None
    return found[0] if found else None


def wt_command(team: str, folder: str, where: tuple[int, int] | None, tabs_window: str = "") -> list[str]:
    """wt.exe's command line for the monitor: its own named window, maximized, on the second screen
    when there is one. The monitor runs as the tab's own process, with no shell around it, so q
    closes the tab with it. The corner is in the pixels Windows gives this process, which is not
    DPI-aware: right on screens at 100%; on a scaled screen, untried."""
    args = ["wt.exe", "-w", _wt_escape(window_name(team))]
    if where is not None:
        args += ["--pos", f"{where[0] + OFFSET},{where[1] + OFFSET}"]
    # Titled by the monitor itself as well (it writes its title, as the dashboard does), so T, which
    # shows another team there, names the tab after that team: M looks for it by that name.
    args += ["--maximized", "new-tab", "--title", _wt_escape(title(team))]
    if folder and os.path.isdir(folder):
        args += ["-d", _wt_escape(folder)]
    return [*args, *(_wt_escape(arg) for arg in command(team, tabs_window))]


def open_monitor(team: str, folder: str = "", tabs_window: str = "") -> str:
    """M: the team's monitor in a window of its own, or its open window to the front. Returns what
    happened in a few words, for a note; raises OSError when the terminal would not start.
    tabs_window: the dashboard's window, when it knows its name (command)."""
    if focus.focus_tab(title(team)):
        return f"The monitor of {team} is open already: brought to the front."
    kind = terminal_kind()
    if kind == "windows-terminal":
        subprocess.Popen(wt_command(team, folder, second_screen(), tabs_window),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return f"The monitor of {team} opens in a window of its own."
    if kind == "tmux":
        subprocess.run(["tmux", "new-window", "-n", title(team)[:30], shlex.join(command(team))], check=False,
                       capture_output=True)
        return f"The monitor of {team} opens in a tmux window."
    return f"Run `tabmancer team watch {team}` in a window of its own for the monitor."


def open_dashboard(dashboard: str, folder: str = "") -> str:
    """A monitor's D: the dashboard's tab (titled dashboard) to the front; with none open, the
    dashboard in a new tab. Returns a word for a note, or "" when there is nothing to say."""
    if focus.focus_tab(dashboard):
        return ""
    from .teams import _tabmancer_cmd

    kind = terminal_kind()
    if kind == "windows-terminal":
        # A window of its own ("0", the one used last, is the monitor's, which this is called from),
        # named, and the dashboard told its name: what it opens by itself later goes there by name.
        from .shortcuts import DASHBOARD_WINDOW

        args = ["wt.exe", "-w", DASHBOARD_WINDOW, "new-tab", "--title", _wt_escape(dashboard)]
        if folder and os.path.isdir(folder):
            args += ["-d", _wt_escape(folder)]
        line = [*_tabmancer_cmd(), "--tabs-window", DASHBOARD_WINDOW]
        subprocess.Popen([*args, *(_wt_escape(arg) for arg in line)],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "The dashboard opens in a new tab."
    if kind == "tmux":
        subprocess.run(["tmux", "new-window", "-n", dashboard, shlex.join(_tabmancer_cmd())], check=False,
                       capture_output=True)
        return "The dashboard opens in a tmux window."
    return "Run `tabmancer` in another window for the dashboard."
