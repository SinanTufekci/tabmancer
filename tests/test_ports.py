"""macOS and Linux: when the computer shut down, start at login, the picker's window, and the
session's tab brought to the front. The commands are stubbed; the Linux ones that need no
desktop also run for real on Linux (CI's Ubuntu runner)."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime

import pytest

from tabmancer import cli, focus, shortcuts, shutdowns

FOCUS_TAB = focus.focus_tab  # the real one: conftest keeps the other tests off real tabs

# --- when it shut down --------------------------------------------------------------------------

JOURNAL_JSON = ('[{"index":-2,"boot_id":"a","first_entry":1761000000000000,"last_entry":1761030000000000},'
                '{"index":-1,"boot_id":"b","first_entry":1761050000000000,"last_entry":1761080000000000},'
                '{"index":0,"boot_id":"c","first_entry":1761100000000000,"last_entry":1761110000000000}]')
JOURNAL_TEXT = """IDX BOOT ID                          FIRST ENTRY                 LAST ENTRY
 -1 3c1f0a8b9d2e4f60a1b2c3d4e5f60718 Tue 2026-10-21 08:00:01 +03 Tue 2026-10-21 18:30:12 +03
  0 4d2a1b9c8e3f5a60b1c2d3e4f5061728 Wed 2026-10-22 08:01:10 +03 Wed 2026-10-22 12:00:00 +03"""
LINUX_LAST = """reboot   system boot  6.8.0-45-generic Wed Oct 22 08:01:02 2026   still running
shutdown system down  6.8.0-45-generic Tue Oct 21 18:30:40 2026 - Wed Oct 22 08:01:02 2026  (13:30)
shutdown system down  6.8.0-45-generic Mon Oct 20 19:02:05 2026 - Tue Oct 21 07:58:44 2026  (12:56)

wtmp begins Mon Oct  6 09:00:00 2026"""
MAC_LAST = """shutdown  ~                         Tue Oct 21 18:30
shutdown  ~                         Mon Oct 20 19:02

