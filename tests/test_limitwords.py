"""What the c dialog says about the sessions stopped at a usage limit, with no UI (limitwords.py): the
ways and their order, each session's line, the cost and the away words. test_limits shows them as the
terminal draws them; test_desktop_extra proves the module loads with neither Textual nor Qt."""

from __future__ import annotations

from pathlib import Path

from tabmancer import limitwords
from tabmancer.accounts import Account
from tabmancer.palette import AMBER, MUTED
from tabmancer.sessions import Session


def s(sid, context, project="shop", title="Fix the login loop") -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=f"C:/work/{project}", title=title,
                   context=context)


def said(parts) -> str:
    return "".join(words for words, _ in parts)


def test_the_ways_and_their_order():
    assert [w for w, _ in limitwords.ways("at 16:00", True, False, False)] == ["now", "lift", "handover"]
    assert [w for w, _ in limitwords.ways("at 16:00", False, True, False)] == ["lift", "now", "handover", "off"]
    lift = dict(limitwords.ways("at 16:00", False, False, False))["lift"]
    assert "  at 16:00\n" in said(lift)
    over = dict(limitwords.ways("", True, False, True))["handover"]
    assert over[-1][1] == AMBER and "only a digest goes" in over[-1][0]
    assert dict(limitwords.ways("", True, False, False))["handover"][-1][0] == ""


def test_the_lines_and_what_going_on_costs():
    big, small, none = s("a", 612_000, "a-very-long-project-name-here"), s("b", 120_000), s("c", 0)
    assert limitwords.biggest([small, none, big]) == [big, small, none]
    line = limitwords.line_parts(big)
    assert len(line[0][0]) == 20 and line[2] == ("612k context", AMBER)
    assert limitwords.line_parts(small)[2] == ("120k context", MUTED) and limitwords.line_parts(none)[2][0] == ""
    assert limitwords.ticked_total([big, small, none], ["b", "c"]) == 120_000
    assert limitwords.cost_parts(0) == []
    assert limitwords.cost_parts(732_000)[1] == ("732k tokens", f"bold {AMBER}")
    assert limitwords.title_words(1, "at 16:00") == ("Stopped at a usage limit", "1 session", "it lifts at 16:00")


def test_where_going_on_sends_them():
    away = {"a": ["Gmail"], "b": ["Drive", "Gmail"], "c": []}
    assert limitwords.away_parts(["d"], away, "x") == []
    one = said(limitwords.away_parts(["c", "d"], away, "your personal account"))
    assert one == "One of the ticked ran on another organisation: its conversation goes to your personal account if it goes on."
    two = said(limitwords.away_parts(["a", "b"], away, "the Example Labs organisation"))
    assert two.startswith("2 of the ticked") and two.endswith("if they go on (Drive / Gmail content in them).")


def test_which_ran_on_another_organisation():
    me = Account("claude", "me@example.com", "Max 5x", "acc1", "orgP", "", "claude_max")
    here, there, unknown = s("a", 1), s("b", 1), s("c", 1)
    here.org, there.org, there.sensitive = "orgP", "orgB", ["Gmail"]
    assert limitwords.away(me, [here, there, unknown]) == {"b": ["Gmail"]}
    assert limitwords.away(None, [there]) == {}
