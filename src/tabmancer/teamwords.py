"""What the team monitor says, with no UI behind it: the members' colours, the plan limits and agy's
chain, who needs you, and a member's facts. Each comes as parts, (words, style): the style a colour
from palette.py with 'bold ' before it where the words stand out, '' for none. The terminal monitor
(monitorview, teamview) puts them into rich Text, the desktop window into its own; both say the same."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import NamedTuple

from . import agy, teams
from . import viewmodel as VM
from .palette import AMBER, CYAN, GREEN, MUTED, ORANGE, RED, TEXT
from .tokens import human

Part = tuple[str, str]  # (words, style)

PALETTE = [CYAN, GREEN, AMBER, ORANGE, "#c678dd", "#61afef", "#e5c07b", "#56b6c2"]  # the members, in turn


def colours(team: teams.Team, accent: str) -> dict[str, str]:
    """The lead in the theme's accent, the others in the palette's colours; peers, all of them."""
    out = {teams.YOU: TEXT, teams.TABMANCER: MUTED}
    if team.lead is not None:
        out[team.lead.name] = accent
    others = [m for m in team.members if not m.lead]
    out.update({m.name: PALETTE[i % len(PALETTE)] for i, m in enumerate(others)})
    return out


def percent_style(percent: float) -> str:
    """Red from 85%, amber from 60%, plain below."""
    return f"bold {RED}" if percent >= 85 else AMBER if percent >= 60 else TEXT


def quota_parts(limits: list, agy_view: dict, with_agy: bool, now: datetime | None = None) -> list[Part]:
    """The plan limits as the dashboard read them last ('session 42% · week 18%') and, for a team
    with agy members, where agy's chain stands ('Neon out till Tue 09:02 · Hydrogen 31%'). A quota
    read longer ago than agy.STALE says so instead: it tells nothing of now."""
    now = now or datetime.now()
    out: list[Part] = []
    for i, limit in enumerate(limits):
        out.append((" · " if i else "Claude ", MUTED))
        out.append((f"{limit.label} {limit.percent}%", percent_style(limit.percent)))
    if not with_agy or not agy_view:
        return out
    if out:
        out.append(("   ", ""))
    read_at = agy_view.get("read_at")
    if read_at is None:
        return [*out, ("agy quota not read", MUTED)]
    if now - read_at > agy.STALE:
        return [*out, (f"agy quota read {read_at:%H:%M}", MUTED)]
    out.append(("agy ", MUTED))
    for i, standing in enumerate(agy_view.get("chain") or []):
        if i:
            out.append((" · ", MUTED))
        name, left, back = standing.get("name") or standing.get("model", "?"), standing.get("left"), standing.get("back")
        if back is not None:
            when = f"{back:%H:%M}" if back.date() == now.date() else f"{back:%a %H:%M}"
            out.append((f"{name} out till {when}", RED))
        elif left is None:
            out.append((f"{name} ?", MUTED))
        else:
            out.append((f"{name} {left:.0f}%", percent_style(100 - left)))
    return out


def needs_parts(view: VM.TeamView, palette: dict[str, str], tone_style: Callable[[str], str]) -> list[Part] | None:
    """'NEEDS YOU  lead: waiting for an answer · dash: done   j picks them in turn'; None when
    nobody waits for you. tone_style: the front end's style for a member's tone (VM.MemberView.tone)."""
    waiting = [m for m in view.members if m.needs_you]
    if not waiting:
        return None
    out: list[Part] = [("NEEDS YOU  ", f"bold {RED}")]
    for i, member in enumerate(waiting):
        if i:
            out.append((" · ", MUTED))
        out.append((member.name, f"bold {palette.get(member.name, TEXT)}"))
        out.append((f": {member.label}", tone_style(member.tone)))
    return [*out, ("   j picks them in turn", MUTED)]


def one_line(text: str) -> str:
    return " ".join(text.split())


def head(state: teams.TeamState, tokens: int, accent: str) -> list[Part]:
    """'TEAM tabmancer · 3 of 3 open · 12 messages · 14.9M tokens': a team's preview, first line."""
    team = state.team
    said = len(state.between_members())
    what = f"  ·  {state.open_count()} of {len(team.members)} open  ·  {said} message{'s' if said != 1 else ''}"
    return [("TEAM ", f"bold {MUTED}"), (team.name, f"bold {accent}"), (what, MUTED),
            (f"  ·  {human(tokens)} tokens" if tokens else "", MUTED)]


class TeamDetail(NamedTuple):
    """A team's preview, its first lines: the head, its goal, and while it is closed what Enter does
    ([] while at work)."""

    head: list[Part]
    goal: list[Part]
    closed: list[Part]


def team_detail(state: teams.TeamState, tokens: int, accent: str, at_work: bool) -> TeamDetail:
    """A team's preview, its first lines, as both fronts show them. at_work: any of it open."""
    closed: list[Part] = []
    if not at_work:
        last = f", {state.team.lead.name} last" if state.team.lead is not None else ""
        closed = [(f"Closed. Enter starts it: each member resumed as itself{last}.", AMBER)]
    return TeamDetail(head(state, tokens, accent), [("Goal  ", MUTED), (one_line(state.team.goal), TEXT)], closed)


def gist(message: teams.Message, other: str = "") -> str:
    """What a message says, on one line: its summary when it has one. A summary that starts with
    the other side's name ("cad: start once the memory is free") loses it where that name shows."""
    text = one_line(message.summary or message.text)
    if other and text.casefold().startswith(f"{other.casefold()}:"):
        text = text[len(other) + 1:].lstrip()
    return text


def message_parts(message: teams.Message, member: str, colours: dict[str, str]) -> list[Part]:
    """'→ dash: Heads-up…' when this member said it, '← lead: …' when it was said to it."""
    out = message.sender == member
    other = message.receiver if out else message.sender
    return [("→ " if out else "← ", MUTED), (other, f"bold {colours.get(other, TEXT)}"), (": ", MUTED),
            (gist(message, other), TEXT)]


def model_of(member: VM.MemberView) -> str:
    """'claude · opus 5.5 · high', 'agy · Neon (High)': as the team view and the desktop monitor say it."""
    return f"{member.cli} · {VM.model_words(member)}"


def facts(member: VM.MemberView) -> list[Part]:
    """What the picked member's first line and the table say of it: its model first."""
    out: list[Part] = [(model_of(member), CYAN if member.cli != "claude" else MUTED)]
    if member.fell_back:
        out.append((f"fell back: {member.fell_back}", AMBER))
    if member.context is not None:
        out.append((f"context {member.context:.0%}", percent_style(member.context * 100)))
    if member.tokens:
        out.append((f"{human(member.tokens)} tokens", MUTED))
    if member.bus is not None and member.state != "not-started":
        since = member.bus.get("since")
        if member.bus.get("listening"):
            out.append((f"bus: listening since {since:%H:%M}" if since else "bus: listening", GREEN))
        elif member.bus.get("runner"):
            out.append(("bus: runner alive", MUTED))
        else:
            out.append(("bus: not listening", AMBER))
    if member.unread:
        out.append((f"{member.unread} unread", AMBER))
    if member.git:
        git = member.git
        words = git.get("branch") or "git"
        words += f" +{git['changed']}" if git.get("changed") else ""
        words += f" ↑{git['ahead']}" if git.get("ahead") else ""
        out.append((words, MUTED))
    if member.quota and member.quota.get("back") is not None:
        out.append((f"out of quota till {member.quota['back']:%H:%M}", RED))
    if member.model_note:
        out.append((member.model_note, MUTED))
    return out
