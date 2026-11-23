"""tabmancer's Antigravity plugin: the team bus as MCP tools and two hooks, for agy members.

agy reads neither a workspace's MCP config nor its hooks for a fresh folder; a plugin it has
installed (`agy plugin install <folder>`, copied to ~/.gemini/config/plugins/<name>) it does, for
every session. Installing it changes agy's global state, so it is a step of its own, never done
silently: `tabmancer agy-plugin install`, undone by `tabmancer agy-plugin remove`.

Both parts run in every agy session, team or not, and both get agy's environment: a member that
`tabmancer team run` started has TABMANCER_TEAM and TABMANCER_MEMBER, anything else has neither
and is left alone (the tools say it is not a team session; the hook does nothing).

The Stop hook (`python -I -m tabmancer.agyplugin hook`, after every agy turn):
- a turn that failed on quota: notes the model out until its reset (agy.note_quota_error), so the
  next pick skips it;
- a member with messages waiting: answers {"decision": "continue"} with them, so it reads them
  before it stops (at most MAX_CONTINUES times in one stop chain), and marks them read once out.
It imports only the standard library until it knows it has something to do.

The PreToolUse hook (`... hook pretool`, before each MCP tool call): allows the team's own tools
(TEAM_TOOLS, not team_request) for a team member, so a member is not stopped at a permission
prompt nobody watches (the 2026-11-20 seam test hung there). For anything else, and on input it
cannot read, it answers an empty decision: no opinion, agy asks or allows as it would anyway.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import IO

NAME = "tabmancer"
MAX_CONTINUES = 3  # stop answering "continue" once a stop chain has gone round this often
HOOK_TIMEOUT = 10  # seconds agy gives the hook before it gives up on it
PRETOOL_TIMEOUT = 5  # the same for the PreToolUse hook, which holds up a tool call
# agy names a tool step by its type lowercased (CORTEX_STEP_TYPE_MCP_TOOL); the model's call is
# call_mcp_tool. Either way the matcher is anchored, so other tools never start the hook.
PRETOOL_MATCHER = "mcp_tool|call_mcp_tool"
# Allowed without asking for a member: teams.MCP_ALLOWED (a test keeps them equal; this module
# imports nothing heavy). Never team_request: what a lead starts or adds is asked of you.
TEAM_TOOLS = frozenset({"team_send", "team_inbox", "team_wait", "team_who", "team_status"})
SERVER_NAMES = frozenset({NAME, f"{NAME}_{NAME}"})  # agy calls a plugin's server <plugin>_<server>
STDIN_WAIT = 3.0  # an event that never closes stdin does not hold the hook up
STATE_FILE = "agy-plugin.json"  # what was there before the first install, for remove


# --- the hook: runs after every agy turn, so it starts and ends fast ------------------------------

def _read_event(stdin: IO[str]) -> dict:
    box: dict = {}

    def read() -> None:
        try:
            box["text"] = stdin.read()
        except (OSError, ValueError):
            box["text"] = ""

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(STDIN_WAIT)
    try:
        event = json.loads(box.get("text") or "{}")
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def _turns(value: object) -> int:
    """executionNum as a number. Left out or unreadable: as many as MAX_CONTINUES, so the hook sends
    no turn round (a loop it cannot count is worse than a message left for the next turn)."""
    if isinstance(value, bool):
        return MAX_CONTINUES
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return MAX_CONTINUES


def continue_reason(messages: list[dict]) -> str:
    lines = "\n".join(f"[{m['at'].strftime('%H:%M:%S')} from {m.get('from')}]: {m.get('text')}" for m in messages)
    return (f"Messages from your team arrived while you worked (tabmancer team bus):\n{lines}\n"
            "Handle them now: answer with the team_send tool (or your bus send command) where one is "
            "needed, then go on or wait for the next message.")


def hook(stdin: IO[str] | None = None, stdout: IO[str] | None = None,
         environ: dict[str, str] | None = None) -> int:
    """agy's Stop hook: see the module's docstring. Always exits 0: a failing hook only slows agy."""
    source: IO[str] = stdin if stdin is not None else sys.stdin
    sink: IO[str] = stdout if stdout is not None else sys.stdout
    environ = environ if environ is not None else dict(os.environ)
    event = _read_event(source)
    if not event:
        return 0
    if str(event.get("terminationReason") or "").upper() == "ERROR":
        error = str(event.get("error") or "")
        if error:
            try:
                from . import agy

                agy.note_quota_error(error, str(event.get("modelName") or ""))
            except Exception:  # noqa: BLE001, S110  (a hook never breaks agy's turn)
                pass
        return 0  # a failed turn is not sent round again: on quota it would only fail again
    team, member = environ.get("TABMANCER_TEAM", ""), environ.get("TABMANCER_MEMBER", "")
    if not (team and member) or _turns(event.get("executionNum")) >= MAX_CONTINUES:
        return 0
    from . import teambus  # the standard library only

    member = teambus.normalize_member(team, member)
    waiting = teambus.unread(team, member, since=teambus._read_team_file(team)[0])
    if not waiting:
        return 0
    try:  # ASCII: the console's code page cannot spoil it on the way to agy
        sink.write(json.dumps({"decision": "continue", "reason": continue_reason(waiting)}))
        sink.flush()
    except (OSError, ValueError):
        return 0
    teambus.mark_delivered(team, member, [m["id"] for m in waiting])  # only once it is out
    return 0


def _plain(value: object) -> object:
    """A tool argument as agy may send it: the value itself, or the value as JSON text."""
    if isinstance(value, str) and value[:1] in ('"', "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def tool_decision(event: dict, environ: dict[str, str]) -> str:
    """'allow' for one of the team's tools in a team member's session, else '' (no opinion)."""
    if not (environ.get("TABMANCER_TEAM") and environ.get("TABMANCER_MEMBER")):
        return ""
    call = event.get("toolCall")
    args = _plain(call.get("args")) if isinstance(call, dict) else None
    if not isinstance(args, dict):
        return ""
    server, tool = _plain(args.get("ServerName")), _plain(args.get("ToolName"))
    if not (isinstance(server, str) and isinstance(tool, str)):
        return ""
    return "allow" if server.lower() in SERVER_NAMES and tool in TEAM_TOOLS else ""


def pretool(stdin: IO[str] | None = None, stdout: IO[str] | None = None,
            environ: dict[str, str] | None = None) -> int:
    """agy's PreToolUse hook: see the module's docstring. Always answers and exits 0."""
    source: IO[str] = stdin if stdin is not None else sys.stdin
    sink: IO[str] = stdout if stdout is not None else sys.stdout
    try:
        decision = tool_decision(_read_event(source), environ if environ is not None else dict(os.environ))
    except Exception:  # noqa: BLE001  (no opinion rather than a broken tool call)
        decision = ""
    answer = {"decision": decision}
    if decision:
        answer["reason"] = "tabmancer team bus tool, allowed for team members"
    try:
        sink.write(json.dumps(answer))
        sink.flush()
    except (OSError, ValueError):
        pass
    return 0


# --- install and remove ---------------------------------------------------------------------------

def gemini_dir() -> Path:
    from . import agy

    return agy.home().parent  # ~/.gemini


def installed_dir() -> Path:
    return gemini_dir() / "config" / "plugins" / NAME


def data_dir() -> Path:
    from . import agy

    return agy.home() / "plugin_data" / NAME


def manifest_file() -> Path:
    return gemini_dir() / "config" / "import_manifest.json"  # agy makes it on the first local install


def source_dir() -> Path:
    from .config import config_dir

    return config_dir() / "agy-plugin"


def _state_path() -> Path:
    from .config import config_dir

    return config_dir() / STATE_FILE


def _cmd_word(path: str) -> str:
    """A path as cmd.exe takes it (hooks run through cmd exactly as written): always in quotes,
    where & ^ ( ) | < > are plain text, and a % as launch._for_cmd writes it, or %NAME% in a
    folder's name would be read as the variable."""
    from .launch import _for_cmd

    return _for_cmd(path)


