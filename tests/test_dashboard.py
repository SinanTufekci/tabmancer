"""Tooltips, prompting sessions, glyphs and the project sidebar."""

import unicodedata

from conftest import box_has_focus

from tabmancer import app as app_module
from tabmancer import launch as L
from tabmancer import sessions
from tabmancer.app import STATUS_LABEL, PromptDialog, SessionPicker
from tabmancer.sessions import Exit, Status
from tabmancer.widgets import ProjectList, StatCard


def replace_cwd(session, cwd):
    """The session as if it ran in another folder (its own id, so each one is a project of its own)."""
    from dataclasses import replace

    return replace(session, session_id=f"{session.session_id}@{cwd}", cwd=cwd)


def test_a_card_emptied_at_shutdown_takes_a_late_update_quietly():
    # Seen on CI: a live update landed while the app was closing and the card had no caption left.
    card = StatCard("TRANSCRIPTS", "MB, these sessions", "MB")  # never mounted: no caption inside
    card.caption_text = "KB, these sessions"
    card.fit_caption()  # used to raise NoMatches
    assert card.caption_text == "KB, these sessions"


async def test_the_top_bar_takes_a_late_update_quietly():
    # Seen on CI: a poll landed while the app was closing, the bar emptied: NoMatches.
    from textual.app import App

    from tabmancer.widgets import TopBar

    class Host(App):
        def compose(self):
            yield TopBar(id="topbar")

    app = Host()
    async with app.run_test():
        bar = app.query_one(TopBar)
        await bar.query("Static").remove()  # as when the app shuts down
        bar.show_info("2 sessions")
        bar.show_usage([])
        bar.tick()


async def loaded(pilot, app):
    for _ in range(60):
        await pilot.pause(0.05)
        if app.sessions:
            return
    raise AssertionError("sessions never loaded")


def test_status_symbols_are_plain_text_not_emoji():
    # Characters Windows Terminal draws as colour emoji, which looked out of place.
    emoji = set("⚠⏸✉⏹⛔✅❌")
    for label, _ in STATUS_LABEL.values():
        assert not emoji & set(label), label
        assert all(unicodedata.category(c) != "So" or c in "✓▲‖○●◆◐" for c in label), label


async def test_clicking_a_row_opens_it_to_show_how_it_ended(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        session = app.current()
        heading, body = app.outcome(session)
        assert heading.plain.startswith("Finished") and body == "done"  # the fixture's reply
        await pilot.click("#table", offset=(60, 1))  # the first data row
        await pilot.pause(0.1)
        first = app.table.ordered_rows[0]
        assert first.key.value in app.expanded and first.height > 1
        title = str(app.table.get_cell(first.key.value, "title"))
        assert "Finished" in title and "done" in title
        await pilot.click("#table", offset=(60, 1))  # again: it closes
        await pilot.pause(0.1)
        assert not app.expanded and app.table.ordered_rows[0].height == 1
        await pilot.press("e")  # the key does the same
        assert app.current().session_id in app.expanded
        session.status, session.last_prompt = Status.CUT_OFF, "refactor the parser"
        assert app.outcome(session)[1] == "Last asked: refactor the parser"


async def test_an_opened_row_stays_open_through_live_updates(fake_home, monkeypatch):
    from dataclasses import replace

    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        session = app.current()
        await pilot.press("e")
        rebuilds = []
        real = app.refresh_rows
        monkeypatch.setattr(app, "refresh_rows", lambda: rebuilds.append(1) or real())
        app.apply_live_updates({session.session_id: replace(session, size=session.size + 1)})
        assert session.session_id in app.expanded and rebuilds == []


async def test_prompt_opens_a_tab_with_the_prompt_and_stays_open(fake_home, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        target = app.current()
        await pilot.press("p")
        assert isinstance(app.screen, PromptDialog)
        await box_has_focus(pilot, app)
        await pilot.press(*"add tests", "enter")
        assert app.is_running and not isinstance(app.screen, PromptDialog)
    (launch,) = opened
    assert launch.argv == ["claude", "--resume", target.session_id, "add tests"]


async def test_prompt_can_run_in_the_background(fake_home, monkeypatch):
    started = []
    monkeypatch.setattr(app_module, "run_in_background",
                        lambda launch: started.append(launch) or "backgrounded · abc12345")
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        await pilot.press("p", *"keep going", "ctrl+b")
        for _ in range(40):
            await pilot.pause(0.05)
            if started:
                break
    assert started and started[0].argv[-1] == "keep going"


async def test_prompt_on_an_open_session_goes_to_its_tab(fake_home, monkeypatch):
    from tabmancer import focus

    asked = []
    monkeypatch.setattr(focus, "focus_tab", lambda title: asked.append(title) or True)
    # Open as Claude Code itself reports it. Marked open by hand, a live poll read it back from
    # its transcript as closed (seen on the Windows CI runner).
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "one", "kind": "interactive", "status": "idle"}])
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions["one"].exit is Exit.RUNNING and not app.full_poll_due:
                break
        app.table.move_cursor(row=app.table.get_row_index("one"))
        session = app.current()
        assert session.status is Status.RUNNING
        await pilot.press("p")
        assert not isinstance(app.screen, PromptDialog)  # nothing to type into from here
        # Going to a tab is one job at a time: a second press at once replaces the first
        # before it has run (seen on the Windows CI runner), so wait as a person would.
        for _ in range(40):
            await pilot.pause(0.05)
            if asked:
                break
        await pilot.press("enter")  # Enter on an open session goes there too
        for _ in range(40):
            await pilot.pause(0.05)
            if len(asked) == 2:
                break
        assert app.is_running  # going to a tab does not close the dashboard
    assert asked == [session.title, session.title]


