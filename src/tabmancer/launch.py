"""Open sessions in new terminal tabs."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import claudeupdate
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
    cap: str = ""     # --autocompact for Claude Code, such as 300k; empty or "auto": Claude Code's own
    advisor: str = ""  # --advisor for Claude Code (picks.ADVISORS); empty: none
    new_id: str = ""  # a fresh session's id, given by tabmancer (--session-id) so its pick is kept
    env: tuple[tuple[str, str], ...] = ()  # set in the tab for this command, e.g. a sign-in's BROWSER
    wrapper: tuple[str, ...] = ()  # optional prefix command (e.g. tabmancer team run <team> <member> --)

    @property
    def posix_argv(self) -> list[str]:
        """argv for a POSIX shell, with env's variables before it."""
        return ["env", *(f"{name}={value}" for name, value in self.env), *self.argv] if self.env else self.argv

    @property
    def argv(self) -> list[str]:
        if self.cli == "codex":
            inner = self._codex_argv()
        elif self.cli == "agy":
            inner = self._agy_argv()
        else:
            inner = self._claude_argv()
        return [*self.wrapper, *inner] if self.wrapper else inner

    def _claude_argv(self) -> list[str]:
        capped = self.cap and self.cap != "auto" and "--autocompact" not in self.options
        more = [*(["--model", self.model] if self.model else []), *(["--effort", self.effort] if self.effort else []),
                *(["--autocompact", self.cap] if capped else []),
                *(["--advisor", self.advisor] if self.advisor and "--advisor" not in self.options else []),
                *self.options]
        if self.mode is Mode.NEW and self.new_id:
            more = ["--session-id", self.new_id, *more]
        if self.mode is Mode.RESUME:
            return ["claude", "--resume", self.session_id, *more, *_said(self.prompt)]
        if self.mode is Mode.AUTO:
            return ["claude", "--resume", self.session_id, *more, *_said(self.prompt or continue_prompt())]
        if self.mode is Mode.FORK:
            return ["claude", "--resume", self.session_id, "--fork-session", *more]
        if self.mode is Mode.CONTINUE:
            return ["claude", "--continue", *more]
        if self.mode is Mode.ATTACH:
            return ["claude", "attach", self.session_id]
        return ["claude", *more, *_said(self.prompt)]

    def _codex_argv(self) -> list[str]:
        # options: a team member's -c overrides (its MCP server, for this session only), before the
        # session and the prompt, after the subcommand that takes them.
        prompt, more = _said(self.prompt), list(self.options)
        if self.mode is Mode.RESUME:
            return ["codex", "resume", *more, self.session_id, *prompt]
        if self.mode is Mode.AUTO:
            return ["codex", "resume", *more, self.session_id, *_said(self.prompt or continue_prompt())]
        if self.mode is Mode.FORK:
            return ["codex", "fork", *more, self.session_id]
        if self.mode is Mode.CONTINUE:
            return ["codex", "resume", *more, "--last"]  # the most recent session in this folder
        return ["codex", *more, *prompt]

    def _agy_argv(self) -> list[str]:
        # -i sends a first prompt and keeps the session open; -p would print and exit.
        from . import agy

        # Never a Claude or GPT model through agy, on any path: a --model in the options too, and a
        # resume of a conversation that ran on one (agy.model_flags). No --effort: agy's model ids
        # carry theirs.
        resumes = self.mode in (Mode.RESUME, Mode.FORK, Mode.AUTO)
        model, options, _ = agy.model_flags(self.model, self.options, self.session_id if resumes else "")
        more = [*model, *options]
        prompt = ["-i", self.prompt] if self.prompt else []
        if self.mode in (Mode.RESUME, Mode.FORK):  # agy cannot fork: the session itself
            return ["agy", "--conversation", self.session_id, *more, *prompt]
        if self.mode is Mode.AUTO:
            return ["agy", "--conversation", self.session_id, *more, "-i", self.prompt or continue_prompt()]
        if self.mode is Mode.CONTINUE:
            return ["agy", "--continue", *more]
        return ["agy", *more, *prompt]

    @property
    def directory(self) -> str:
        return self.cwd if self.cwd and Path(self.cwd).is_dir() else str(Path.home())


def _said(prompt: str | None) -> list[str]:
    """A first message as the last arguments: after a '--' when it begins with a dash, or claude took
    it for an option ("error: unknown option '- fix the login'", a list's line written as a bullet)."""
    if not prompt:
        return []
    return ["--", prompt] if prompt.startswith("-") else [prompt]