def files() -> dict[str, str]:
    """The plugin folder's files: name -> text. The commands run tabmancer's own interpreter, which
    can import it (agy starts both outside its terminal sandbox)."""
    from . import __version__

    python = sys.executable
    return {
        "plugin.json": json.dumps({
            "name": NAME, "version": __version__,
            "description": "tabmancer's team bus for agy members of a tabmancer team: tools to message the "
                           "team, and a Stop hook that hands over messages that arrived during a turn."}, indent=2),
        "mcp_config.json": json.dumps({"mcpServers": {NAME: {
            "command": python, "args": ["-m", "tabmancer", "mcp"]}}}, indent=2),
        "hooks.json": json.dumps({
            f"{NAME}-stop": {"Stop": [{
                "type": "command", "command": f"{_cmd_word(python)} -I -m tabmancer.agyplugin hook",
                "timeout": HOOK_TIMEOUT}]},
            f"{NAME}-tools": {"PreToolUse": [{"matcher": PRETOOL_MATCHER, "hooks": [{
                "type": "command", "command": f"{_cmd_word(python)} -I -m tabmancer.agyplugin hook pretool",
                "timeout": PRETOOL_TIMEOUT}]}]},
        }, indent=2),
    }


def _agy() -> str | None:
    import shutil

    return shutil.which("agy")


