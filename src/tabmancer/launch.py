"""Open sessions in new terminal tabs."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import read_json
from .sessions import Status

DEFAULT_CONTINUE_PROMPT = (
    "Continue where you left off. This session was interrupted (the computer shut down or the "
    "terminal closed), so first check what state the work was left in.")
# What stopped it, said as it was: the default above would tell Claude the machine shut down.
CONTINUE_BECAUSE = {
    Status.LIMIT: ("Continue where you left off. This session stopped at a usage limit, so first "
                   "check what state the work was left in."),
    Status.DROPPED: ("Continue where you left off. The connection to the API dropped in the middle "
                     "of this session, so first check what state the work was left in."),
}
# A running Claude Code sets these for its child processes. A tab that inherited them
# would take itself for a subagent of that session and not save its transcript.
SESSION_MARKERS = {
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SSE_PORT",
}


class Mode(str, Enum):
    RESUME = "resume"          # pick up the same session
    AUTO = "auto-continue"     # resume and tell Claude to carry on with the interrupted work
    FORK = "fork"              # new session that starts from a copy of this one's history
    NEW = "new"                # fresh session in the same folder
    CONTINUE = "continue"      # the folder's most recent session, whichever it is
    ATTACH = "attach"          # a background session; session_id holds its short id


def default_mode(status: Status) -> Mode:
    """Sessions that stopped in the middle of something carry on by themselves."""
    interrupted = (Status.CUT_OFF, Status.UNANSWERED, Status.ERROR, Status.LIMIT, Status.DROPPED)
    return Mode.AUTO if status in interrupted else Mode.RESUME


def continue_prompt(status: Status | None = None) -> str:
    """What auto-continue sends: TABMANCER_CONTINUE_PROMPT, the saved one, or the default for
    what stopped the session."""
    saved = read_json("config.json")
    stored = saved.get("continue_prompt") if isinstance(saved, dict) else None
    return (os.environ.get("TABMANCER_CONTINUE_PROMPT") or stored
            or CONTINUE_BECAUSE.get(status, DEFAULT_CONTINUE_PROMPT))


def clean_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() not in SESSION_MARKERS}


@dataclass
class Launch:
    title: str
    cwd: str
    mode: Mode
    session_id: str
    prompt: str | None = None  # sent as soon as the session opens
    cli: str = "claude"        # see clis.CLIS
    options: tuple[str, ...] = ()  # more Claude Code flags, e.g. a team member's name and role
    model: str = ""   # --model for Claude Code; empty: your default (see picks)
    effort: str = ""  # --effort for Claude Code; empty: your default
    new_id: str = ""  # a fresh session's id, given by tabmancer (--session-id) so its pick is kept

    @property
    def argv(self) -> list[str]:
        if self.cli == "codex":
            return self._codex_argv()
        if self.cli == "agy":
            return self._agy_argv()
        more = [*(["--model", self.model] if self.model else []), *(["--effort", self.effort] if self.effort else []),
                *self.options]
        if self.mode is Mode.NEW and self.new_id:
            more = ["--session-id", self.new_id, *more]
        if self.mode is Mode.RESUME:
            return ["claude", "--resume", self.session_id, *more, *([self.prompt] if self.prompt else [])]
        if self.mode is Mode.AUTO:
            return ["claude", "--resume", self.session_id, *more, self.prompt or continue_prompt()]
        if self.mode is Mode.FORK:
            return ["claude", "--resume", self.session_id, "--fork-session", *more]
        if self.mode is Mode.CONTINUE:
            return ["claude", "--continue", *more]
        if self.mode is Mode.ATTACH:
            return ["claude", "attach", self.session_id]
        return ["claude", *more, *([self.prompt] if self.prompt else [])]

    def _codex_argv(self) -> list[str]:
        prompt = [self.prompt] if self.prompt else []
        if self.mode is Mode.RESUME:
            return ["codex", "resume", self.session_id, *prompt]
        if self.mode is Mode.AUTO:
            return ["codex", "resume", self.session_id, self.prompt or continue_prompt()]
        if self.mode is Mode.FORK:
            return ["codex", "fork", self.session_id]
        if self.mode is Mode.CONTINUE:
            return ["codex", "resume", "--last"]  # the most recent session in this folder
        return ["codex", *prompt]

    def _agy_argv(self) -> list[str]:
        # -i sends a first prompt and keeps the session open; -p would print and exit.
        prompt = ["-i", self.prompt] if self.prompt else []
        if self.mode in (Mode.RESUME, Mode.FORK):  # agy cannot fork: the session itself
            return ["agy", "--conversation", self.session_id, *prompt]
        if self.mode is Mode.AUTO:
            return ["agy", "--conversation", self.session_id, "-i", self.prompt or continue_prompt()]
        if self.mode is Mode.CONTINUE:
            return ["agy", "--continue"]
        return ["agy", *prompt]

    @property
    def directory(self) -> str:
        return self.cwd if self.cwd and Path(self.cwd).is_dir() else str(Path.home())


def run_in_background(launch: Launch) -> str:
    """Continue a session as a background session: `claude --bg --resume <id> <prompt>`.

    Returns Claude's one-line confirmation (it names the short id to attach to).
    Raises OSError if the CLI fails.
    """
    claude = shutil.which("claude")
    if not claude:
        raise OSError("The claude CLI is not on PATH.")
    try:
        result = subprocess.run(
            [claude, "--bg", *launch.argv[1:]], cwd=launch.directory, env=clean_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.SubprocessError as error:
        raise OSError(str(error)) from error
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or not lines:
        raise OSError(result.stderr.strip()[:300] or "claude --bg did not start the session")
    return lines[0]


def _wt_escape(value: str) -> str:
    # wt.exe splits its command line on ';' even inside arguments.
    return value.replace(";", r"\;")


def _ps_quote(arg: str) -> str:
    """Quote one argument for a PowerShell command line.

    PowerShell ends a '...' string at the curly single quotes as well, so those are doubled
    too. A % goes in as [char]37, because Windows Terminal expands %NAME% from the environment.
    """
    if re.fullmatch(r"[\w\-./:=]+", arg):
        return arg
    parts = ["'" + re.sub(r"(['‘’‚‛])", r"\1\1", part) + "'" for part in arg.split("%")]
    return parts[0] if len(parts) == 1 else "(" + "+[char]37+".join(parts) + ")"


def _escape_quotes(command: str) -> str:
    """Windows Terminal hands the command to PowerShell inside double quotes of its own, which a
    plain " would end: each goes as \\", with the backslashes before it doubled."""
    return re.sub(r'(\\*)"', lambda m: m.group(1) * 2 + '\\"', command)


def _for_windows_powershell(arg: str) -> str:
    """Windows PowerShell 5.1 hands an argument to claude inside double quotes when it has a space,
    escaping nothing: a " in it travels as a single quote, and backslashes at its end are doubled,
    or the last one would escape the closing quote. PowerShell 7 passes arguments on intact."""
    arg = arg.replace('"', "'")
    return re.sub(r"(\\+)$", r"\1\1", arg) if re.search(r"\s", arg) else arg


def windows_terminal_command(launches: list[Launch], window: str) -> list[str]:
    pwsh = shutil.which("pwsh")
    shell = "pwsh.exe" if pwsh else "powershell.exe"
    args = ["wt.exe", "-w", window]
    for launch in launches:
        if len(args) > 3:
            args.append(";")
        argv = launch.argv if pwsh else [_for_windows_powershell(arg) for arg in launch.argv]
        args += [
            "new-tab",
            "--title", _wt_escape(launch.title),
            "-d", _wt_escape(launch.directory),
            # -NoExit keeps the tab as a shell in that folder after Claude exits.
            shell, "-NoLogo", "-NoExit", "-Command",
            _wt_escape(_escape_quotes(" ".join(_ps_quote(arg) for arg in argv))),
        ]
    return args


def shell_line(launch: Launch) -> str:
    return f"cd {shlex.quote(launch.directory)} && {shlex.join(launch.argv)}"


def _keep_open(launch: Launch) -> list[str]:
    """POSIX: run Claude, then the user's shell, so the tab stays open in that folder."""
    return ["sh", "-c", f'{shlex.join(launch.argv)}; exec "${{SHELL:-sh}}"']