wtmp begins Mon Oct  6 09:00"""


def test_linux_shutdowns_from_the_journal_or_wtmp(monkeypatch):
    assert shutdowns._journal_boots(JOURNAL_JSON) == [datetime.fromtimestamp(1761080000), datetime.fromtimestamp(1761030000)]
    assert shutdowns._journal_boots("not json") == []
    assert shutdowns._journal_boots_text(JOURNAL_TEXT) == [datetime(2026, 10, 21, 18, 30, 12)]
    assert shutdowns._linux_last(LINUX_LAST) == [datetime(2026, 10, 21, 18, 30, 40), datetime(2026, 10, 20, 19, 2, 5)]
    outputs = {"json": "", "text": "", "last": LINUX_LAST}  # an old systemd and no journal: wtmp then

    def output(argv):
        return outputs["json" if "json" in argv else "last" if argv[0] == "last" else "text"]

    monkeypatch.setattr(shutdowns.sys, "platform", "linux")
    monkeypatch.setattr(shutdowns, "_output", output)
    assert shutdowns.recent_shutdowns()[0] == datetime(2026, 10, 21, 18, 30, 40)
    outputs["json"] = JOURNAL_JSON
    assert shutdowns.recent_shutdowns()[0] == datetime.fromtimestamp(1761080000)


def test_mac_shutdowns_from_last_whose_lines_have_no_year(monkeypatch):
    assert shutdowns._mac_last(MAC_LAST, now=datetime(2026, 10, 22, 9, 0)) == [
        datetime(2026, 10, 21, 18, 30), datetime(2026, 10, 20, 19, 2)]
    # In January, a December shutdown was last year.
    assert shutdowns._mac_last("shutdown  ~   Wed Dec 31 23:10", now=datetime(2027, 1, 2)) == [
        datetime(2026, 12, 31, 23, 10)]
    monkeypatch.setattr(shutdowns.sys, "platform", "darwin")
    monkeypatch.setattr(shutdowns, "_output", lambda argv: MAC_LAST if argv == ["last", "shutdown"] else "")
    assert len(shutdowns.recent_shutdowns()) == 2


@pytest.mark.skipif(sys.platform != "linux", reason="the real journal and wtmp are Linux's")
def test_linux_shutdowns_for_real_never_fail():
    found = shutdowns.recent_shutdowns()
    assert isinstance(found, list) and all(isinstance(moment, datetime) for moment in found)
    assert found == sorted(found, reverse=True)


# --- start at login and the picker's window ---------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(shortcuts.shutil, "which",
                        lambda name: {"tabmancer": "/usr/bin/tabmancer", "tabmancer-startup": "/usr/bin/tabmancer-startup",
                                      "gnome-terminal": "/usr/bin/gnome-terminal"}.get(name))
    return tmp_path


def test_start_at_login_on_linux_is_an_autostart_entry(home, monkeypatch, capsys):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    cli.manage_startup("on")
    entry = home / "config" / "autostart" / "tabmancer.desktop"
    text = entry.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/tabmancer-startup\n" in text and "Type=Application" in text
    cli.manage_startup("status")
    assert "On" in capsys.readouterr().out
    cli.manage_startup("off")
    assert not entry.exists()


def test_start_at_login_on_macos_is_a_launch_agent(home, monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    cli.manage_startup("on")
    agent = plistlib.loads((home / "Library" / "LaunchAgents" / "com.tabmancer.startup.plist").read_bytes())
    assert agent["ProgramArguments"] == ["/usr/bin/tabmancer-startup"] and agent["RunAtLoad"] is True


def test_an_exec_line_quotes_what_the_desktop_spec_reserves():
    assert shortcuts.desktop_exec(["/usr/bin/tabmancer", "open", "PCB work"]) == '/usr/bin/tabmancer open "PCB work"'
    assert shortcuts.desktop_exec(["a", 'say "$HOME"', "100%"]) == 'a "say \\"\\$HOME\\"" 100%%'


def test_the_picker_opens_in_a_terminal_window_of_its_own(home, monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    assert shortcuts.picker_argv([]) == ["/usr/bin/gnome-terminal", "--", "/usr/bin/tabmancer"]
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert shortcuts.picker_argv([]) == ["/usr/bin/tabmancer"]  # no display: nothing to show a window on
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    argv = shortcuts.picker_argv(["open", 'PCB "rev 2"'])
    assert argv[:2] == ["osascript", "-e"] and argv[-1].endswith(
        'do script "/usr/bin/tabmancer open \'PCB \\"rev 2\\"\'"')


# --- the session's tab to the front ------------------------------------------------------------------

def test_a_title_loses_claudes_glyph():
    assert focus.plain("✳ shop-api") == "shop-api" and focus.plain("⠂ Fix the login") == "Fix the login"


def test_tmux_selects_the_pane_titled_after_the_session(monkeypatch):
    ran = []
    listed = "main\t@1\t%1\tbash\nwork\t@2\t%3\t✳ shop-api\n"

    def quiet(argv, timeout=5):
        ran.append(argv[1:])
        return listed if argv[1] == "list-panes" else ""

    monkeypatch.setattr(focus.sys, "platform", "linux")
    monkeypatch.setattr(focus, "_quiet", quiet)
    assert FOCUS_TAB("shop-api")
    assert ["switch-client", "-t", "work"] in ran and ["select-window", "-t", "@2"] in ran
    assert ["select-pane", "-t", "%3"] in ran
    ran.clear()
    assert not FOCUS_TAB("nobody") and ran == [["list-panes", "-a", "-F", ran[0][3]]]


def test_iterm2_or_terminal_selects_the_tab_titled_after_the_session(monkeypatch):
    calls = []
    listed = "iTerm2\t1\t1\t1\tzsh\niTerm2\t1\t2\t1\t✳ shop-api (claude)\nTerminal\t1\t1\t0\tnotes\n"

    def quiet(argv, timeout=5):
        calls.append(argv)
        if argv[0] == "tmux":
            return None  # no tmux here
        return listed if len(argv) == 3 else ""

    monkeypatch.setattr(focus.sys, "platform", "darwin")
    monkeypatch.setattr(focus, "_quiet", quiet)
    assert FOCUS_TAB("shop-api")
    assert calls[-1][3:] == ["iTerm2", "1", "2", "1"]  # the select script, told which tab
    assert not FOCUS_TAB("shop")  # a name that only starts the same is not it


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("tmux"), reason="real tmux, on Linux")
def test_tmux_for_real(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))  # a server of its own
    monkeypatch.delenv("TMUX", raising=False)

    def tmux(*args):
        return subprocess.run(["tmux", *args], capture_output=True, text=True, check=True).stdout

    tmux("new-session", "-d", "-s", "t", "-n", "one")
    try:
        tmux("new-window", "-t", "t", "-n", "two")
        tmux("select-pane", "-t", "t:two", "-T", "✳ shop-api")
        tmux("select-window", "-t", "t:one")
        time.sleep(0.2)
        assert FOCUS_TAB("shop-api")
        assert tmux("display-message", "-p", "-t", "t", "#{window_name}").strip() == "two"
    finally:
        subprocess.run(["tmux", "kill-server"], capture_output=True, check=False)
