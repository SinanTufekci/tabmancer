"""The dashboard kept simple: headings over the open sessions and the rest, nothing said that
says nothing, and j to whoever waits for you."""

from __future__ import annotations

from pathlib import Path

from tabmancer import focus, sessions
from tabmancer.app import HEADING, SessionPicker, home_short


async def wait_for(pilot, condition, tries=60):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


async def test_open_sessions_and_the_rest_have_headings_the_cursor_steps_over(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda: [{"sessionId": "one", "kind": "interactive",
                                                            "status": "busy"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("one") and app.sessions["one"].live_state)
        await pilot.pause(0.2)
        keys = [row.key.value for row in app.table.ordered_rows]
        assert keys == [f"{HEADING}open", "one", f"{HEADING}rest", "two"]
        heading = str(app.table.get_cell(f"{HEADING}open", "project"))
        assert heading == "\nOPEN NOW · 1"  # a blank line above it sets it apart
        assert [row.height for row in app.table.ordered_rows] == [2, 1, 2, 1]
        app.table.move_cursor(row=0)  # onto the heading: it moves on to the session under it
        await pilot.pause(0.1)
        assert app.current().session_id == "one"
        await pilot.press("down")
        assert app.current().session_id == "two"  # over the second heading
        await pilot.press("up")
        assert app.current().session_id == "one"
        await pilot.press("l")  # the live view lists open sessions only: no headings
        await pilot.pause(0.1)
        assert [row.key.value for row in app.table.ordered_rows] == ["one"]


async def test_nothing_is_said_that_says_nothing(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions)
        assert app.sub_title == "2 sessions, last 14 days"  # no "0 selected", no "0 looked open"
        await pilot.press("space")
        assert "1 selected" in app.sub_title
        two = app.sessions["two"]
        assert str(app.process_cell(two)) == "" and str(app.mode_cell(two)) == ""  # clean, resume: the usual
        assert str(app.mode_cell(two, plain=True)) == "resume"  # the preview says it all the same
        assert len(app.cards) == 4


async def test_j_goes_round_the_sessions_waiting_for_you(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda: [
        {"sessionId": "one", "kind": "interactive", "status": "idle"},
        {"sessionId": "two", "kind": "interactive", "status": "blocked"}])
    went = []
    monkeypatch.setattr(focus, "focus_tab", lambda title: went.append(title) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: all(app.sessions.get(s) and app.sessions[s].live_state for s in ("one", "two")))
        await pilot.press("j")
        await wait_for(pilot, lambda: len(went) == 1)
        await pilot.press("j")
        await wait_for(pilot, lambda: len(went) == 2)
        await pilot.press("j")
        await wait_for(pilot, lambda: len(went) == 3)
        assert went[0] != went[1] and went[2] == went[0]  # one, the other, the first again
        assert app.current().title == went[2]


def test_the_home_folder_is_written_as_a_tilde():
    home = str(Path.home())
    assert home_short(home + "\\Desktop\\ccrestore") == "~\\Desktop\\ccrestore"
    assert home_short("D:\\work") == "D:\\work" and home_short("") == ""
