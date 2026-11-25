"""The CLI's commands run headless: with neither Textual nor Qt importable, each prints what it prints
on a made-up home (demo.prepare), in folders of the test's own. What the terminal dashboard is
archived without, and what a desktop window's stats stand on.

Each command runs in a subprocess of its own, with textual, PySide6 and shiboken6 blocked by a
meta_path finder. An audit hook stops the command should it open anything of the real Claude Code,
Codex or Antigravity folders (the real ~/.claude/.credentials.json above all), or start any program:
the claude, agy and codex CLIs are left off its PATH too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import tabmancer

SOURCE = Path(tabmancer.__file__).parent.parent
BLOCKED = ("textual", "PySide6", "shiboken6")
REAL_HOME = Path.home()
GUARD = """
import os, sys
from pathlib import Path

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + name)

sys.meta_path.insert(0, Blocked())
home = Path(HOME)
forbidden = [os.path.normcase(os.path.join(REAL_HOME, name)) for name in (".claude", ".codex", ".gemini")]

def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
        if any(path.startswith(root) for root in forbidden) or (
                path.endswith(".credentials.json") and not path.startswith(os.path.normcase(str(home)))):
            raise PermissionError("the test opened a real file: " + path)
    elif event == "subprocess.Popen":
        raise PermissionError("the test started a program: " + repr(args[1]))

sys.addaudithook(audit)
from tabmancer import demo
if not (home / "claude").exists():
    demo.prepare(home)
from tabmancer import agy, sessions, summarize, usage
sessions._agents_json = lambda include_finished=False: []  # never the real `claude agents`
usage.read_usage = list  # nor `claude -p /usage`
usage.read_codex = lambda now=None: []
agy._run_usage = lambda timeout: None  # nor `agy -p /usage`
summarize.ask = lambda prompt, model, timeout=180, effort="": "A made-up week: the checkout got done."
from tabmancer.cli import main
try:
    main(sys.argv[1:])
finally:
    print("LOADED", sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED), file=sys.stderr)
"""


@pytest.fixture(scope="module")
def home(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("headless")


def run(home: Path, *command: str) -> subprocess.CompletedProcess:
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("ANTHROPIC_", "CLAUDE_", "CODEX_", "GEMINI_"))}
    env.update(PYTHONPATH=str(SOURCE), HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
               LOCALAPPDATA=str(home / "localappdata"), XDG_CONFIG_HOME=str(home / "xdg"),
               CLAUDE_CONFIG_DIR=str(home / "claude"), TABMANCER_CONFIG_DIR=str(home / "config"),
               CODEX_HOME=str(home / "codex"), TABMANCER_SHORTCUT_DIR=str(home / "shortcuts"),
               TABMANCER_UPDATE_CHECK="0", TABMANCER_CONTEXT_CAP="auto", COLUMNS="120",
               PATH=os.path.dirname(sys.executable))  # no claude, agy or codex to find
    given = {"BLOCKED": BLOCKED, "HOME": str(home), "REAL_HOME": str(REAL_HOME)}
    code = "".join(f"{name} = {value!r}\n" for name, value in given.items()) + GUARD
    return subprocess.run([sys.executable, "-c", code, *command], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, cwd=home, timeout=120,
                          stdin=subprocess.DEVNULL, check=False)


COMMANDS = {  # the command, and words its output must hold
    "stats": (["stats"], ["TOKENS", "pcb-router"]),
    "stats all time": (["stats", "--all-time"], ["TOKENS"]),
    "stats json": (["stats", "--json"], ['"days"']),
    "stats csv": (["stats", "--csv"], ["date"]),
    "tokens": (["tokens"], ["pcb-router"]),
    "tokens json": (["tokens", "--json"], ['"sessions"']),
    "report": (["report"], ["A made-up week"]),
    "list": (["list"], ["pcb-router"]),
    "list json": (["list", "--json"], ['"session_id"']),
    "clis": (["clis"], ["claude"]),
    "workspaces": (["workspaces"], []),
    "continue-prompt": (["continue-prompt"], []),
    "notifications": (["notifications"], []),
    "updates": (["updates"], [tabmancer.__version__]),
    "claude-updates": (["claude-updates"], []),
    "team who": (["team", "who", "shop"], ["lead"]),
}


@pytest.mark.parametrize("name", list(COMMANDS))
def test_a_command_runs_without_textual_or_qt(home, name):
    command, words = COMMANDS[name]
    done = run(home, *command)
    assert "blocked:" not in done.stderr, done.stderr[-3000:]
    assert "the test opened a real file" not in done.stderr, done.stderr[-3000:]
    assert "the test started a program" not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0, done.stderr[-3000:]
    assert "LOADED []" in done.stderr
    for word in words:
        assert word in done.stdout, done.stdout[-3000:]


def test_wrapped_saves_its_picture_without_textual_or_qt(home, tmp_path):
    out = tmp_path / "week.png"
    done = run(home, "wrapped", "--out", str(out))
    assert done.returncode == 0, done.stderr[-3000:]
    assert "LOADED []" in done.stderr
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_guard_catches_textual_and_a_real_file(home):
    """The guard itself: what needs Textual is stopped (`live` is the dashboard, Textual's on
    purpose), and so is reading the real ~/.claude/.credentials.json or starting a program."""
    assert "blocked: textual" in run(home, "live").stderr
    given = {"BLOCKED": BLOCKED, "HOME": str(home), "REAL_HOME": str(REAL_HOME)}
    guard = "".join(f"{name} = {value!r}\n" for name, value in given.items())
    guard += GUARD.split("from tabmancer import demo")[0]  # the finder and the audit hook only
    for code, said in ((f"open({str(REAL_HOME / '.claude' / '.credentials.json')!r})", "opened a real file"),
                       ("import subprocess; subprocess.run(['claude', '--version'])", "started a program")):
        stopped = subprocess.run([sys.executable, "-c", guard + code], capture_output=True, text=True,
                                 env={**os.environ, "PYTHONPATH": str(SOURCE)}, timeout=60, check=False)
        assert f"the test {said}" in stopped.stderr
