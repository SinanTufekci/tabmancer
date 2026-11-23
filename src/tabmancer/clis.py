"""The coding CLIs tabmancer works with, and which of them you use.

Claude Code is the reference: everything works with it. Codex and Antigravity (agy)
add their sessions to the list and open in tabs the same way, with what their
session files let tabmancer read. Only Claude Code is on until you turn others on.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .config import merge_json, read_json, write_json

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
        reads="titles, folders, times, transcripts, models, tokens and mixed teams"),
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


# The CLI you mainly use: what n starts by default, what a team member with no CLI tag runs on, whose
# limits the top bar shows first, and which CLI answers tabmancer's own background calls (router,
# summaries, improve, the team line-up, handovers, the report). Claude Code unless you pick agy:
# with a Google AI plan and no Claude account, tabmancer then runs on Antigravity alone. Codex is
# not offered: it has no background run tabmancer can use.
MAIN_KEY = "main_cli"
MAINS = ("claude", "agy")
MAIN_COST = {  # what each choice costs, said where it is picked
    "claude": "tabmancer's own calls (router, summaries, improve, team line-up, handovers, report) "
              "run on your Claude plan: a small model, a few seconds each.",
    "agy": "tabmancer's own calls run on agy's model chain (Neon, then Hydrogen), never Claude: about "
           "10 s and some 1% of the 5-hour Gemini quota each. Nothing runs while the chain is out of quota.",
}


def main_cli() -> str:
    """config.json "main_cli": "claude" or "agy"; anything else, or nothing, is Claude Code."""
    saved = read_json("config.json")
    chosen = saved.get(MAIN_KEY) if isinstance(saved, dict) else None
    return chosen if chosen in MAINS else "claude"


def set_main(name: str) -> str:
    """Save the main CLI (one of MAINS, else Claude Code) and return what is saved now. agy as main is
    turned on in the list too. Nothing is written over a config.json that cannot be read."""
    chosen = name if name in MAINS else "claude"
    on = enabled()
    keys: dict[str, object] = {MAIN_KEY: chosen}
    if chosen not in on:
        keys[CONFIG_KEY] = [n for n in CLIS if n in [*on, chosen]]
    merge_json("config.json", **keys)
    return main_cli()


def label(name: str) -> str:
    cli = CLIS.get(name)
    return cli.label if cli else name
