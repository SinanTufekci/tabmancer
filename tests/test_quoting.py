"""Whatever a prompt says, it reaches claude as one whole argument, and nothing in it runs.

A prompt can be anything: what you type, a handover a model wrote from a transcript, a team's
goal. It goes through a shell on its way to the tab (PowerShell under Windows Terminal, sh under
tmux and the other POSIX terminals), so these tests send made-up nasty prompts the same way, with
a small Python script in claude's place that writes back the arguments it got.
"""

from __future__ import annotations

import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from tabmancer import launch as L
from tabmancer.launch import Launch, Mode

ECHO = "import json, os, sys\nsys.stdout.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
NASTY = [
    "it's done; carry on",
    'say "hi" and leave',
    "ends with a backslash \\",
    "ends with two backslashes \\\\",
    "no-space-but-a-backslash\\",
    "a\ttab, then a backslash\\",
    "a\nnewline, then a backslash\\",
    "a backslash \\ in the middle, then \\\\\" and \\",
    'a quote at the end "',
    'C:\\dir\\" then C:\\other\\\\"',
    "100% done, %PATH% and %USERPROFILE%",
    "$(Write-Output pwned) `whoami` $env:USERNAME ${HOME} $((1+1))",
    "'; Write-Output pwned; '",
    "‘; Write-Output pwned; ’ ‚ ‛",
    '"; Write-Output pwned; "',
    "“smart” „quotes‟ and – — dashes",
    "line one\nline two\r\nline three",
    "--% the rest unparsed",
    "@args & | < > ^ ! && || ;; \\; a\\;b",
    "Türkçe ğüşiöç İı, emoji 🚀",
    "   spaces around   ",
    "-Command Write-Output pwned",
    "\ttabs\t",
    "`n`r`t backticks `",
    "$null; exit 3",
]
ALPHABET = list("abcXYZ019 '\"`$%;&|<>(){}[]@#^!~*?\\/,.:=+-\t\n") + list("‘’‚‛“”„ğşıİé–—🚀\u00a0")


def made_up(count: int, seed: int = 2026) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 40))) for _ in range(count)]


PROMPTS = NASTY + made_up(24)


def tab(prompt: str, folder: str, role: bool = False) -> Launch:
    """A resume with a prompt; with role, a team member's role as well, which carries a goal you wrote."""
    options = ("--name", "shop-api", "--append-system-prompt", f"Your goal: {prompt}") if role else ()
    return Launch(title=f"t {prompt[:10]}", cwd=folder, mode=Mode.RESUME, session_id="abc-123",
                  prompt=prompt, options=options)