def _for_cmd(arg: str) -> str:
    """An argument for a claude.cmd (npm's shim), quoted for cmd.exe, which runs it and reads the
    line before claude does. Always in quotes, where & | < > ^ ( ) are plain text (a word with no
    space went bare, and `tests&calc` ran calc). cmd.exe takes no escape for a ", so it travels as a
    single quote, as for Windows PowerShell 5.1; a line break would end the command there, so lines
    go joined by a space. A % goes as %%cd:~,% (an empty piece of %cd%), or %NAME% in a message read
    as the variable's value. Backslashes before the closing quote are doubled for claude's own
    reading of the line, or the last would escape the quote."""
    arg = re.sub(r"\r\n|\r|\n", " ", arg.replace('"', "'"))
    arg = re.sub(r"(\\+)$", r"\1\1", arg)
    return '"' + arg.replace("%", "%%cd:~,%") + '"'


def run_in_background(launch: Launch) -> str:
    """Continue a session as a background session: `claude --bg --resume <id> <prompt>`.

    Returns Claude's one-line confirmation (it names the short id to attach to).
    Raises OSError if the CLI fails.
    """
    claude = shutil.which("claude")
    if not claude:
        raise OSError("The claude CLI is not on PATH.")
    claudeupdate.before_opening()  # today's claude update first, when it is on
    _note([launch])
    command: str | list[str] = [claude, "--bg", *launch.argv[1:]]
    if Path(claude).suffix.lower() in (".cmd", ".bat"):  # the line goes to cmd.exe as written here
        command = subprocess.list2cmdline([claude]) + " " + " ".join(_for_cmd(arg) for arg in command[1:])
    try:
        result = subprocess.run(
            command, cwd=launch.directory, env={**clean_env(), **dict(launch.env)},
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
    A bare -- is quoted: PowerShell takes it as the end of a script's parameters and drops it, and
    npm's claude.ps1 is a script, so a message after it beginning with a dash read as an option.
    """
    if arg != "--" and re.fullmatch(r"[\w\-./:=]+", arg):
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


ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _ps_string(value: str) -> str:
    """A value as a PowerShell string, always quoted: after `$env:NAME=` a bare word is taken for a
    command (`$env:X=edge:Default` ran "edge:Default" and set nothing), where _ps_quote leaves one
    bare as an argument."""
    quoted = _ps_quote(value)
    return quoted if quoted != value else f"'{value}'"


def _ps_env(env: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """PowerShell lines setting env's variables before the command, and putting back what they were
    after it: the tab stays open as a shell, and a claude run there later would still open its pages
    with the sign-in's BROWSER (what `env NAME=value command` does for one command on POSIX)."""
    for name, _ in env:
        if not ENV_NAME.fullmatch(name):
            raise ValueError(f"not a variable name: {name!r}")
    before = "".join(f"$tabmancer_{i}=$env:{name}; $env:{name}={_ps_string(value)}; "
                     for i, (name, value) in enumerate(env))
    after = "".join(f"; $env:{name}=$tabmancer_{i}" for i, (name, _) in enumerate(env))  # $null: unset again
    return before, after


def _ps_command(argv: list[str], env: tuple[tuple[str, str], ...]) -> str:
    before, after = _ps_env(env)
    return before + " ".join(_ps_quote(arg) for arg in argv) + after


def windows_terminal_command(launches: list[Launch], window: str) -> list[str]:
    pwsh = shutil.which("pwsh")
    shell = "pwsh.exe" if pwsh else "powershell.exe"
    args = ["wt.exe", "-w", window]
    for launch in launches:
        if len(args) > 3:
            args.append(";")
        argv = launch.argv if pwsh else [_for_windows_powershell(arg) for arg in launch.argv]
        command = _ps_command(argv, launch.env)
        args += [
            "new-tab",
            "--title", _wt_escape(launch.title),
            "-d", _wt_escape(launch.directory),
            # -NoExit keeps the tab as a shell in that folder after Claude exits.
            shell, "-NoLogo", "-NoExit", "-Command",
            _wt_escape(_escape_quotes(command)),
        ]
    return args


# Characters in one wt.exe command line. Windows takes 32767 at most: past that wt.exe never starts,
# so many tabs, or tabs with long first messages, go in several command lines.
WT_MOST = 30000


def windows_terminal_commands(launches: list[Launch], window: str) -> list[list[str]]:
    """wt.exe command lines for the launches, each short enough to run, in as few as that allows. When
    there is more than one, a new window is given a name of its own, so that they all go in it."""
    batches: list[list[Launch]] = []
    for launch in launches:
        if batches and len(subprocess.list2cmdline(windows_terminal_command([*batches[-1], launch], window))) <= WT_MOST:
            batches[-1].append(launch)
        else:
            batches.append([launch])
    if len(batches) > 1 and window == "new":
        window = f"tabmancer-{os.getpid()}-{len(launches)}"
    return [windows_terminal_command(batch, window) for batch in batches]


def shell_line(launch: Launch) -> str:
    """A line to run by hand where no tab could open: for PowerShell on Windows when it sets variables
    (`env NAME=value` is no command there), for a POSIX shell otherwise."""
    if sys.platform == "win32" and launch.env:
        return f"Set-Location -LiteralPath {_ps_string(launch.directory)}; {_ps_command(launch.argv, launch.env)}"
    return f"cd {shlex.quote(launch.directory)} && {shlex.join(launch.posix_argv)}"


def _keep_open(launch: Launch) -> list[str]:
    """POSIX: run Claude, then the user's shell, so the tab stays open in that folder."""
    return ["sh", "-c", f'{shlex.join(launch.posix_argv)}; exec "${{SHELL:-sh}}"']


def tmux_commands(launches: list[Launch]) -> list[list[str]]:
    return [["tmux", "new-window", "-c", launch.directory, "-n", launch.title[:30],
             shlex.join(launch.posix_argv)] for launch in launches]


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


# A front with no terminal around it (the desktop window), while it runs (gui_tabs): the Windows Terminal window
# its tabs go to by name, and that it is one (off Windows, a terminal found on the machine opens them).
_gui_window: str | None = None
_gui = False


@contextmanager
def gui_tabs(window: str) -> Iterator[None]:
    """While the desktop window runs. Started from a shortcut, the tray or at login it has no WT_SESSION, so its
    tabs would each open a new Windows Terminal window: they go into the one named window instead (window, as
    the terminal dashboard's do with --tabs-window), made by the first and found by the rest. Off Windows a
    GUI app has none of the terminals' variables (TMUX, KITTY_WINDOW_ID…): a terminal found on the machine
    opens them (gui_terminal), not shell lines to run by hand. Put back after."""
    global _gui, _gui_window
    kept = _gui, _gui_window
    _gui, _gui_window = True, window or None
    try:
        yield
    finally:
        _gui, _gui_window = kept


def gui_terminal() -> str:
    """Off Windows, for a front with no terminal around it: macOS's Terminal (always there), or on Linux with a
    display GNOME Terminal when installed (it hands each tab to its server, so starting one never waits for it
    to close); else none, and the commands are given back. Konsole, kitty and WezTerm open tabs only from inside
    themselves (their variables, read in terminal_kind)."""
    if sys.platform == "darwin":
        return "apple-terminal"
    has_display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if sys.platform != "win32" and has_display and shutil.which("gnome-terminal"):
        return "gnome-terminal"
    return "none"


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
    return gui_terminal() if _gui else "none"


COMMAND_BUILDERS = {
    "tmux": tmux_commands,
    "wezterm": wezterm_commands,
    "kitty": kitty_commands,
    "gnome-terminal": gnome_terminal_commands,
    "konsole": konsole_commands,
}
SCRIPT_BUILDERS = {"iterm": iterm_script, "apple-terminal": apple_terminal_script}


def _note(launches: list[Launch]) -> None:
    """The caps of resumed sessions kept, so their next resume has the same (picks.note_opened)."""
    from .picks import note_opened  # picks builds on this module

    try:
        note_opened(launches)
    except OSError:
        pass  # a pick not saved costs the next resume its cap, never this launch


def open_tabs(launches: list[Launch], window: str | None = None) -> list[str]:
    """Open each launch in its own tab. Returns the commands left for the user to run.

    Inside Windows Terminal the tabs join the current window (the one used last: "0"), otherwise a
    new one; window says which instead ("0", "new" or a window's name), for a caller that is not
    where the tabs belong (the team monitor, in a window of its own). The desktop window's go to its
    named one (gui_tabs) unless window says otherwise. Anything that could not be
    opened comes back as a shell line to run by hand. Raises OSError when Windows Terminal would
    not start.
    """
    if any(launch.cli == "claude" for launch in launches):
        claudeupdate.before_opening()  # today's claude update first, when it is on
    _note(launches)
    kind = terminal_kind()
    if kind == "windows-terminal":
        window = window or _gui_window or ("0" if os.environ.get("WT_SESSION") else "new")
        started = None
        for command in windows_terminal_commands(launches, window):
            if started is not None:  # in their order: a team's lead, which comes last, opens last
                try:
                    started.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            try:
                started = subprocess.Popen(command, env=clean_env())
            except OSError as error:
                raise OSError(f"Windows Terminal would not start: {error.strerror or error}") from error
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
