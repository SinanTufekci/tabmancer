"""Anyone's words show as written, never as markup: session titles, projects, member names and errors
in the dashboard's notes, and the lead's handover in the team handover dialog. A stray closing tag
('[/b]') once made Textual raise MarkupError where it was drawn, and a word in brackets went missing."""

from __future__ import annotations

from textual.widgets import Static
from textual.widgets._toast import Toast

from tabmancer import teams
from tabmancer.app import SessionPicker
from tabmancer.teamview import TeamHandoverDialog

WORDS = "close the [/b] tag, keep [the] word and [red]this[/red]"


async def until(pilot, check, tries=80):
    for _ in range(tries):
        if check():
            return
        await pilot.pause(0.05)
    raise AssertionError("never came")


async def test_a_note_says_brackets_as_written(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        app.notify(f"Renamed to “{WORDS}”.")
        await until(pilot, lambda: list(app._notifications))
        note = list(app._notifications)[-1]
        assert not note.markup and WORDS in Toast(note).render().plain


async def test_the_team_handover_says_the_leads_words_as_written(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        team = teams.new_team("shop", "C:/work/shop", "g", teams.parse_members("lead: steer\napi: a", lead_ok=True))
        app.push_screen(TeamHandoverDialog(team, f"## Goal\n{WORDS}\n- [x] done", "", suggested=False))
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        await pilot.pause(0.1)
        shown = str(app.screen.query_one("#handover-text", Static).render())
        assert WORDS in shown and "- [x] done" in shown
