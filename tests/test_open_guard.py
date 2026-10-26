"""A session a Claude process has open right now is never opened a second time: two processes on
one transcript would both write to it. The list can be a moment behind, so opening asks afresh."""

import pytest
from conftest import box_has_focus

from tabmancer import app as app_module
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode


@pytest.fixture
def opened(monkeypatch):
    tabs = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: tabs.extend(launches) or [])
    return tabs


async def loaded(pilot, app):
    for _ in range(100):
        await pilot.pause(0.05)
        if app.sessions and app.full_polls:
            return
    raise AssertionError("never loaded")


def told(app) -> list[str]:
    return [note.message for note in app._notifications]


async def test_a_ticked_session_opened_elsewhere_meanwhile_is_left_alone(fake_home, opened, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        app.selected |= {"one", "two"}
        # "two" was opened in another tab a moment ago: its pid file is there, the list not yet.
        monkeypatch.setattr(app_module, "_registry_sessions", lambda: [{"sessionId": "two", "pid": 1}])
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert [launch.session_id for launch in opened] == ["one"]
        assert any("Open already" in note and "Second task" in note for note in told(app))
        assert "two" not in app.selected


async def test_before_the_first_full_poll_the_cli_itself_is_asked(fake_home, opened, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        app.full_polls = 0  # as in the first second: background sessions are not known yet
        monkeypatch.setattr(app_module, "live_sessions", lambda: {"two": {"sessionId": "two", "kind": "background"}})
        app.open_sessions([Launch("t", "C:/work/demo", Mode.AUTO, "two"), Launch("t", "C:/work/demo", Mode.NEW, "two")])
        # Resuming it would make a second process; a fresh session in its folder does not.
        assert [launch.mode for launch in opened] == [Mode.NEW]


async def test_a_prompt_for_a_session_opened_elsewhere_meanwhile_is_not_sent(fake_home, opened, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("p")
        await box_has_focus(pilot, app)
        monkeypatch.setattr(app_module, "_registry_sessions", lambda: [{"sessionId": "one", "pid": 1}])
        await pilot.press(*"go on", "enter")
        await pilot.pause(0.2)
        assert opened == []
        assert any("open in another tab now" in note for note in told(app))


async def test_a_session_that_never_comes_up_is_named(fake_home, opened, monkeypatch):
    monkeypatch.setattr(app_module, "CAME_UP_SECONDS", 0.5)
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("enter")  # Claude Code would not resume it: no process ever shows up
        for _ in range(60):
            await pilot.pause(0.05)
            if any("did not come up" in note for note in told(app)):
                break
        assert [launch.session_id for launch in opened] == ["one"]
        assert any("demo did not come up: its tab says why" in note for note in told(app))


async def test_a_session_that_comes_up_is_not_named(fake_home, opened, monkeypatch):
    monkeypatch.setattr(app_module, "CAME_UP_SECONDS", 1.0)
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("enter")
        monkeypatch.setattr(app_module, "_registry_sessions", lambda: [{"sessionId": "one", "pid": 1}])
        app.poll_live(full=False)  # its pid file is there: it came up
        await pilot.pause(1.5)
        assert "one" in app.came_up
        assert not any("did not come up" in note for note in told(app))
