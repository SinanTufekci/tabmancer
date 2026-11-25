"""Anyone's words show as written, never as markup: session titles, projects, member names and errors
in the dashboard's notes, the lead's handover in the team handover dialog, and the other accounts'
names in the c dialog's plan. A stray closing tag ('[/b]') once made Textual raise MarkupError where it
was drawn, and a word in brackets went missing."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Label, Static
from textual.widgets._toast import Toast

from tabmancer import actions, teams
from tabmancer.app import LimitDialog, SessionPicker
from tabmancer.sessions import Session
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


async def test_the_limit_dialogs_plan_says_brackets_as_written(fake_home, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        stopped = [Session(session_id="s1", path=Path("C:/nowhere/s1.jsonl"), cwd="C:/work/shop", title="t",
                           context=120_000)]
        monkeypatch.setattr(actions, "stopped_at_limit", lambda sessions: stopped)
        app.other_accounts_words = lambda: [f"me@example.com · {WORDS}: session 1% · read 1h ago"]
        app.action_continue_at_reset()
        await until(pilot, lambda: isinstance(app.screen, LimitDialog))
        await pilot.pause(0.1)
        assert WORDS in str(app.screen.query_one(".plan", Label).render())
