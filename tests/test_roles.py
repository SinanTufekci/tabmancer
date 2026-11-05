"""Help with the roles: the files a member owns, the rules of the usual kinds of member, a line-up
suggested for the goal (Ctrl+R) and what a member is told, word for word (Ctrl+G)."""

from __future__ import annotations

import subprocess

from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import summarize, teams
from tabmancer.teamview import NewTeamDialog, RoleDialog

REAL_ASK = summarize.ask  # conftest stubs it for every test; one test drives it through a fake subprocess


def test_a_line_can_say_which_files_are_the_members_own():
    api, review = teams.parse_members("- api: the endpoints [src/api, docs/api.md] @sonnet @high\n2. review: checks")
    assert (api.name, api.focus, api.owns, api.model, api.effort) == (
        "api", "the endpoints", "src/api, docs/api.md", "sonnet", "high")
    assert api.line() == "api: the endpoints [src/api, docs/api.md] @sonnet @high" and review.line() == "review: checks"
    team = teams.new_team("shop", "C:/shop", "Ship it", [api, review])
    role = teams.role(team, api)
    assert "Yours to edit: src/api, docs/api.md. The others' files are theirs" in role
    assert "shop-api (the endpoints; owns src/api, docs/api.md)" in teams.role(team, review)  # the others know
    teams.save({"shop": team})
    assert teams.load()["shop"].members[1].owns == "src/api, docs/api.md"


def test_the_usual_kinds_of_member_get_their_rules():
    team = teams.new_team("shop", "C:/shop", "Ship it",
                          teams.parse_members("reviewer: checks\ntests: e2e\ndocs: guide\nresearch: options\napi: x"))
    rules = {m.name: teams.role(team, m) for m in team.members if not m.lead}
    assert "you do not change the code yourself" in rules["reviewer"]
    assert "rather than fixing their code yourself" in rules["tests"]
    assert "You write the documentation" in rules["docs"] and "change no files" in rules["research"]
    assert teams.kind_of(team.members[-1]) is None and teams.duties(team.members[-1]) == ""


