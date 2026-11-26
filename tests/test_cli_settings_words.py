"""The terminal's , dialog says clis.py's words and takes its steps (Picked, Saved): one copy with the desktop
window's dialog, nothing of its own."""

from __future__ import annotations

import inspect

from tabmancer import clis, clisettings


def test_no_words_or_saving_of_its_own():
    source = inspect.getsource(clisettings)
    for said in ("Which CLIs do you use?", "Their sessions join the list", "turn one on or off", "set_main",
                 "set_enabled", "next_main"):
        assert said not in source, said


async def test_enter_saves_through_picked_and_answers_with_what_it_came_to(monkeypatch):
    from textual.app import App

    monkeypatch.setattr(clis, "SETTINGS_TITLE", "pick them")
    got = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(clisettings.CliSettings(), got.append)

    app = Host()
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        assert str(app.screen.query("Label").first().content) == "pick them"
        await pilot.press("down", "space", "enter")
        await pilot.pause()
    assert got == [clis.Saved(["claude"], ["claude", "codex"], kept=True)]
    assert got[0].words == clis.listing_words(["claude", "codex"]) and clis.enabled() == ["claude", "codex"]
