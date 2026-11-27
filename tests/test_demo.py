"""`tabmancer --demo`: the made-up sessions in the dashboard, and nothing real within reach."""

import os
from pathlib import Path

import pytest
from conftest import box_has_focus, wait_until

from tabmancer import actions, alerts, app, cli, demo, focus, launch, teambus, teamview
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    """The demo's dashboard, folder and stand-ins, all put back after the test."""
    monkeypatch.setenv("CODEX_HOME", "restored after the test")  # prepare() sets it too
    live = demo.prepare(tmp_path)
    app = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, app.told):
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
        "usage.read_usage", "usage.read_codex", "bgjobs._run", "report.save", "stats.write_files",
        "app.recent_shutdowns", "shutdowns.recent_shutdowns", "gitstate.read", "agy.home", "share.open_page", "share.reveal",
        "browsers.places", "actions.close_tabs", "app.close_tabs", "teamview.close_tabs",
    }


def test_x_twice_in_the_demo_says_which_tabs_would_have_closed_and_closes_none(tmp_path, monkeypatch):
    for module, name, value in demo.stand_ins(tmp_path, [], print):
        monkeypatch.setattr(module, name, value)
    asked = []
    monkeypatch.setattr(focus, "close_tab", asked.append)  # never reached: nothing is looked for
    note = actions.closed_note(["api", "ui", "tests"])  # the desktop window's x twice
    assert note.words == ("Nothing closes in the demo. For real, the tabs of api, ui and tests would close, as on "
                          "/exit; the sessions stay.") and note.severity == "information"
    assert actions.closed_note(["api"], app.close_tabs).words.startswith("Nothing closes in the demo. For real, the "
                                                                         "tab of api would close")  # the dashboard's
    assert teamview.close_tabs is app.close_tabs is actions.close_tabs and asked == []  # the monitor's: the same


async def test_the_demo_sets_the_dashboard_up_once(in_demo, monkeypatch):
    # Textual calls on_mount on every class that has one: a super() call made it twice, every timer
    # twice, and the Windows runner could not keep up with the timers (the tests took minutes).
    mounted = []
    mount = SessionPicker.on_mount
    monkeypatch.setattr(SessionPicker, "on_mount", lambda self: mounted.append(1) or mount(self))
    async with in_demo.run_test(size=(150, 40)) as pilot:
        await pilot.pause(0.3)
    assert len(mounted) == 1


async def test_the_demo_lists_the_made_up_sessions_says_so_and_opens_nothing(in_demo):
    app = in_demo
    async with app.run_test(size=(150, 40)) as pilot:
        await wait_until(pilot, lambda: len(app.sessions) >= len(demo.SESSIONS))
        assert {"pcb-router", "web-app", "api-server"} <= set(app.sessions)
        assert {"pcb-router", "web-app"} <= app.preselected  # open at the made-up shutdown
        assert "demo" in str(app.query_one("#brand").render())
        assert demo.NOTE in told(app)
        # On a session's row: on the team's, which may come in first, Enter unfolds the team.
        await wait_until(pilot, lambda: "pcb-router" in app.table.rows)
        app.table.move_cursor(row=app.table.get_row_index("pcb-router"))
        await pilot.press("enter")
        await wait_until(pilot, lambda: any("Nothing opens in the demo" in note for note in told(app)))
        opening = [note for note in told(app) if "Nothing opens in the demo" in note]
        assert opening and "claude --resume" in opening[0]
        await pilot.pause(0.3)
        assert not [note for note in told(app) if note.startswith("Opened")]  # it did not
        assert app.selected  # still ticked: nothing was opened


async def test_a_prompt_written_in_the_demo_opens_nothing_and_says_so(in_demo):
    app = in_demo
    async with app.run_test(size=(150, 40)) as pilot:
        await wait_until(pilot, lambda: "mobile" in app.table.rows)
        app.table.move_cursor(row=app.table.get_row_index("mobile"))
        await pilot.press("p")
        await box_has_focus(pilot, app)
        await pilot.press(*"go on", "enter")
        await wait_until(pilot, lambda: any("Nothing opens in the demo" in note for note in told(app)))
        assert any("claude --resume mobile" in note for note in told(app))
        assert not [note for note in told(app) if note.startswith("Opened")]


