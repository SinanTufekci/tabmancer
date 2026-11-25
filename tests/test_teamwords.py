"""The team monitor's words with no UI (teamwords.py): the members' colours, the plan limits and agy's
chain, who needs you and a member's facts, as (words, style) parts. test_monitor shows them as the
terminal monitor draws them; test_desktop_extra proves the module loads with neither Textual nor Qt."""

from __future__ import annotations

from datetime import datetime, timedelta

from tabmancer import teams, teamwords
from tabmancer import viewmodel as VM
from tabmancer.palette import AMBER, CYAN, MUTED, RED, TEXT

NOW = datetime(2026, 11, 25, 14, 0)


class Limit:
    def __init__(self, label, percent):
        self.label, self.percent = label, percent


def test_the_lead_takes_the_accent_and_the_others_the_palette():
    team = teams.new_team("shop", "C:/work/shop", "g", teams.parse_members("lead: steer\napi: a\nui: b", lead_ok=True))
    shown = teamwords.colours(team, "#d97757")
    assert shown["lead"] == "#d97757" and shown["api"] == teamwords.PALETTE[0] and shown["ui"] == teamwords.PALETTE[1]
    assert shown[teams.YOU] == TEXT and shown[teams.TABMANCER] == MUTED


def test_the_limits_and_agys_chain():
    assert teamwords.percent_style(85) == f"bold {RED}" and teamwords.percent_style(60) == AMBER
    assert teamwords.percent_style(59) == TEXT
    assert teamwords.quota_parts([Limit("session", 42)], {}, True, NOW) == [("Claude ", MUTED), ("session 42%", TEXT)]
    chain = [{"name": "Neon", "left": 31.4}, {"name": "Hydrogen", "back": NOW + timedelta(days=1)}]
    parts = teamwords.quota_parts([], {"read_at": NOW, "chain": chain}, True, NOW)
    assert "".join(w for w, _ in parts) == "agy Neon 31% · Hydrogen out till Thu 14:00"
    assert parts[-1][1] == RED
    old = teamwords.quota_parts([], {"read_at": NOW - timedelta(hours=3), "chain": chain}, True, NOW)
    assert old == [("agy quota read 11:00", MUTED)]
    assert teamwords.quota_parts([], {"read_at": NOW, "chain": chain}, False, NOW) == []


def member(name, **kw):
    return VM.MemberView(name, kw.pop("lead", False), "f", "s", "idle", kw.pop("label", "waiting for you"),
                         kw.pop("tone", "your-turn"), **kw)


def test_who_needs_you_in_the_front_ends_tones():
    view = VM.TeamView("t", "t", "g", 1, 0, [member("lead", needs_you=True), member("api")], [], [])
    parts = teamwords.needs_parts(view, {"lead": "#d97757"}, lambda tone: f"<{tone}>")
    assert parts == [("NEEDS YOU  ", f"bold {RED}"), ("lead", "bold #d97757"), (": waiting for you", "<your-turn>"),
                     ("   j picks them in turn", MUTED)]
    view.members[0].needs_you = False
    assert teamwords.needs_parts(view, {}, str) is None


def test_a_members_facts_model_first():
    agy = member("api", cli="agy", model="gemini-4-neon-high", context=0.9, unread=2, quota={"back": NOW},
                 git={"branch": "feat", "changed": 3})
    facts = teamwords.facts(agy)
    assert facts[0] == ("agy · Neon (High)", CYAN)
    assert ("context 90%", f"bold {RED}") in facts and ("2 unread", AMBER) in facts
    assert ("feat +3", MUTED) in facts and ("out of quota till 14:00", RED) in facts
    assert teamwords.facts(member("x", model="claude-opus-5-5", effort="high")) == [("claude · opus 5.5 · high", MUTED)]
