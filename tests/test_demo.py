"""`tabmancer --demo`: the made-up sessions in the dashboard, and nothing real within reach."""

import pytest
from conftest import box_has_focus

from tabmancer import cli, demo


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    """The demo's dashboard, folder and stand-ins, all put back after the test."""
    monkeypatch.setenv("CODEX_HOME", "restored after the test")  # prepare() sets it too
    # Every open session waits quietly: sessions at work redraw the table twelve times a second,
    # which the Windows runner could not keep up with (these tests took minutes there).
    live = [dict(entry, status="idle", state="idle") for entry in demo.prepare(tmp_path)]
    app = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, app.tell):
        monkeypatch.setattr(module, name, value)
    return app


def told(app) -> list[str]:
    return [note.message for note in app._notifications]


def test_everything_that_reaches_outside_has_a_stand_in(tmp_path):
    replaced = {f"{module.__name__.rsplit('.', 1)[-1]}.{name}" for module, name, _ in demo.stand_ins(tmp_path, [], print)}
    # What conftest keeps every test away from, and more: the demo must keep the user away from it too.
    assert replaced >= {
        "sessions._agents_json", "app.open_tabs", "app.run_in_background", "focus.focus_tab", "focus.close_tab",
        "toast.show", "typein.write_keys", "rename.rename", "handover.write_itself", "summarize.ask",
        "usage.read_usage", "usage.read_codex", "background._run", "report.save", "stats.write_files",
        "app.recent_shutdowns", "gitstate.read", "agy.home",
    }


async def test_the_demo_lists_the_made_up_sessions_says_so_and_opens_nothing(in_demo):
    app = in_demo
    async with app.run_test(size=(150, 40)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if len(app.sessions) >= len(demo.SESSIONS):
                break
        assert {"pcb-router", "web-app", "api-server"} <= set(app.sessions)
        assert {"pcb-router", "web-app"} <= app.preselected  # open at the made-up shutdown
        assert "demo" in str(app.query_one("#brand").render())
        assert demo.NOTE in told(app)
        # On a session's row: on the team's, which may come in first, Enter unfolds the team.
        for _ in range(100):
            await pilot.pause(0.05)
            if "pcb-router" in app.table.rows:
                break
        app.table.move_cursor(row=app.table.get_row_index("pcb-router"))
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.05)
            if any("Nothing opens in the demo" in note for note in told(app)):
                break
        opening = [note for note in told(app) if "Nothing opens in the demo" in note]
        assert opening and "claude --resume" in opening[0]
        await pilot.pause(0.3)
        assert not [note for note in told(app) if note.startswith("Opened")]  # it did not
        assert app.selected  # still ticked: nothing was opened


async def test_a_prompt_written_in_the_demo_opens_nothing_and_says_so(in_demo):
    app = in_demo
    async with app.run_test(size=(150, 40)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if "mobile" in app.table.rows:
                break
        app.table.move_cursor(row=app.table.get_row_index("mobile"))
        await pilot.press("p")
        await box_has_focus(pilot, app)
        await pilot.press(*"go on", "enter")
        for _ in range(100):
            await pilot.pause(0.05)
            if any("Nothing opens in the demo" in note for note in told(app)):
                break
        assert any("claude --resume mobile" in note for note in told(app))
        assert not [note for note in told(app) if note.startswith("Opened")]


def test_the_made_up_answers_stand_in_for_the_models(in_demo):
    from tabmancer import report, summarize

    assert summarize.ask(summarize.INSTRUCTIONS + "notes", "haiku") == demo.SUMMARY_ANSWER
    assert summarize.ask(report.INSTRUCTIONS + "notes", "sonnet") == demo.DEMO_REPORT


def test_tabmancer_demo_runs_in_a_folder_of_its_own_and_cleans_it_up(tmp_path, monkeypatch):
    for module, name, _ in demo.stand_ins(tmp_path, [], print):
        monkeypatch.setattr(module, name, getattr(module, name))  # run() replaces them for good: put back
    monkeypatch.setenv("CODEX_HOME", "restored after the test")
    from tabmancer import app as app_module

    real_open_tabs = app_module.open_tabs
    seen = {}

    class Dashboard:
        def tell(self, text):
            pass

        def run(self):
            import os
            from pathlib import Path

            seen["home"] = Path(os.environ["CLAUDE_CONFIG_DIR"]).parent
            seen["sessions"] = len(list(Path(os.environ["CLAUDE_CONFIG_DIR"]).glob("projects/*/*.jsonl")))
            seen["stand-in"] = app_module.open_tabs is not real_open_tabs

    monkeypatch.setattr(demo, "picker", lambda days: Dashboard())
    cli.main(["--demo"])
    assert seen["sessions"] >= len(demo.SESSIONS) and seen["stand-in"]
    assert seen["home"].name.startswith("tabmancer-demo-") and not seen["home"].exists()


def test_demo_with_a_command_says_to_leave_it_out(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--demo", "list"])
    assert "leave out the command" in capsys.readouterr().err
