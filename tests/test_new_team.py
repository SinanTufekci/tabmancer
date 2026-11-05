"""The new-team dialog: the line-up one member a line, a model per member, what will open shown
as you type, and the project folder completed from the folders you have worked in."""

from __future__ import annotations

from conftest import team_preview
from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import teams
from tabmancer.teamview import LEAD_LINE, LineupGraph, NewTeamDialog


def test_a_model_per_member_and_a_lead_line_of_its_own():
    lineup = "lead @opus\napi: the endpoints @sonnet\nmail: write to admin@host\ntests @claude-haiku-5-5"
    members = teams.parse_members(lineup, lead_ok=True)
    assert [(m.name, m.focus, m.lead, m.model) for m in members] == [
        ("lead", teams.LEAD_FOCUS, True, "opus"), ("api", "the endpoints", False, "sonnet"),
        ("mail", "write to admin@host", False, ""),  # an @ inside the words is not a model
        ("tests", "", False, "claude-haiku-5-5")]
    assert [m.name for m in teams.parse_members(lineup)] == ["api", "mail", "tests"]  # adding members: no lead
    team = teams.new_team("shop", ".", "goal", members)
    assert team.members[0].lead and team.members[0].model == "opus"
    argv = teams.launch(team, team.members[1]).argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--model" not in teams.launch(team, team.members[2]).argv  # none given: Claude Code's default
    plain = teams.new_team("shop", ".", "goal", teams.parse_members("api: x"))
    assert plain.members[0].lead and plain.members[0].focus == teams.LEAD_FOCUS  # the lead is always there


def test_models_are_kept_with_the_team(fake_home):
    team = teams.new_team("shop", ".", "goal", teams.parse_members("lead @opus\napi: x @sonnet", lead_ok=True))
    teams.save({team.name: team})
    again = teams.load()["shop"]
    assert [(m.name, m.model) for m in again.members] == [("lead", "opus"), ("api", "sonnet")]


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, "not yet"

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda team: setattr(self, "result", team))


async def test_the_dialog_says_what_is_missing_and_then_what_will_open(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    dialog = NewTeamDialog(str(tmp_path / "nowhere"), {"taken"}, [str(folder), str(tmp_path / "other")])
    host = Host(dialog)
    async with host.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        preview = dialog.query_one("#team-preview", Static)
        assert dialog.query_one("#team-members", TextArea).text.startswith(LEAD_LINE)  # the lead, shown
        hint = dialog.query_one(".hint")
        # The keys fit one line of the dialog: the dashboard wraps a longer one, and in a 120x30
        # window the second line is cut off.
        assert len(str(hint.render())) <= hint.parent.content_size.width
        shown = str(preview.render())
        assert "Still needed: a name, a project folder that exists, the goal" in shown
        await pilot.press("ctrl+s")  # not yet: nothing closes
        assert host.result == "not yet"
        dialog.query_one("#team-name", Input).value = "Taken"
        await pilot.pause(0.1)
        assert "taken exists already" in str(preview.render())
        dialog.query_one("#team-name", Input).value = "Check Out"
        box = dialog.query_one("#team-folder", Input)
        box.value = ""
        box.focus()
        await pilot.press(*str(tmp_path / "sh"))
        await pilot.pause(0.2)
        await pilot.press("end", "right")  # → takes the folder it completes to
        assert box.value == str(folder)
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        dialog.query_one("#team-members", TextArea).text = "lead @opus\napi: endpoints\ntests @sonnet"
        await pilot.pause(0.1)
        assert team_preview(dialog).startswith("Opens 3 tabs in shop. The lead starts last")
        # ...and the team drawn as the team view draws it: the lead on top, each card with its model.
        drawn = dialog.query_one(LineupGraph).render().plain.split("\n")
        assert "★ lead" in drawn[1] and "opus" in drawn[1]
        assert "api" in drawn[5] and "tests" in drawn[5] and "sonnet" in drawn[5] and "┴" in drawn[4]
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
        assert isinstance(host.result, teams.Team) and host.result.name == "check-out"
        assert [(m.name, m.model) for m in host.result.members] == [("lead", "opus"), ("api", ""), ("tests", "sonnet")]


def made_projects(tmp_path):
    from datetime import datetime, timedelta

    from tabmancer.newsessions import Project

    now = datetime.now()
    found = []
    for name, sessions, hours in [("shop", 12, 1), ("blog", 3, 30), ("pcb-router", 7, 5)]:
        (tmp_path / name).mkdir()
        found.append(Project(str(tmp_path / name), sessions, now - timedelta(hours=hours)))
    return found


async def test_the_project_is_picked_from_the_ones_you_worked_in_as_in_n(tmp_path):
    from tabmancer.teamview import ProjectList

    found = made_projects(tmp_path)
    dialog = NewTeamDialog(found[0].cwd, set(), [p.cwd for p in found], found)
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        side, listing = dialog.query_one("#team-side"), dialog.query_one(ProjectList)
        assert not side.has_class("-picking")  # the name has the focus: the team drawn
        await pilot.press("enter")  # on to the Project box: the projects in the drawing's place
        await pilot.pause(0.2)
        assert dialog.focused.id == "team-folder" and side.has_class("-picking")
        rows = [str(listing.get_option_at_index(i).prompt) for i in range(listing.option_count)]
        assert [row.split()[0] for row in rows] == ["shop", "blog", "pcb-router"]  # newest first
        assert rows[0].split()[1] == "12" and listing.highlighted == 0  # the box's own, lit
        assert str(dialog.query_one("#projects-card").border_subtitle) == "3 projects"
        assert "shop" in str(dialog.query_one("#team-project-where", Static).render())
        await pilot.press("r", "o")  # typed over the box's words (all selected): they filter
        await pilot.pause(0.1)
        found_rows = [str(listing.get_option_at_index(i).prompt).split()[0] for i in range(listing.option_count)]
        assert found_rows == ["pcb-router", "shop", "blog"][:len(found_rows)] and found_rows[0] == "pcb-router"
        await pilot.press("backspace", "backspace", "b", "l")
        await pilot.pause(0.1)
        assert "blog" in str(listing.get_option_at_index(0).prompt) and listing.highlighted == 0
        await pilot.press("enter")  # picks it, and on to the goal
        await pilot.pause(0.1)
        assert dialog.query_one("#team-folder", Input).value.endswith("blog")
        assert dialog.focused.id == "team-goal" and not side.has_class("-picking")
        await pilot.press("shift+tab")  # back: every project again, blog lit
        await pilot.pause(0.1)
        assert listing.option_count == 3 and listing.highlighted == 1
        await pilot.press("down", "enter")  # ↓ moves through them from the box
        await pilot.pause(0.1)
        assert dialog.query_one("#team-folder", Input).value.endswith("pcb-router")
        await pilot.press("shift+tab")
        await pilot.pause(0.1)
        await pilot.click(ProjectList, offset=(3, 0))  # a click picks one too
        await pilot.pause(0.2)
        assert dialog.query_one("#team-folder", Input).value.endswith("shop") and dialog.focused.id == "team-goal"


def test_a_folder_written_out_comes_first_even_when_a_newer_one_matches_it_too(tmp_path):
    from datetime import datetime, timedelta

    from tabmancer.newsessions import Project, find

    now = datetime.now()
    (tmp_path / "api-v2").mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "web-app").mkdir()
    newer, older = Project(str(tmp_path / "api-v2"), 5, now), Project(str(tmp_path / "api"), 2, now - timedelta(days=3))
    app = Project(str(tmp_path / "web-app"), 1, now - timedelta(days=9))
    assert find(str(tmp_path / "api"), [newer, older, app])[0] is older  # what was typed, not the newer api-v2
    assert find("", [newer, older, app]) == [newer, older, app]
    # Name matches first: every folder here is under one temp folder, which any word of its path finds.
    assert find("app", [newer, older, app])[0].name == "web-app"


