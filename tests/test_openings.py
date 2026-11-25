"""openings.Openings: what opening sessions keeps between presses, one copy for every front end (G3.3),
on a clock of its own."""

from __future__ import annotations

from pathlib import Path

from tabmancer import actions, openings, teams
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def made(sid: str, status: Status = Status.DONE, exit: Exit = Exit.CLEAN, live: str = "",
         cwd: str = "C:/work/shop") -> Session:
    return Session(session_id=sid, path=Path(f"{sid}.jsonl"), cwd=cwd, title=f"task {sid}", first_prompt="",
                   started=None, last_active=None, status=status, exit=exit, live_state=live)


def resumed(sid: str) -> Launch:
    return Launch(title=sid, cwd="C:/work", mode=Mode.RESUME, session_id=sid)


def test_the_ones_opened_a_moment_ago_are_not_chosen_again_while_they_start():
    clock = Clock()
    kept = openings.Openings(clock=clock)
    one, two = made("one"), made("two")
    assert kept.choose([one, two]) == [one, two]
    assert kept.took(actions.Opened([resumed("one")], [], [])) == {"one"}
    assert kept.recent() == {"one"} and kept.choose([one, two]) == [two]
    clock.now += actions.OPENING_SECONDS
    assert kept.recent() == set() and kept.choose([one, two]) == [one, two]


def test_nothing_opened_keeps_nothing():
    kept = openings.Openings(clock=Clock())
    assert kept.took(actions.Opened([], [resumed("one")], [])) == set()
    assert kept.took(actions.Opened([], [], ["claude --resume one"])) == set()
    assert kept.opening == {} and kept.checks == []


def test_a_fresh_one_is_kept_as_opening_but_never_looked_for():
    kept = openings.Openings(clock=Clock())
    fresh = Launch(title="new", cwd="C:/work", mode=Mode.NEW, session_id="n1")
    assert kept.took(actions.Opened([fresh, resumed("r1")], [], [])) == {"r1"}
    assert kept.opening.keys() == {"n1", "r1"} and kept.checks == [(100.0 + openings.CAME_UP_SECONDS, {"r1"})]


def test_one_that_never_showed_open_is_named_once_when_its_check_is_due():
    clock = Clock()
    kept = openings.Openings(clock=clock)
    kept.took(actions.Opened([resumed("up"), resumed("gone")], [], []))
    now = {"up": made("up", Status.RUNNING, Exit.RUNNING, "busy"), "gone": made("gone")}
    clock.now += 10
    kept.seen(now)
    assert kept.came_up == {"up"} and kept.due(now) is None  # not yet
    now["up"] = made("up")  # closed again since: it did come up
    clock.now += openings.CAME_UP_SECONDS
    assert kept.due(now) == "shop did not come up: its tab says why."
    assert kept.due(now) is None and kept.checks == []  # said once


def test_opened_again_it_has_to_come_up_again():
    kept = openings.Openings(clock=Clock(), came_up={"one"})
    kept.took(actions.Opened([resumed("one")], [], []), look_later=False)
    assert kept.came_up == set() and kept.checks == []  # a front with its own timer looks itself


def test_seen_takes_only_the_ones_opened_from_here_that_show_open():
    kept = openings.Openings(clock=Clock(), opening={"mine": 1.0, "shut": 1.0})
    kept.seen({"mine": made("mine", Status.RUNNING, Exit.RUNNING, "idle"), "shut": made("shut"),
               "other": made("other", Status.RUNNING, Exit.RUNNING, "busy")})
    assert kept.came_up == {"mine"}


def test_launches_open_as_m_says_the_lead_last_and_attach_to_a_background_one(monkeypatch):
    saved = {"shop": object()}
    monkeypatch.setattr(teams, "load", lambda: saved)
    asked = []

    def lead_last(sessions, given):  # the lead is found by the saved teams: here, the first given
        asked.append(given)
        return sessions[1:] + sessions[:1]

    monkeypatch.setattr(teams, "lead_last", lead_last)
    monkeypatch.setattr(teams, "as_member", lambda session, plain, given=None: plain)
    kept = openings.Openings(clock=Clock())
    lead, api, bg = made("l1"), made("a1"), made("b1", Status.BACKGROUND, Exit.RUNNING, "busy")
    modes = {"a1": Mode.FORK}
    out = kept.launches([lead, api, bg], lambda s: modes.get(s.session_id, Mode.RESUME),
                        {"b1": {"id": "7f3a", "sessionId": "b1"}, "l1": {"id": "nope"}})
    assert [(launch.session_id, launch.mode) for launch in out] == [
        ("a1", Mode.FORK), ("7f3a", Mode.ATTACH), ("l1", Mode.RESUME)]  # a closed one is never attached to
    assert asked == [saved]
    other = {"other": object()}  # given: not loaded again
    assert kept.launches([api], lambda s: Mode.NEW, {}, saved=other)[0].mode is Mode.NEW
    assert asked == [saved, other]