def test_run_in_background_uses_claude_bg_resume(monkeypatch):
    calls = []

    class Result:
        returncode, stdout, stderr = 0, "backgrounded · abc12345\n  claude agents  list sessions\n", ""

    monkeypatch.setattr(L.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(L.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or Result())
    launch = L.Launch("t", "/nowhere", L.Mode.RESUME, "abc-123", prompt="go on")
    assert L.run_in_background(launch) == "backgrounded · abc12345"
    (argv, kw), = calls
    assert argv == ["claude", "--bg", "--resume", "abc-123", "go on"]
    assert "CLAUDE_CODE_CHILD_SESSION" not in kw["env"]


async def test_project_list_updates_in_place_and_keeps_its_order(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        projects: ProjectList = app.projects
        first = [projects.get_option_at_index(i) for i in range(projects.option_count)]
        projects.highlighted = 1
        projects.show_projects(list(app.sessions.values()))  # a live update with the same projects
        again = [projects.get_option_at_index(i) for i in range(projects.option_count)]
        assert [o.id for o in again] == [o.id for o in first]
        assert projects.highlighted == 1  # nothing jumped


async def test_the_sidebar_lines_up_and_its_legend_follows_the_list(fake_home):
    """The list as wide as the sidebar (it was 4 cells short), its text where the heading's is, the
    legend right under it (not at the bottom, with empty lines between) and the chart still last."""
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        await pilot.pause(0.3)
        side, projects = app.query_one("#sidebar"), app.projects
        heading, legend, chart = (app.query_one(".heading"), app.query_one(".legend"), app.query_one("#activity"))
        assert projects.region.width == side.region.width  # no dead strip on its right
        # The text of the lines starts where the heading's does: the first mark under the heading's first letter.
        assert projects.region.x + ProjectList.INSET == heading.region.x + heading.styles.padding.left
        assert legend.region.y - (projects.region.y + projects.region.height) == 1  # one line between
        assert projects.region.height == projects.option_count + 1  # as high as its lines and the rule under All
        assert chart.region.y > legend.region.bottom  # the chart is below, where it was


async def test_a_long_project_list_scrolls_and_leaves_the_legend_and_chart_in_sight(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 30)) as pilot:
        await loaded(pilot, app)
        projects = app.projects
        first = next(iter(app.sessions.values()))
        many = [replace_cwd(first, f"/work/project-{i}") for i in range(40)]
        projects.show_projects(many)
        await pilot.pause(0.3)
        legend, chart = app.query_one(".legend"), app.query_one("#activity")
        assert projects.region.bottom <= legend.region.y  # it does not push the legend off
        assert legend.region.bottom <= chart.region.y and chart.region.bottom <= app.size.height
        assert projects.virtual_size.height > projects.size.height  # so the rest scrolls


async def test_live_updates_change_rows_in_place(fake_home, monkeypatch):
    from dataclasses import replace

    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        rebuilds = []
        real = app.refresh_rows
        monkeypatch.setattr(app, "refresh_rows", lambda: rebuilds.append(1) or real())
        session = app.visible()[-1]  # the bottom row, so opening it has to move it
        busier = replace(session, last_prompt="still going", size=session.size + 10)
        app.apply_live_updates({session.session_id: busier})
        assert rebuilds == []  # same rows: updated in place, no flicker
        opened = replace(session, status=Status.RUNNING, exit=Exit.RUNNING, live_state="busy")
        app.apply_live_updates({session.session_id: opened})
        assert rebuilds == [1]  # it moved to the open group: now a rebuild is needed
        assert app.visible()[0].session_id == session.session_id


async def test_working_animation_uses_no_emoji(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        cell = app.working("working", "").plain
        assert cell[0] in app_module.STAR and cell.endswith("working")
        assert "✳" not in app_module.STAR  # drawn as an emoji by Windows Terminal


async def test_narrow_windows_shorten_titles_instead_of_scrolling_sideways(fake_home):
    from textual.widgets import Label

    app = SessionPicker(days=14)
    async with app.run_test(size=(100, 40)) as pilot:
        await loaded(pilot, app)
        for session in app.sessions.values():
            session.title = "A long title that would push the other columns out of a narrow window"
        app.refresh_rows()
        await pilot.pause(0.2)
        assert app.title_width < app_module.EXPAND_WIDTH
        assert app.table.max_scroll_x == 0
        await pilot.press("e")  # an opened row, its heading included, still fits
        await pilot.pause(0.1)
        assert app.table.max_scroll_x == 0
        await pilot.press("l")  # and so does the live view, whose columns are wider
        await pilot.pause(0.1)
        assert app.table.max_scroll_x == 0
        # A card too narrow for its caption shows the short one, and only then.
        for card in app.cards.values():
            shown = str(card.query_one(".caption", Label).render())
            fits = len(card.caption_text) <= card.content_size.width
            assert shown == (card.caption_text if fits else card.short_text), card.id


async def test_a_click_opens_the_row_under_it_not_the_last_one_hovered(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        first, second = (row.key.value for row in app.table.ordered_rows)
        await pilot.hover("#table", offset=(60, 1))  # the mouse was last over the first row...
        await pilot.click("#table", offset=(60, 2))  # ...when the terminal reported a click on the second
        await pilot.pause(0.1)
        assert app.expanded == {second} and app.current().session_id == second
        await pilot.click("#table", offset=(60, 20))  # the empty space under the rows
        await pilot.pause(0.1)
        assert app.expanded == {second}
        await pilot.click("#table", offset=(60, 1))  # rows above an opened one stay where they were
        await pilot.pause(0.1)
        assert app.expanded == {first, second}
        await pilot.click("#table", offset=(60, 1 + app.table.ordered_rows[0].height + 2))  # its text
        await pilot.pause(0.1)
        assert app.expanded == {first}  # clicking an opened row's text closes that row


async def test_clicking_the_box_ticks_the_row_instead_of_opening_it(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        second = app.table.ordered_rows[1].key.value
        await pilot.click("#table", offset=(2, 2))  # the [ ] of the second row
        await pilot.pause(0.1)
        assert app.selected == {second} and not app.expanded
        assert str(app.table.get_cell(second, "sel")) == "[x]"
        await pilot.click("#table", offset=(2, 2))  # again: unticked
        await pilot.pause(0.1)
        assert not app.selected


async def test_moving_through_the_list_never_moves_the_preview_or_bit(fake_home):
    # Previews differ in length. The preview used to size itself to each one, so its
    # top edge, and Bit walking on it, jumped up and down under the arrow keys.
    import json

    project = fake_home / "projects" / "C--work-demo"
    long_text = " ".join(["a long prompt that wraps over several lines in the preview"] * 6)
    records = [{"type": "user", "cwd": "C:/work/demo", "entrypoint": "cli", "message": {"content": long_text}},
               {"type": "assistant", "cwd": "C:/work/demo", "stop_reason": "end_turn",
                "message": {"content": [{"type": "text", "text": long_text}], "stop_reason": "end_turn"}},
               {"type": "user", "cwd": "C:/work/demo", "message": {"content": long_text + " again"}},
               {"type": "ai-title", "aiTitle": "Long one"}]
    (project / "long.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        await pilot.pause(0.2)
        seen = {}
        for key in ["down"] * app.table.row_count + ["up"] * app.table.row_count:
            await pilot.press(key)
            await pilot.pause(0.1)
            seen[app.current().session_id] = (app.inspector.region.y, app.inspector.region.height,
                                              app.mascot.region.y)
        assert len(seen) == 3 and len(set(seen.values())) == 1, seen


async def test_the_click_that_brings_the_window_to_the_front_does_nothing_else(fake_home):
    # As the terminal sends them: to the app, which passes them on (pilot.click goes round it).
    from textual import events
    from textual.pilot import _get_mouse_message_arguments

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        first = app.table.ordered_rows[0].key.value

        async def click_first_row():
            where = _get_mouse_message_arguments(app.table, (60, 1), button=1)
            app.post_message(events.MouseDown(**where))
            app.post_message(events.MouseUp(**where))
            await pilot.pause(0.2)

        await click_first_row()
        assert app.expanded == {first}  # an ordinary click opens the row
        app.post_message(events.AppBlur())  # you went to another window...
        await pilot.pause(0.1)
        await click_first_row()  # ...and clicked this one to come back: that is all it does
        assert app.expanded == {first}
        await click_first_row()  # the next click is a click
        assert not app.expanded
        app.post_message(events.AppFocus())  # the terminal says so first, this time
        await pilot.pause(0.05)
        await click_first_row()
        assert not app.expanded
