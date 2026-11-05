"""A goal weighed against a team at work in its project (T's Ctrl+R): the suggestion is told of the team,
and says to make a team of its own, to add members to that one (its lead told), or that it has the goal
already (a warning, and the goal goes to its lead as a prompt)."""

from __future__ import annotations

from dataclasses import replace

from conftest import team_preview
from textual.app import App
from textual.widgets import Static, TextArea

from tabmancer import summarize, teams, typein
from tabmancer.sessions import Exit, Session, Status
from tabmancer.teamview import LEAD_LINE, JoinTeam, LineupGraph, NewTeamDialog, TellTeam


def empire(folder):
    project = folder / "game"
    project.mkdir(exist_ok=True)
    return teams.new_team("arcade", str(project), "Build the idle game", teams.parse_members(
        "lead @opus\nui: the screens [src/ui]\nbuild: packaging [build]", lead_ok=True))


def test_a_team_at_work_is_told_to_the_suggestion_and_weighed_first(tmp_path):
    team = empire(tmp_path)
    prompt = teams.suggestion_prompt("Add sound effects", team.cwd, working=[(team, {"lead", "ui"})])
    assert "Team arcade (led by arcade-lead). Its goal: Build the idle game" in prompt
    assert "- arcade-ui: the screens [src/ui] (open)" in prompt
    assert "- arcade-build: packaging [build] (closed)" in prompt
    assert prompt.index("Decision: add to <team>") < prompt.index("Team size: N")  # decided first
    plain = teams.suggestion_prompt("Add sound effects", team.cwd)
    assert "Decision:" not in plain and "Already at work" not in plain


def test_adding_to_a_team_at_work_names_only_the_members_to_add_and_whom_to_tell(tmp_path):
    team = empire(tmp_path)
    answer = ("Decision: add to arcade\nTeam size: 1, because sound is a part of its own.\n"
              "lead: plans @opus\nsound: effects and music [src/sound] @sonnet @high\n"
              "To arcade-lead: Sound joins the game: arcade-sound makes the effects,\n"
              "hand it the moments that need them.")
    found = teams.read_suggestion(answer, [team], "Add sound effects")
    assert (found.kind, found.team, found.to, found.count, found.lead) == (
        teams.ADD_TO, "arcade", "arcade-lead", 1, False)
    assert found.lines == "sound: effects and music [src/sound] @sonnet @high"  # no lead line: it has one
    assert found.message == ("Sound joins the game: arcade-sound makes the effects, hand it the moments "
                             "that need them.")  # the message goes on to the answer's end
    assert found.why == "because sound is a part of its own."


def test_a_team_at_work_that_has_the_goal_gets_it_as_a_prompt(tmp_path):
    team = empire(tmp_path)
    found = teams.read_suggestion("**Decision:** arcade has it\nTo `arcade-ui`: Make the shop scroll.",
                                  [team], "the shop should scroll")
    assert (found.kind, found.to, found.message, found.lines) == (
        teams.HAS_IT, "arcade-ui", "Make the shop scroll.", "")
    found = teams.read_suggestion("Decision: arcade has it\nTo someone-else: x", [team], "g")
    assert found.to == "arcade-lead" and found.message == "x"  # nobody on it by that name: its lead
    found = teams.read_suggestion("Decision: arcade has it", [team], "Scroll the shop")
    assert found.message == "A request from the user for the team: Scroll the shop"
    added = teams.read_suggestion("Decision: add to arcade\nTo arcade-lead: do it", [team], "g")
    assert added.kind == teams.HAS_IT  # members to add, but none named: the team has it


def test_a_message_written_before_the_members_takes_none_of_them(tmp_path):
    team = empire(tmp_path)
    answer = ("Decision: add to arcade\nTo arcade-lead: Sound joins the game,\nhand it the moments.\n"
              "Team size: 2, because two parts.\nsound: effects [src/sound]\nmusic: the score")
    found = teams.read_suggestion(answer, [team], "g")
    assert (found.kind, found.lines, found.count) == (teams.ADD_TO, "sound: effects [src/sound]\nmusic: the score", 2)
    assert found.message == "Sound joins the game, hand it the moments."


def test_a_decision_for_no_team_at_work_is_a_new_team(tmp_path):
    team = empire(tmp_path)
    for decided in ("Decision: new team", "Decision: add to another-team", "Decision: other has it"):
        found = teams.read_suggestion(f"{decided}\nTeam size: 2, because x\napi: a\nweb: b", [team], "g")
        assert (found.kind, found.lines, found.count) == (teams.OWN_TEAM, "api: a\nweb: b", 2), decided
    assert teams.suggested_lines("Decision: new team\napi: a") == "api: a"  # the decision is no member


