"""A team of peers: no lead, each member on a topic of its own, you talking to each, and the members
telling each other what touches the other's work. Made by leaving the lead line out."""

from __future__ import annotations

from conftest import role_of, team_preview
from test_team_tree import peer, send, transcript
from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import sessions, teams, teamtree
from tabmancer.app import SessionPicker
from tabmancer.teamview import NewTeamDialog, TeamScreen


def pair(folder) -> teams.Team:
    return teams.new_team("docs", str(folder), "Ship the docs site",
                          teams.parse_members("api: the API reference @sonnet\nweb: the site's pages", lead_ok=True),
                          peers=True)


def test_peers_have_no_lead_and_each_starts_on_its_own_part(tmp_path):
    team = pair(tmp_path)
    assert team.lead is None and team.peers and [m.name for m in team.ordered] == ["api", "web"]
    api, web = team.members
    argv = teams.launch(team, api).argv
    role = role_of(argv)
    assert "one of the peers" in role and "Nobody leads" in role and "docs-web (the site's pages)" in role
    assert "orchestrator" not in role and "--allowedTools=SendMessage,ListAgents" in argv
    assert argv[-1].startswith("Goal: Ship the docs site Your part: the API reference.")  # it starts by itself
    assert teams.from_tabmancer(team, argv[-1])  # ...and that prompt is not taken for yours
    assert [launch.title for launch in teams.start(team, {})] == ["docs: api", "docs: web"]
    team.background = True  # never for peers: you talk to each of them
    assert not any(teams.in_background(team, launch) for launch in teams.start(team, {}))
    hello = teams.joining(team, [web])[0].argv[-1]
    assert hello.startswith("You just joined the team docs. Introduce yourself to the others (docs-api)")
    assert "leave it out" in teams.left_note(team, "old") and "tell the others" in teams.goal_note(team)
    teams.save({"docs": team})
    again = teams.load()["docs"]
    assert again.lead is None and [(m.name, m.model) for m in again.members] == [("api", "sonnet"), ("web", "")]


def test_a_lead_line_still_makes_a_lead():
    team = teams.new_team("shop", "C:/shop", "goal", teams.parse_members("lead\napi: x", lead_ok=True))
    assert team.lead is not None and team.lead.name == "lead" and not team.peers
    assert teams.new_team("shop", "C:/shop", "goal", teams.parse_members("api: x")).lead.name == "lead"  # as before


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog, self.result = dialog, None

    def on_mount(self) -> None:
        self.push_screen(self.dialog, lambda team: setattr(self, "result", team))


async def test_leaving_the_lead_line_out_makes_peers(tmp_path):
    dialog = NewTeamDialog(str(tmp_path), set())
    host = Host(dialog)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        for _ in range(len(teams.TEMPLATES)):  # past the line-ups with a lead: two topics
            await pilot.press("ctrl+t")
        assert "● two topics" in str(dialog.query_one("#team-lineup", Static).render())
        assert "lead" not in dialog.query_one("#team-members", TextArea).text and dialog.peers()
        dialog.query_one("#team-members", TextArea).text = "api: the API reference\nweb: the site's pages"
        dialog.query_one("#team-name", Input).value = "docs"
        dialog.query_one("#team-goal", TextArea).text = "Ship the docs site"
        await pilot.pause(0.1)
        preview = team_preview(dialog)
        assert preview.startswith("Opens 2 tabs in") and "Nobody leads" in preview
        assert "each in a tab (peers: you talk to each)" in str(dialog.query_one("#team-where", Static).render())
        await pilot.press("ctrl+b")  # peers run in tabs: the switch says so and stays
        assert not dialog.background
        assert dialog.query_one(".hint").region.bottom <= 30  # the keys still on a 120x30 screen
        dialog.query_one("#team-members", TextArea).text = "api: the API reference"
        await pilot.pause(0.1)
        assert "two members at least" in str(dialog.query_one("#team-preview", Static).render())
        dialog.query_one("#team-members", TextArea).text = "api: the API reference\nweb: the site's pages"
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
    assert host.result.peers and [m.name for m in host.result.members] == ["api", "web"]


def peers_at_work(home, tmp_path, monkeypatch) -> teams.Team:
    """The peers api and web, both open, two messages between them."""
    folder = tmp_path / "docs"
    folder.mkdir()
    team = pair(folder)
    teams.save({"docs": team})
    transcript(home, folder, "api-1", "docs-api", [send("docs-web", "the endpoints are listed", 9),
                                                  peer("docs-web", "linked them", 2)])
    transcript(home, folder, "web-1", "docs-web", [peer("docs-api", "the endpoints are listed", 8),
                                                  send("docs-api", "linked them", 3)])
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "api-1", "kind": "interactive", "status": "idle"},
        {"sessionId": "web-1", "kind": "interactive", "status": "idle"}])
    return team


async def test_peers_stand_side_by_side_and_idle_ones_wait_for_you(fake_home, tmp_path, monkeypatch):
    team = peers_at_work(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 44)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            state = app.team_states.get("docs")
            if state is not None and state.open_count() == 2:
                break
        state = app.team_states["docs"]
        assert not app.resting_members() & {"api-1", "web-1"}  # idle peers wait for you, not for a lead
        lines = [line.plain for line in teamtree.tree(state, 120, {}, {})]
        assert lines[0].startswith("├") and lines[1].startswith("└") and not any("★" in line for line in lines)
        assert "1▶──1◀" in lines[0] and "docs-web" not in lines[0]  # what web sent api, what api sent web
        app.push_screen(TeamScreen(team, "#d97757"))
        await pilot.pause(0.5)
        graph = app.screen.graph
        places, rows = graph.layout_boxes(graph.size.width)
        assert places["api"][1] == places["web"][1] == 1 and rows == [["api", "web"]]  # one row, at the top
        drawn = graph.render().plain
        assert "★" not in drawn and "1▼ 1▲" in drawn
        path = graph.path("api", "web", places)
        assert path[0] == (places["api"][0] + places["api"][2] // 2, 1) and len(path) > 3  # along their wire
