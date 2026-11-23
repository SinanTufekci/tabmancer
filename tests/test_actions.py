"""The dashboard's actions without a screen (actions.py): what they say and do, from fakes, and that
no UI comes with them. The dashboard's own tests (test_handover) show the same through its keys."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tabmancer import actions, clis, handover, summarize, teams
from tabmancer.sessions import Exit, Session, Status


def test_actions_load_no_ui():
    code = ("import sys, tabmancer.actions; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def one(sid="a", title="Checkout", state="idle", path=None, **kw) -> Session:
    return Session(session_id=sid, path=path, cwd="C:/work/shop", title=title, exit=Exit.RUNNING, live_state=state, **kw)


def test_what_stops_a_handover_in_the_words_the_dashboard_says(tmp_path, monkeypatch):
    background = one(status=Status.BACKGROUND)
    assert actions.handover_refusal(background, []) == actions.Refusal(
        "Background sessions cannot be handed over from here: attach to it first.")

    monkeypatch.setattr(clis, "installed", lambda name: None)
    unread = one(cli="agy")
    assert actions.handover_refusal(unread, [unread]).words == (
        "Antigravity has no readable transcript for this session and agy is not on PATH.")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    assert actions.handover_refusal(one(cli="agy", path=transcript), []) is None  # its transcript will do

    waiting, twin = one(), one("b")
    refusal = actions.handover_refusal(waiting, [waiting, twin])
    assert refusal.seconds == 10 and refusal.words.startswith("shop cannot be handed over now: another open session")
    assert actions.handover_refusal(waiting, [waiting, twin], queued=True) is None  # h again: cancelled, not checked
    assert actions.handover_refusal(one(state="busy"), [one(state="busy")]) is None  # handed over once it finishes
    closed = Session(session_id="c", path=None, cwd="C:/work/shop", exit=Exit.CLEAN)
    assert actions.handover_refusal(closed, [waiting, twin]) is None  # nothing to close


@pytest.fixture
def calls(monkeypatch):
    """What write_handover asked of handover: no CLI and no tab is touched."""
    said: list = []
    monkeypatch.setattr(handover, "from_transcript", lambda s: said.append("transcript") or "from the transcript")
    monkeypatch.setattr(handover, "write_itself", lambda s: said.append("self") or "by itself")
    monkeypatch.setattr(handover, "close", lambda s, open_ones: said.append(("close", s.live_state, len(open_ones))))
    monkeypatch.setattr(teams, "member_of", lambda s: None)
    return said


def test_a_handover_written_both_ways(calls):
    closed = Session(session_id="c", path=None, cwd="C:/work/shop", exit=Exit.CLEAN)
    assert actions.write_handover(closed, "self", lambda: pytest.fail("not open: nothing to read")) == \
        actions.Handed("by itself")
    session = one()
    now = lambda: (one(state="idle"), [session, one("b")])
    assert actions.write_handover(session, "transcript", now) == actions.Handed("from the transcript")
    assert calls == ["self", "transcript", ("close", "idle", 2)]  # written first, then closed as it is now
    calls.clear()
    assert actions.write_handover(session, "self", now).written == "by itself"
    assert calls == [("close", "idle", 2), "self"]  # closed first: it resumes to write


def test_a_close_that_fails(calls, monkeypatch):
    def fails(s, open_ones):
        raise handover.HandoverError("its tab was not found in Windows Terminal")

    monkeypatch.setattr(handover, "close", fails)
    session = one()
    now = lambda: (session, [session])
    # From the transcript it is written already: the fresh one opens, the old one is said to be open.
    assert actions.write_handover(session, "transcript", now) == actions.Handed(
        "from the transcript", "its tab was not found in Windows Terminal")
    # A team member's is not: its fresh session would share its name with the open one.
    monkeypatch.setattr(teams, "member_of", lambda s: ("team", "member"))
    with pytest.raises(actions.ActionError) as raised:
        actions.write_handover(session, "transcript", now)
    assert raised.value.words == "shop was not handed over: its tab was not found in Windows Terminal."
    assert not raised.value.closed
    assert "self" not in calls  # the session writing it itself never ran: it could not be closed


def test_a_failure_after_the_close_says_it_is_closed(calls, monkeypatch):
    def nothing(s):
        raise summarize.SummaryError("claude returned nothing.")

    monkeypatch.setattr(handover, "write_itself", nothing)
    session = one()
    with pytest.raises(actions.ActionError) as raised:
        actions.write_handover(session, "self", lambda: (session, [session]))
    assert raised.value.closed
    assert raised.value.words == ("shop was not handed over: claude returned nothing. "
                                  "It is closed now; Enter on it resumes it.")
    monkeypatch.setattr(handover, "from_transcript", lambda s: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(actions.ActionError, match="^shop was not handed over: disk full.$"):
        actions.write_handover(session, "transcript", lambda: (session, [session]))


# --- a whole team's handover ------------------------------------------------------------------------

def shop_team():
    return teams.new_team("shop", "C:/shop", "A checkout", teams.parse_members(
        "lead: leads @opus; api: endpoints; ui: screens", lead_ok=True))


def member(name, running=True, state="idle"):
    return Session(session_id=name, path=None, cwd="C:/shop", title=f"shop-{name}",
                   exit=Exit.RUNNING if running else Exit.CLEAN, live_state=state)


@pytest.fixture
def team_calls(monkeypatch):
    said: list = []
    monkeypatch.setattr(handover, "work_left", lambda team, members: said.append("left") or "a file not committed")
    monkeypatch.setattr(handover, "close", lambda s, open_ones, again="": said.append(("close", s.title, again)))
    monkeypatch.setattr(handover, "write_team", lambda lead, team, left: said.append(("write", lead.title, left))
                        or ("## Goal\nA checkout.", "api: endpoints @sonnet"))
    return said


def test_a_team_handed_over_closes_the_rest_then_its_lead_which_writes(team_calls):
    team = shop_team()
    members = {"lead": member("lead"), "api": member("api"), "ui": member("ui", running=False)}
    closed: list = []
    handed = actions.write_team_handover(team, members, lambda lead: (lead, list(members.values())),
                                         lambda names: closed.append(names) or ("closed", "information"))
    assert handed == actions.TeamHanded(members["lead"], "## Goal\nA checkout.", "api: endpoints @sonnet", True)
    assert closed == [["shop-api"]]  # only the open ones, never the lead's here
    assert team_calls[0] == "left"  # what git shows, read before anyone closes
    assert team_calls[1][:2] == ("close", "shop-lead") and "h twice again" in team_calls[1][2]
    assert team_calls[2] == ("write", "shop-lead", "a file not committed")


def test_no_line_up_suggested_keeps_the_teams_own(team_calls, monkeypatch):
    monkeypatch.setattr(handover, "write_team", lambda lead, team, left: ("## Goal", ""))
    team = shop_team()
    handed = actions.write_team_handover(team, {"lead": member("lead", running=False)}, lambda lead: pytest.fail("closed"))
    assert handed.lineup == handover.current_lineup(team) and not handed.suggested
    assert actions.write_team_handover(team, {}, lambda lead: pytest.fail("no lead")) is None  # no session yet
    peers = teams.new_team("pals", "C:/pals", "goal", teams.parse_members("a: x; b: y"), peers=True)
    assert peers.lead is None and actions.write_team_handover(peers, {}, lambda lead: pytest.fail("no lead")) is None


def test_a_tab_that_would_not_close_stops_the_team_handover(team_calls):
    team = shop_team()
    members = {"lead": member("lead"), "api": member("api")}
    with pytest.raises(actions.ActionError) as raised:
        actions.write_team_handover(team, members, lambda lead: (lead, []),
                                    lambda names: ("Could not find the tab of shop-api: close it yourself (/exit).", "warning"))
    assert raised.value.words == ("shop was not handed over: Could not find the tab of shop-api: close it yourself "
                                  "(/exit). Members closed on the way stay closed: s in the team's view starts them again.")
    assert not any(call[0] in ("close", "write") for call in team_calls if isinstance(call, tuple))
