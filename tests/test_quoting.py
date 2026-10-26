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


# --- POSIX terminals → sh -------------------------------------------------------------------------

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
