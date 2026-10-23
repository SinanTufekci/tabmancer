"""The coding CLIs tabmancer works with, and which of them you use.

Claude Code is the reference: everything works with it. Codex and Antigravity (agy)
add their sessions to the list and open in tabs the same way, with what their
session files let tabmancer read. Only Claude Code is on until you turn others on.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .config import read_json, write_json

CONFIG_KEY = "clis"
DEFAULT = ("claude",)


@dataclass(frozen=True)
class Cli:
    name: str        # the id used in config files and on the command line
    label: str
    command: str     # the executable
    can_fork: bool
    can_background: bool
    reads: str       # what tabmancer can tell about its sessions, for the settings dialog


CLIS: dict[str, Cli] = {cli.name: cli for cli in (
    Cli("claude", "Claude Code", "claude", can_fork=True, can_background=True,
        reads="everything: endings, live state, tokens, summaries, background sessions"),
    Cli("codex", "Codex", "codex", can_fork=True, can_background=False,
        reads="endings, last prompt and reply, edited files, tokens, summaries"),
    Cli("agy", "Antigravity", "agy", can_fork=False, can_background=False,
        reads="titles, folders and times (its conversations are stored in a binary format)"),
)}


def enabled() -> list[str]:
    """The CLIs whose sessions are listed, in the order of CLIS."""
    saved = read_json("config.json")
    chosen = saved.get(CONFIG_KEY) if isinstance(saved, dict) else None
    if not isinstance(chosen, list):
        chosen = list(DEFAULT)
    return [name for name in CLIS if name in chosen]


def set_enabled(names: list[str]) -> list[str]:
    """Save which CLIs are on. At least one stays on: Claude Code if none is left."""
    chosen = [name for name in CLIS if name in names] or list(DEFAULT)
    saved = read_json("config.json")
    saved = saved if isinstance(saved, dict) else {}
    saved[CONFIG_KEY] = chosen
    write_json("config.json", saved, indent=2)
    return chosen


def installed(name: str) -> str | None:
    """Where the CLI's executable is, or None."""
    return shutil.which(CLIS[name].command)


def label(name: str) -> str:
    cli = CLIS.get(name)
    return cli.label if cli else name
