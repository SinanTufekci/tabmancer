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
            raise ModuleNotFoundError("blocked: " + name, name=name)  # as a missing one says it

sys.meta_path.insert(0, Blocked())
home = Path(HOME)
forbidden = [os.path.normcase(os.path.join(REAL_HOME, name)) for name in (".claude", ".codex", ".gemini")]

def stop(said):
    # Said before it is raised: code that takes an OSError in its stride would swallow it unseen.
    sys.__stderr__.write(said + chr(10))
    sys.__stderr__.flush()
    raise PermissionError(said)

def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
        if any(path.startswith(root) for root in forbidden) or (
                path.endswith(".credentials.json") and not path.startswith(os.path.normcase(str(home)))):
            stop("the test opened a real file: " + path)
    elif event == "subprocess.Popen":
        stop("the test started a program: " + repr(args[1]))

sys.addaudithook(audit)
from tabmancer import demo
if not (home / "claude").exists():
    demo.prepare(home)
from tabmancer import agy, sessions, shutdowns, summarize, usage
shutdowns.recent_shutdowns = lambda limit=30: []  # nor the system's own log (wevtutil, journalctl, last)
sessions._agents_json = lambda include_finished=False: []  # never the real `claude agents`
usage.read_usage = list  # nor `claude -p /usage`
usage.read_codex = lambda now=None: []
agy._run_usage = lambda timeout: None  # nor `agy -p /usage`
summarize.ask = lambda prompt, model, timeout=180, effort="": "A made-up week: the checkout got done."
"""
CLI = """
from tabmancer.cli import main
try:
    main(sys.argv[1:])
finally:
    print("LOADED", sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED), file=sys.stderr)
