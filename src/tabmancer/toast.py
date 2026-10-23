"""Desktop notifications: a session that starts waiting for you, told outside the dashboard too.

On Windows one PowerShell run does it all. If the window in front is Windows Terminal with
the dashboard's own tab selected, you are looking at it and nothing is shown; otherwise a
Windows toast is. This does not depend on the terminal reporting focus, which Windows
Terminal may not do for a tab switch. The text travels in the environment and goes into
the toast through CreateTextNode, so nothing in it needs escaping.

macOS shows it through osascript and Linux through notify-send, when the dashboard is not
focused (the terminal reports that there).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .config import read_json

# Windows PowerShell's own app id: a toast needs one that Windows knows, and this one
# needs no shortcut of ours. The toast says it comes from Windows PowerShell.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SHOW = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace W -Name U -MemberDefinition '
  [DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();'
$A = [System.Windows.Automation.AutomationElement]
$scope = [System.Windows.Automation.TreeScope]
function Plain([string]$s) { ($s -replace '^[^\p{L}\p{N}]+', '').Trim() }
$front = [W.U]::GetForegroundWindow()
$byClass = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'CASCADIA_HOSTING_WINDOW_CLASS')
$byTab = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
foreach ($w in $A::RootElement.FindAll($scope::Children, $byClass)) {
  if ([System.IntPtr]$w.Current.NativeWindowHandle -ne $front) { continue }
  foreach ($t in $w.FindAll($scope::Descendants, $byTab)) {
    $item = $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
    if ($item.Current.IsSelected -and (Plain $t.Current.Name) -eq (Plain $env:CCR_DASHBOARD)) { exit 4 }
  }
}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$kind = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($kind)
$lines = $xml.GetElementsByTagName('text')
[void]$lines.Item(0).AppendChild($xml.CreateTextNode($env:CCR_TOAST_TITLE))
[void]$lines.Item(1).AppendChild($xml.CreateTextNode($env:CCR_TOAST_BODY))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:CCR_TOAST_APP).Show($toast)
exit 0
"""
SHOWN, LOOKING, FAILED = "shown", "looking", "failed"


def enabled() -> bool:
    """On unless config.json says "desktop_notifications": false (`tabmancer notifications off`)."""
    saved = read_json("config.json")
    return not (isinstance(saved, dict) and saved.get("desktop_notifications") is False)


def show(title: str, body: str, dashboard: str) -> str:
    """Show a notification unless the dashboard tab titled `dashboard` is in front (Windows).

    Returns SHOWN, LOOKING (you were looking at the dashboard: none shown) or FAILED.
    """
    try:
        if sys.platform == "win32":
            env = dict(os.environ, CCR_TOAST_TITLE=title, CCR_TOAST_BODY=body, CCR_TOAST_APP=APP_ID,
                       CCR_DASHBOARD=dashboard)
            # Windows PowerShell, not pwsh: only it can load the WinRT toast types.
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", _SHOW], env=env, capture_output=True, text=True, timeout=30, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {0: SHOWN, 4: LOOKING}.get(result.returncode, FAILED)
        if sys.platform == "darwin":
            script = "display notification (system attribute \"CCR_TOAST_BODY\") with title " \
                     "(system attribute \"CCR_TOAST_TITLE\")"
            result = subprocess.run(["osascript", "-e", script], env=dict(os.environ, CCR_TOAST_TITLE=title,
                                    CCR_TOAST_BODY=body), capture_output=True, timeout=10, check=False)
            return SHOWN if result.returncode == 0 else FAILED
        if shutil.which("notify-send"):
            result = subprocess.run(["notify-send", "--", title, body], capture_output=True, timeout=10,
                                    check=False)
            return SHOWN if result.returncode == 0 else FAILED
    except (OSError, subprocess.SubprocessError):
        pass
    return FAILED
