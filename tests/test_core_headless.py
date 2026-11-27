"""The core stays headless (0.2.0: Qt an optional extra, Textual gone at the archive): with Textual and PySide6
blocked as if not installed, every module outside the terminal dashboard's and the window's imports, and the
CLI's headless commands run. In a child process each time (a meta_path hook raises ModuleNotFoundError for
them, as a core install would), every config folder and the home in tmp; no process starts there (claude,
agy, the shutdown records), so doctor answers on a made-up machine. Never tabmancer.__main__: that starts
the dashboard."""

import json
import os
import subprocess
import sys
from pathlib import Path

import tabmancer

PACKAGE = Path(str(tabmancer.__file__)).parent
# The terminal dashboard's modules (Textual), until the archive takes them; nothing else may need Textual or Qt.
# A module here that no longer exists fails the last test: take it off the list.
TEXTUAL = {"accountsview", "app", "background", "cleanup", "clisettings", "dialogs", "handoverview", "keysview",
           "mascot", "monitorview", "newsessions", "safework", "statsview", "teamtree", "teamview", "theme",
           "tokenchart", "widgets", "wrappedview"}
NEVER = {"__main__"}  # starts the dashboard

# Run in the child before anything of tabmancer: Textual and Qt are not there; nor are other programs.
GUARD = r'''
import importlib.abc, json, subprocess, sys
BLOCKED = ("textual", "PySide6", "shiboken6")
asked = []
class NotInstalled(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            asked.append(name)
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, NotInstalled())
class NoProcess(subprocess.Popen):
    def __init__(self, args, *rest, **named):
        raise FileNotFoundError(f"no process in this test: {args!r}")
subprocess.Popen = NoProcess
'''

IMPORT_ALL = GUARD + r'''
import importlib
failed = {}
for name in json.loads(sys.argv[1]):
    try:
        importlib.import_module(name)
    except Exception as error:
        failed[name] = f"{type(error).__name__}: {error}"
print(json.dumps({"failed": failed, "asked": asked}))
'''

RUN_CLI = GUARD + r'''
import atexit
atexit.register(lambda: print("\nGUARD-ASKED " + json.dumps(asked)))
from tabmancer.cli import main
main(sys.argv[1:])
'''


def child_env(tmp_path: Path) -> dict[str, str]:
    """The child's: every config folder and the home in tmp (doctor reads the Desktop and the shortcuts there)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "TABMANCER_CONFIG_DIR": str(tmp_path / "tabmancer"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
           "CODEX_HOME": str(tmp_path / "codex"), "HOME": str(home), "USERPROFILE": str(home),
           "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("QT_QPA_PLATFORM", None)
    return env


def core_modules() -> list[str]:
    stems = sorted(path.stem for path in PACKAGE.glob("*.py"))
    return [("tabmancer" if stem == "__init__" else f"tabmancer.{stem}") for stem in stems
            if stem not in TEXTUAL | NEVER]


def cli(tmp_path: Path, *args: str) -> tuple[int, str, list[str]]:
    """tabmancer <args> in a child with the guard: its exit code, what it printed, what it asked of Textual/Qt."""
    done = subprocess.run([sys.executable, "-c", RUN_CLI, *args], check=False, env=child_env(tmp_path), capture_output=True,
                          text=True, encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL)
    out, _, asked = done.stdout.rpartition("\nGUARD-ASKED ")
    assert asked, f"the child did not finish: {done.stderr[-2000:]}"
    return done.returncode, out + done.stderr, json.loads(asked)


def test_every_core_module_imports_without_textual_or_qt(tmp_path):
    names = core_modules()
    assert "tabmancer.cli" in names and "tabmancer.doctor" in names and "tabmancer.mcpserver" in names
    done = subprocess.run([sys.executable, "-c", IMPORT_ALL, json.dumps(names)], check=False, env=child_env(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", timeout=300, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr[-2000:]
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert said["failed"] == {}
    assert said["asked"] == []  # not even tried and caught


def test_the_headless_commands_run_without_textual_or_qt(tmp_path):
    code, out, asked = cli(tmp_path, "--help")
    assert code == 0 and "usage: tabmancer" in out and asked == []
    code, out, asked = cli(tmp_path, "team", "who", "shop")
    assert code == 1 and "There is no team called shop" in out and asked == []  # said, as an error
    code, out, asked = cli(tmp_path, "cleanup", "--help")
    assert code == 0 and "--older" in out and asked == []
    for command in ("stats", "wrapped", "report", "tokens"):
        code, out, asked = cli(tmp_path, command, "--help")
        assert code == 0 and "usage: tabmancer" in out and asked == [], command


def test_doctor_answers_on_a_made_up_machine(tmp_path):
    code, out, asked = cli(tmp_path, "doctor")
    assert code in (0, 1) and asked == []  # 1: something to fix (no claude here), said, not a crash
    assert "Traceback" not in out and "✓" in out
    assert f"Own files in {tmp_path / 'tabmancer'}" in out
    assert not (tmp_path / "claude" / ".claude.json").exists()  # claude never ran


def test_the_textual_list_names_modules_that_are_there():
    assert {path.stem for path in PACKAGE.glob("*.py")} >= TEXTUAL
