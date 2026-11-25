"""Every command a window can run, found by a few of its words: the desktop windows' command palette
(Ctrl+K), as plain data.

A command is a key of keys.py, named as the menus name it (short) and with what keys.py says it does;
one the window does not do is greyed and says why (where it is done for now, or what to pick first). A
jump goes to a team, a session or a team's member by name. find() ranks them all for the words typed: the
key itself, then names that start with the words, then a word in them that does (a jump's kind counts:
"team shop"), then the words anywhere in the name, then in what a key does or a jump's project, then their
letters in order; a session also by its folder and prompts, as the list's / finds it (search.matches). It
imports neither Textual nor Qt.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import search

if TYPE_CHECKING:
    from .sessions import Session
    from .viewmodel import MemberView, SessionView, TeamView

KINDS = ("key", "team", "session", "member")  # their order at an equal rank
MOST = 60  # what the palette lists at most


@dataclass(frozen=True)
class Command:
    kind: str  # key | team | session | member
    words: str  # as the menus say it (a key), or its name (a jump)
    said: str = ""  # what a key does in full (keys.py), or a jump's state and project
    key: str = ""  # a key as keys.py writes it
    group: str = ""  # a key's group in keys.py; a jump's project, or a member's part
    target: str = ""  # a jump's session id, team or member name
    enabled: bool = True
    why: str = ""  # why it is greyed


def short(words: str) -> str:
    """A key's words for a menu: up to the first ':', ';', ',' or '(' of what keys.py says it does."""
    cut = len(words)
    for mark in (":", ";", ",", " ("):
        at = words.find(mark)
        if at != -1:
            cut = min(cut, at)
    text = words[:cut].strip()
    return text[:1].upper() + text[1:]


def commands(keys: list[tuple[str, list[tuple[str, str, str]]]], here: Iterable[str], elsewhere: str, *,
             needs_pick: Iterable[str] = (), picked: bool = True, pick_first: str = "") -> list[Command]:
    """Every key of keys.py's list (KEYS or MONITOR_KEYS), in its order: the ones the window does (here),
    and the rest greyed with where they are done (elsewhere); one that acts on the picked one, greyed
    with pick_first while nothing is picked."""
    here, needs_pick = set(here), set(needs_pick)
    made = []
    for group, entries in keys:
        for key, _action, words in entries:
            why = elsewhere if key not in here else pick_first if key in needs_pick and not picked else ""
            made.append(Command("key", short(words), words, key, group, enabled=not why, why=why))
    return made


def team_jumps(teams: Iterable[TeamView]) -> list[Command]:
    return [Command("team", team.name, team.goal, group=team.project, target=team.name) for team in teams]


def session_jumps(views: Iterable[SessionView]) -> list[Command]:
    return [Command("session", view.title or "(no title yet)", f"{view.label} · {view.project}", group=view.project,
                    target=view.id) for view in views]


def member_jumps(members: Iterable[MemberView]) -> list[Command]:
    return [Command("member", member.name, f"{member.label} · {member.focus}" if member.focus else member.label,
                    group="lead" if member.lead else "member", target=member.name) for member in members]


def rank(typed: str, command: Command) -> int | None:
    """How well the words typed name a command (lower is better), or None: not at all."""
    needle = search.fold(typed.strip())
    if not needle:
        return None
    if command.kind == "key" and typed.strip() == command.key:  # as typed: t and T are two keys
        return 0
    words = search.fold(command.words)
    if words.startswith(needle) or (command.kind != "key" and f"{command.kind} {words}".startswith(needle)):
        return 1
    starts = words.split() + ([command.kind] if command.kind != "key" else [])  # "team shop" names the team shop
    if all(any(word.startswith(part) for word in starts) for part in needle.split()):
        return 2
    if needle in words:
        return 3
    if needle in search.fold(f"{command.group} {command.said}"):
        return 4
    if in_order(needle.replace(" ", ""), words):
        return 5
    return None


def in_order(letters: str, words: str) -> bool:
    """Whether the letters come in the words in this order, others between them."""
    at = 0
    for letter in letters:
        at = words.find(letter, at) + 1
        if not at:
            return False
    return True


def find(entries: list[Command], typed: str, sessions: dict[str, Session] | None = None) -> list[Command]:
    """The commands and jumps the words name, the best first (then keys, teams, sessions, members, each
    in its own order); nothing typed: every key, in keys.py's order. A session also by its folder and
    prompts (search.matches), after what its name gives."""
    needle = search.fold(typed.strip())
    if not needle:
        return [command for command in entries if command.kind == "key"][:MOST]
    scored = []
    for order, command in enumerate(entries):
        score = rank(typed, command)
        if score is None and command.kind == "session" and sessions:
            session = sessions.get(command.target)
            if session is not None and search.matches(session, needle):
                score = 4
        if score is not None:
            scored.append((score, KINDS.index(command.kind), order, command))
    scored.sort(key=lambda item: item[:3])
    return [command for *_, command in scored[:MOST]]
