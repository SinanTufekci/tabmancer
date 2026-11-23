"""Find the terminal tab that holds a session: bring it to the front, or close it.

Claude Code names its tab after the session (with a status glyph in front: ✳ when
waiting, a spinner when working). Windows UI Automation lists the tabs of every
Windows Terminal window, so the tab can be found by that title and selected, the
same as clicking it. Nothing is typed: you write the prompt yourself.

Closing presses the tab's own close button. Claude Code exits cleanly then (it saves
its state, as on /exit), and the session stays resumable.

Elsewhere, focusing (not closing) works in tmux, where Claude's title becomes the pane's
title, and on macOS in iTerm2 and Terminal through AppleScript: one script lists the tabs,
this module picks the one, a second selects it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

_FIND = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$scope = [System.Windows.Automation.TreeScope]
$byClass = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'CASCADIA_HOSTING_WINDOW_CLASS')
$byTab = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
function Plain([string]$s) { ($s -replace '^[^\p{L}\p{N}]+', '').Trim() }
$want = Plain $env:CCR_TITLE
"""
_FOCUS = _FIND + r"""
Add-Type -Namespace W -Name U -MemberDefinition '
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(System.IntPtr h);'
foreach ($w in $A::RootElement.FindAll($scope::Children, $byClass)) {
  foreach ($t in $w.FindAll($scope::Descendants, $byTab)) {
    if ((Plain $t.Current.Name) -ne $want) { continue }
    $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
    $hwnd = [System.IntPtr]$w.Current.NativeWindowHandle
    if ([W.U]::IsIconic($hwnd)) { [void][W.U]::ShowWindow($hwnd, 9) }   # SW_RESTORE
    try { $w.SetFocus() } catch { }
    [void][W.U]::SetForegroundWindow($hwnd)
    # The keys to the terminal in the tab, not to the tab in the strip, where the window's own
    # focus can land: what you type goes to the prompt, the arrow keys to Claude's choices. The
    # terminal is named as its tab; the tab's own may take a moment to come in.
    $byTerm = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'TermControl')
    for ($i = 0; $i -lt 40; $i++) {
      $term = $w.FindAll($scope::Descendants, $byTerm) | Where-Object { (Plain $_.Current.Name) -eq $want } | Select-Object -First 1
      if ($term) { try { $term.SetFocus() } catch { }; break }
      Start-Sleep -Milliseconds 25
    }
    exit 0
  }
}
exit 3
"""
_CLOSE = _FIND + r"""
$byClose = New-Object System.Windows.Automation.PropertyCondition($A::AutomationIdProperty, 'CloseButton')
$found = @()
foreach ($w in $A::RootElement.FindAll($scope::Children, $byClass)) {
  foreach ($t in $w.FindAll($scope::Descendants, $byTab)) {
    if ((Plain $t.Current.Name) -eq $want) { $found += $t }
  }
}
if ($found.Count -ne 1) { exit 3 }   # none, or tabs of the same name: never guess which one
$button = $found[0].FindFirst($scope::Descendants, $byClose)
if (-not $button) { exit 3 }
$button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
exit 0
"""

_ANY = _FIND + r"""
foreach ($w in $A::RootElement.FindAll($scope::Children, $byClass)) {
  foreach ($t in $w.FindAll($scope::Descendants, $byTab)) {
    if ((Plain $t.Current.Name).StartsWith($want, [System.StringComparison]::Ordinal)) { exit 0 }
  }
}
exit 3
"""


def _run(script: str, title: str, timeout: float) -> bool:
    if sys.platform != "win32" or not title.strip():
        return False
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            env=dict(os.environ, CCR_TITLE=title), capture_output=True, text=True, timeout=timeout,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def focus_tab(title: str, timeout: float = 20) -> bool:
    """Select the tab titled after this session: in Windows Terminal, a tmux pane, iTerm2 or
    Terminal. False if none matches."""
    if sys.platform == "win32":
        return _run(_FOCUS, title, timeout)
    if not title.strip():
        return False
    if _tmux_select(title):
        return True
    return sys.platform == "darwin" and _mac_select(title, timeout)


_GIVE_BACK = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace W -Name U -MemberDefinition '
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr h);'
$h = [System.IntPtr][long]$env:CCR_HWND
try { [System.Windows.Automation.AutomationElement]::FromHandle($h).SetFocus() } catch { }
if ([W.U]::SetForegroundWindow($h)) { exit 0 }
exit 3
"""


def foreground() -> int:
    """The window that has the keys now (its handle), 0 off Windows or when none has."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except (OSError, AttributeError):
        return 0


def is_window(hwnd: int) -> bool:
    """Whether this handle is still a window (closed meanwhile, it is not)."""
    try:
        import ctypes
        from ctypes import wintypes

        return bool(ctypes.windll.user32.IsWindow(wintypes.HWND(hwnd)))
    except (OSError, AttributeError):
        return False


TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"  # a Windows Terminal window
TERMINAL_EXE = "windowsterminal.exe"


def is_terminal(hwnd: int) -> bool:
    """Whether this window is Windows Terminal's: by its window class, else by the process it
    belongs to. False when neither can be read."""
    try:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        name = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(wintypes.HWND(hwnd), name, 256) and name.value == TERMINAL_CLASS:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return False
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if not kernel32.QueryFullProcessImageNameW(wintypes.HANDLE(handle), 0, path, ctypes.byref(size)):
                return False
            return os.path.basename(path.value).lower() == TERMINAL_EXE
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, AttributeError, ValueError):
        return False


def give_back(hwnd: int, wait: float = 3.0, timeout: float = 20) -> bool:
    """After tabs opened in another window: the window that had the keys (hwnd, from foreground())
    gets them back once the terminal has taken them; a terminal brings the window it opens a tab in
    to the front a moment after it is asked. False when nothing took them within wait seconds, and
    nothing is given back when what took them is not a Windows Terminal window (you went to the
    browser meanwhile: you stay there) or the window that had them has closed since."""
    if sys.platform != "win32" or not hwnd:
        return False
    end = time.monotonic() + wait
    taken = foreground()
    while taken in (hwnd, 0):
        if time.monotonic() >= end:
            return False
        time.sleep(0.1)
        taken = foreground()
    if not is_window(hwnd) or not is_terminal(taken):
        return False
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _GIVE_BACK],
            env=dict(os.environ, CCR_HWND=str(hwnd)), capture_output=True, text=True, timeout=timeout,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def tab_open(start: str, timeout: float = 5) -> bool:
    """Whether a Windows Terminal tab's title (without Claude's glyph) starts with these words. Only
    looks: no tab is selected, no window comes to the front, the keys stay where they are."""
    return sys.platform == "win32" and _run(_ANY, start, timeout)


def plain(title: str) -> str:
    """A title without Claude's status glyph in front (✳, a spinner)."""
    return re.sub(r"^[\W_]+", "", title).strip()


def _quiet(argv: list[str], timeout: float = 5) -> str | None:
    """What a command prints, or None when it is missing or fails."""
    if not shutil.which(argv[0]):
        return None
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _tmux_select(title: str) -> bool:
    """The tmux pane titled after the session: its window and pane selected, and an attached
    client switched to its session (that part fails quietly when none is attached)."""
    listed = _quiet(["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{window_id}\t#{pane_id}\t#{pane_title}"])
    want = plain(title)
    for line in (listed or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and plain(parts[3]) == want:
            session, window, pane, _ = parts
            _quiet(["tmux", "switch-client", "-t", session])
            return (_quiet(["tmux", "select-window", "-t", window]) is not None
                    and _quiet(["tmux", "select-pane", "-t", pane]) is not None)
    return False


# Every tab of iTerm2 and Terminal, a line each: app, window, tab, session, title.
_MAC_LIST = """
set out to ""
if application "iTerm2" is running then
  tell application "iTerm2"
    repeat with w from 1 to count of windows
      repeat with t from 1 to count of tabs of window w
        repeat with s from 1 to count of sessions of tab t of window w
          set out to out & "iTerm2" & tab & w & tab & t & tab & s & tab & (name of session s of tab t of window w) & linefeed
        end repeat
      end repeat
    end repeat
  end tell
end if
if application "Terminal" is running then
  tell application "Terminal"
    repeat with w from 1 to count of windows
      repeat with t from 1 to count of tabs of window w
        set out to out & "Terminal" & tab & w & tab & t & tab & 0 & tab & (custom title of tab t of window w) & linefeed
      end repeat
    end repeat
  end tell
end if
return out
"""
_MAC_SELECT = """
on run argv
  set {app_name, w, t, s} to {item 1 of argv, (item 2 of argv) as integer, (item 3 of argv) as integer, (item 4 of argv) as integer}
  if app_name is "iTerm2" then
    tell application "iTerm2"
      tell window w
        select
        tell tab t to select
        tell session s of tab t to select
      end tell
      activate
    end tell
  else
    tell application "Terminal"
      set selected of tab t of window w to true
      set index of window w to 1
      activate
    end tell
  end if
end run
"""


def _mac_select(title: str, timeout: float) -> bool:
    """iTerm2's or Terminal's tab titled after the session, selected and brought to the front.
    iTerm2 may add the job to a name ("shop-api (claude)"): that still counts."""
    listed = _quiet(["osascript", "-e", _MAC_LIST], timeout)
    want = plain(title)
    for line in (listed or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 5 and (plain(parts[4]) == want or plain(parts[4]).startswith(f"{want} (")):
            return _quiet(["osascript", "-e", _MAC_SELECT, *parts[:4]], timeout) is not None
    return False


def close_tab(title: str, timeout: float = 20) -> bool:
    """Close the one Windows Terminal tab titled after this session. False if no tab, or more
    than one, has that title."""
    return _run(_CLOSE, title, timeout)
