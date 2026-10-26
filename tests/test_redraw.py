"""The table draws itself anew for every cell that changes, so each frame touches only the cells
that animate: an open session waiting quietly would otherwise redraw it twelve times a second."""

from tabmancer import sessions
from tabmancer.app import SessionPicker


async def test_each_frame_touches_only_the_cells_that_animate(fake_home, monkeypatch):
    states = {"one": "idle", "two": "busy"}
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": sid, "kind": "interactive", "status": state} for sid, state in states.items()])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if all(app.sessions.get(sid) and app.sessions[sid].live_state == state for sid, state in states.items()):
                break
        app.flash.clear()  # the highlight of a change animates too, for a moment
        touched = []
        update = app.table.update_cell
        monkeypatch.setattr(app.table, "update_cell",
                            lambda row, column, value, **kwargs: touched.append(row) or update(row, column, value, **kwargs))
        for _ in range(5):
            app.spin()
        assert touched.count("two") == 5  # working: its star turns
        assert "one" not in touched  # waiting for you: nothing changes, nothing is redrawn
