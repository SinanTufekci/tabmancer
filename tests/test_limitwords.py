"""What the c dialog says about the sessions stopped at a usage limit, with no UI (limitwords.py): the
ways and their order, each session's line, the cost and the away words. test_limits shows them as the
terminal draws them; test_desktop_extra proves the module loads with neither Textual nor Qt."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import actions, limitwords, usage
from tabmancer.accounts import Account
from tabmancer.palette import AMBER, MUTED
from tabmancer.sessions import Exit, Session, Status


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


def test_where_the_window_does_not_act_it_offers_no_lift_and_no_off():
    assert [w for w, _ in limitwords.ways("at 16:00", True, True, False, acts=False)] == ["now", "handover"]
    assert [w for w, _ in limitwords.ways("at 16:00", False, True, False, acts=False)] == ["now", "handover"]
    assert limitwords.ways("at 16:00", False, True, True) == limitwords.ways("at 16:00", False, True, True, acts=True)
    assert "dashboard that acts" in limitwords.NOT_HERE and "(tabmancer)" not in limitwords.NOT_HERE


def test_the_first_way_and_a_way_with_nothing_ticked():
    assert limitwords.first_way(["lift", "now", "handover"], False) == 0
    assert limitwords.first_way(["lift", "now", "handover"], True) == 2  # another organisation: the handover first
    assert limitwords.first_way(["now"], True) == 0
    assert limitwords.pick_refusal([], "now") is limitwords.TICK_FIRST
    assert limitwords.pick_refusal([], "off") is None and limitwords.pick_refusal(["a"], "now") is None


def stopped(sid, context, resets) -> Session:
    return Session(session_id=sid, path=None, cwd=f"C:/work/{sid}", exit=Exit.CLEAN, status=Status.LIMIT,
                   resets=resets, context=context)


def test_what_c_asks_about():
    soon = datetime.now() + timedelta(hours=2)
    a, b = stopped("a", 400_000, soon), stopped("b", 1_000, soon + timedelta(hours=1))
    working = Session(session_id="w", path=None, cwd="C:/work/w")
    assert limitwords.ask({"w": working}, [], None, [], {}) is actions.NONE_STOPPED
    limits = [usage.Limit("session", 40, None, "4pm"), usage.Limit("week", 70, None, "Mon")]
    asked = limitwords.ask({"a": a, "b": b, "w": working}, limits, None, ["other than key"], {"a": soon})
    assert isinstance(asked, limitwords.Ask)
    assert asked.stopped == [a, b] and asked.when == actions.continue_when([a, b]) and asked.when.startswith("at ")
    assert not asked.armed and asked.room and asked.away == {} and asked.where == "this account"
    assert asked.plan == "Your plan now: session 40% · week 70%\nother than key"
    assert limitwords.ask({"a": a, "b": b}, [], None, [], {"a": soon, "b": soon}).armed


def test_where_the_window_does_not_type_into_tabs_now_says_the_clipboard():
    def now(types):
        return "".join(words for words, _ in dict(limitwords.ways("at 16:00", True, False, False, types=types))["now"])
    assert limitwords.NOW_OPEN_TYPED in now(True)
    assert limitwords.NOW_OPEN_CLIPBOARD in now(False) and "in its tab," not in now(False)
    assert limitwords.ways("at 16:00", True, False, False) == limitwords.ways("at 16:00", True, False, False, types=True)
