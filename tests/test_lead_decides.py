"""A team whose lead decides: it starts alone, reads the goal and adds the members it needs, up to
a limit, with `tabmancer team add`, the one command it may run without asking."""

from __future__ import annotations

from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import app as app_module
from tabmancer import cli, teams
from tabmancer.teamview import NewTeamDialog, TeamScreen


def deciding(tmp_path, most=3) -> teams.Team:
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    return teams.new_team("shop", str(folder), "Add a checkout", teams.parse_members("lead @opus", lead_ok=True),
                          decide=most)


def test_the_lead_is_told_to_make_the_team_and_may_run_only_the_add_command(tmp_path):
    team = deciding(tmp_path)
    lead = team.lead
    argv = teams.launch(team, lead).argv
    tools = next(a for a in argv if a.startswith("--allowedTools="))
    assert tools == "--allowedTools=SendMessage,ListAgents,Bash(tabmancer team add:*)"
    role = argv[argv.index("--append-system-prompt") + 1]
    assert 'tabmancer team add shop "name: area @model @effort; name: area"' in role and "at most 3" in role
    assert "There are no other members yet" in role
    assert "tabmancer team add shop" in argv[-1]  # its first prompt: make the team, then hand out work
    fixed = teams.new_team("fixed", str(tmp_path), "goal", teams.parse_members("api: x"))
    for member in fixed.members:  # a line-up given: nobody runs commands unasked
        assert "--allowedTools=SendMessage,ListAgents" in teams.launch(fixed, member).argv


def test_members_join_up_to_the_limit_and_are_kept(fake_home, tmp_path):
    team = deciding(tmp_path, most=2)
    teams.save({team.name: team})
    added, note = teams.add_members(team, "api: endpoints @sonnet; ui: screens; docs: the guide")
    assert [(m.name, m.model) for m in added] == [("api", "sonnet"), ("ui", "")]
    assert "docs did not join" in note
    assert [m.name for m in teams.load()["shop"].members] == ["lead", "api", "ui"]
    added, note = teams.add_members(teams.load()["shop"], "api: again")
    assert not added and "already on the team: api" in note


def test_the_add_command_opens_their_tabs_and_says_whom_to_message(fake_home, tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    team = deciding(tmp_path)
    teams.save({team.name: team})
    assert cli.add_to_team("nosuch", "api: x") == 1 and "no team called nosuch" in capsys.readouterr().out
    assert cli.add_to_team("shop", "api: the endpoints @sonnet; ui: the screens") == 0
    said = capsys.readouterr().out
    assert "shop-api (the endpoints, sonnet), shop-ui (the screens)" in said and "SendMessage" in said
    assert [launch.title for launch in opened] == ["shop: api", "shop: ui"]
    assert "Introduce yourself to shop-lead" in opened[0].argv[-1]
    role = opened[1].argv[opened[1].argv.index("--append-system-prompt") + 1]
    assert "shop-api" in role and "shop-lead (orchestrator)" in role  # the newcomer knows the others


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, None

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda team: setattr(self, "result", team))


async def test_the_dialog_offers_the_lead_deciding_with_a_limit(tmp_path):
    dialog = NewTeamDialog(str(tmp_path), set())
    host = Host(dialog)
    async with host.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert not dialog.query_one("#team-most-row").display  # a line-up given: no limit to set
        for _ in range(3):
            await pilot.press("ctrl+t")
        assert "● lead decides" in str(dialog.query_one("#team-lineup", Static).render())
        assert dialog.query_one("#team-members", TextArea).text == "lead: " + teams.LEAD_FOCUS
        assert dialog.query_one("#team-most-row").display
        dialog.query_one("#team-name", Input).value = "shop"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        dialog.query_one("#team-most", Input).value = "6"
        await pilot.pause(0.1)
        preview = str(dialog.query_one("#team-preview", Static).render())
        assert preview.startswith("Opens 1 tab in") and "adds up to 6 members itself" in preview
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
        assert host.result.decide == 6 and [m.name for m in host.result.members] == ["lead"]


async def test_written_members_mean_a_line_up_of_your_own(tmp_path):
    dialog = NewTeamDialog(str(tmp_path), set())
    async with Host(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        for _ in range(3):
            await pilot.press("ctrl+t")
        dialog.query_one("#team-members", TextArea).text += "\napi: endpoints"
        await pilot.pause(0.1)
        assert not dialog.query_one("#team-most-row").display and not dialog.deciding()


async def test_the_team_view_shows_members_the_lead_adds(fake_home, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: [])
    team = deciding(tmp_path)
    teams.save({team.name: team})

    class Viewer(App):
        def on_mount(self) -> None:
            self.push_screen(TeamScreen(team, "#d97757"))

    viewer = Viewer()
    async with viewer.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        screen = viewer.screen
        assert [m.name for m in screen.graph.order] == ["lead"]
        assert "the lead picks up to 3 members" in str(screen.query_one("#team-head", Static).render())
        teams.add_members(teams.load()["shop"], "api: endpoints; ui: screens")  # from the lead's tab
        for _ in range(80):
            await pilot.pause(0.05)
            if len(screen.graph.order) == 3:
                break
        assert [m.name for m in screen.graph.order] == ["lead", "api", "ui"]
