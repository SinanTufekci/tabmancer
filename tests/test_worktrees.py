"""A team whose members each work in a git worktree of their own: claude --worktree <session name>
makes <project>/.claude/worktrees/<session name> on the branch worktree-<session name> (tried with
Claude Code 2.1.317), and the members are still known as the team's there."""

from __future__ import annotations

import subprocess
from datetime import datetime

from conftest import team_preview
from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import teams
from tabmancer.launch import Mode
from tabmancer.sessions import Session
from tabmancer.teamview import NewTeamDialog


def repository(folder):
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(folder), "init", "-q"], check=True, capture_output=True)
    return folder


def test_members_start_in_worktrees_of_their_own_and_the_lead_in_the_project(tmp_path):
    team = teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: x\nui: y"), worktrees=True)
    lead, api, _ = team.members
    argv = teams.launch(team, api).argv
    assert argv[argv.index("--worktree") + 1] == "shop-api" and "--worktree" not in teams.launch(team, lead).argv
    assert team.branch(api) == "worktree-shop-api"
    assert "on the branch worktree-shop-api" in teams.role(team, api) and "tell shop-lead" in teams.role(team, api)
    assert "merge it into the project here" in teams.role(team, lead)
    peers = teams.new_team("docs", str(tmp_path), "Docs", teams.parse_members("a: x\nb: y"), peers=True, worktrees=True)
    assert all("--worktree" in teams.launch(peers, m).argv for m in peers.members)  # no lead: every one
    assert "tell the user when the branch is ready" in teams.role(peers, peers.members[0])
    plain = teams.new_team("plain", str(tmp_path), "Goal", teams.parse_members("api: x"))
    assert not any("--worktree" in teams.launch(plain, m).argv for m in plain.members)
    assert "git worktree of your own" not in teams.role(plain, plain.members[1])
    teams.save({"shop": team})
    assert teams.load()["shop"].worktrees and not teams.load()["shop"].background


def test_a_member_in_its_worktree_is_still_the_teams_and_goes_on_there(tmp_path):
    team = teams.new_team("shop", str(tmp_path / "shop"), "Goal", teams.parse_members("api: x"), worktrees=True)
    there = str(tmp_path / "shop" / ".claude" / "worktrees" / "shop-api")
    session = Session("s1", None, cwd=there, title="shop-api", last_active=datetime.now())
    assert teams.in_team_folder(team, there) and teams.member_of(session, {"shop": team})[1].name == "api"
    assert not teams.in_team_folder(team, str(tmp_path / "other" / ".claude" / "worktrees" / "shop-api"))
    # A member that went into a folder of the project is still the member, and goes on in the project.
    inside = Session("s2", None, cwd=str(tmp_path / "shop" / "src"), title="shop-api", last_active=datetime.now())
    assert teams.in_team_folder(team, inside.cwd) and teams.member_of(inside, {"shop": team})[1].name == "api"
    assert teams.launch(team, team.members[1], resume=inside).cwd == team.cwd
    deeper = Session("s3", None, cwd=str(tmp_path / "shop" / ".claude" / "worktrees" / "shop-api" / "src"),
                     title="shop-api", last_active=datetime.now())
    assert teams.launch(team, team.members[1], resume=deeper).cwd == there  # on in its worktree
    assert not teams.in_team_folder(team, str(tmp_path / "shopping"))
    assert teams.member_sessions(team, [session], live={})["api"] is session
    again = teams.launch(team, team.members[1], resume=session)
    assert again.mode is Mode.RESUME and again.cwd == there and "--worktree" not in again.argv  # made once


def test_only_a_repositorys_top_folder_takes_worktrees(tmp_path):
    top = repository(tmp_path / "repo")
    (top / "src").mkdir()
    assert teams.repository_root(str(top))
    assert not teams.repository_root(str(top / "src")) and not teams.repository_root(str(tmp_path))


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, None

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda team: setattr(self, "result", team))


async def test_ctrl_o_gives_the_members_worktrees_in_a_repository_only(tmp_path):
    plain, top = tmp_path / "plain", repository(tmp_path / "repo")
    plain.mkdir()
    dialog = NewTeamDialog(str(plain), set())
    host = Host(dialog)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-name", Input).value = "shop"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        await pilot.press("ctrl+o")
        await pilot.pause(0.1)
        assert "[x] own git worktrees" in str(dialog.query_one("#team-where", Static).render())
        assert "for worktrees, the top folder of a git repository" in str(dialog.query_one("#team-preview",
                                                                                             Static).render())
        assert dialog.query_one(".hint").region.bottom <= 30
        dialog.query_one("#team-folder", Input).value = str(top)
        await pilot.pause(0.1)
        assert team_preview(dialog).startswith("Opens 4 tabs")
        # An earlier team "shop" left its worktrees: a fresh member would start in the old one's.
        (top / ".claude" / "worktrees" / "shop-api").mkdir(parents=True)
        dialog.query_one("#team-name", Input).value = "shop "  # a change, to look again
        await pilot.pause(0.1)
        assert "an earlier team shop left its worktrees" in team_preview(dialog)
        dialog.query_one("#team-name", Input).value = "shop2"
        await pilot.pause(0.1)
        assert team_preview(dialog).startswith("Opens 4 tabs")
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
    assert host.result.worktrees and host.result.name == "shop2"