def _run(argv: list[str], runner=None) -> tuple[int, str]:
    import subprocess

    runner = runner or subprocess.run
    try:
        done = runner(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                      check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as problem:
        return -1, str(problem)
    return done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()


def status() -> str:
    """'installed', 'outdated' (its files differ from what install writes now) or 'missing'."""
    folder = installed_dir()
    if not (folder / "plugin.json").is_file():
        return "missing"
    for name, text in files().items():
        try:
            if json.loads((folder / name).read_text(encoding="utf-8")) != json.loads(text):
                return "outdated"
        except (OSError, ValueError):
            return "outdated"
    return "installed"


def install(runner=None) -> tuple[bool, str]:
    """Write the plugin folder, let agy check it, install it. (done, what to tell)."""
    agy_exe = _agy()
    if agy_exe is None:
        return False, "agy is not on PATH: install Antigravity's CLI first."
    from .config import write_json

    if not _state_path().is_file():  # the first install: what agy had before it, for remove
        write_json(STATE_FILE, {"manifest": manifest_file().is_file(), "data": data_dir().is_dir()})
    folder = source_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files().items():
        (folder / name).write_text(text, encoding="utf-8")
    code, said = _run([agy_exe, "plugin", "validate", str(folder)], runner)
    if code != 0:
        return False, f"agy plugin validate failed ({code}): {said}"
    code, said = _run([agy_exe, "plugin", "install", str(folder)], runner)
    if code != 0:
        return False, f"agy plugin install failed ({code}): {said}"
    return True, (f"Installed agy plugin {NAME}: agy members of tabmancer teams get the team_* tools "
                  "(allowed without asking, but team_request) and messages handed over when a turn ends. Other agy sessions are left alone. Undo: "
                  "tabmancer agy-plugin remove")


def remove(runner=None) -> tuple[bool, str]:
    """Uninstall it and take away what installing it left behind that was not there before."""
    from .config import read_json

    agy_exe = _agy()
    if agy_exe is None:
        return False, "agy is not on PATH."
    if status() != "missing":
        code, said = _run([agy_exe, "plugin", "uninstall", NAME], runner)
        if code != 0:
            return False, f"agy plugin uninstall failed ({code}): {said}"
    before = read_json(STATE_FILE) if _state_path().is_file() else {}
    if isinstance(before, dict) and before:
        data = data_dir()
        if not before.get("data") and data.is_dir() and not any(data.iterdir()):
            data.rmdir()
        manifest = manifest_file()
        if not before.get("manifest") and manifest.is_file():
            try:
                if json.loads(manifest.read_text(encoding="utf-8")) in ({"imports": None}, {"imports": []}):
                    manifest.unlink()
            except (OSError, ValueError):
                pass
    import shutil

    shutil.rmtree(source_dir(), ignore_errors=True)
    _state_path().unlink(missing_ok=True)
    return True, f"Removed agy plugin {NAME}."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    action = args[0] if args else "status"
    if action == "hook":
        return pretool() if args[1:2] == ["pretool"] else hook()
    if action == "status":
        print({"installed": "installed", "outdated": "installed, but older than this tabmancer: run "
               "tabmancer agy-plugin install", "missing": "not installed: tabmancer agy-plugin install"}[status()])
        return 0
    if action in ("install", "remove"):
        done, said = install() if action == "install" else remove()
        print(said, file=sys.stdout if done else sys.stderr)
        return 0 if done else 1
    print("usage: tabmancer agy-plugin install|remove|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