"""


@pytest.fixture(scope="module")
def home(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("headless")


def run(home: Path, *command: str, tail: str = CLI) -> subprocess.CompletedProcess:
    """The CLI with these arguments, guarded; or, given a tail, that code in its place."""
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("ANTHROPIC_", "CLAUDE_", "CODEX_", "GEMINI_"))}
    env.update(PYTHONPATH=str(SOURCE), HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
               LOCALAPPDATA=str(home / "localappdata"), XDG_CONFIG_HOME=str(home / "xdg"),
               CLAUDE_CONFIG_DIR=str(home / "claude"), TABMANCER_CONFIG_DIR=str(home / "config"),
               CODEX_HOME=str(home / "codex"), TABMANCER_SHORTCUT_DIR=str(home / "shortcuts"),
               TABMANCER_UPDATE_CHECK="0", TABMANCER_CONTEXT_CAP="auto", COLUMNS="120",
               PATH=os.path.dirname(sys.executable))  # no claude, agy or codex to find
    given = {"BLOCKED": BLOCKED, "HOME": str(home), "REAL_HOME": str(REAL_HOME)}
    code = "".join(f"{name} = {value!r}\n" for name, value in given.items()) + GUARD + tail
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
    "new help": (["new", "--help"], ["--prompt", "--cli"]),
    "cleanup list": (["cleanup", "--list"], ["sessions use", "pcb-router"]),
    "cleanup older": (["cleanup", "--older", "7"], ["Would move", "add --yes"]),  # says, moves nothing
    "cleanup json": (["cleanup", "--json"], ['"session_id"']),
    "cleanup, no screen": (["cleanup"], ["sessions use", "--older DAYS"]),  # no Textual: the list
    "wrapped": (["wrapped", "--out", "wrapped.png"], ["saved as wrapped.png"]),  # in the made-up home
    "startup status": (["startup", "status"], ["Off"]),
    "agy-plugin status": (["agy-plugin", "status"], ["not installed"]),
    "mcp help": (["mcp", "--help"], ["--team", "--member"]),
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


def test_doctor_checks_without_textual_or_qt(home):
    """With no claude on PATH it finds a fault (exit 1) and says so, and checks the rest: the
    transcripts, and how Bit is drawn (bitart.way_of, not the mascot's widget)."""
    done = run(home, "doctor")
    assert "blocked:" not in done.stderr and "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 1 and "LOADED []" in done.stderr
    assert "Claude Code CLI (claude) is not on PATH" in done.stdout
    assert "Endings readable" in done.stdout and "Bit" in done.stdout


def test_new_builds_its_launches_without_textual_or_qt(home, tmp_path):
    """`tabmancer new` up to the tabs it would open: the form's rules (newforms) and the launches,
    with open_tabs stood in for, so nothing is started."""
    folder = tmp_path / "shopfront"
    folder.mkdir()
    stand_in = ("from tabmancer import cli\n"
                "cli.open_tabs = lambda launches: print('WOULD OPEN', [launch.title for launch in launches]) or []\n")
    done = run(home, "new", str(folder), "--prompt", "hi", tail=stand_in + CLI)
    assert "blocked:" not in done.stderr and "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0 and "LOADED []" in done.stderr, done.stderr[-3000:]
    assert "WOULD OPEN ['shopfront: hi']" in done.stdout


def test_no_command_without_textual_or_qt_says_how_to_get_the_window(home):
    """A core install's `tabmancer`: no terminal dashboard and no Qt, so the install line and the
    commands, and exit 0."""
    done = run(home)
    assert "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0 and "LOADED []" in done.stderr, done.stderr[-3000:]
    assert 'uv tool install "tabmancer[desktop]"' in done.stdout
    assert "cleanup" in done.stdout and "doctor" in done.stdout  # the command list


WINDOWS = {  # what opened the terminal dashboard, and the desktop window it opens now
    "no command": ([], "WINDOW False '' False 14 False '' ''"),
    "--days --all": (["--days", "3", "--all"], "WINDOW False '' False 3.0 True '' ''"),  # listing as the terminal's
    "--tabs-window": (["--tabs-window", "mine"], "WINDOW False '' False 14 False 'mine' ''"),  # its tabs go there
    "--demo": (["--demo"], "WINDOW False '' True 14 False '' ''"),  # tabmancer desktop --demo
    "--demo --days": (["--demo", "--days", "2"], "WINDOW False '' True 2.0 False '' ''"),
    "live": (["live"], "WINDOW False '' False 14 False '' 'live'"),  # on its Live view
    "live --days --all": (["live", "--days", "2", "--all"], "WINDOW False '' False 2.0 True '' 'live'"),
    "team watch": (["team", "watch", "shop"], "WINDOW True 'shop' False 14 False '' ''"),  # desktop --monitor shop
    "team watch, no team": (["team", "watch"], "WINDOW True '' False 14 False '' ''"),  # the window asks which
}
DEPRECATED = {"live": "tabmancer live is now the window's Live view (l); tabmancer --terminal live",
              "team": "tabmancer team watch is now tabmancer desktop --monitor; tabmancer --terminal team watch"}


@pytest.mark.parametrize("name", list(WINDOWS))
def test_the_desktop_window_opens_instead(home, name):
    """With Qt there (stood in for: this test's Python has none), each is the window, by
    `tabmancer desktop`'s own path, not a copy of it. live and team watch say so, on stderr."""
    command, said = WINDOWS[name]
    stand_in = ("from tabmancer import cli, desktop\n"
                "desktop.available = lambda: True\n"
                "cli.desktop_window = lambda monitor, name, demo=False, days=14, include_all=False, tabs_window='', "
                "view='': print('WINDOW', monitor, repr(name), demo, days, include_all, repr(tabs_window), "
                "repr(view)) or 0\n")
    done = run(home, *command, tail=stand_in + CLI)
    assert "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0 and "LOADED []" in done.stderr, done.stderr[-3000:]
    assert said in done.stdout and "uv tool install" not in done.stdout
    word = DEPRECATED.get(command[0] if command else "", "")
    assert (word in done.stderr) if word else " is now " not in done.stderr, done.stderr[-3000:]


@pytest.mark.parametrize(("command", "what"), [(["--demo"], "The demo"),
                                               (["team", "watch", "shop"], "A team's monitor")])
def test_without_textual_or_qt_they_say_how_to_get_the_window(home, command, what):
    done = run(home, *command)
    assert "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0 and "LOADED []" in done.stderr, done.stderr[-3000:]
    assert f'{what} is a desktop window, which needs Qt: uv tool install "tabmancer[desktop]"' in done.stdout
    assert ("doctor" in done.stdout) == (command == ["--demo"])  # the command list, as bare tabmancer says it
    assert "The terminal one runs as before" not in done.stdout  # nothing to offer without Textual


WATCHED = {  # team watch with neither Textual nor Qt: what `team who` says, then the hint; exit code
    "named": (["shop"], ["shop-lead", "· claude ·", "· lead"], 0),
    "named, any case": (["SHOP"], ["shop-lead"], 0),
    "none named, none at work": ([], ["Teams: shop. `tabmancer team who TEAM` lists one's members."], 0),
}


@pytest.mark.parametrize("name", list(WATCHED))
def test_team_watch_without_textual_or_qt_says_who_is_in_the_team(home, name):
    given, words, code = WATCHED[name]
    done = run(home, "team", "watch", *given)
    assert "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == code and "LOADED []" in done.stderr, done.stderr[-3000:]
    for word in words:
        assert word in done.stdout, done.stdout[-3000:]
    hint = "A team's monitor is a desktop window, which needs Qt"
    assert done.stdout.rstrip().endswith(f'{hint}: uv tool install "tabmancer[desktop]"'), done.stdout[-3000:]


def test_team_watch_without_textual_or_qt_says_there_is_no_such_team(home):
    done = run(home, "team", "watch", "docs")
    assert "the test " not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 1 and "There is no team docs. Teams: shop." in done.stderr
    assert "A team's monitor is a desktop window, which needs Qt" in done.stdout


def test_live_without_qt_says_it_is_the_windows_live_view_and_how_to_get_it(home):
    done = run(home, "live")
    assert "the test " not in done.stderr and "Traceback" not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 1 and "LOADED []" in done.stderr  # it could not do what it was asked
    assert DEPRECATED["live"] in done.stderr
    assert 'The dashboard is a desktop window, which needs Qt: uv tool install "tabmancer[desktop]"' in done.stdout


TERMINAL = {  # --terminal without Textual: what it says on stderr, and its exit code
    "alone": ([], "The terminal dashboard is not installed (no Textual)", 1),
    "--demo": (["--demo"], "The terminal dashboard is not installed (no Textual)", 1),
    "live": (["live"], "The terminal dashboard is not installed (no Textual)", 1),
    "team watch": (["team", "watch", "shop"], "The terminal monitor is not installed (no Textual)", 1),
    "with list": (["list"], "--terminal is the terminal dashboard: alone, with --demo, live or team watch", 2),
}


@pytest.mark.parametrize("name", list(TERMINAL))
def test_terminal_without_textual_says_it_is_not_installed(home, name):
    given, said, code = TERMINAL[name]
    done = run(home, "--terminal", *given)
    assert "the test " not in done.stderr and "Traceback" not in done.stderr, done.stderr[-3000:]
    assert done.returncode == code and said in done.stderr, done.stderr[-3000:]
    assert "LOADED []" in done.stderr


def test_wrapped_saves_its_picture_without_textual_or_qt(home, tmp_path):
    out = tmp_path / "week.png"
    done = run(home, "wrapped", "--out", str(out))
    assert done.returncode == 0, done.stderr[-3000:]
    assert "LOADED []" in done.stderr
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_guard_catches_textual_and_a_real_file(home):
    """The guard itself: what needs Textual is stopped (the terminal dashboard, Textual's on
    purpose), and so is reading the real ~/.claude/.credentials.json or starting a program."""
    assert "blocked: textual" in run(home, tail="from tabmancer.app import SessionPicker").stderr
    for code, said in ((f"open({str(REAL_HOME / '.claude' / '.credentials.json')!r})", "opened a real file"),
                       ("import subprocess; subprocess.run(['claude', '--version'])", "started a program")):
        assert f"the test {said}" in run(home, tail=code).stderr


# What the CLI's commands and the dashboard's screens share, lifted out of the screens: each loads,
# and does a little, with neither Textual nor Qt.
LIFTED = """
from tabmancer import errorlog, pixelcharts, statstext

palette, rows = pixelcharts.draw([1.0, 3.0, 2.0], 30, 12, (1, 2, 3), (200, 100, 0), (9, 9, 9))
assert len(rows) == 12 and len(palette) > 2
assert pixelcharts.draw_week([[0.0] * 24, [1.0] * 24], (8, 16), 24 * 3 * 8, 2 * 16, (0, 0, 0), (9, 9, 9),
                             (5, 5, 5))[1]
assert errorlog.log_error("headless", ValueError("made up")).read_text(encoding="utf-8").count("made up") == 1
assert statstext.bar(5, 10, 10) and statstext.TABS[0] == "overview"
from tabmancer import bgjobs, diskclean
assert bgjobs.jobs() == [] and bgjobs.ago(None) == ""
try:
    bgjobs.stop("b1")  # no claude on PATH: said, and nothing started
except OSError as error:
    assert "not on PATH" in str(error)
else:
    raise AssertionError("bgjobs.stop ran without the CLI")
items = diskclean.collect()
assert items and all(item.size > 0 and item.files for item in items)
assert diskclean.human(2048) == "2 KB"
from tabmancer import bitart, clis
assert bitart.way_of("calm") == "calm" and bitart.way_of(False) == "away"
words = "".join(text for text, _ in clis.option_parts("agy", True) + clis.main_parts(clis.next_main("claude")))
assert words.startswith("[x] Antigravity") and "Main CLI: Antigravity" in words and "never Claude" in words
print("LIFTED ok")
"""


DEMO = """
from tabmancer import bgjobs, demo, sessions
live = demo.prepare(home / "made-up")
told = []
replaced = demo.stand_ins(home / "made-up", live, told.append)
assert not any(module.__name__ == "tabmancer.app" for module, _, _ in replaced)  # no dashboard to stand in for
for module, name, value in replaced:
    setattr(module, name, value)
assert sessions._agents_json() == live
try:
    bgjobs.stop("b1")  # the demo's own refusal, not the real CLI
except OSError:
    pass
print("DEMO ok", len(replaced), told)
print("LOADED", sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED), file=sys.stderr)
"""


def test_the_demo_stands_in_without_textual_or_qt(home):
    """The desktop window's demo on a core install: the made-up home and every stand-in, with no
    terminal dashboard whose copies would need standing in for, and nothing real reached."""
    done = run(home, tail=DEMO)
    assert "the test " not in done.stderr and "blocked:" not in done.stderr, done.stderr[-3000:]
    assert done.returncode == 0 and "LOADED []" in done.stderr, done.stderr[-3000:]
    assert "DEMO ok" in done.stdout


def test_the_lifted_modules_work_without_textual_or_qt(home):
    done = run(home, tail=LIFTED)
    assert done.returncode == 0, done.stderr[-3000:]
    assert "LIFTED ok" in done.stdout