@pytest.fixture
def echo(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text(ECHO, encoding="utf-8")
    return str(script)


def heard(output: bytes) -> dict:
    """What the script wrote back. Anything else on stdout (say "pwned") makes this fail."""
    return json.loads(output.decode("ascii"))


# --- Windows Terminal → PowerShell ---------------------------------------------------------------

def through_windows_terminal(command: str, shell: str, echo: str) -> subprocess.CompletedProcess:
    """Run the tab's -Command the way Windows Terminal hands it to PowerShell, with the echo script
    in claude's place.

    Windows Terminal splits its own command line at every ; not escaped as \\; and expands %NAME%
    from the environment. It then starts the shell with the -Command argument inside double quotes
    of its own, without escaping anything in it.
    """
    assert "%" not in command, "Windows Terminal would expand it"
    assert not re.search(r"(?<!\\);", command), "Windows Terminal would end the tab's command there"
    command = command.replace("\\;", ";")
    assert command.startswith("claude ")
    program = f"& {L._ps_quote(sys.executable)} {L._ps_quote(echo)}"
    line = f'"{shutil.which(shell)}" -NoLogo -NoProfile -NonInteractive -Command "{program}{command[6:]}"'
    return subprocess.run(line, capture_output=True, timeout=60, check=False)


def shells_and_prompts() -> list[tuple[str, str]]:
    """PowerShell 7 with every prompt; Windows PowerShell 5.1, the fallback, with the nasty ones (each
    run starts a shell, which is slow on the Windows runner)."""
    if sys.platform != "win32":
        return []
    return ([("pwsh", prompt) for prompt in PROMPTS if shutil.which("pwsh")]
            + [("powershell", prompt) for prompt in NASTY if shutil.which("powershell")])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Terminal and PowerShell")
@pytest.mark.parametrize(("shell", "prompt"), shells_and_prompts())
def test_a_prompt_reaches_claude_whole_through_windows_terminal(shell, prompt, echo, tmp_path, monkeypatch):
    if shell == "powershell":  # what tabmancer uses when PowerShell 7 is not installed
        which = shutil.which
        monkeypatch.setattr(L.shutil, "which", lambda name: None if name == "pwsh" else which(name))
    # With a role the prompt is in the middle of the command as well as at its end: one shell for both.
    launch = tab(prompt, str(tmp_path), role=True)
    args = L.windows_terminal_command([launch], window="0")
    assert args[args.index("-Command") - 3] == f"{shell}.exe"
    result = through_windows_terminal(args[-1], shell, echo)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    # Windows PowerShell 5.1 cannot pass a " on whole, so there it goes as '.
    expected = [arg.replace('"', "'") if shell == "powershell" else arg for arg in launch.argv[1:]]
    assert heard(result.stdout)["argv"] == expected


ENV_ECHO = ("import json, os, sys\nnames = ('BROWSER', 'TABMANCER_OPEN_IN', 'TABMANCER_PYTHON')\n"
            "sys.stdout.write(json.dumps({'argv': sys.argv[1:], 'env': {n: os.environ.get(n) for n in names}}))\n")
# A sign-in tab's environment (browsers.login_environment), with paths as odd as a profile's can be,
# and a bare word: `$env:X=edge:Default` once ran edge:Default as a command and set nothing.
SIGN_IN_ENV = (("BROWSER", "C:\\Users\\Ünal Öz\\it's 100% $(x)\\tabmancer\\open-in.cmd"),
               ("TABMANCER_OPEN_IN", "edge:Default"),
               ("TABMANCER_PYTHON", "C:\\Users\\Ünal Öz\\AppData\\Roaming\\uv\\tools\\tabmancer\\Scripts\\python.exe"))


def sign_in_tab(folder: str) -> Launch:
    return Launch(title="Sign in", cwd=folder, mode=Mode.NEW, session_id="",
                  options=("auth", "login", "--email", "me+x@example.com"), env=SIGN_IN_ENV)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Terminal and PowerShell")
@pytest.mark.parametrize("shell", [s for s in ("pwsh", "powershell") if shutil.which(s)])
def test_a_sign_in_tabs_environment_reaches_claude_whole_through_windows_terminal(shell, tmp_path, monkeypatch):
    if shell == "powershell":
        which = shutil.which
        monkeypatch.setattr(L.shutil, "which", lambda name: None if name == "pwsh" else which(name))
    echo = tmp_path / "env_echo.py"
    echo.write_text(ENV_ECHO, encoding="utf-8")
    launch = sign_in_tab(str(tmp_path))
    command = L.windows_terminal_command([launch], window="0")[-1]
    assert "%" not in command and not re.search(r"(?<!\\);", command)  # what Windows Terminal would touch
    command = command.replace("\\;", ";").replace("claude ", f"& {L._ps_quote(sys.executable)} {L._ps_quote(str(echo))} ", 1)
    line = f'"{shutil.which(shell)}" -NoLogo -NoProfile -NonInteractive -Command "{command}"'
    result = subprocess.run(line, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    got = heard(result.stdout)
    assert got == {"argv": launch.argv[1:], "env": dict(SIGN_IN_ENV)}


def test_every_value_is_a_quoted_string_after_env_and_put_back_after_the_command():
    before, after = L._ps_env((("A", "edge:Default"), ("B", ""), ("C", "it's")))
    assert before == ("$tabmancer_0=$env:A; $env:A='edge:Default'; $tabmancer_1=$env:B; $env:B=''; "
                      "$tabmancer_2=$env:C; $env:C='it''s'; ")
    assert after == "; $env:A=$tabmancer_0; $env:B=$tabmancer_1; $env:C=$tabmancer_2"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Terminal and PowerShell")
def test_after_the_sign_in_the_tab_has_its_own_variables_back(tmp_path):
    """The tab stays open as a shell: a claude run there later must not open its pages with the
    sign-in's launcher, and a BROWSER of your own comes back."""
    echo = tmp_path / "env_echo.py"
    echo.write_text(ENV_ECHO, encoding="utf-8")
    command = L.windows_terminal_command([sign_in_tab(str(tmp_path))], window="0")[-1].replace("\\;", ";")
    command = command.replace("claude ", f"& {L._ps_quote(sys.executable)} {L._ps_quote(str(echo))} ", 1)
    line = (f'"{shutil.which("pwsh") or shutil.which("powershell")}" -NoLogo -NoProfile -NonInteractive -Command '
            f'"$env:BROWSER=\'mine\'; Remove-Item Env:TABMANCER_OPEN_IN -ErrorAction Ignore; {command}; '
            "[Console]::Out.Write('|' + $env:BROWSER + '|' + [string]($null -eq $env:TABMANCER_OPEN_IN))\"")
    result = subprocess.run(line, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    during, back, unset = result.stdout.decode("ascii").rsplit("|", 2)
    assert json.loads(during)["env"] == dict(SIGN_IN_ENV)  # set for the sign-in...
    assert (back, unset) == ("mine", "True")  # ...and as they were once it is over


def test_where_no_tab_opens_on_windows_the_line_to_run_is_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(L.sys, "platform", "win32")
    line = L.shell_line(sign_in_tab(str(tmp_path)))
    assert line.startswith(f"Set-Location -LiteralPath {L._ps_string(str(tmp_path))}; $tabmancer_0=$env:BROWSER;")
    assert "claude auth login --email 'me+x@example.com'" in line and " env " not in f" {line}"
    monkeypatch.setattr(L.sys, "platform", "linux")
    posix = L.shell_line(sign_in_tab(str(tmp_path)))
    assert posix.startswith(f"cd {L.shlex.quote(str(tmp_path))} && env ") and "BROWSER=" in posix


def test_a_variable_name_that_is_not_one_is_refused():
    launch = Launch(title="t", cwd=".", mode=Mode.NEW, session_id="", env=(("BROWSER; rm -rf ~", "x"),))
    with pytest.raises(ValueError):
        L.windows_terminal_command([launch], window="0")


# --- POSIX terminals → sh -------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="sh")
def test_a_sign_in_tabs_environment_reaches_claude_whole_through_sh(tmp_path):
    echo = tmp_path / "env_echo.py"
    echo.write_text(ENV_ECHO, encoding="utf-8")
    launch = sign_in_tab(str(tmp_path))
    env = dict(os.environ, SHELL="true")
    (tmux,) = L.tmux_commands([launch])
    (wezterm,) = L.wezterm_commands([launch])
    for how, line in {"tmux": tmux[-1], "keep open": wezterm[-1], "by hand": L.shell_line(launch)}.items():
        line = line.replace("claude ", shlex.join([sys.executable, str(echo)]) + " ", 1)
        result = subprocess.run(["sh", "-c", line], capture_output=True, timeout=30, check=False, env=env)
        assert result.returncode == 0, (how, result.stderr.decode(errors="replace"))
        assert heard(result.stdout) == {"argv": launch.argv[1:], "env": dict(SIGN_IN_ENV)}, how

@pytest.mark.skipif(sys.platform == "win32", reason="sh")
@pytest.mark.parametrize("prompt", PROMPTS)
def test_a_prompt_reaches_claude_whole_through_sh(prompt, echo, tmp_path):
    folder = tmp_path / "it's a \"folder\"; $(x) `y` 100% ü"
    folder.mkdir()
    program = shlex.join([sys.executable, echo])
    env = dict(os.environ, SHELL="true")  # the shell a tab keeps open afterwards: here it just ends
    for role in (False, True):
        launch = tab(prompt, str(folder), role)
        (tmux,) = L.tmux_commands([launch])
        (wezterm,) = L.wezterm_commands([launch])
        lines = {
            "tmux": tmux[-1],                  # tmux runs it with sh -c
            "keep open": wezterm[-1],          # wezterm, kitty, GNOME Terminal, Konsole: sh -c
            "by hand": L.shell_line(launch),   # printed when no terminal can open tabs
        }
        for how, line in lines.items():
            assert line.count("claude ") >= 1
            line = line.replace("claude ", program + " ", 1)
            result = subprocess.run(["sh", "-c", line], capture_output=True, timeout=30, check=False,
                                    cwd=str(folder) if how != "by hand" else None, env=env)
            assert result.returncode == 0, (how, result.stderr.decode(errors="replace"))
            got = heard(result.stdout)
            assert got["argv"] == launch.argv[1:], how
            assert os.path.samefile(got["cwd"], folder), how


@pytest.mark.skipif(sys.platform != "darwin", reason="AppleScript")
@pytest.mark.parametrize("prompt", PROMPTS)
def test_a_prompt_survives_an_applescript_string(prompt):
    result = subprocess.run(["osascript", "-e", f"return {L._applescript_string(prompt)}"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout[:-1] == prompt.replace("\r\n", "\n").replace("\r", "\n")  # osascript prints \r as \n
