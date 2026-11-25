"""The board's lanes (viewmodel.board): the cards' rules, a team as one card, pinned first."""

from __future__ import annotations

from datetime import datetime, timedelta

from tabmancer import teams
from tabmancer import viewmodel as VM
from tabmancer.sessions import Exit, Session, Status


def made(sid: str, status: Status = Status.DONE, exit: Exit = Exit.CLEAN, live: str = "", project: str = "",
         minutes: int = 0) -> Session:
    return Session(session_id=sid, path=None, cwd=f"C:/work/{project or sid}", status=status, exit=exit,
                   live_state=live, last_active=datetime.now() - timedelta(minutes=minutes))


def running(sid: str, live: str, **more) -> Session:
    return made(sid, Status.RUNNING, Exit.RUNNING, live, **more)


def world() -> list[Session]:
    return [running("asks", "blocked"), running("waits", "idle"), running("busy", "busy"),
            made("cut", Status.CUT_OFF, minutes=5), made("done", minutes=9), made("old", minutes=60)]


def shop(lead: Session, api: Session, ui: Session | None = None) -> dict[str, teams.TeamState]:
    team = teams.new_team("shop", "C:/work/shop", "Add a checkout", teams.parse_members("api: endpoints; ui: screens"))
    return {"shop": teams.TeamState(team, {"lead": lead, "api": api, "ui": ui}, [])}


def lanes_of(lanes: list[VM.Lane]) -> dict[str, list[str]]:
    return {lane.key: lane.items for lane in lanes}


def test_each_session_is_in_the_lane_its_card_counts_it_in():
    shown = VM.visible(world(), lambda s: False)
    lanes = VM.board(shown, {}, set())
    assert [lane.key for lane in lanes] == ["waiting", "open", "continue", "done"]
    assert [lane.title for lane in lanes[:3]] == [VM.CARDS[lane.key][1] for lane in lanes[:3]]  # the cards' titles
    assert lanes_of(lanes) == {"waiting": ["asks", "waits"], "open": ["busy"], "continue": ["cut"],
                               "done": ["done", "old"]}
    items = [item for lane in lanes for item in lane.items]
    assert sorted(items) == sorted(s.session_id for s in shown)  # each in exactly one lane
    for lane in lanes[:3]:  # NEED YOU and TO CONTINUE are the cards' numbers; an open one needing you is not twice
        assert all(VM.counted_by(s, lane.key) for s in shown if s.session_id in lane.items)
    assert [lane.count for lane in lanes] == [2, 1, 1, 2]
    assert sum(VM.counted_by(s, "waiting") for s in shown) == lanes[0].count
    assert [lane.folded for lane in lanes] == [False, False, False, True]  # DONE folded, with its count


def test_what_a_lane_holds_is_said_as_on_its_card():
    shown = VM.visible([*world(), running("more", "busy"), running("rests", "idle", project="asks")], lambda s: False)
    need, opened, later, done = VM.board(shown, {}, set())
    assert need.words == ["asks ×2", "waits"]  # the projects, the busiest first
    assert opened.words == ["2 working"]
    assert later.words == ["cut"] and done.words == [] and done.caption


def test_pinned_cards_come_first_and_hidden_ones_are_left_out():
    sessions = world()
    hidden = {"waits"}
    shown = VM.visible(sessions, lambda s: s.session_id in hidden)
    lanes = VM.board(shown, {}, {"old"})
    assert lanes_of(lanes)["done"] == ["old", "done"]  # pinned on top, then the list's order (newest first)
    assert lanes_of(lanes)["waiting"] == ["asks"]
    shown = VM.visible(sessions, lambda s: s.session_id in hidden, show_hidden=True)  # X
    assert lanes_of(VM.board(shown, {}, set()))["waiting"] == ["asks", "waits"]


def test_a_team_is_one_card_in_the_lane_of_its_most_urgent_member():
    lead, api, ui = running("lead-1", "busy", project="shop"), running("api-1", "busy", project="shop"), \
        made("ui-1", project="shop")
    states = shop(lead, api, ui)
    shown = VM.visible([lead, api, ui, *world()], lambda s: False)
    lanes = VM.board(shown, states, set())
    assert lanes_of(lanes)["open"] == ["busy", "team:shop"]  # where its first member comes (by project)
    assert not any(item.startswith(("api", "lead")) for lane in lanes for item in lane.items)
    assert lanes_of(lanes)["done"] == ["done", "old"]  # ui is on the team's card, not in DONE
    assert lanes[3].away == {"shop": 1} and lanes[3].count == 3  # ...yet counted there, and said so
    api.live_state = "blocked"  # api needs you: the whole team's card goes to NEED YOU
    lanes = VM.board(VM.visible([lead, api, ui, *world()], lambda s: False), states, set())
    assert "team:shop" in lanes_of(lanes)["waiting"] and "team:shop" not in lanes_of(lanes)["open"]
    assert lanes[0].count == 3 and lanes[1].count == 2 and lanes[1].away == {"shop": 1}
    assert sum(item == "team:shop" for lane in lanes for item in lane.items) == 1


def test_a_team_with_a_pinned_member_comes_first_in_its_lane():
    lead, api = made("lead-1", project="shop", minutes=50), made("api-1", project="shop", minutes=40)
    shown = VM.visible([lead, api, *world()], lambda s: False)
    assert lanes_of(VM.board(shown, shop(lead, api), set()))["done"] == ["done", "team:shop", "old"]
    assert lanes_of(VM.board(shown, shop(lead, api), {"api-1"}))["done"] == ["team:shop", "done", "old"]


def test_a_team_whose_members_are_all_hidden_has_no_card():
    lead, api = running("lead-1", "busy", project="shop"), running("api-1", "idle", project="shop")
    shown = VM.visible([lead, api, *world()], lambda s: s.session_id in {"lead-1", "api-1"})
    assert not any(item.startswith(VM.TEAM_CARD) for lane in VM.board(shown, shop(lead, api), set())
                   for item in lane.items)


def test_no_sessions_no_cards():
    lanes = VM.board([], {}, set())
    assert [lane.items for lane in lanes] == [[], [], [], []] and [lane.count for lane in lanes] == [0, 0, 0, 0]
    assert all(lane.caption for lane in lanes)
