"""Drive the picker headlessly against a fake ~/.claude (fake_home is in conftest.py)."""

import io

import pytest
from rich.console import Console
from textual.screen import ModalScreen

from tabmancer import app as app_module
from tabmancer import workspaces
from tabmancer.app import SessionPicker
from tabmancer.launch import Mode


@pytest.fixture
def opened(monkeypatch):
    """Launches the dashboard opened in tabs (instead of opening real ones)."""
    tabs = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: tabs.extend(launches) or [])
    return tabs


async def loaded(pilot, app):
    for _ in range(50):
        await pilot.pause(0.05)
        if app.sessions:
            return
    raise AssertionError("sessions never loaded")


async def test_select_cycle_mode_and_open(fake_home, opened):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        assert app.query_one("DataTable").row_count == 2
        first = app.current()
        await pilot.press("space")
        assert app.selected == {first.session_id}
        assert app.mode(first) is Mode.RESUME  # finished sessions just resume
        await pilot.press("up", "m")
        assert app.mode(first) is Mode.AUTO
        await pilot.press("m")
        assert app.mode(first) is Mode.FORK
        await pilot.press("enter")
        assert app.is_running and not app.selected  # stays open; the opened one is unticked
    (launch,) = opened
    assert launch.session_id == first.session_id
    assert launch.argv[-1] == "--fork-session"


async def test_interrupted_sessions_default_to_auto_continue(fake_home, monkeypatch, opened):
    from tabmancer.sessions import Status

    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        session = app.current()
        session.status = Status.CUT_OFF
        assert app.mode(session) is Mode.AUTO
        monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", "devam et")
        await pilot.press("enter")
    (launch,) = opened
    assert launch.argv == ["claude", "--resume", session.session_id, "devam et"]


async def test_sessions_open_at_shutdown_start_ticked(fake_home, monkeypatch, opened):
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: {"two"})
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        assert app.selected == {"two"}
        await pilot.press("enter")
    assert [launch.session_id for launch in opened] == ["two"]


async def test_enter_without_selection_opens_highlighted(fake_home, opened):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        highlighted = app.current()
        await pilot.press("enter")
        await pilot.press("enter")  # again while it starts up: not opened twice
        assert app.is_running
    assert [launch.session_id for launch in opened] == [highlighted.session_id]


async def test_filter_and_escape(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        await pilot.press("slash", *"second")
        assert [s.title for s in app.visible()] == ["Second task"]
        await pilot.press("escape")
        assert app.query_one("DataTable").row_count == 2
        await pilot.press("escape")
    assert app.return_value is None


async def test_enter_in_filter_box_confirms_instead_of_opening(fake_home, opened):
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        await pilot.press("slash", *"first", "enter")
        assert not opened and app.is_running
        assert app.focused is app.query_one("DataTable")
        await pilot.press("enter")
    assert [launch.session_id for launch in opened] == ["one"]


async def test_save_and_open_workspace(fake_home, tmp_path, monkeypatch, opened):
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(workspaces.Path, "is_dir", lambda self: True)
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        await pilot.press("w")  # nothing ticked yet: only a warning
        assert not isinstance(app.screen, ModalScreen)
        await pilot.press("space", "w", *"Demo", "enter")
        assert list(workspaces.load()) == ["Demo"]
        await pilot.press("o")
        assert isinstance(app.screen, ModalScreen)
        await pilot.press("enter")
        assert app.is_running
    # The workspace holds the folder, so it continues the newest session there.
    (launch,) = opened
    assert launch.mode is Mode.RESUME


async def test_summary_key_shows_result_and_failure(fake_home, monkeypatch):
    from tabmancer import summarize

    monkeypatch.setattr(summarize, "summarize", lambda s: {"summary": "Did the thing.", "size": s.size})
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        await pilot.press("s")
        for _ in range(40):
            await pilot.pause(0.05)
            if app.current().session_id in app.summaries:
                break
        console = Console(width=120, file=io.StringIO(), record=True)
        console.print(app.detail.content)
        assert "Did the thing." in console.export_text()

        def boom(s):
            raise summarize.SummaryError("no quota")

        monkeypatch.setattr(summarize, "summarize", boom)
        await pilot.press("down", "s")
        for _ in range(40):
            await pilot.pause(0.05)
            if not app.summarizing:
                break
        assert app.current().session_id not in app.summaries


async def test_a_summary_reaches_the_preview_after_a_poll_replaced_the_session(fake_home):
    # An open session's object is replaced by every live poll, and a summary takes seconds: it was
    # compared by identity, so the preview kept saying "asking Claude" until something else redrew it.
    from dataclasses import replace

    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        asked = app.current()
        app.summarizing.add(asked.session_id)
        app.sessions[asked.session_id] = replace(asked)
        app.summary_done(asked, {"summary": "Did the thing.", "size": asked.size}, None)
        console = Console(width=120, file=io.StringIO(), record=True)
        console.print(app.detail.content)
        assert "Did the thing." in console.export_text()


async def test_a_resize_under_a_dialog_reaches_the_dashboard(fake_home):
    # The layout's classes went on the screen in front, a dialog, and the dashboard kept its old ones.
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 40)) as pilot:
        await loaded(pilot, app)
        dashboard = app.screen
        await pilot.press("question_mark")
        await pilot.resize_terminal(100, 26)
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert {"narrow", "short", "no-sidebar"} <= dashboard.classes


async def test_black_themes_cycle_and_old_saves_fall_back(fake_home):
    from tabmancer.config import read_json, write_json
    from tabmancer.theme import DEFAULT_THEME, THEMES

    write_json("config.json", {"theme": "tokyo-night"})  # a purple theme saved by an older version
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        assert app.theme == DEFAULT_THEME
        await pilot.press("t")
        assert app.theme == list(THEMES)[1]
    assert read_json("config.json")["theme"] == list(THEMES)[1]


async def test_a_theme_saved_under_the_old_name_is_kept(fake_home):
    from tabmancer.config import write_json

    for saved in ("ccrestore-black-green", "ccharbor-black-mono"):
        write_json("config.json", {"theme": saved})
        app = SessionPicker(days=14)
        async with app.run_test() as pilot:
            await loaded(pilot, app)
            assert app.theme == "tabmancer-" + saved.split("-", 1)[1]


async def test_inspector_shows_facts_next_to_the_preview(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await loaded(pilot, app)
        import io

        from rich.console import Console

        console = Console(width=80, record=True, file=io.StringIO())
        console.print(app.meta_table(app.current()))
        facts = console.export_text()
    assert "Folder" in facts and "Last active" in facts
    assert "Opens as" in facts and "claude --resume" in facts


async def test_without_a_terminal_for_tabs_it_closes_with_the_commands(fake_home, monkeypatch):
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: [f"claude --resume {launches[0].session_id}"])
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
        highlighted = app.current()
        await pilot.press("enter")
    assert app.return_value == [f"claude --resume {highlighted.session_id}"]


async def test_a_poll_that_lands_while_the_app_closes_does_nothing(fake_home):
    # Seen on the Windows CI: the 2 s poll fired with no screen left, and asked for one.
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        await loaded(pilot, app)
    app.poll_tick()
