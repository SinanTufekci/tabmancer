"""claude update falling due while the dashboard is open (the day turned) never runs on the screen's thread."""

from __future__ import annotations

import threading
from dataclasses import replace

from tabmancer import app as app_module
from tabmancer import claudeupdate
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit


def slow_update(ran: list, release: threading.Event):
    def run(claude=None, timeout=0):
        ran.append(threading.current_thread() is threading.main_thread())
        release.wait(10)
        return claudeupdate.Outcome(True, "2.1.322", "2.1.323")
    return run


async def test_what_opens_unasked_waits_for_the_update_in_a_worker(fake_home, monkeypatch):
    ran, release, opened = [], threading.Event(), []
    monkeypatch.setattr(claudeupdate, "run", slow_update(ran, release))
    monkeypatch.setattr(app_module, "terminal_kind", lambda: "windows-terminal")
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, window=None: opened.append(window) or [])
    app = SessionPicker(days=14, tabs_window="tabmancer")
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        claudeupdate.set_enabled(True)  # due now: the day turned while the dashboard was open
        stopped = replace(app.sessions["one"], exit=Exit.CLEAN)
        app.continue_these([stopped], unasked=True)  # its limit lifted
        for _ in range(20):
            await pilot.pause(0.05)
            if ran:
                break
        frame = app.frame
        await pilot.pause(0.3)
        assert app.frame > frame  # the screen goes on while the update runs
        assert ran == [False] and opened == []  # in a worker, and nothing opened before it is done
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
    assert opened == ["tabmancer"] and ran == [False]  # opened once, after the one update


async def test_a_key_press_waits_for_the_update_without_freezing(fake_home, monkeypatch):
    ran, release, tabs = [], threading.Event(), []
    monkeypatch.setattr(claudeupdate, "run", slow_update(ran, release))
    monkeypatch.setattr("tabmancer.doctor.missing_from_cli", lambda claude: [])
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: tabs.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        assert not app.updating_claude  # off when the dashboard started
        claudeupdate.set_enabled(True)
        app.open_sessions([app.launch_for(replace(app.sessions["one"], exit=Exit.CLEAN))])
        await pilot.pause(0.2)
        assert app.updating_claude and not tabs and ran == [False]
        assert any("Claude Code is updating: the session opens" in n.message for n in app._notifications)
        release.set()
        for _ in range(50):
            await pilot.pause(0.05)
            if tabs:
                break
    assert len(tabs) == 1 and ran == [False]
