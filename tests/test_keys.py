"""The ? screen lists every key the dashboard has."""

from __future__ import annotations

from tabmancer.app import SessionPicker
from tabmancer.keysview import KEYS, KeysScreen


def test_every_key_of_the_dashboard_is_explained():
    explained = {action for _, rows in KEYS for _, action, _ in rows}
    bound = {binding.action for binding in SessionPicker.BINDINGS}
    assert bound <= explained, f"keys missing from the ? screen: {sorted(bound - explained)}"


async def test_question_mark_lists_every_key_and_runs_the_one_picked(fake_home):
    from textual.widgets import OptionList

    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.press("question_mark")
        assert isinstance(app.screen, KeysScreen)
        menu = app.screen.query_one(OptionList)
        await pilot.press(*["down"] * 14)
        await pilot.pause(0.2)
        assert menu.scroll_y > 0  # at 30 rows the list is longer than the screen: the arrows scroll it
        await pilot.press("escape")
        assert not isinstance(app.screen, KeysScreen) and app.is_running  # Esc closes the list, not the app
        await pilot.press("question_mark")
        menu = app.screen.query_one(OptionList)
        menu.highlighted = next(i for i in range(menu.option_count) if menu.get_option_at_index(i).id == "toggle_live")
        await pilot.press("enter")  # runs it, as l would
        await pilot.pause(0.2)
        assert not isinstance(app.screen, KeysScreen) and app.live_mode


def test_the_footer_shows_the_keys_and_starts_with_where_the_rest_are():
    shown = [binding.key for binding in SessionPicker.BINDINGS if binding.show]
    assert shown[0] == "question_mark"  # a narrow footer still shows it
    assert {"enter", "space", "j", "p", "n", "slash", "l", "u", "h", "q"} <= set(shown)

async def test_no_key_is_cut_off_the_footer_it_takes_rows_instead(fake_home):
    from textual._context import visible_screen_stack

    from tabmancer.widgets import KeysFooter

    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 30)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        footer = app.query_one(KeysFooter)
        for cols, rows in ((200, 1), (120, 2), (80, 3), (200, 1)):  # and back: a resize fits it again
            await pilot.resize_terminal(cols, 30)
            await pilot.pause(0.3)
            assert footer.size.height == rows, f"{cols} columns"
            visible_screen_stack.set([])
            screen = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
            for binding in app.screen.active_bindings.values():
                if binding.binding.show and binding.enabled:
                    assert f"{app.get_key_display(binding.binding)} {binding.binding.description}" in screen, \
                        f"{binding.binding.description} cut off at {cols} columns"
