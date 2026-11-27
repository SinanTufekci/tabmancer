"""tabmancer's Antigravity plugin: the team bus as MCP tools and a Stop hook, for agy members.

agy reads neither a workspace's MCP config nor its hooks for a fresh folder; a plugin it has
installed (`agy plugin install <folder>`, copied to ~/.gemini/config/plugins/<name>) it does, for
every session. Installing it changes agy's global state, so it is a step of its own, never done
silently: `tabmancer agy-plugin install`, undone by `tabmancer agy-plugin remove`.

Both parts run in every agy session, team or not, and both get agy's environment: a member that
`tabmancer team run` started has TABMANCER_TEAM and TABMANCER_MEMBER, anything else has neither
and is left alone (the tools say it is not a team session; the hook does nothing).

agy asks before every MCP tool call, and a PreToolUse hook answering "allow" does not change that
(agy 1.5.15, the 2026-11-23 retest: the hook ran, its reason is in the step, the prompt came all
the same). What does is agy's own allow list, permissions.allow in
~/.gemini/antigravity-cli/settings.json: install adds mcp(tabmancer_tabmancer/<tool>) for the
team's tools (TEAM_TOOLS, never team_request), merged into what is there; remove takes away the
ones it added. They hold for every agy session, which is harmless: those tools only read and write
tabmancer's team bus, and outside a team they say so and do nothing.

The Stop hook (`python -I -m tabmancer.agyplugin hook`, after every agy turn):
- a turn that failed on quota: notes the model out until its reset (agy.note_quota_error), so the
  next pick skips it;
- a member with messages waiting: answers {"decision": "continue"} with them, so it reads them
  before it stops (at most MAX_CONTINUES times in one stop chain), and marks them read once out.
  Seen at work with agy 1.5.15 (2026-11-23): a message sent mid-turn came back answered.
- messages the member's own listen already printed are not handed over again: in agy's sandbox the
  listen cannot mark them read ([acks off]), so they were still unread, and a model could redo the
  task. The hook finds the listen's line for each ('[12:41:59 from lead]: <text>', teambus.header)
  in the member's agy transcript and marks those read instead. With no transcript to look in, it hands
  everything over and says some may have come already.
It imports only the standard library until it knows it has something to do.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

NAME = "tabmancer"
MAX_CONTINUES = 3  # stop answering "continue" once a stop chain has gone round this often
HOOK_TIMEOUT = 10  # seconds agy gives the hook before it gives up on it
# In agy's allow list: teams.MCP_ALLOWED (a test keeps them equal; this module imports nothing
# heavy). Never team_request: what a lead starts or adds is asked of you.
TEAM_TOOLS = ("team_send", "team_inbox", "team_wait", "team_who", "team_status")
# agy calls a plugin's server <plugin>_<server> (the retests' transcripts: tabmancer_tabmancer).
SERVER_NAME = f"{NAME}_{NAME}"
STDIN_WAIT = 3.0  # an event that never closes stdin does not hold the hook up
SHOWN_WITHIN = 15 * 60  # seconds: the turn that just ended wrote its member's transcript this recently
SHOWN_TEXT = 60  # characters of a message's text a transcript must hold after its header
TAG_WITHIN = 64 * 1024  # bytes: a member's first prompt, with its [tabmancer:<session>] tag, is near the top
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


def continue_reason(messages: list[dict], unsure: bool = False) -> str:
    from . import teambus

    lines = "\n".join(f"{teambus.header(m)}: {m.get('text')}" for m in messages)
    return (f"Messages from your team arrived while you worked (tabmancer team bus):\n{lines}\n"
            + ("Your listen may have shown you some of them already: do not redo what you have done. "
               if unsure else "")
            + "Handle them now: answer with the team_send tool (or your bus send command) where one is "
            "needed, then go on or wait for the next message.")


def shown_by_listen(team: str, member: str, messages: list[dict]) -> set[str] | None:
    """The ids of the messages this member's listen already printed into its agy conversation, by
    their header line in the transcripts of its conversations ([tabmancer:<team>-<member>] in the
    first prompt) written in the last SHOWN_WITHIN seconds. None when no such transcript is found:
    then nothing can be told."""
    from . import agy, teambus

    tag = f"[tabmancer:{team}-{member}]".lower().encode()
    try:
        folders = list((agy.home() / "brain").iterdir())
    except OSError:
        return None
    now, shown, found = time.time(), set(), False
    for folder in folders:
        path = agy.transcript_path(folder.name)
        try:
            if now - path.stat().st_mtime > SHOWN_WITHIN:
                continue
            text = path.read_bytes()
        except OSError:
            continue
        if tag not in text[:TAG_WITHIN].lower():
            continue
        found = True
        shown.update(m["id"] for m in messages if _printed(m, teambus.header(m)) in text)
    return shown if found else None


def _printed(message: dict, header: str) -> bytes:
    """The start of the line a listen printed for a message, as it stands in a transcript: its
    header and the text up to the first character JSON may write otherwise (at most SHOWN_TEXT).
    The header alone is not enough: two messages from one sender in one second share it."""
    text = str(message.get("text") or "")
    plain = "".join(itertools.takewhile(lambda c: " " <= c <= "~" and c not in '"\\<>&', text))
    return f"{header}: {plain[:SHOWN_TEXT]}".encode()


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
    try:
        shown = shown_by_listen(team, member, waiting)
    except Exception:  # noqa: BLE001  (a transcript it cannot read: hand everything over)
        shown = None
    if shown:  # they reached the member through its listen, which could not mark them read
        teambus.mark_delivered(team, member, sorted(shown))
        waiting = [m for m in waiting if m["id"] not in shown]
        if not waiting:
            return 0
    try:  # ASCII: the console's code page cannot spoil it on the way to agy
        sink.write(json.dumps({"decision": "continue", "reason": continue_reason(waiting, unsure=shown is None)}))
        sink.flush()
    except (OSError, ValueError):
        return 0
    teambus.mark_delivered(team, member, [m["id"] for m in waiting])  # only once it is out
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
    can import it (agy starts both outside its terminal sandbox): never pythonw, should the window
    install it (teambus.console_python)."""
    from . import __version__
    from .teambus import console_python

    python = console_python()
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
        }, indent=2),
    }


