"""The layered look: no frames, a few tones of black, the highlighted row in the theme's colour,
loud states as badges, a legend under the projects, and two-line cards on a short screen."""

from __future__ import annotations

from rich.style import Style

from tabmancer import app as app_module
from tabmancer.app import BADGE, LEGEND, SessionPicker, badge
from tabmancer.theme import AMBER, MUTED, SURFACE, THEMES, mix


async def settle(pilot, app, tries=200):
    """Till the dashboard has faded in and drawn itself again (a slow runner takes its time)."""
    for _ in range(tries):
        await pilot.pause(0.05)
        if app.sessions and app.faded:
            break
    await pilot.pause(0.2)


def test_a_loud_state_is_a_badge_and_a_quiet_one_stays_plain():
    assert badge(f"bold {AMBER}") == f"bold {AMBER} on {mix('#000000', AMBER, BADGE)}"
    assert badge(MUTED) == MUTED and badge(AMBER) == AMBER


def test_the_sidebar_says_what_its_marks_mean_in_its_width():
    lines = LEGEND.plain.split("\n")
    assert lines == ["● your turn  ● open", "▲ to continue  ± ↑ git"]
    assert all(len(line) <= 22 for line in lines)  # the sidebar's 26 less its padding


async def test_the_highlighted_row_takes_the_themes_colour(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await settle(pilot, app)
        for theme, (_, accent) in THEMES.items():
            app.theme = theme
            await pilot.pause(0.05)
            assert app.get_css_variables()["block-cursor-background"].lower() == mix("#000000", accent, 0.36)
        done = app.cells(app.sessions["one"])[3]  # a closed one, done: quiet, no badge
        assert " on " not in str(done.style)


async def test_every_part_has_its_tone_after_the_fade_in(fake_home):
    # The dashboard fades in; a part drawn during the fade kept its colours blended towards black
    # (the preview's facts column stayed black for good).
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await settle(pilot, app)
        app.table.move_cursor(row=app.table.get_row_index("two"))
        # It stayed black for good: give a slow runner time to redraw, but no more than that.
        for _ in range(120):
            await pilot.pause(0.05)
            backgrounds = {seg.style.bgcolor.triplet.hex for seg in app.meta.render_line(0)
                           if isinstance(seg.style, Style) and seg.style.bgcolor}
            if backgrounds == {SURFACE}:
                break
        # Seen once on a slow runner, never here: should it come again, what stood how then.
        assert backgrounds == {SURFACE}, (
            f"faded={app.faded} opacity={app.body.styles.opacity} cursor={app.table.cursor_row} "
            f"meta background={app.meta.background_colors[1].hex} body={app.body.background_colors[1].hex}")


async def test_a_short_screen_has_two_line_cards(fake_home, monkeypatch):
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        assert app.dashboard.has_class("mini") and not app.dashboard.has_class("short")
        card = app.cards["card-open"]
        assert card.size.height == 2 and card.head.display and not card.number.parent.display  # the big number hidden
        assert card.head.render().plain.startswith("OPEN NOW  ")
        await pilot.resize_terminal(150, 44)
        await pilot.pause(0.3)
        assert not app.dashboard.has_class("mini") and app.cards["card-open"].size.height == 5


def test_cards_are_mini_below_forty_rows():
    assert app_module.MINI_BELOW == 40