def tmux_commands(launches: list[Launch]) -> list[list[str]]:
    return [["tmux", "new-window", "-c", launch.directory, "-n", launch.title[:30],
             shlex.join(launch.argv)] for launch in launches]


def wezterm_commands(launches: list[Launch]) -> list[list[str]]:
    return [["wezterm", "cli", "spawn", "--cwd", launch.directory, "--", *_keep_open(launch)]
            for launch in launches]


def kitty_commands(launches: list[Launch]) -> list[list[str]]:
    # Needs allow_remote_control in kitty.conf; without it the commands fail and are printed.
    return [["kitty", "@", "launch", "--type=tab", f"--tab-title={launch.title}",
             f"--cwd={launch.directory}", *_keep_open(launch)] for launch in launches]


def gnome_terminal_commands(launches: list[Launch]) -> list[list[str]]:
    return [["gnome-terminal", "--tab", f"--title={launch.title}", f"--working-directory={launch.directory}",
             "--", *_keep_open(launch)] for launch in launches]


def konsole_commands(launches: list[Launch]) -> list[list[str]]:
    return [["konsole", "--new-tab", "--workdir", launch.directory, "-e", *_keep_open(launch)]
            for launch in launches]


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def iterm_script(launches: list[Launch]) -> str:
    lines = ['tell application "iTerm2"', "activate",
             "if (count of windows) = 0 then create window with default profile", "tell current window"]
    for launch in launches:
        lines += ["set newTab to (create tab with default profile)",
                  f"tell current session of newTab to write text {_applescript_string(shell_line(launch))}"]
    return "\n".join([*lines, "end tell", "end tell"])


