"""conftest's guards hold at the core, not only on the terminal dashboard's copies: no test opens a real tab,
starts `claude --bg` or reads the system's shutdown log, whatever reaches launch (the desktop window's openers
look launch.open_tabs up when called) and with tabmancer.app not there at all, as once the terminal dashboard
is archived. Proven in this process, and in one of its own with Textual (and Qt) blocked."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import tabmancer
from tabmancer import actions, cli, doctor, launch, shutdowns

PROBE = "TABMANCER_GUARD_PROBE"
BLOCK = """
import sys

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("textual", "PySide6", "shiboken6"):
            raise ModuleNotFoundError("blocked: " + name, name=name)

sys.meta_path.insert(0, Blocked())
import pytest
sys.exit(pytest.main([sys.argv[1], "-q", "-p", "no:cacheprovider", "-k", "probe"]))
"""


def guards_hold(monkeypatch, tmp_path) -> None:
    started: list = []
    for name in ("Popen", "run"):  # were a guard gone, nothing would start: it would be kept here
        monkeypatch.setattr(subprocess, name, lambda *args, **kwargs: started.append(args))
    one = launch.Launch("made-up", str(tmp_path), launch.Mode.RESUME, "made-up-id")
    assert launch.terminal_kind() == "none"
    assert launch.open_tabs([one]) == [launch.shell_line(one)] and not started  # the command, given back
    for start in (launch.run_in_background, actions.run_in_background):
        with pytest.raises(OSError, match="no background sessions in tests"):
            start(one)
    assert not started


def test_the_core_openers_are_guarded(monkeypatch, tmp_path):
    guards_hold(monkeypatch, tmp_path)


def test_fake_home_never_reads_the_shutdown_log(fake_home):
    assert shutdowns.recent_shutdowns() == cli.recent_shutdowns() == doctor.recent_shutdowns() == []


def test_the_guards_hold_without_the_terminal_dashboard(tmp_path):
    env = {**os.environ, PROBE: "1", "PYTHONPATH": str(Path(tabmancer.__file__).parent.parent)}
    done = subprocess.run([sys.executable, "-c", BLOCK, __file__], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, cwd=Path(__file__).parent.parent,
                          timeout=120, stdin=subprocess.DEVNULL, check=False)
    assert done.returncode == 0 and "2 passed" in done.stdout, done.stdout[-3000:] + done.stderr[-3000:]


@pytest.mark.skipif(not os.environ.get(PROBE), reason="run with Textual blocked, by the test above")
def test_probe_the_openers_without_the_terminal_dashboard(monkeypatch, tmp_path):
    assert "textual" not in sys.modules and "tabmancer.app" not in sys.modules
    guards_hold(monkeypatch, tmp_path)


@pytest.mark.skipif(not os.environ.get(PROBE), reason="run with Textual blocked, by the test above")
def test_probe_fake_home_without_the_terminal_dashboard(fake_home):
    assert "tabmancer.app" not in sys.modules
    assert shutdowns.recent_shutdowns() == cli.recent_shutdowns() == doctor.recent_shutdowns() == []
