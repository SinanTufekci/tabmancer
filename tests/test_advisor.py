"""An advisor for a session (Claude Code's --advisor): picked in n and for a team's lead, given as a
flag so it holds for that session only, kept with the pick and given again on resume; never one
weaker than the session's model."""

from __future__ import annotations

from conftest import box_has_focus
from textual.app import App
from textual.widgets import Input, TextArea

from tabmancer import app as app_module
from tabmancer import cli, newsessions, picks, teams
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.newsessions import NewSessions
from tabmancer.picks import Pick
from tabmancer.teamview import NewTeamDialog


def test_only_a_model_as_strong_or_stronger_advises():
    assert picks.advisor_ok("sonnet", "opus") and picks.advisor_ok("sonnet", "fable")
    assert picks.advisor_ok("opus", "fable") and picks.advisor_ok("claude-opus-5-5", "opus")
    assert not picks.advisor_ok("fable", "opus")  # a Fable session takes only Fable
    assert picks.advisor_ok("fable", "fable") and picks.advisor_ok("", "opus")  # your default: not known here
    assert picks.advisor_ok("haiku", "") and not picks.advisor_ok("opus", "nonsense")


def test_the_advisor_is_a_flag_kept_and_given_again_on_resume():
    fresh = picks.fresh(Launch("api: new", ".", Mode.NEW, ""), Pick("opus", "high", "300k", "fable"))
    argv = fresh.argv
    assert argv[argv.index("--advisor") + 1] == "fable" and fresh.new_id
    assert picks.recall(fresh.new_id) == Pick("opus", "high", "300k", "fable")
    again = picks.resumed(Launch("api", ".", Mode.RESUME, fresh.new_id))
    assert again.argv.count("--advisor") == 1 and again.advisor == "fable"
    plain = picks.resumed(Launch("other", ".", Mode.RESUME, "never-picked"))
    assert "--advisor" not in plain.argv  # nothing picked, nothing given


def test_a_teams_lead_runs_with_its_advisor_and_keeps_it(fake_home):
    team = teams.new_team("shop", ".", "goal", teams.parse_members("lead @opus\napi: x @sonnet", lead_ok=True))
    team.members[0] = teams.Member(**{**team.members[0].__dict__, "advisor": "fable"})
    lead, api = team.members
    argv = teams.launch(team, lead).argv
    assert argv[argv.index("--advisor") + 1] == "fable" and argv.count("--advisor") == 1
    assert "--advisor" not in teams.launch(team, api).argv  # the lead's alone
    teams.save({team.name: team})
    assert teams.load()["shop"].members[0].advisor == "fable"


def test_tabmancer_new_takes_an_advisor(monkeypatch, tmp_path, capsys):
    started = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: started.extend(launches) or [])
    cli.main(["new", str(tmp_path), "--model", "sonnet", "--advisor", "opus"])
    assert started and started[0].advisor == "opus"
    try:
        cli.main(["new", str(tmp_path), "--model", "fable", "--advisor", "opus"])
    except SystemExit as stop:
        assert "opus cannot advise fable" in str(stop)
    else:
        raise AssertionError("a weaker advisor was taken")


async def test_the_n_dialog_has_an_advisor_row(fake_home, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    folder = tmp_path / "api"
    folder.mkdir()
    monkeypatch.setattr(newsessions, "projects", lambda sessions: [newsessions.Project(str(folder))])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(50):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.press("n")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, NewSessions):
                break
        dialog = app.screen
        await box_has_focus(pilot, app)
        assert dialog.query_one("#pick-card").border_title == "Model · Effort · Context · Advisor"
        dialog.query_one("#new-model").pick("fable")
        dialog.query_one("#new-advisor").pick("opus")
        await pilot.pause(0.1)
        assert dialog.pick().advisor == ""  # opus cannot advise fable: none is given
        assert "opus cannot advise fable" in str(dialog.query_one("#new-pick-words").render())
        dialog.query_one("#new-model").pick("opus")
        dialog.query_one("#new-advisor").pick("fable")
        await pilot.pause(0.1)
        assert (dialog.pick().model, dialog.pick().advisor) == ("opus", "fable")
        assert "fable advisor" in dialog.pick_parts()
        assert dialog.query_one(".hint").region.bottom <= 30  # the keys line still on a 120x30 screen
        await pilot.press("space", "ctrl+s")
        for _ in range(50):
            await pilot.pause(0.05)
            if opened:
                break
    argv = opened[0].argv
    assert argv[argv.index("--advisor") + 1] == "fable"


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, "not yet"

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda team: setattr(self, "result", team))


async def test_the_new_team_form_gives_the_lead_an_advisor(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    dialog = NewTeamDialog(str(folder), set(), [str(folder)])
    host = Host(dialog)
    async with host.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-name", Input).value = "checkout"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        dialog.query_one("#team-advisor").pick("fable")
        dialog.query_one("#team-advisor").focus()
        await pilot.pause(0.1)
        assert "the lead's advisor, for its session only" in str(dialog.query_one("#team-help").render())
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
    team = host.result
    assert isinstance(team, teams.Team)
    assert team.lead.advisor == "fable" and all(not m.advisor for m in team.members if not m.lead)
