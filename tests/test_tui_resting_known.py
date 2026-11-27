"""The terminal dashboard: a member done for now rests (waits for its lead, not for you) from the moment its team is
known (show_teams), not from the first live read that brings that member up to date, which could be the full one 16 s
on; till then its row said, in red, that it waited for you."""

from __future__ import annotations

from types import SimpleNamespace

from tabmancer import teams
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit, Session


def open_one(sid: str, state: str) -> Session:
    return Session(session_id=sid, path=None, cwd="C:/shop", title=sid, exit=Exit.RUNNING, live_state=state)


def picker(sessions: dict[str, Session]) -> SimpleNamespace:
    """What show_teams asks of the dashboard, standing in (no app, no screen)."""
    synced: list[set[str]] = []
    return SimpleNamespace(
        engine=SimpleNamespace(team_sessions=lambda updates: set(), heard=lambda states: []),
        team_flights={}, team_graph=None, team_states={}, sessions=sessions, synced=synced, sync_rows=synced.append)


def shop(sessions: dict[str, Session], peers: bool = False) -> dict[str, teams.TeamState]:
    team = teams.new_team("shop", "C:/shop", "goal", teams.parse_members("api: a; ui: b"), peers=peers)
    names = [m.name for m in team.members]
    return {"shop": teams.TeamState(team, dict(zip(names, sessions.values())), [])}


def test_a_member_done_for_now_rests_as_soon_as_its_team_is_known():
    lead, api, ui = open_one("s-lead", "idle"), open_one("s-api", "idle"), open_one("s-ui", "working")
    shown = picker({s.session_id: s for s in (lead, api, ui)})
    states = shop({"lead": lead, "api": api, "ui": ui})
    assert states["shop"].team.lead is not None and states["shop"].team.lead.name == "lead"
    SessionPicker.show_teams(shown, states)
    assert shown.sessions["s-api"].live_state == "resting"  # waits for its lead
    assert shown.sessions["s-ui"].live_state == "working"  # at work: as it was
    assert shown.sessions["s-lead"].live_state == "idle"  # the lead, idle, waits for you
    assert shown.synced == [{"s-lead", "s-api", "s-ui"}]  # their rows drawn again


def test_a_team_of_peers_has_nobody_resting():
    one, two = open_one("s-api", "idle"), open_one("s-ui", "done")
    shown = picker({s.session_id: s for s in (one, two)})
    states = shop({"api": one, "ui": two}, peers=True)
    assert states["shop"].team.lead is None
    SessionPicker.show_teams(shown, states)
    assert [s.live_state for s in shown.sessions.values()] == ["idle", "done"]  # each, idle, waits for you


def test_the_same_teams_again_change_nothing():
    api = open_one("s-api", "idle")
    lead = open_one("s-lead", "working")
    shown = picker({s.session_id: s for s in (lead, api)})
    states = shop({"lead": lead, "api": api, "ui": None})
    shown.team_states = states
    SessionPicker.show_teams(shown, states)
    assert api.live_state == "idle" and shown.synced == []  # nothing new was read: left to the next change
