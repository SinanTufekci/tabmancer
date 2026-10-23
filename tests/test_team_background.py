"""A team whose members run in the background: chosen in the new-team dialog (Ctrl+B), its
members started with claude --bg before the lead's tab, and stopped rather than closed."""

from __future__ import annotations

from datetime import datetime

from test_new_team import Host
from textual.widgets import Input, Static, TextArea

from tabmancer import app as app_module
from tabmancer import background, cli, focus, teams
from tabmancer import launch as launch_module
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.teamview import NewTeamDialog, close_tabs


def a_team(tmp_path, background_too=True) -> teams.Team:
    return teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: x; ui: y"),
                          background=background_too)


async def test_ctrl_b_in_the_dialog_runs_the_members_in_the_background(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    dialog = NewTeamDialog(str(folder), set())
    host = Host(dialog)
    async with host.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-name", Input).value = "shop"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        await pilot.pause(0.1)
        assert "Opens 4 tabs" in str(dialog.query_one("#team-preview", Static).render())
        await pilot.press("ctrl+b")
        shown = str(dialog.query_one("#team-preview", Static).render())
        assert "Starts 3 in the background" in shown and "then opens a tab for shop-lead" in shown
        assert "● the members in the background" in str(dialog.query_one("#team-where", Static).render())
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
    assert isinstance(host.result, teams.Team) and host.result.background


def test_the_choice_is_kept_and_the_lead_is_told(tmp_path, monkeypatch):
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    team = a_team(tmp_path)
    teams.save({team.name: team})
    assert teams.load()["shop"].background
    deciding = teams.new_team("solo", str(tmp_path), "goal", [], decide=3, background=True)
    assert "starts as a background session, with no tab" in teams.role(deciding, deciding.lead)
    lead, api = (teams.launch(team, m) for m in team.members[:2])
    assert not teams.in_background(team, lead) and teams.in_background(team, api)
    first = teams.background_start(team, api)
    assert first.prompt.startswith("You are on the team shop, in the background.")
    assert teams.from_tabmancer(team, first.prompt)  # tabmancer's words, not yours


async def test_the_members_start_in_the_background_before_the_leads_tab(fake_home, tmp_path, monkeypatch):
    order: list[tuple[str, str]] = []
    monkeypatch.setattr(app_module, "run_in_background",
                        lambda launch: order.append(("bg", launch.title)) or "started")
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: order.extend(("tab", x.title) for x in launches) or [])
    team = a_team(tmp_path)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.2)
        app.open_team(team, teams.start(team, {}))
        for _ in range(60):
            await pilot.pause(0.05)
            if len(order) == 3:
                break
    assert order == [("bg", "shop: api"), ("bg", "shop: ui"), ("tab", "shop: lead")]


def test_a_lead_adding_members_starts_them_in_the_background(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    started: list[Launch] = []
    monkeypatch.setattr(launch_module, "run_in_background", lambda launch: started.append(launch) or "started")
    monkeypatch.setattr(cli, "open_tabs", lambda launches: (_ for _ in ()).throw(AssertionError("no tabs")))
    team = teams.new_team("solo", str(tmp_path), "goal", [], decide=3, background=True)
    teams.save({team.name: team})
    assert cli.add_to_team("solo", "api: the endpoints; ui: the screens") == 0
    assert [launch.title for launch in started] == ["solo: api", "solo: ui"]
    assert all(launch.mode is Mode.NEW and "--name" in launch.options for launch in started)
    assert "starting in the background" in capsys.readouterr().out


def test_closing_stops_the_members_that_have_no_tab(monkeypatch):
    stopped: list[str] = []
    monkeypatch.setattr(focus, "close_tab", lambda title: title == "shop-lead")  # only the lead has a tab
    monkeypatch.setattr(background, "jobs", lambda: [
        background.Job("b1", "s1", "shop-api", "C:/shop", "idle", True, datetime.now()),
        background.Job("b2", "s2", "shop-ui", "C:/shop", "stopped", False, datetime.now())])
    monkeypatch.setattr(background, "stop", lambda job_id: stopped.append(job_id) or "stopped")
    said, severity = close_tabs(["shop-lead", "shop-api"])
    assert stopped == ["b1"] and severity == "information"
    assert said == "The team's members: closed 1 tab and stopped 1 in the background."
    said, severity = close_tabs(["shop-ui"])  # not running: nothing to stop, and it says so
    assert severity == "warning" and "shop-ui" in said
