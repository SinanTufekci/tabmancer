"""The coding CLIs tabmancer works with, and which of them you use.

Claude Code is the reference: everything works with it. Codex and Antigravity (agy)
add their sessions to the list and open in tabs the same way, with what their
session files let tabmancer read. Only Claude Code is on until you turn others on.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass

from .config import NOT_SAVED, merge_json, read_json
from .palette import CYAN, GREEN, MUTED, TEXT

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
    """Save which CLIs are on (at least one: Claude Code if none is left) and return those on now. A
    config.json that is there but cannot be read is left as it is (merge_json): the CLIs on are then
    those on before, and the theme, Bit and the rest it keeps are not written over."""
    merge_json("config.json", **{CONFIG_KEY: [name for name in CLIS if name in names] or list(DEFAULT)})
    return enabled()


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


# What the , dialog says of each CLI and of the main one, as (words, style) parts: a palette colour,
# with 'bold ' before it where the words stand out (teamwords' Part). The terminal's dialog
# (clisettings.py) puts them into rich Text, a desktop window into its own; both say the same.
Part = tuple[str, str]


def option_parts(name: str, on: bool) -> list[Part]:
    """[x] or [ ] in front, as in the session list (a dimmed X read as a tick), the CLI, whether it is
    installed, and what tabmancer can tell of its sessions."""
    cli = CLIS[name]
    found = installed(name)
    return [("[x] " if on else "[ ] ", f"bold {CYAN}" if on else MUTED), (f"{cli.label:<13}", f"bold {TEXT}"),
            ("installed" if found else "not found", GREEN if found else MUTED), (f"\n    {cli.reads}", MUTED)]


def main_parts(main: str) -> list[Part]:
    """The main CLI and what it costs: tabmancer's own calls run on it."""
    return [("Main CLI: ", MUTED), (label(main), f"bold {CYAN}"),
            (" (starts by default, runs tabmancer's own calls)\n", MUTED), (MAIN_COST[main], MUTED)]


def next_main(main: str) -> str:
    """The main CLI after this one, in turn (m in the , dialog)."""
    return MAINS[(MAINS.index(main) + 1) % len(MAINS)] if main in MAINS else MAINS[0]


# The rest of what the , dialog says, and its steps: one copy for the terminal's dialog and a window's.
SETTINGS_TITLE = "Which CLIs do you use?"
SETTINGS_ABOUT = ("Their sessions join the list, open in tabs and count in the stats. Claude Code is the one every "
                  "feature works with.")
SETTINGS_KEYS = (("Space", "turn one on or off"), ("m", "main CLI"), ("Enter", "save"), ("Esc", "cancel"))


def listing_words(names: Iterable[str]) -> str:
    """Said once the , dialog has changed which CLIs are listed."""
    return "Listing sessions from " + ", ".join(label(name) for name in names) + "."


@dataclass(frozen=True)
class Saved:
    """What Enter in the , dialog came to: the CLIs listed before and now, and whether all of it was
    saved (config.json there but unreadable is left as it is: nothing changes then)."""

    before: list[str]
    after: list[str]
    kept: bool

    @property
    def changed(self) -> bool:
        """The list reads its sessions again: other CLIs are listed now."""
        return self.after != self.before

    @property
    def words(self) -> str:
        if not self.kept:
            return NOT_SAVED
        return listing_words(self.after) if self.changed else ""


@dataclass
class Picked:
    """What the , dialog has picked so far: the CLIs on and the main one. Nothing is saved till save()."""

    on: set[str]
    main: str

    @classmethod
    def saved(cls) -> Picked:
        """As config.json has them now."""
        return cls(set(enabled()), main_cli())

    def toggle(self, name: str) -> bool:
        """Space (or a click): one turned on or off; False for a name that is not a CLI."""
        if name not in CLIS:
            return False
        self.on ^= {name}
        return True

    def turn_main(self) -> None:
        """m: Claude Code and agy in turn; agy as main is turned on in the list too."""
        self.main = next_main(self.main)
        self.on.add(self.main)

    def save(self) -> Saved:
        """Enter: the main CLI, then which are on, saved."""
        before = enabled()
        if self.main != main_cli():
            set_main(self.main)
        wanted = [name for name in CLIS if name in self.on] or list(DEFAULT)
        after = set_enabled(wanted)
        return Saved(before, after, kept=after == wanted and main_cli() == self.main)
