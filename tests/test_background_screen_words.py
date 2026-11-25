"""The terminal's B screen says backgroundwords.py's words and asks its steps (one copy with the desktop
window): no words or second-press timing of its own."""

from __future__ import annotations

import inspect

from test_background import until

from tabmancer import background as B
from tabmancer import backgroundwords, sessions


def test_no_copy_of_the_words_or_the_steps_of_its_own():
    source = inspect.getsource(B)
    for said in ("Nothing to clean up", "press x again", "Press d again", "Press c again", "No background sessions.",
                 "is not running", "Working…", "Stopped", "Removed"):
        assert said not in source, said
    assert B.CONFIRM_SECONDS == backgroundwords.CONFIRM_SECONDS and not hasattr(B.BackgroundDialog, "confirmed")


async def test_the_screen_shows_what_backgroundwords_says(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [])
    monkeypatch.setattr(backgroundwords, "EMPTY", "nothing here")
    monkeypatch.setattr(backgroundwords, "NOTHING_TO_CLEAN", "nothing to tidy")
    monkeypatch.setattr(backgroundwords, "KEYS", (("Esc", "away"),))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.press("B")
        await until(pilot, lambda: isinstance(app.screen, B.BackgroundDialog))
        dialog = app.screen
        assert str(dialog.query_one("#note").content) == "nothing here"
        assert str(dialog.query(".hint").last().render()) == "Esc  away"
        await pilot.press("c")
        assert str(dialog.query_one("#note").content) == "nothing to tidy"