def _agy() -> str | None:
    import shutil

    return shutil.which("agy")


def _run(argv: list[str], runner=None) -> tuple[int, str]:
    """agy's plugin command: (exit code, what it said). Its output goes to a file, not a pipe: agy
    1.5.15 leaves a process behind that holds a pipe open, so reading it to the end waited out the
    60 s timeout on validate and install (to a file: 0.2 s). No stdin: nothing is asked."""
    import subprocess
    import tempfile

    runner = runner or subprocess.run
    try:
        with tempfile.TemporaryFile() as out:
            done = runner(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=60,
                          check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out.seek(0)
            said = out.read().decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as problem:
        return -1, str(problem)
    return done.returncode, said.strip()


# --- agy's allow list -----------------------------------------------------------------------------

class SettingsError(Exception):
    """agy's settings file is there but not one tabmancer can add to without rewriting it."""


def settings_file() -> Path:
    from . import agy

    return agy.home() / "settings.json"  # ~/.gemini/antigravity-cli/settings.json


def allow_entry(tool: str) -> str:
    return f"mcp({SERVER_NAME}/{tool})"


def _read_settings() -> dict:
    """agy's settings ({} when there is no file). SettingsError when they cannot be added to as they
    are: not JSON, not an object, or permissions / permissions.allow of another shape."""
    path = settings_file()
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as problem:
        raise SettingsError(f"{path} cannot be read as JSON ({problem})") from problem
    if not isinstance(settings, dict):
        raise SettingsError(f"{path} is not a JSON object")
    permissions = settings.get("permissions", {})
    if not isinstance(permissions, dict) or not isinstance(permissions.get("allow", []), list):
        raise SettingsError(f"{path}: permissions.allow is not a list")
    return settings


def _write_settings(settings: dict) -> None:
    """Through a temporary file in the same folder, then put in place: agy never reads half a file.
    As agy writes it: two-space indent, LF, a newline at the end."""
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def asked_tools() -> list[str]:
    """The team's tools agy still asks about (not in its allow list); all of them when its settings
    cannot be read."""
    try:
        allowed = _read_settings().get("permissions", {}).get("allow", [])
    except SettingsError:
        return list(TEAM_TOOLS)
    return [tool for tool in TEAM_TOOLS if allow_entry(tool) not in allowed]


def allow_tools() -> list[str]:
    """Add the team's tools that are missing to agy's allow list, after what is there, which is kept
    as it was. The entries it added (none: the file is not written). SettingsError: nothing done."""
    settings = _read_settings()
    allowed = settings.setdefault("permissions", {}).setdefault("allow", [])
    added = [allow_entry(tool) for tool in TEAM_TOOLS if allow_entry(tool) not in allowed]
    if added:
        allowed.extend(added)
        _write_settings(settings)
    return added


def disallow_tools(entries: list[str]) -> list[str]:
    """Take these of the team's entries out of agy's allow list, and nothing else. The ones taken."""
    ours = {allow_entry(tool) for tool in TEAM_TOOLS}
    if not settings_file().exists():
        return []
    settings = _read_settings()
    allowed = settings.get("permissions", {}).get("allow", [])
    going = [e for e in allowed if e in ours and e in entries]
    if going:
        settings["permissions"]["allow"] = [e for e in allowed if e not in going]
        _write_settings(settings)
    return going


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


def installed_version() -> str:
    """The version in the installed plugin's plugin.json (tabmancer's when it was installed); ''."""
    try:
        version = json.loads((installed_dir() / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return ""
    return version if isinstance(version, str) else ""


def enabled() -> bool | None:
    """agy's own switch for the plugin (~/.gemini/config/config.json, plugins.<name>.enabled, which
    `agy plugin enable` / `disable` write): None when it says nothing. Only read."""
    try:
        entry = json.loads((gemini_dir() / "config" / "config.json").read_text(encoding="utf-8"))["plugins"][NAME]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    switch = entry.get("enabled") if isinstance(entry, dict) else None
    return switch if isinstance(switch, bool) else None


def install(runner=None) -> tuple[bool, str]:
    """Write the plugin folder, let agy check it, install it, and add the team's tools to agy's
    allow list. (done, what to tell). Settings it cannot add to: nothing is done."""
    agy_exe = _agy()
    if agy_exe is None:
        return False, "agy is not on PATH: install Antigravity's CLI first."
    from .config import read_json, write_json

    try:
        _read_settings()
    except SettingsError as problem:
        return False, (f"Nothing installed: {problem}. tabmancer adds the team's tools to its permissions.allow "
                       "and will not rewrite a file it cannot read; fix it or move it away, then install again.")
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
    try:
        added = allow_tools()
    except (SettingsError, OSError) as problem:
        return False, (f"Installed agy plugin {NAME}, but its tools are not in agy's allow list ({problem}): agy "
                       f"will ask for {', '.join(asked_tools())}.")
    state = read_json(STATE_FILE) if _state_path().is_file() else {}
    state = state if isinstance(state, dict) else {}
    mine = state.get("allowed") if isinstance(state.get("allowed"), list) else []
    write_json(STATE_FILE, {**state, "allowed": [*mine, *(e for e in added if e not in mine)]})
    return True, (f"Installed agy plugin {NAME}: agy members of tabmancer teams get the team_* tools and messages "
                  f"handed over when a turn ends. {', '.join(TEAM_TOOLS)} are in agy's allow list "
                  f"({settings_file()}, permissions.allow; {len(added)} added), so agy runs them without "
                  "asking, in any session (they only touch tabmancer's team bus); team_request still asks. "
                  "Undo: tabmancer agy-plugin remove")


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
    # The allow entries install added; ones agy had before (a tab's "always allow") stay. With no
    # record (an install from before it kept one): all of the team's.
    added = before.get("allowed") if isinstance(before, dict) and isinstance(before.get("allowed"), list) else (
        [allow_entry(tool) for tool in TEAM_TOOLS])
    try:
        taken, left = disallow_tools(added), ""
    except (SettingsError, OSError) as problem:
        taken, left = [], f" Its tools stay in agy's allow list: {problem}."
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
    return True, (f"Removed agy plugin {NAME}" + (f" and {len(taken)} entries from agy's allow list." if taken
                                                   else ".") + left)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    action = args[0] if args else "status"
    if action == "hook":
        return hook()
    if action == "status":
        state = status()
        print({"installed": "installed", "outdated": "installed, but older than this tabmancer: run "
               "tabmancer agy-plugin install", "missing": "not installed: tabmancer agy-plugin install"}[state])
        asked = asked_tools()
        if state != "missing" and asked:
            print(f"agy will ask for {', '.join(asked)}: tabmancer agy-plugin install adds them to its allow list")
        return 0
    if action in ("install", "remove"):
        done, said = install() if action == "install" else remove()
        print(said, file=sys.stdout if done else sys.stderr)
        return 0 if done else 1
    print("usage: tabmancer agy-plugin install|remove|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
