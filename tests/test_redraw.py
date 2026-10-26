"""The table draws itself anew for every cell that changes, so each frame touches only the cells
that animate: an open session waiting quietly would otherwise redraw it twelve times a second."""

import asyncio
import sys

import pytest

from tabmancer import sessions
from tabmancer.app import TIMER_THREADS, SessionPicker


@pytest.mark.skipif(sys.platform != "win32", reason="Textual sleeps in threads on Windows only")
async def test_on_windows_every_timer_gets_a_thread_of_its_own(fake_home):
    # Textual sleeps out each timer in a thread of asyncio's default pool, cores + 4 threads: on a
    # 2-core machine the dashboard's timers outnumbered them and these tests ran 2.7 times slower.
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert asyncio.get_running_loop()._default_executor._max_workers == TIMER_THREADS


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