def test_the_goal_grows_and_joining_members_are_told_what_for(tmp_path):
    team = empire(tmp_path)
    teams.save({team.name: team})
    grown = teams.add_to_goal("arcade", "Add sound\neffects")
    assert grown.goal.startswith("Build the idle game\n\nAdded ") and grown.goal.endswith(": Add sound\neffects")
    assert teams.load()["arcade"].goal == grown.goal and teams.add_to_goal("nobody", "x") is None
    (launch,) = teams.joining(grown, teams.parse_members("sound: effects"), adds="Add sound\neffects")
    assert launch.prompt.startswith("You just joined the team arcade for what its goal now adds: Add sound "
                                    "effects. Introduce yourself to arcade-lead")
    assert teams.from_tabmancer(grown, launch.prompt)  # tabmancer's words, not yours, in the team's talk
    first = teams.launch(team, team.lead).prompt  # what the lead started with, before the goal grew
    assert first.startswith("Goal: Build the idle game ") and teams.from_tabmancer(grown, first)
    assert not teams.from_tabmancer(grown, "Goal: something you wrote yourself")


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, "not yet"

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda result: setattr(self, "result", result))


async def until(pilot, check, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def test_ctrl_r_adds_to_a_team_at_work_and_ctrl_n_makes_a_new_one_after_all(tmp_path, monkeypatch):
    team = empire(tmp_path)
    asked = []

    def answer(prompt, model, timeout=180, effort=""):
        asked.append(prompt)
        return ("Decision: add to arcade\nTeam size: 1, because sound is its own part.\n"
                "sound: effects [src/sound] @sonnet\nTo arcade-lead: Sound joins: hand it the moments.")

    monkeypatch.setattr(summarize, "ask", answer)
    dialog = NewTeamDialog(team.cwd, {"arcade"}, working=[(team, {"lead", "ui"})])
    host = Host(dialog)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        line = dialog.query_one("#team-atwork", Static)
        assert line.display and "arcade (2 open) at work in this project" in str(line.render())  # said first
        dialog.query_one("#team-goal", TextArea).text = "Add sound effects"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.toward is not None)
        assert "Team arcade (led by arcade-lead)" in asked[0]
        assert dialog.query_one("#team-members", TextArea).text == "sound: effects [src/sound] @sonnet"
        assert str(dialog.query_one("#lineup-card").border_title) == "Members to add to arcade"
        assert team_preview(dialog).startswith("Adds 1 to arcade. arcade is at work here")
        assert [m.name for m in dialog.query_one(LineupGraph).team.members] == ["lead", "ui", "build", "sound"]
        assert not dialog.query_one("#team-atwork").display  # the card says it now
        await pilot.press("ctrl+n")  # a team of its own after all
        await pilot.pause(0.1)
        assert dialog.toward is None and dialog.query_one("#team-members", TextArea).text.startswith(LEAD_LINE)
        assert str(dialog.query_one("#lineup-card").border_title) == "Line-up"
        mine = "lead @opus\nsfx: the sounds\nmix: the levels"
        dialog.query_one("#team-members", TextArea).text = mine  # a line-up of your own...
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.toward is not None)
        await pilot.press("ctrl+n")
        await pilot.pause(0.1)
        assert dialog.query_one("#team-members", TextArea).text == mine  # ...back as you left it
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.toward is not None)
        await pilot.press("ctrl+t")  # so does Ctrl+T, back to the line-up it was on, not the next one
        await pilot.pause(0.1)
        assert dialog.toward is None and dialog.template == 0
        assert dialog.query_one("#team-members", TextArea).text == mine
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.toward is not None)
        await pilot.press("ctrl+s")  # no name of its own needed: it joins the team at work
        await until(pilot, lambda: host.result != "not yet")
        assert host.result == JoinTeam("arcade", "sound: effects [src/sound] @sonnet", "arcade-lead",
                                       "Sound joins: hand it the moments.", "Add sound effects")


async def test_a_team_at_work_that_has_the_goal_is_warned_of_and_its_lead_gets_the_message(tmp_path, monkeypatch):
    team = empire(tmp_path)
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="": (
        "Decision: arcade has it\nTo arcade-lead: Make the shop screen scroll on small windows."))
    notes = []
    dialog = NewTeamDialog(team.cwd, set(), working=[(team, {"lead"})])
    monkeypatch.setattr(dialog, "notify", lambda message, **kw: notes.append((message, kw.get("severity"))))
    host = Host(dialog)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-goal", TextArea).text = "the shop screen should scroll"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.toward is not None)
        assert notes[-1][1] == "warning" and notes[-1][0].startswith("arcade is at work on this goal already")
        box = dialog.query_one("#team-members", TextArea)
        assert box.text == "Make the shop screen scroll on small windows."
        assert team_preview(dialog).startswith("arcade has this goal. arcade is at work on this goal")
        assert str(dialog.query_one("#lineup-card").border_title) == "To arcade-lead"
        box.text = "Make the shop screen scroll,\non small windows too."  # yours to change
        await pilot.press("ctrl+s")
        await until(pilot, lambda: host.result != "not yet")
        assert host.result == TellTeam("arcade", "arcade-lead",
                                       "Make the shop screen scroll, on small windows too.")


