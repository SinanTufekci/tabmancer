"""The new-team dialog: the line-up one member a line, a model per member, what will open shown
as you type, and the project folder completed from the folders you have worked in."""

from __future__ import annotations

from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import teams
from tabmancer.teamview import LEAD_LINE, NewTeamDialog


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
        shown = str(preview.render())
        assert shown.startswith("Opens 3 tabs in shop: check-out-lead (opus), check-out-api, check-out-tests (sonnet)")
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
        assert isinstance(host.result, teams.Team) and host.result.name == "check-out"
        assert [(m.name, m.model) for m in host.result.members] == [("lead", "opus"), ("api", ""), ("tests", "sonnet")]


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
