"""The live view: open sessions only, refreshed, with an alert when one needs you."""

from tabmancer import app as app_module
from tabmancer import sessions
from tabmancer.app import SessionPicker, live_label
from tabmancer.sessions import Exit, Status
from tabmancer.theme import RED


def test_live_labels():
    assert live_label("busy")[0] == "working"
    assert live_label("idle")[0] == "waiting for you"
    assert live_label("blocked")[:2] == ("needs you", f"bold {RED}")
    assert live_label("something-new") == ("something-new", "", 3)


async def wait_for(pilot, condition, tries=60):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


async def test_open_sessions_drop_out_of_the_automatic_ticks(fake_home, monkeypatch):
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: {"one", "two"})
    monkeypatch.setattr(sessions, "_agents_json", lambda: [
        {"sessionId": "one", "kind": "background", "state": "blocked"}])
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.sessions.get("one") and app.sessions["one"].live_state)
        assert app.selected == {"two"}
        assert app.preselected == {"two"}
        assert "1 looked open" in app.sub_title


async def test_live_view_tracks_state_and_alerts(fake_home, monkeypatch):
    state = {"value": [{"sessionId": "one", "kind": "interactive", "status": "busy"}]}
    monkeypatch.setattr(sessions, "_agents_json", lambda: state["value"])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)  # the fake state comes from claude agents
    app = SessionPicker(days=14, live=True)
    bells = []
    monkeypatch.setattr(app, "bell", lambda: bells.append(1))
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.table.row_count == 1)  # only the open session
        assert app.current().session_id == "one"
        assert not app.check_action("toggle", ())  # picker keys are off in live view

        state["value"] = [{"sessionId": "one", "kind": "interactive", "status": "idle"}]
        await wait_for(pilot, lambda: app.sessions["one"].live_state == "idle")
        assert bells == [1]

        state["value"] = []  # the session was closed
        await wait_for(pilot, lambda: app.table.row_count == 0)
        assert app.sessions["one"].exit is Exit.CLEAN
        assert app.sessions["one"].status is Status.DONE

        await pilot.press("l")  # back to the full list
        assert app.table.row_count == 2
        assert app.check_action("toggle", ())


async def test_a_quick_poll_does_not_cancel_a_full_one_on_its_way(fake_home, monkeypatch):
    # Seen on CI (test_rename): the 2 s tick's quick poll replaced a full poll before it ran,
    # so a session opened in the meantime showed as open only 16 s on.
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await wait_for(pilot, lambda: app.sessions)
        monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
            {"sessionId": "two", "kind": "interactive", "status": "idle"}])
        app.poll_live(full=True)
        app.poll_live(full=False)  # the tick, right after
        await wait_for(pilot, lambda: app.sessions["two"].live_state == "idle")
        assert not app.full_poll_due
        app.poll_live(full=False)  # with none on its way, quick polls run again
