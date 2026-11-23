"""An agy model as the dashboard names it: 'Neon (High)', never 'gemini-4-ne…'."""

from __future__ import annotations

from tabmancer import agy, context, teamview
from tabmancer.picks import Pick
from tabmancer.sessions import Exit, Session


def test_an_agy_model_is_named_by_its_label():
    assert agy.label("gemini-4-neon-high") == "Neon (High)"
    assert agy.label("Gemini 4 Hydrogen (High)") == "Hydrogen (High)"  # its display name too
    assert agy.label("gemini-3.8-flash-medium") == "Flash (Medium)"
    assert agy.label("claude-opus-5-5") == "" and agy.label("") == "" and agy.label("gpt-oss-120b") == ""
    assert context.short_model("gemini-4-neon-high") == "Neon (High)"
    assert context.short_model("gemini-4-hydrogen-high") == "Hydrogen"  # its level does not fit in 12
    assert context.short_model("gemini-4-hydrogen-high", 16) == "Hydrogen (High)"
    assert context.short_model("claude-sonnet-5-5") == "sonnet 5.5"  # Claude's as before


async def test_the_model_column_and_a_members_card_name_an_agy_model(fake_home):
    from tabmancer.app import SessionPicker

    session = Session(session_id="g", path=None, cli="agy", model="gemini-4-hydrogen-high", exit=Exit.RUNNING,
                      live_state="idle")
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.1)
        assert str(app.model_cell(session)) == "Hydrogen (High)"  # the column's whole room: no effort after it
    assert teamview.card_model(session, Pick(), "agy") == ("agy Hydrogen", False)
