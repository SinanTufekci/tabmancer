"""A team's cache: kept warm while its members wait in the day (their role says how), and a team
started after its cache has gone says first what going on costs and what a handover saves."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import teams
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit, Session
from tabmancer.teamview import cold_note


def team() -> teams.Team:
    return teams.new_team("shop", ".", "goal", teams.parse_members("lead\napi: endpoints\nui: screens", lead_ok=True))


def closed(context: int, hours: float) -> Session:
    return Session(session_id=f"s{context}{hours}", path=Path("x.jsonl"), context=context, exit=Exit.CLEAN,
                   last_active=datetime.now() - timedelta(hours=hours))


def test_a_role_says_to_keep_the_cache_warm_while_waiting_and_not_to_poll():
    shop = team()
    for member in shop.members:
        role = teams.role(shop, member)
        assert "sleep 3300 in the background" in role and "three times in a row at most" in role
        assert "never wait for one with sleep" in role


def test_the_note_counts_members_whose_cache_has_gone():
    shop = team()
    sessions = {"lead": closed(300_000, 14), "api": closed(220_000, 14), "ui": closed(40_000, 14)}
    note = cold_note(shop, sessions)
    assert note is not None and "520k of conversation again (2 sessions)" in note  # ui is too small to count
    assert "h twice hands it over" in note and "220k less" in note
    assert cold_note(shop, {"lead": closed(300_000, 14), "api": closed(220_000, 0.5)}) is None  # api still cached
    assert cold_note(shop, {"lead": closed(300_000, 14)}) is None  # the lead alone: a handover writes it again too
    running = closed(220_000, 14)
    running.exit = Exit.RUNNING
    assert cold_note(shop, {"api": running}) is None  # open: not started again


async def test_a_cold_team_starts_on_the_second_press(fake_home):
    shop = team()
    sessions = {"lead": closed(300_000, 14), "api": closed(220_000, 14)}
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        assert not app.cold_start_ok(shop, sessions)  # first: what it costs
        assert app.cold_start_ok(shop, sessions)  # again: it starts
        assert not app.cold_start_ok(shop, sessions)  # a later start says it again
        warm = {"lead": closed(300_000, 0.2), "api": closed(220_000, 0.2)}
        assert app.cold_start_ok(shop, warm)  # cache still there: at once
