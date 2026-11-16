"""Ctrl+E: a first message or a team's goal rewritten clearer by Claude, put in as one edit that
Ctrl+Z takes back; in n, one line for each line, since each line is a session."""

from __future__ import annotations

import pytest
from conftest import box_has_focus
from textual.app import App
from textual.widgets import TextArea

from tabmancer import improve, summarize
from tabmancer.app import SessionPicker
from tabmancer.newsessions import NewSessions
from tabmancer.teamview import NewTeamDialog


def frame_words(card) -> str:
    """What a card's frame says at its foot, as words (Textual keeps it as markup)."""
    from rich.text import Text

    return Text.from_markup(str(card.border_subtitle)).plain


async def until(pilot, check, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


def answering(monkeypatch, answer: str) -> list[tuple[str, str, str]]:
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="":
                        asked.append((prompt, model, effort)) or answer)
    return asked


def test_the_prompt_keeps_the_lines_and_asks_for_nothing_made_up():
    one = improve.session_prompt("fix the login page", ["shop"])
    assert "single line" in one and "fix the login page" in one and "shop" in one
    two = improve.session_prompt("fix the login page\nwrite its tests", [])
    assert "exactly 2 lines" in two and "(not chosen yet)" in two
    assert "Never add requirements" in improve.RULES and "language" in improve.RULES
    goal = improve.goal_prompt("add Stripe checkout with tests", "C:/work/shop")
    assert "each member is given all of it" in goal and "C:/work/shop" in goal
    assert len(improve.session_prompt("x " * 20000, [])) < 14000  # a pasted page is sent cut


def test_the_answer_is_taken_without_its_wrapping():
    assert improve.tidy("```\nFix the login page.\n```") == "Fix the login page."
    assert improve.tidy("Rewritten prompt: Fix it.") == "Fix it."
    assert improve.tidy("**Improved:** Fix it.") == "Fix it."
    assert improve.tidy('"Fix the login page."') == "Fix the login page."
    assert improve.tidy('Fix the "login" page.') == 'Fix the "login" page.'  # quotes inside stay


def test_a_rewrite_keeps_one_line_for_each_session(monkeypatch):
    asked = answering(monkeypatch, "Fix the login form's validation.\nAdd tests for it.")
    assert improve.improve_message("fix login\nadd tests", ["shop"]) == (
        "Fix the login form's validation.\nAdd tests for it.")
    assert asked[0][1:] == (improve.MODEL, improve.EFFORT)
    answering(monkeypatch, "Fix the login form.\n\nKeep the API as it is.")
    assert improve.improve_message("fix the login form", []) == "Fix the login form. Keep the API as it is."
    answering(monkeypatch, "Only one line.")
    with pytest.raises(improve.NotImproved, match="each line is a session"):
        improve.improve_message("fix login\nadd tests", [])


async def test_ctrl_e_improves_the_first_message_and_ctrl_z_puts_it_back(fake_home, monkeypatch):
    answering(monkeypatch, "Fix the validation of the login form in src/login.py.")
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await box_has_focus(pilot, app)
        dialog = app.screen
        assert "Ctrl+E" in frame_words(dialog.query_one("#message-card"))
        await pilot.press("ctrl+e")  # nothing written yet: nothing asked
        await pilot.press("tab")
        dialog.message.insert("fix login form validation")
        await pilot.press("ctrl+e")
        await until(pilot, lambda: dialog.message.text.startswith("Fix the validation"))
        card = dialog.query_one("#message-card")
        await pilot.pause(0.1)  # the box's own word that it changed comes after the rewrite
        # Done, and the frame says so (not back to what it said before Ctrl+E), until the text changes.
        assert dialog.improving is None and not dialog.message.read_only
        assert frame_words(card).startswith("✓ improved (4 → 9 words)")
        assert card.has_class("-improved")  # a moment in green
        dialog.message.focus()
        await pilot.press("ctrl+z")
        await until(pilot, lambda: dialog.message.text == "fix login form validation")
        await until(pilot, lambda: frame_words(card) == dialog.MESSAGE_NOTE)


