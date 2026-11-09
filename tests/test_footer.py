"""The footer follows the row: a key says what it does there, a key that does nothing there is
dimmed (never hidden), and the keys keep to the ? screen's groups."""

from __future__ import annotations

from test_team_tree import plain, ready, shop
from textual.widgets._footer import FooterKey

from tabmancer import sessions
from tabmancer.app import TEAM_ROW, SessionPicker


def footer(app) -> dict[str, tuple[str, bool]]:
    """action -> (its words, dimmed), as the footer shows them now."""
    return {key.action: (key.description, key.has_class("-disabled")) for key in app.query(FooterKey)}


async def whole_footer(pilot, app) -> dict[str, tuple[str, bool]]:
    """The footer once it is made again: a slow runner can be read in the middle of that (seen on CI)."""
    for _ in range(80):
        await pilot.pause(0.05)
        keys = footer(app)
        if {"open", "keys", "next_waiting", "quit"} <= keys.keys() and app.footer_was == app.footer_context():
            return keys
    raise AssertionError("the footer was never made again")


async def on_row(pilot, app, row: str) -> dict[str, tuple[str, bool]]:
    app.table.move_cursor(row=app.table.get_row_index(row))
    await pilot.pause(0.2)
    return await whole_footer(pilot, app)


async def test_the_footer_says_what_each_key_does_on_the_row(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 44)) as pilot:
        await ready(pilot, app)
        app.selected.clear()
        keys = await on_row(pilot, app, f"{TEAM_ROW}shop")  # an open team, folded
        assert keys["open"] == ("Team view", False) and keys["summarize"][0] == "Start rest"
        assert keys["hide"][0] == "Close team" and keys["prompt"] == ("Prompt", True)  # dimmed, still there
        assert keys["rename"][1] and keys["pin"][1] and keys["cycle_mode"][1]
        assert keys["handover"] == ("Hand team over", False)  # the whole team, as h in its view
        app.toggle_folded("shop")
        await pilot.pause(0.2)
        assert (await whole_footer(pilot, app))["open"][0] == "Team view"
        keys = await on_row(pilot, app, "one")  # an open session
        assert keys["open"][0] == "Its tab" and keys["toggle"][1] and keys["cycle_mode"][1]
        assert keys["summarize"] == ("Summary", False) and keys["hide"] == ("Hide", True)
        keys = await on_row(pilot, app, "two")  # a closed one: every key as it is named
        assert keys["open"] == ("Open", False) and not keys["toggle"][1] and not keys["cycle_mode"][1]
        app.toggle_selected("two")
        await pilot.pause(0.2)
        assert (await whole_footer(pilot, app))["open"][0] == "Open 1"


async def test_a_dimmed_key_does_nothing_and_j_dims_when_nobody_waits(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "one", "kind": "interactive", "status": "busy"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions.get("one") is not None and app.sessions["one"].live_state:
                break
        keys = await on_row(pilot, app, "one")
        assert keys["next_waiting"][1]  # "one" works: nobody waits for you
        await pilot.press("space")
        assert "one" not in app.selected


async def test_the_keys_keep_to_their_groups(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.pause(0.2)
        await whole_footer(pilot, app)  # not in the middle of making it (seen on the Windows runner)
        starts = [key.action for key in app.query(FooterKey) if key.has_class("-group-start")]
        assert starts == ["prompt", "hide", "toggle_live", "quit"]  # after pick: work, tidy, views, the rest


async def test_the_preview_says_what_enter_does_and_the_bar_keeps_the_ticks(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "one", "kind": "interactive", "status": "idle"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions.get("one") is not None and app.sessions["one"].live_state:
                break
        app.selected.clear()
        assert app.enter_words(app.sessions["one"]) == "goes to its tab, to type there"
        two = app.sessions["two"]
        assert app.enter_words(two) == "resumes it in a new tab · m changes how"
        for _ in range(40):  # on its row, and still there when m goes (a poll may make the rows again)
            app.table.move_cursor(row=app.table.get_row_index("two"))
            await pilot.pause(0.05)
            if app.cursor_key() == "two":
                break
        await pilot.press("m")  # resume → auto-continue
        assert app.enter_words(two).startswith("resumes it in a new tab and tells Claude to carry on")
        assert "tells Claude to carry on" in plain(app.detail.content)
        await pilot.press("space")
        assert app.enter_words(two) == "opens the 1 ticked session, each in a tab"
        assert app.enter_words(app.sessions["one"]).endswith("(not this one: Space ticks it)")
        await pilot.press("2")  # a card's filter too: both stay whole at 120 columns
        pills = plain(app.topbar.query_one("#pills").content)
        assert "1 ticked" in pills and "only NEED YOU" in pills
