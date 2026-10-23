"""Typing a prompt into an open session's console: only into the process its pid file names, and
only while that process is the one that wrote it."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from tabmancer import sessions, typein
from tabmancer.typein import write_keys as real_write_keys  # conftest puts a stub in its place

windows = pytest.mark.skipif(sys.platform != "win32", reason="consoles to type into are Windows ones")

READER = r"""
import sys
with open("CONIN$", encoding="utf-8") as keys:
    line = keys.readline()
open(sys.argv[1], "w", encoding="utf-8").write(line)
"""


@pytest.fixture
def console(tmp_path):
    """A console of the test's own, with no window: a Python that reads one line from it and
    writes the line to a file. Yields (the process, the file)."""
    out = tmp_path / "line.txt"
    child = subprocess.Popen([sys.executable, "-c", READER, str(out)], creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(0.5)
    yield child, out
    if child.poll() is None:
        child.kill()


def pid_file(home, session_id: str, pid: int, started) -> None:
    folder = home / "sessions"
    folder.mkdir(exist_ok=True)
    (folder / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": session_id, "procStart": str(started),
                                                    "kind": "interactive", "status": "idle"}), encoding="utf-8")


def test_a_prompt_of_several_lines_goes_in_as_one():
    assert typein.one_line("Continue.\n\n  Check the  state first.\n") == "Continue. Check the state first."


def test_nothing_is_typed_into_a_session_that_is_not_open(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    written = []
    monkeypatch.setattr(typein, "write_keys", lambda pid, text: written.append(pid) or True)
    assert typein.type_into("gone", "continue") is False and not written


@windows
def test_the_keys_reach_the_console_as_typed(console):
    child, out = console
    text = "Continue where you left off. Şimdi devam: çğıöşü 🙂"
    assert real_write_keys(child.pid, text)
    child.wait(timeout=10)
    assert out.read_text(encoding="utf-8") == text + "\n"


@windows
def test_only_the_process_that_wrote_the_pid_file_is_typed_into(console, tmp_path, monkeypatch):
    child, out = console
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(typein, "write_keys", real_write_keys)
    pid_file(tmp_path, "other", child.pid, started=1)  # the pid, but another start: Windows gave it away
    assert typein.type_into("other", "not for you") is False
    pid_file(tmp_path, "mine", child.pid, started=sessions._windows_process_start(child.pid))
    assert typein.type_into("mine", "Continue.\nFirst check the state.") is True
    child.wait(timeout=10)
    assert out.read_text(encoding="utf-8") == "Continue. First check the state.\n"
