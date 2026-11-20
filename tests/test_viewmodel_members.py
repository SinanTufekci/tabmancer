"""A team's members as the view carries them (G2): the CLI, the model and why, the quota, the
context, the bus, git and the last word, for the team monitor and any front end."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tabmancer import agy, teambus, teams
from tabmancer import app as app_module
from tabmancer import viewmodel as VM
from tabmancer.sessions import Exit, Session, Status

ARGON, HYDROGEN, FLASH = agy.CHAIN
SOON = datetime.now(timezone.utc) + timedelta(days=3)
ARGON_OUT = [["Gemini Models", "Weekly Limit Remaining", "80%", SOON.isoformat()],
             ["Gemini 4 Argon", "Gemini 4 Argon Weekly Limit", "0%", SOON.isoformat()]]


def session(sid: str, live: str = "busy", **more) -> Session:
    return Session(session_id=sid, path=None, cwd=f"C:/work/shop/{sid}", status=Status.RUNNING, exit=Exit.RUNNING,
                   live_state=live, **more)


def mixed_team(tmp_path) -> teams.Team:
    members = teams.parse_members("lead: plans @opus @high\napi: endpoints @sonnet\nui: screens @agy @opus\n"
                                  "qa: tests @agy @gemini-3.8-flash-high", lead_ok=True)
    team = teams.new_team("shop", str(tmp_path), "Build the shop", members, cap="200k")
    return team


@pytest.fixture
def quota(monkeypatch):
    """The saved agy quota a test sets: Argon out, the Gemini models at 80%, read just now."""
    monkeypatch.setattr(agy, "installed", lambda: True)
    data = {"read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": ARGON_OUT, "out": {},
            "unreadable": ""}
    agy._save_quota(data)
    return data


def test_a_mixed_teams_members_carry_cli_model_quota_and_state(tmp_path, quota):
    team = mixed_team(tmp_path)
    warm = datetime.now() + timedelta(minutes=40)
    lead = session("lead-1", "busy", model="claude-opus-5-5", context=50_000)
    api = session("api-1", "idle", model="claude-sonnet-5-5", context=120_000, warm_until=warm, turn_over=True,
                  fell_back="opus 5.5 → sonnet 5.5")
    said = [teams.Message(datetime.now() - timedelta(minutes=5), "lead", "api", "make the endpoint"),
            teams.Message(datetime.now() - timedelta(minutes=1), "api", "lead", "long text", summary="endpoint done")]
    state = teams.TeamState(team, {"lead": lead, "api": api, "ui": None, "qa": None}, said)
    git = {"C:/work/shop/api-1": {"branch": "worktree-shop-api", "changed": 3, "ahead": 2}}
    view = VM.team_view(state, {"lead-1": SimpleNamespace(total=1_100_000)}, {}, git_of=git.get, bus=({}, {}))
    by = {m.name: m for m in view.members}

    assert view.clis == ["claude", "agy"] and view.uses_bus and view.lead == "lead" and not view.peers
    assert view.cap == 200_000 and view.folder == str(tmp_path)

    assert by["lead"].cli == "claude" and by["lead"].model == "claude-opus-5-5" and by["lead"].effort == "high"
    assert by["lead"].context == pytest.approx(0.25) and by["lead"].cap == 200_000
    assert by["lead"].tokens == 1_100_000 and by["lead"].quota is None and not by["lead"].needs_you

    assert by["api"].needs_you and by["api"].warm_until == warm and by["api"].fell_back == "opus 5.5 → sonnet 5.5"
    assert by["api"].git == {"branch": "worktree-shop-api", "changed": 3, "ahead": 2}
    assert by["api"].last is not None and by["api"].last.text == "endpoint done" and by["api"].last.receiver == "lead"
    assert by["lead"].last == by["api"].last  # the newest to or from it

    # agy, not started: the model it starts with, never Claude; Argon out, so Hydrogen, with why.
    ui = by["ui"]
    assert ui.cli == "agy" and ui.model == HYDROGEN and not agy.barred(ui.model) and ui.effort == ""
    assert "opus never runs through agy" in ui.model_note and ui.state == "not-started"
    assert ui.context is None and ui.quota is not None and ui.quota["left"] == 80.0 and ui.quota["span"] == "week"
    assert by["qa"].model == FLASH and by["qa"].model_note == ""  # a chain model written for it: kept


def test_an_agy_member_out_of_quota_says_when_it_is_back(tmp_path, quota):
    members = teams.parse_members("lead: plans\nui: screens @agy @gemini-4-argon-high", lead_ok=True)
    team = teams.new_team("shop", str(tmp_path), "Build it", members)
    ui = session("ui-1", "busy", cli="agy", model=ARGON)  # it runs on Argon: Argon it says
    view = VM.team_view(teams.TeamState(team, {"lead": None, "ui": ui}, []), {}, {}, bus=({}, {}))
    standing = view.members[1].quota
    assert view.members[1].model == ARGON
    assert standing["left"] == 0.0 and standing["back"] is not None and standing["back"].tzinfo is None


def test_a_member_on_the_bus_shows_its_runner_listener_and_unread(tmp_path, monkeypatch):
    team = mixed_team(tmp_path)
    started = "2026-11-20T14:21:00+00:00"
    monkeypatch.setattr(teambus, "read_presence", lambda name: {
        "ui": {"member": "ui", "runner_alive": True, "listening": False, "started": started}})
    monkeypatch.setattr(teambus, "unread", lambda name, who, **_: [{"id": "1"}] if who.endswith("ui") else [])
    view = VM.team_view(teams.TeamState(team, {m.name: None for m in team.members}, []), {}, {})
    by = {m.name: m for m in view.members}
    assert by["ui"].bus == {"runner": True, "listening": False,
                            "since": datetime.fromisoformat(started).astimezone().replace(tzinfo=None)}
    assert by["ui"].unread == 1 and by["qa"].bus is None and by["qa"].unread == 0


def test_a_claude_only_team_reads_no_bus(tmp_path, monkeypatch):
    monkeypatch.setattr(teambus, "read_presence", lambda name: pytest.fail("a Claude team has no bus"))
    team = teams.new_team("shop", str(tmp_path), "Build it", teams.parse_members("api: endpoints; ui: screens"))
    view = VM.team_view(teams.TeamState(team, {m.name: None for m in team.members}, []), {}, {})
    assert not view.uses_bus and view.clis == ["claude"] and all(m.bus is None for m in view.members)


def test_the_view_carries_agys_quota_and_goes_out_as_json(tmp_path, quota):
    team = mixed_team(tmp_path)
    state = teams.TeamState(team, {m.name: None for m in team.members}, [])
    view = VM.build({}, hidden=set(), pinned=set(), git={}, team_states={}, tokens={}, limits=[], armed={},
                    queued={})
    assert [s["model"] for s in view.agy_quota["chain"]] == list(agy.CHAIN)
    assert view.agy_quota["pick"] == HYDROGEN and view.agy_quota["chain"][0]["left"] == 0.0
    team_view = VM.team_view(state, {}, {}, bus=({}, {}))
    data = json.loads(json.dumps(VM.to_json(VM.View(datetime.now(), [], [], [team_view], [], {},
                                                    agy_quota=view.agy_quota))))
    assert data["teams"][0]["members"][2]["quota"]["left"] == 80.0
    assert data["agy_quota"]["chain"][0]["back"]  # a time, as text


def test_no_agy_no_quota(monkeypatch):
    monkeypatch.setattr(agy, "installed", lambda: False)
    assert agy.quota_view() == {}


def test_standing_from_nothing_known_says_nothing(monkeypatch):
    assert agy.standing(ARGON) == {"model": ARGON, "name": "Argon", "left": None, "span": "", "back": None}
    assert agy.standing("gemini-4-neon-high")["left"] is None  # no row of its own


def test_the_dashboard_draws_the_view_models_member_words_and_team_keys():
    assert app_module.TEAM_GLYPHS == {state: (glyph, app_module.TONE[tone])
                                      for state, (glyph, tone) in VM.TEAM_GLYPHS.items()}
    assert VM.member_words(None) == ("not started", "done")
    busy = session("x", "busy")
    assert VM.member_words(busy) == ("✶ working", "working")
    assert VM.member_words(session("y", "blocked"))[0].startswith("! ")
    done = Session(session_id="z", path=None, cwd="C:/w", status=Status.DONE, exit=Exit.CLEAN)
    assert VM.member_words(done) == VM.STATUS[Status.DONE]
    assert ("s", "start the team") in VM.team_key_pairs(False, False)
    assert ("e", "fold") in VM.team_key_pairs(True, True) and ("e", "unfold") in VM.team_key_pairs(True, False)