async def test_a_folder_written_out_is_offered_first_and_tab_never_stops_in_the_list(tmp_path):
    from tabmancer.teamview import ProjectList

    found = made_projects(tmp_path)
    elsewhere = tmp_path / "far" / "away"
    elsewhere.mkdir(parents=True)
    dialog = NewTeamDialog(found[0].cwd, set(), None, found)
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("tab")
        assert dialog.focused.id == "team-folder"
        box, listing = dialog.query_one("#team-folder", Input), dialog.query_one(ProjectList)
        box.value = str(elsewhere)  # a folder not among them, written out
        await pilot.pause(0.1)
        offered = listing.get_option_at_index(0)
        assert listing.option_count == 1 and not offered.disabled and str(offered.prompt).startswith("away ")
        box.value = str(tmp_path / "nothing-here")
        await pilot.pause(0.1)
        assert listing.get_option_at_index(0).disabled  # says nothing matches, and Enter keeps what is typed
        await pilot.press("enter")
        assert box.value == str(tmp_path / "nothing-here") and dialog.focused.id == "team-goal"
        assert "a project folder that exists" in team_preview(dialog)
        await pilot.press("shift+tab", "tab")  # Tab from the box goes to the goal, never into the list
        assert dialog.focused.id == "team-goal" and dialog.folders == [p.cwd for p in found]


async def test_line_ups_are_shown_by_name_and_keep_the_lead_line(tmp_path):
    dialog = NewTeamDialog(str(tmp_path), set())
    async with Host(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        lineup = dialog.query_one("#team-lineup", Static)
        assert "● feature team" in str(lineup.render()) and "○ bug hunt" in str(lineup.render())
        members = dialog.query_one("#team-members", TextArea)
        members.text = members.text.replace(LEAD_LINE, "lead @opus")  # the lead's model, chosen
        await pilot.pause(0.1)
        assert "● feature team" in str(lineup.render())  # still the same members
        members.text = members.text.replace("ui:", "web:")
        await pilot.pause(0.1)
        assert "● your own" in str(lineup.render())  # a member changed: no longer the ready-made one
        await pilot.press("ctrl+t")
        assert "● bug hunt" in str(lineup.render())
        assert members.text.split("\n")[0] == "lead @opus"  # the lead line stays as it was
        assert "reproduce:" in members.text