@pytest.mark.parametrize("env", [(), (("BROWSER", "made-up"),)])  # a cd, or PowerShell's Set-Location
def test_what_would_have_opened_is_said_in_the_made_up_folder_never_your_home(tmp_path, env):
    told = []
    stand_in = {(module, name): value for module, name, value in demo.stand_ins(tmp_path, [], told.append)}
    opened = stand_in[launch, "open_tabs"]
    made_up = Launch(title="data", cwd="C:/dev/data-pipeline", mode=Mode.RESUME, session_id="data", env=env)
    nowhere = Launch(title="new", cwd="", mode=Mode.NEW, session_id="")
    assert opened([made_up]) == [] and opened([nowhere]) == []
    assert "C:/dev/data-pipeline" in told[0] and "claude --resume data" in told[0]
    assert all(str(Path.home()) not in words for words in told)  # a GIF of the demo shows nobody's home


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
        def told(self, text):
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


async def test_an_event_in_the_demo_is_said_as_the_dashboard_says_it(in_demo):
    """The demo's own words (what would have opened) are not the dashboard's tell(event): an event the
    engine tells reaches its note, as outside the demo, not a "Demo" note of the Event itself."""
    app = in_demo
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        app.engine.tell(alerts.newer_event("9.9", "uv tool upgrade tabmancer"))
        await pilot.pause()
        said = told(app)
        assert any(str(message).startswith("tabmancer 9.9 is out") for message in said), said
        assert not any("Event(" in str(message) for message in said), said


def test_demo_with_a_command_says_to_leave_it_out(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--demo", "list"])
    assert "leave out the command" in capsys.readouterr().err


async def test_the_demo_shows_a_second_account_and_a_session_from_another_organisation(in_demo):
    """The work account (a Team seat) it was signed in with this morning: its last limits in A, Bit's
    note at the limit, and c's word on the session that ran on its organisation."""
    from textual.widgets import Label, OptionList

    from tabmancer.accountsview import AccountsDialog
    from tabmancer.app import LimitDialog

    app = in_demo
    work = demo._account(demo.WORK)
    async with app.run_test(size=(160, 44)) as pilot:
        await wait_until(pilot, lambda: {"ml", "wiki"} <= app.told_limit)
        assert app.ran_elsewhere([app.sessions["wiki"]]) and not app.ran_elsewhere([app.sessions["ml"]])
        assert app.sessions["wiki"].sensitive == ["browser"]
        said = " ".join(saying.text for saying in app.mascot.bubbles)
        assert "you@work.example.com has room (session reset since, 5h" in said and said.endswith("ago): A to switch")
        await pilot.press("c")
        await wait_until(pilot, lambda: isinstance(app.screen, LimitDialog))
        assert str(app.screen.query_one(".away", Label).render()) == (
            "One of the ticked ran on another organisation: its conversation goes to your personal account "
            "if it goes on (browser content in it).")
        await pilot.press("escape")
        await pilot.pause(0.1)
        await pilot.press("A")
        await wait_until(pilot, lambda: isinstance(app.screen, AccountsDialog))
        options = app.screen.query_one(OptionList)
        line = next(str(options.get_option_at_index(i).prompt) for i in range(options.option_count)
                    if options.get_option_at_index(i).id == work.key)
        assert "session reset since · week 47% · read 5h" in line  # as Bit says it
        await pilot.press("escape")


def test_the_demo_never_reads_the_real_team_buses(tmp_path):
    """The machine's team buses (<temp>/tabmancer-bus: your teams', and a test's left behind) are never the
    demo's: a message on the real "shop" bus does not show as the demo team's. conftest's bus folder stands in
    for the real one, and is put back after (as made_up_world puts ENVIRON back)."""
    real = teambus.bus_root()
    teambus.send("shop", "lead", "dev", "from outside the demo")
    before = dict(os.environ)
    demo.prepare(tmp_path / "home")
    assert teambus.bus_root() == tmp_path / "home" / "bus" and teambus.bus_root() != real
    assert not any("outside the demo" in message["text"] for message in teambus.messages("shop"))
    changed = {name for name in {*before, *os.environ} if before.get(name) != os.environ.get(name)}
    assert changed <= set(demo.ENVIRON)  # all it points elsewhere is put back by name
