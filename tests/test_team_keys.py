"""A team's keys in one copy (teamkeys.TeamKeys), with no UI: what s, x, Enter, a, d and g say and do
through a surface that records, the presses that need a second one, and the timer that forgets them."""

from __future__ import annotations

from pathlib import Path

from tabmancer import actions, teamkeys, teams
from tabmancer.sessions import Exit, Session, Status


class Recorder:
    """A surface that keeps what it was asked; its timers wait in `timers` until fired."""

    def __init__(self, recent: set[str] | None = None, cold_ok: bool = True) -> None:
        self.calls: list = []
        self.timers: list = []
        self.kept: dict = {"shop": "a handover written before"}
        self.recent_ids = recent
        self.cold_ok = cold_ok
        self.notes_held: dict = {}

    def say(self, note) -> None:
        self.calls.append(("say", note.words, note.severity, note.seconds))

    def later(self, seconds, then) -> None:
        self.timers.append((seconds, then))

    def open_team(self, team, launches) -> None:
        self.calls.append(("open_team", team.name, [launch.title for launch in launches]))

    def open_sessions(self, launches) -> None:
        self.calls.append(("open_sessions", [launch.title for launch in launches]))

    def cold_start_ok(self, team, members) -> bool:
        self.calls.append(("cold_start_ok", team.name))
        return self.cold_ok

    def recent(self):
        return self.recent_ids

    def kept_handovers(self) -> dict:
        return self.kept

    def close_tabs(self, names) -> None:
        self.calls.append(("close_tabs", names))

    def adopt(self, team) -> None:
        self.calls.append(("adopt", [m.name for m in team.members]))

    def go_to_tab(self, session) -> None:
        self.calls.append(("go_to_tab", session.session_id))

    def put_on_clipboard(self, text) -> None:
        self.calls.append(("clipboard", text[:40]))


def shop(tmp_path) -> teams.Team:
    team = teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: x; ui: y"))
    teams.save({"shop": team})
    return team


def closed(sid: str) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), exit=Exit.CLEAN, title=f"shop-{sid}")


def opened(sid: str, state: str = "idle", status: Status = Status.RUNNING) -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), exit=Exit.RUNNING, status=status, live_state=state,
                   title=f"shop-{sid}")


def test_x_twice_closes_the_open_members_and_the_timer_forgets_the_first(fake_home, tmp_path):
    team, surface = shop(tmp_path), Recorder()
    keys = teamkeys.TeamKeys(surface)
    members = {"lead": opened("L"), "api": opened("A", "busy"), "ui": None}
    keys.close(team, {"lead": closed("L"), "api": None, "ui": None})
    assert surface.calls == [("say", teamkeys.NOBODY_OPEN, "information", 3)] and keys.closing is None
    surface.calls.clear()
    keys.close(team, members)
    assert keys.closing == "shop" and [c[0] for c in surface.calls] == ["say"] and not any(
        c[0] == "close_tabs" for c in surface.calls)
    assert surface.timers[0][0] == teamkeys.CLOSE_AGAIN_SECONDS
    surface.timers[0][1]()  # six seconds on: the first press is forgotten
    assert keys.closing is None
    keys.close(team, members)
    keys.close(team, members)
    assert surface.calls[-1] == ("close_tabs", ["shop-lead", "shop-api"]) and keys.closing is None


def test_s_drops_the_kept_handover_leaves_the_opening_ones_and_asks_a_cold_team(fake_home, tmp_path):
    team = shop(tmp_path)
    members = {"lead": closed("L"), "api": closed("A"), "ui": None}
    surface = Recorder(recent={"L"})
    teamkeys.TeamKeys(surface).start(team, members)
    assert surface.kept == {}
    assert surface.calls == [("cold_start_ok", "shop"), ("open_team", "shop", ["shop: api", "shop: ui"])]
    cold = Recorder(cold_ok=False)
    teamkeys.TeamKeys(cold).start(team, members)
    assert cold.calls == [("cold_start_ok", "shop")]  # it said what going on costs: nothing opens
    everyone = Recorder(recent={"U"})
    teamkeys.TeamKeys(everyone).start(team, {"lead": opened("L"), "api": opened("A"), "ui": closed("U")})
    assert everyone.calls == [("say", "Every member of shop is open already, or opening.", "information", 3)]


def test_a_cold_start_needs_a_second_press(fake_home, tmp_path, monkeypatch):
    team, surface = shop(tmp_path), Recorder()
    keys = teamkeys.TeamKeys(surface)
    monkeypatch.setattr(actions, "cold_note", lambda team, members: "its cache is gone")
    assert not keys.cold_start_ok(team, {}) and keys.cold == "shop"
    assert surface.calls == [("say", "its cache is gone", "information", teamkeys.COLD_AGAIN_SECONDS)]
    assert keys.cold_start_ok(team, {}) and keys.cold is None
    monkeypatch.setattr(actions, "cold_note", lambda team, members: None)
    assert keys.cold_start_ok(team, {})  # warm: at once


def test_enter_on_a_member(fake_home, tmp_path):
    team, surface = shop(tmp_path), Recorder()
    keys = teamkeys.TeamKeys(surface)
    lead, api, ui = team.members
    keys.open_member(team, None, None)
    keys.open_member(team, api, opened("A", "busy", Status.BACKGROUND))
    keys.open_member(team, lead, opened("L"))
    keys.open_member(team, ui, closed("U"))
    assert surface.calls == [("say", teamkeys.PICK_FIRST, "information", 3),
                             ("say", "api runs in the background, with no tab: B attaches to it.", "information", 5),
                             ("go_to_tab", "L"), ("open_sessions", ["shop: ui"])]


def test_a_on_a_closed_team_saves_them_and_on_one_at_work_opens_them(fake_home, tmp_path):
    team, surface = shop(tmp_path), Recorder()
    keys = teamkeys.TeamKeys(surface)
    keys.add(team, "qa: tests", at_work=False)
    assert surface.calls == [("adopt", ["lead", "api", "ui", "qa"]),
                             ("say", "qa joined shop: they start with the team (Enter on its row).", "information", 6)]
    surface.calls.clear()
    keys.add(teams.load()["shop"], "docs: guide", at_work=True)
    assert surface.calls == [("adopt", ["lead", "api", "ui", "qa", "docs"]), ("open_team", "shop", ["shop: docs"])]
    surface.calls.clear()
    keys.add(teams.load()["shop"], None, at_work=True)  # Esc: nothing
    assert all(c[0] == "say" for c in surface.calls)


def test_d_twice_takes_a_member_off_and_the_lead_hears(fake_home, tmp_path):
    team, surface = shop(tmp_path), Recorder()
    keys = teamkeys.TeamKeys(surface)
    lead, api, _ = team.members
    assert keys.remove(team, None, None) is None and keys.remove(team, lead, None) is None
    assert [c[2] for c in surface.calls] == ["information", "warning"] and keys.removing is None
    assert keys.remove(team, api, None) is None and keys.removing == "api"
    assert [m.name for m in teams.load()["shop"].members] == ["lead", "api", "ui"]
    changed = keys.remove(team, api, None)
    assert changed is not None and keys.removing is None
    surface.calls.clear()
    members = {"lead": opened("L"), "api": None, "ui": None}
    keys.changed(changed, members)
    assert surface.calls[0] == ("adopt", ["lead", "ui"])
    assert ("go_to_tab", "L") in surface.calls and surface.calls[-1][0] == "say"  # no bus: the clipboard, its tab
    surface.calls.clear()
    keys.changed(None, members)  # g with the goal as it was
    assert surface.calls == []