def test_the_suggestion_is_asked_with_the_goal_and_the_projects_top_level(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("# Shop\nA small web shop.", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    prompt = teams.suggestion_prompt("Add a checkout", str(tmp_path), 4)
    assert "Goal: Add a checkout" in prompt and "At its top level: src/, README.md" in prompt
    assert "A small web shop." in prompt and ".git" not in prompt and "besides a lead: 2 to 4," in prompt
    assert "Team size: N, because" in prompt  # the size and why, asked for first
    answer = ("Here you go:\n```\nTeam size: 1, because one part.\nlead: plans @opus @xhigh\n"
              "- api: endpoints [src/api] @sonnet\n```")
    lines = teams.suggested_lines(answer)
    assert lines == "lead: plans @opus @xhigh\napi: endpoints [src/api] @sonnet"  # the size line is no member
    assert teams.suggested_size(answer, lines) == (1, True, "because one part.")


def test_the_size_is_what_the_lines_name_and_the_why_is_claudes():
    for said, why in [("**Team size:** 3, because the parts are separate", "because the parts are separate"),
                      ("**Team size**: 3, because the parts are separate", "because the parts are separate"),
                      ("Team size: 3 — three separate parts.", "three separate parts."),
                      ("- team size: 3", ""), ("Nothing about the size.", "")]:
        lines = "web: pages\napi: endpoints\ntests: e2e"
        assert teams.suggested_size(f"{said}\n{lines}", lines) == (3, False, why), said
    lines = "web: pages\napi: endpoints"  # it said 5 and named 2: what is written counts
    assert teams.suggested_size(f"Team size: 5, because\n{lines}", lines)[0] == 2
    assert teams.SUGGEST_MOST == 8 and "2 to 8," in teams.suggestion_prompt("g", ".")


def test_in_a_git_repository_the_suggestion_sees_its_files_folder_by_folder(tmp_path):
    for name in ("src/api/routes.py", "src/api/models.py", "README.md"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    for argv in (["init", "-q"], ["add", "."]):
        subprocess.run(["git", "-C", str(tmp_path), *argv], check=True, capture_output=True)
    assert teams.project_files(tmp_path) == "./: README.md\nsrc/api/: models.py, routes.py"
    assert "Its files, by folder: \n./: README.md\nsrc/api/: models.py, routes.py" in teams.suggestion_prompt(
        "goal", str(tmp_path))


def test_ask_leaves_the_model_to_your_settings_and_passes_the_effort(monkeypatch):
    ran = []
    monkeypatch.setattr(summarize.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(summarize.subprocess, "run",
                        lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "ok", ""))
    assert REAL_ASK("hello", "", effort="medium") == "ok"
    assert "--model" not in ran[0] and ran[0][ran[0].index("--effort") + 1] == "medium"
    REAL_ASK("hello", "haiku")
    assert ran[1][ran[1].index("--model") + 1] == "haiku" and "--effort" not in ran[1]


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog = dialog

    def on_mount(self) -> None:
        self.push_screen(self.dialog)


async def until(pilot, check, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def test_ctrl_r_writes_a_suggested_line_up_and_ctrl_g_shows_a_role(tmp_path, monkeypatch):
    asked = []

    def answer(prompt, model, timeout=180, effort=""):
        asked.append((model, effort))
        return ("Team size: 2, because the pages and the index are separate parts.\n"
                "web: the pages [src/web] @sonnet @high\nsearch: the index [src/search] @sonnet @medium")

    monkeypatch.setattr(summarize, "ask", answer)
    dialog = NewTeamDialog(str(tmp_path), set())
    notes = []
    real_notify = dialog.notify
    monkeypatch.setattr(dialog, "notify", lambda message, **kw: notes.append(message) or real_notify(message, **kw))
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+r")  # no goal yet: nothing is asked
        await pilot.pause(0.1)
        assert not asked and not dialog.asking
        dialog.query_one("#team-name", Input).value = "docs"
        dialog.query_one("#team-goal", TextArea).text = "Search on the docs site"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: "web: the pages" in dialog.query_one("#team-members", TextArea).text)
        assert asked == [("", "medium")]  # your default model, a medium effort
        assert "Team size" not in dialog.query_one("#team-members", TextArea).text
        assert notes[-1].startswith("Claude suggests 2 members, as peers — because the pages and the index are "
                                    "separate parts. Change what you like")  # the size, and why
        assert dialog.peers() and "Nobody leads" in str(dialog.query_one("#team-preview", Static).render())
        box = dialog.query_one("#team-members", TextArea)
        box.focus()
        box.move_cursor((1, 0))  # the search line
        await pilot.press("ctrl+g")
        await until(pilot, lambda: isinstance(dialog.app.screen, RoleDialog))
        shown = dialog.app.screen
        assert shown.member.name == "search"
        role = str(shown.query_one("#role-text Static").render())
        assert "one of the peers" in role and "Yours to edit: src/search" in role
        await pilot.press("escape")
        await until(pilot, lambda: dialog.app.screen is dialog)


async def test_where_the_lead_decides_ctrl_r_suggests_how_many_it_may_pick(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="": (
        "Team size: 3, because the endpoints, the pages and the tests can go on at once.\n"
        "lead: plans @opus @xhigh\napi: endpoints\nweb: pages\ntests: e2e"))
    notes = []
    dialog = NewTeamDialog(str(tmp_path), set())
    monkeypatch.setattr(dialog, "notify", lambda message, **kw: notes.append(message))
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-name", Input).value = "shop"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        dialog.template = dialog.lineups.index(teams.DECIDE) - 1
        dialog.action_template()  # to lead decides: the lead line alone
        assert dialog.deciding()
        box = dialog.query_one("#team-members", TextArea).text
        dialog.query_one("#team-most", Input).value = "7"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: not dialog.asking and notes)
        assert dialog.query_one("#team-most", Input).value == "3"  # the size, as its limit
        assert dialog.query_one("#team-members", TextArea).text == box and dialog.deciding()  # still the lead's pick
        assert notes[-1].startswith("Claude suggests at most 3 members — because the endpoints, the pages and the "
                                    "tests can go on at once. The lead picks them")


async def test_a_failed_suggestion_leaves_the_box_and_says_why(tmp_path, monkeypatch):
    def fail(prompt, model, timeout=180, effort=""):
        raise summarize.SummaryError("You've hit your limit")

    monkeypatch.setattr(summarize, "ask", fail)
    notes = []
    dialog = NewTeamDialog(str(tmp_path), set())
    monkeypatch.setattr(dialog, "notify", lambda message, **kw: notes.append(message))
    async with Host(dialog).run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        before = dialog.query_one("#team-members", TextArea).text
        dialog.query_one("#team-goal", TextArea).text = "Anything"
        await pilot.press("ctrl+r")
        await until(pilot, lambda: not dialog.asking and notes)
        assert dialog.query_one("#team-members", TextArea).text == before
        assert notes == ["No line-up suggested: You've hit your limit"]
