"""The footer's rules with no UI behind them (footer.py): the row at hand, which keys work, are dimmed
or are not there, and what a key says on the row."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import footer
from tabmancer.sessions import Exit, Session, Status

OWN = {"toggle", "hide", "pin", "open", "rename", "prompt", "summarize", "monitor"}


def s(sid, state="busy", exit=Exit.RUNNING, status=None, resets=None) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd="C:/work", title=sid, exit=exit,
                   status=status or (Status.RUNNING if exit is Exit.RUNNING else Status.DONE), live_state=state,
                   last_active=datetime.now() - timedelta(minutes=3), resets=resets)


def allowed(action, row=footer.NONE, sessions=(), **kw):
    asked = []
    where = {"on_top": False, "monitor": False, "projects_focused": False, "live_mode": False} | kw

    def at():
        asked.append(action)
        return row
    return footer.allowed(action, own=OWN, sessions=list(sessions), at=at, **where), asked


def test_the_row_at_hand():
    busy, idle, closed = s("b"), s("i", state="idle"), s("c", state="", exit=Exit.CLEAN)
    assert footer.row([busy], ("shop", True, False), None, 0, set()) == ("team", False, False)
    assert footer.row([idle], ("shop", False, True), None, 3, set()) == ("team-closed", True, True)  # ticks aside
    assert footer.row([busy], None, None, 0, set()) == footer.Row("none", False, False)
    assert footer.row([idle], None, closed, 2, set()) == ("ticked", 2, True)
    assert footer.row([busy], None, busy, 0, set()) == ("open", False, False)
    assert footer.row([busy], None, busy, 0, {"b"}) == ("member-open", False, False)
    assert footer.row([busy], None, closed, 0, {"c"}) == ("member-closed", False, False)
    assert footer.row([closed], None, closed, 0, set()) == ("closed", False, False)
    assert footer.NONE == ("none", False, False)  # a tuple still: the footer compares it with the last


def test_waiting_is_an_open_session_that_needs_you_or_is_done():
    assert footer.waiting([s("i", state="idle")])
    assert not footer.waiting([s("b"), s("c", state="idle", exit=Exit.CLEAN)])


def test_a_key_works_is_dimmed_or_is_not_there():
    assert allowed("open")[0] is True
    assert allowed("open", projects_focused=True) == (False, [])
    assert allowed("open", on_top=True) == (False, [])
    assert allowed("hide", on_top=True)[0] is False and allowed("quit", on_top=True)[0] is True  # not its own
    assert allowed("pin", monitor=True)[0] is False and allowed("cycle_theme", monitor=True)[0] is False
    assert allowed("cycle_theme")[0] is True
    assert allowed("toggle", live_mode=True)[0] is False and allowed("prompt", live_mode=True)[0] is True
    assert allowed("toggle", footer.Row("open", False, True)) == (None, ["toggle"])  # dimmed, still there
    assert allowed("toggle", footer.Row("closed", False, True))[0] is True
    assert allowed("prompt", footer.Row("team", False, True))[0] is None
    assert allowed("monitor", footer.Row("member-open", False, True))[0] is True
    assert allowed("next_waiting", footer.Row("open", False, False))[0] is None
    assert allowed("next_waiting", footer.Row("open", False, True))[0] is True
    assert allowed("quit") == (True, [])  # the row is asked only when a rule needs it


def test_continue_at_reset_shows_only_with_something_to_continue():
    stopped = s("l", state="", exit=Exit.CLEAN, status=Status.LIMIT, resets=datetime.now() + timedelta(hours=1))
    assert allowed("continue_at_reset", sessions=[s("b")])[0] is False
    assert allowed("continue_at_reset", sessions=[stopped])[0] is True


def test_what_a_key_says_on_the_row():
    team, closed_team = footer.Row("team", False, True), footer.Row("team-closed", False, True)
    assert footer.words("open", team, 0) == "Team view" == footer.words("open", closed_team, 0)
    assert footer.words("open", footer.Row("member-open", False, True), 0) == "Its tab"
    assert footer.words("open", footer.Row("ticked", 3, True), 3) == "Open 3"
    assert footer.words("open", footer.Row("closed", False, True), 0) is None  # as it is named
    assert (footer.words("summarize", team, 0), footer.words("summarize", closed_team, 0)) == ("Start rest", "Start team")
    assert (footer.words("hide", team, 0), footer.words("hide", closed_team, 0)) == ("Close team", "Hide team")
    assert footer.words("toggle", closed_team, 0) == "Tick team" and footer.words("toggle", team, 0) is None
    assert footer.words("handover", team, 0) == "Hand team over" == footer.words("handover", closed_team, 0)
    assert footer.words("quit", team, 0) is None