async def test_a_team_at_work_in_another_project_is_not_weighed(tmp_path, monkeypatch):
    team = empire(tmp_path)
    other = tmp_path / "blog"
    other.mkdir()
    asked = []
    monkeypatch.setattr(summarize, "ask",
                        lambda prompt, model, timeout=180, effort="": asked.append(prompt) or "api: a\nweb: b")
    dialog = NewTeamDialog(str(other), set(), working=[(team, {"lead"})])
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        assert not dialog.query_one("#team-atwork").display
        dialog.query_one("#team-goal", TextArea).text = "g"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: asked and not dialog.asking)
        assert "Already at work" not in asked[0] and dialog.toward is None


# --- the dashboard: the members join, the lead is told --------------------------------------------

async def test_the_lead_gets_the_message_typed_in_once_it_waits_for_you(fake_home, monkeypatch):
    from tabmancer import teamtree
    from tabmancer.app import SessionPicker

    team = empire(fake_home)
    teams.save({team.name: team})
    typed = []
    monkeypatch.setattr(typein, "type_into", lambda sid, text: typed.append((sid, text)) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        lead = Session("lead-1", None, cwd=team.cwd, exit=Exit.RUNNING, status=Status.RUNNING, live_state="busy")
        app.sessions["lead-1"] = lead
        app.team_states = {team.name: teamtree.TeamState(team, {"lead": lead, "ui": None, "build": None}, [])}
        app.tell_team("arcade", "arcade-lead", "Make the shop scroll.")
        await pilot.pause(0.1)
        assert typed == [] and app.notes == {"lead-1": ("arcade-lead", "Make the shop scroll.")}  # at work
        app.apply_live_updates({"lead-1": replace(lead, live_state="permission")})  # asks you something: not now
        await pilot.pause(0.1)
        assert typed == [] and app.notes
        app.apply_live_updates({"lead-1": replace(lead, live_state="idle")})  # waits for you: in it goes
        await until(pilot, lambda: typed)
        assert typed == [("lead-1", "Make the shop scroll.")] and not app.notes


async def test_members_join_a_team_at_work_and_a_closed_lead_opens_with_the_message(fake_home, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker
    from tabmancer.teamview import TeamScreen

    team = empire(fake_home)
    teams.save({team.name: team})
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        app.join_team(JoinTeam("arcade", "sound: effects [src/sound]", "arcade-lead",
                               "Sound joins: hand it the moments.", "Add sound effects"))
        await until(pilot, lambda: len(opened) >= 2)
        saved = teams.load()["arcade"]
        assert [m.name for m in saved.members] == ["lead", "ui", "build", "sound"]
        assert saved.goal.endswith(": Add sound effects")  # the goal grew: its new members read both
        sound, lead = opened
        assert sound.title == "arcade: sound" and "for what its goal now adds: Add sound effects" in sound.prompt
        assert lead.title == "arcade: lead" and lead.prompt == "Sound joins: hand it the moments."
        await until(pilot, lambda: isinstance(app.screen, TeamScreen))


async def test_members_who_cannot_join_are_not_promised_to_the_lead(fake_home, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    team = empire(fake_home)
    teams.save({team.name: team})
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        app.join_team(JoinTeam("arcade", "ui: again\nsound: effects", "arcade-lead",
                               "ui and sound join.", "Add sound"))  # ui is on the team already
        await until(pilot, lambda: len(opened) >= 2)
        assert opened[1].prompt == "ui and sound join. (Not joining after all: ui.)"
        opened.clear()
        monkeypatch.setattr(teams, "opening", lambda now=None: set())  # the lead's tab has come up since
        before = teams.load()["arcade"].goal
        app.join_team(JoinTeam("arcade", "ui: again", "arcade-lead", "ui joins.", "Polish the ui"))
        await until(pilot, lambda: opened)
        assert teams.load()["arcade"].goal == before  # nobody joined: the goal is as it was
        assert [launch.prompt for launch in opened] == ["A request from the user for the team: Polish the ui"]


async def test_a_lead_opening_already_gets_the_message_on_the_clipboard_and_a_long_one_is_cut(fake_home, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    team = empire(fake_home)
    teams.save({team.name: team})
    opened, copied = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(app_module, "copy_text", lambda app, text: copied.append(text))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        teams.note_opening(["arcade-lead"])  # s a moment ago: its tab is coming up
        app.tell_team("arcade", "arcade-lead", "Scroll the shop.")
        await pilot.pause(0.1)
        assert opened == [] and copied == ["Scroll the shop."]  # not lost: yours to paste
        monkeypatch.setattr(teams, "opening", lambda now=None: set())
        plan = "\n".join(f"Step {n}: do the part number {n} of the plan carefully." for n in range(400))
        app.tell_team("arcade", "arcade-lead", plan)
        await until(pilot, lambda: opened)
        prompt = opened[0].prompt
        assert len(prompt) < teams.MESSAGE_IN_PROMPT + 60 and "\n" not in prompt  # fits a command line
        assert prompt.endswith("(the rest follows: the user pastes it)") and copied[-1] == plan
