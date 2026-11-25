"""What the dashboard says when a session or a team is handed over, with no UI (handoverwords.py): the
h dialog's ways and words, the notes, the team handover dialog's line-up preview. test_handover and
test_team_handover show them as the terminal draws them; test_desktop_extra proves the module loads
with neither Textual nor Qt."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tabmancer import clis, handover, handoverwords, picks, teams
from tabmancer.palette import AMBER, GREEN
from tabmancer.sessions import Exit, Session, Status


def s(cli="claude", exit=Exit.CLEAN, state="") -> Session:
    return Session(session_id="s1", path=Path("C:/nowhere/s1.jsonl"), cwd="C:/work/shop", title="t", cli=cli,
                   exit=exit, status=Status.RUNNING if exit is Exit.RUNNING else Status.DONE, live_state=state,
                   last_active=datetime.now())


def test_the_ways_the_session_writes_it_first_where_it_can(monkeypatch):
    assert [w.id for w in handoverwords.ways(s())] == ["self", "transcript"]
    assert [w.id for w in handoverwords.ways(s("codex"))] == ["transcript"]
    monkeypatch.setattr(clis, "installed", lambda name: None)
    assert [w.id for w in handoverwords.ways(s("agy"))] == ["transcript"]
    monkeypatch.setattr(clis, "installed", lambda name: "C:/bin/agy.exe")
    first = handoverwords.ways(s("agy"))[0]
    assert first.id == "self" and "on the chain's model (agy)" in first.how
    assert handover.MODEL in handoverwords.ways(s())[1].how


def test_what_handing_over_does():
    closed = handoverwords.about(s())
    assert "closed first" not in closed and "/compact" not in closed
    busy = handoverwords.about(s(exit=Exit.RUNNING, state="busy"))
    assert "closed first" in busy and "as soon as it finishes" in busy and "/compact" in busy
    idle = handoverwords.about(s(exit=Exit.RUNNING, state="idle"))
    assert "as soon as it finishes" not in idle


def test_the_notes():
    note = handoverwords.started_note(s(exit=Exit.RUNNING, state="idle"), "self")
    assert note.words.startswith("Closing it; it writes its handover") and note.title == "Handing over shop"
    assert handoverwords.started_note(s(), "transcript").words.startswith("Claude (")
    kept = handoverwords.handed_note(s(), "its tab would not close")
    assert kept.severity == "warning" and kept.seconds == 15 and kept.words.endswith("its tab would not close.")
    assert handoverwords.handed_note(s()).severity == "information"
    team = teams.new_team("shop", "C:/work/shop", "g", teams.parse_members("lead: steer\napi: a", lead_ok=True))
    assert handoverwords.team_handed_note(team, team).words.startswith("shop handed over: a fresh lead with the "
                                                                       "handover and 1 fresh member.")
    assert handoverwords.team_not_started_note(team).seconds == 10


def test_the_line_up_preview():
    team = teams.new_team("shop", "C:/work/shop", "g", teams.parse_members("lead: steer\napi: a", lead_ok=True))
    assert handoverwords.lineup_problems(team, []) == ["a member at least, one a line"]
    members = teams.parse_members("api: build @sonnet @high\nui: screens @agy @neon")
    parts = handoverwords.lineup_parts(members, 300_000, [])
    said = "".join(w for w, _ in parts)
    assert said.startswith("Opens 3 tabs: the lead, with the handover\n● api  sonnet/high\n● ui  agy · ")
    assert said.endswith("\nEach under a 300k context cap.") and parts[1] == ("3 tabs", f"bold {GREEN}")
    loud = handoverwords.lineup_parts([], 0, ["a member at least, one a line"])
    assert loud[-1] == ("\n▲ needs a member at least, one a line", AMBER)
    assert handoverwords.cap_words(picks.NO_CAP).startswith("Claude's own: ")