async def test_a_rewrite_is_not_put_over_text_changed_meanwhile(fake_home, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        dialog = app.screen
        dialog.message.insert("fix login form validation and more")
        dialog.improved("fix login form validation", "Fix it all.", "")  # sent before the last words
        assert dialog.message.text == "fix login form validation and more"
        dialog.improved(dialog.message.text, "", "the rewrite had 2 lines for your 1")
        assert dialog.message.text == "fix login form validation and more"


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog = dialog

    def on_mount(self) -> None:
        self.push_screen(self.dialog)


async def test_the_team_form_shows_that_ctrl_e_improves_the_goal(tmp_path, monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="": release.wait(5) and "Better.")
    dialog = NewTeamDialog(str(tmp_path), set())
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        card = dialog.query_one("#team-card")
        assert frame_words(card) == "Ctrl+E improves the goal"  # always there, as n's message card has it
        keys = dialog.query_one("#team-keys")
        assert len(keys.render().plain) <= keys.parent.content_size.width  # one line in a 120x30 window...
        assert "Ctrl+E" not in keys.render().plain  # ...which has no room for one more key: the border says it
        goal = dialog.query_one("#team-goal", TextArea)
        goal.insert("add stripe checkout with tests")
        goal.focus()
        await pilot.press("ctrl+e")
        await until(pilot, lambda: dialog.improving is not None)
        assert "improving the goal with" in frame_words(card)  # at it, and the card says so
        assert frame_words(card).endswith("s")  # ...for how long: "· 0s"
        assert goal.read_only  # a key pressed meanwhile cannot throw the rewrite away
        release.set()
        await until(pilot, lambda: goal.text == "Better.")
        await pilot.pause(0.1)
        assert frame_words(card).startswith("✓ improved")  # it stays, as the toast did not
        assert not goal.read_only
        goal.insert("!")
        await until(pilot, lambda: frame_words(card) == "Ctrl+E improves the goal")
    async with Host(NewTeamDialog(str(tmp_path), set())).run_test(size=(170, 40)) as pilot:
        await pilot.pause(0.3)
        shown = pilot.app.screen.query_one("#team-keys").render().plain
        assert "Ctrl+E  improve" in shown and "Esc  cancel" in shown  # room for it: among the keys


async def test_ctrl_e_improves_a_teams_goal(tmp_path, monkeypatch):
    asked = answering(monkeypatch, "Add Stripe checkout:\n- the payment page\n- tests for each case")
    dialog = NewTeamDialog(str(tmp_path), set())
    app = Host(dialog)
    async with app.run_test(size=(120, 30)) as pilot:
        goal = dialog.query_one("#team-goal", TextArea)
        goal.insert("add stripe checkout with tests")
        goal.focus()
        await pilot.press("ctrl+e")
        await until(pilot, lambda: goal.text.startswith("Add Stripe checkout:"))
        assert goal.text.count("\n") == 2 and str(tmp_path) in asked[0][0]  # a goal keeps its lines
        await pilot.press("ctrl+z")
        await until(pilot, lambda: goal.text == "add stripe checkout with tests")


async def test_ctrl_e_says_on_the_frame_when_it_changed_nothing_or_failed(tmp_path, monkeypatch):
    answers = iter(["add stripe checkout with tests", None])

    def ask(prompt, model, timeout=180, effort=""):
        answer = next(answers)
        if answer is None:
            raise summarize.SummaryError("claude returned nothing")
        return answer

    monkeypatch.setattr(summarize, "ask", ask)
    dialog = NewTeamDialog(str(tmp_path), set())
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        card = dialog.query_one("#team-card")
        goal = dialog.query_one("#team-goal", TextArea)
        goal.insert("add stripe checkout with tests")
        goal.focus()
        await pilot.press("ctrl+e")
        await until(pilot, lambda: frame_words(card).startswith("= clear as it was"))
        assert goal.text == "add stripe checkout with tests"
        await pilot.press("ctrl+e")
        await until(pilot, lambda: frame_words(card).startswith("✗ not improved: claude returned nothing"))
        assert not goal.read_only and not card.has_class("-improved")
