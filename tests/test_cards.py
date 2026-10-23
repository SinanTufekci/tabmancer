"""The cards as filters: 1 2 3 (or a click) show only what OPEN NOW, NEED YOU or TO CONTINUE
counts; the same key or Esc shows everything again; the tokens card opens the stats."""

from __future__ import annotations

from test_team_tree import shop

from tabmancer.app import SessionPicker
from tabmancer.statsview import StatsScreen


async def loaded(pilot, app):
    for _ in range(100):
        await pilot.pause(0.05)
        if app.sessions.get("lead-1") is not None and app.sessions["lead-1"].live_state and app.team_states:
            break
    await pilot.pause(0.2)


def shown(app) -> set[str]:
    return {s.session_id for s in app.visible()}


async def test_one_two_three_show_what_each_card_counts(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)  # the lead waits, "one" works, api was cut off
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        everything = shown(app)
        await pilot.press("2")
        assert shown(app) == {"lead-1"} and app.cards["card-waiting"].has_class("picked")
        assert "only NEED YOU" in app.sub_title
        await pilot.press("1")
        assert shown(app) == {"lead-1", "one"} and not app.cards["card-waiting"].has_class("picked")
        await pilot.press("3")
        assert "api-1" in shown(app) and not {"lead-1", "one", "two"} & shown(app)
        assert all(app.counted_by(app.sessions[sid], "continue") for sid in shown(app))
        await pilot.press("3")  # the same again: everything
        assert shown(app) == everything and app.card_filter is None
        await pilot.press("2", "escape")  # Esc clears a card's filter before it would quit
        assert shown(app) == everything and app.is_running


async def test_a_click_on_a_card_is_the_same_and_tokens_opens_the_stats(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        await pilot.click("#card-waiting")
        assert shown(app) == {"lead-1"}
        await pilot.click("#card-waiting")
        assert app.card_filter is None
        await pilot.click("#card-tokens")
        await pilot.pause(0.2)
        assert isinstance(app.screen, StatsScreen)
