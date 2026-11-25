"""A cap not among picks.CAPS (500k from config.json or TABMANCER_CONTEXT_CAP, a team's own) is kept by the
terminal's forms: shown, picked, answered as it is, and said in words, never turned into 200k unasked."""

from __future__ import annotations

from textual.app import App

from tabmancer import newforms, picks, teamforms, teams
from tabmancer.dialogs import Choice


class Host(App):
    def __init__(self, dialog) -> None:
        super().__init__()
        self.dialog, self.answers = dialog, []

    def on_mount(self) -> None:
        self.push_screen(self.dialog, self.answers.append)


async def test_a_teams_500k_cap_stays_500k_through_the_handover_dialog(fake_home, tmp_path):
    from tabmancer.teamview import TeamHandoverDialog

    team = teams.new_team("shop", str(tmp_path), "g", teams.parse_members("lead: steer\napi: a", lead_ok=True),
                          cap="500k")
    dialog = TeamHandoverDialog(team, "## written", "api: a", True)
    app = Host(dialog)
    async with app.run_test(size=(170, 44)) as pilot:
        await pilot.pause(0.2)
        assert dialog.query_one("#handover-cap", Choice).value == "500k"
        assert "kept as it is" in str(dialog.query_one("#handover-cap-words").render())
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
    assert app.answers == [("api: a", "500k")]


async def test_a_500k_from_config_is_the_new_team_and_new_sessions_forms_cap(fake_home, tmp_path, monkeypatch):
    from tabmancer.newsessions import NewSessions
    from tabmancer.teamview import NewTeamDialog

    monkeypatch.setenv("TABMANCER_CONTEXT_CAP", "500k")
    team_form = NewTeamDialog(str(tmp_path), set())
    async with Host(team_form).run_test(size=(170, 44)) as pilot:
        await pilot.pause(0.2)
        assert team_form.query_one("#team-cap", Choice).value == "500k"
        assert "500k: " + picks.OWN_CAP in team_form.cap_help()
    sessions_form = NewSessions([])
    async with Host(sessions_form).run_test(size=(170, 44)) as pilot:
        await pilot.pause(0.2)
        assert sessions_form.query_one("#new-cap", Choice).value == "500k"
    values = {row.name: row.value for row in newforms.choices()}
    assert newforms.pick_words("claude", values, "cap") == "500k: " + picks.OWN_CAP  # no KeyError
    assert teamforms.cap_help("500k").endswith("500k: " + picks.OWN_CAP + ".")


def test_a_choice_keeps_a_value_it_does_not_list_first_and_picked():
    assert (Choice(["a", "b"], "z").words, Choice(["a", "b"], "z").value) == (["z", "a", "b"], "z")
    assert (Choice(["a", "b"], "b").words, Choice(["a", "b"], "b").value) == (["a", "b"], "b")
    assert (Choice(["a", "b"]).words, Choice(["a", "b"]).value) == (["a", "b"], "a")