def apple_terminal_script(launches: list[Launch]) -> str:
    # Terminal.app has no scriptable "new tab" without accessibility rights: one window each.
    lines = ['tell application "Terminal"', "activate"]
    lines += [f"do script {_applescript_string(shell_line(launch))}" for launch in launches]
    return "\n".join([*lines, "end tell"])


def terminal_kind() -> str:
    env = os.environ
    if sys.platform == "win32" and shutil.which("wt"):
        return "windows-terminal"
    if env.get("TMUX") and shutil.which("tmux"):
        return "tmux"
    if env.get("WEZTERM_PANE") and shutil.which("wezterm"):
        return "wezterm"
    if env.get("KITTY_WINDOW_ID") and shutil.which("kitty"):
        return "kitty"
    if env.get("TERM_PROGRAM") == "iTerm.app":
        return "iterm"
    if env.get("TERM_PROGRAM") == "Apple_Terminal":
        return "apple-terminal"
    if (env.get("GNOME_TERMINAL_SCREEN") or env.get("GNOME_TERMINAL_SERVICE")) and shutil.which("gnome-terminal"):
        return "gnome-terminal"
    if env.get("KONSOLE_VERSION") and shutil.which("konsole"):
        return "konsole"
    return "none"


COMMAND_BUILDERS = {
    "tmux": tmux_commands,
    "wezterm": wezterm_commands,
    "kitty": kitty_commands,
    "gnome-terminal": gnome_terminal_commands,
    "konsole": konsole_commands,
}
SCRIPT_BUILDERS = {"iterm": iterm_script, "apple-terminal": apple_terminal_script}


def open_tabs(launches: list[Launch]) -> list[str]:
    """Open each launch in its own tab. Returns the commands left for the user to run.

    Inside Windows Terminal the tabs join the current window, otherwise a new one.
    Anything that could not be opened comes back as a shell line to run by hand.
    """
    kind = terminal_kind()
    if kind == "windows-terminal":
        window = "0" if os.environ.get("WT_SESSION") else "new"
        subprocess.Popen(windows_terminal_command(launches, window), env=clean_env())
        return []
    if kind in COMMAND_BUILDERS:
        failed = []
        for launch, command in zip(launches, COMMAND_BUILDERS[kind](launches)):
            try:
                ok = subprocess.run(command, check=False, capture_output=True, env=clean_env()).returncode == 0
            except OSError:
                ok = False
            if not ok:
                failed.append(launch)
        return [shell_line(launch) for launch in failed]
    if kind in SCRIPT_BUILDERS:
        try:
            result = subprocess.run(["osascript", "-e", SCRIPT_BUILDERS[kind](launches)], env=clean_env(),
                                    check=False, capture_output=True)
            if result.returncode == 0:
                return []
        except OSError:
            pass
    return [shell_line(launch) for launch in launches]
