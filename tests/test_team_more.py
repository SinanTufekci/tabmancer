"""The team view closes the whole team with x twice and says what each member has used; the
new-team dialog keeps line-ups of your own."""

from __future__ import annotations

from conftest import box_has_focus
from textual.app import App
from textual.widgets import Input, Static, TextArea

from tabmancer import focus, teams
from tabmancer.sessions import Exit, Session
from tabmancer.teamview import NewTeamDialog, TeamScreen
from tabmancer.tokens import Tokens


def a_team(tmp_path) -> teams.Team:
    return teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: x; ui: y"))


def open_session(sid: str, state: str = "idle") -> Session:
    return Session(session_id=sid, path=None, exit=Exit.RUNNING, live_state=state)


class Viewer(App):
    def __init__(self, screen) -> None:
        super().__init__()
        self.first = screen
        self.session_tokens: dict[str, Tokens] = {}

    def on_mount(self) -> None:
        self.push_screen(self.first)


async def test_x_twice_closes_every_open_member(fake_home, tmp_path, monkeypatch):
    closed = []
    monkeypatch.setattr(focus, "close_tab", lambda title: closed.append(title) or True)
    team = a_team(tmp_path)
    screen = TeamScreen(team, "#d97757")
    async with Viewer(screen).run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.2)
        screen.sessions = {"lead": open_session("L"), "api": open_session("A", "busy"), "ui": None}
        screen.action_close_team()  # once: it asks first
        assert not closed and screen.closing
        screen.action_close_team()
        for _ in range(40):
            await pilot.pause(0.05)
            if len(closed) == 2:
                break
        assert closed == ["shop-lead", "shop-api"]  # the closed one (ui) is left alone


async def test_the_head_says_what_each_member_has_used(fake_home, tmp_path):
    team = a_team(tmp_path)
    screen = TeamScreen(team, "#d97757")
    viewer = Viewer(screen)
    async with viewer.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.2)
        viewer.session_tokens = {"L": Tokens(cache_read=3_000_000), "A": Tokens(output=500_000)}
        screen.sessions = {"lead": open_session("L"), "api": open_session("A"), "ui": None}
        screen.show_head()
        head = str(screen.query_one("#team-head", Static).render())
        assert "Tokens 3.5M  ·  lead 3M · api 500k" in head


class Host(App):
    def __init__(self, dialog: NewTeamDialog) -> None:
        super().__init__()
        self.dialog = dialog

    def on_mount(self) -> None:
        self.push_screen(self.dialog)


async def test_a_line_up_of_your_own_is_kept_and_can_be_forgotten(fake_home, tmp_path):
    dialog = NewTeamDialog(str(tmp_path), set())
    async with Host(dialog).run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        members = dialog.query_one("#team-members", TextArea)
        members.text = "lead @opus\nback: the server @sonnet\nfront: the screens"
        await pilot.pause(0.1)
        await pilot.press("ctrl+l")
        await box_has_focus(pilot, dialog.app)
        await pilot.press(*"my crew", "enter")
        await pilot.pause(0.1)
        assert teams.saved_lineups() == {"my crew": "back: the server @sonnet\nfront: the screens"}
        assert "● my crew" in str(dialog.query_one("#team-lineup", Static).render())

    again = NewTeamDialog(str(tmp_path), set())  # another time: it is among the line-ups
    async with Host(again).run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert "my crew" in again.lineups
        for _ in range(len(again.lineups)):
            if again.lineups[again.template] == "my crew":
                break
            await pilot.press("ctrl+t")
        assert again.query_one("#team-members", TextArea).text == \
            "lead: " + teams.LEAD_FOCUS + "\nback: the server @sonnet\nfront: the screens"
        await pilot.press("ctrl+l")  # on a kept one, as it is: once asks...
        assert teams.saved_lineups()
        await pilot.press("ctrl+l")  # ...twice forgets
        assert teams.saved_lineups() == {} and "my crew" not in again.lineups
        again.query_one("#team-name", Input).value = "x"  # the dialog still works on
        await pilot.pause(0.1)


async def test_d_twice_takes_a_member_off_and_the_lead_hears_of_it(fake_home, tmp_path, monkeypatch):
    from tabmancer import teamview

    copied = []
    monkeypatch.setattr(teamview, "copy_text", lambda app, text: copied.append(text))
    team = a_team(tmp_path)
    teams.save({"shop": team})
    screen = TeamScreen(team, "#d97757")
    async with Viewer(screen).run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.2)
        screen.sessions = {"lead": open_session("L"), "api": open_session("A", "busy"), "ui": None}
        await pilot.press("d")  # the lead is picked first: it stays
        assert screen.removing is None and len(teams.load()["shop"].members) == 3
        await pilot.press("right", "d")  # api: once, it asks
        assert screen.removing == "api" and len(teams.load()["shop"].members) == 3
        screen.sessions = {"lead": open_session("L"), "api": open_session("A", "busy"), "ui": None}
        await pilot.press("d")
        assert [m.name for m in teams.load()["shop"].members] == ["lead", "ui"]
        assert [m.name for m in screen.graph.order] == ["lead", "ui"]
        assert copied and "shop-api is no longer on the team" in copied[0] and "shop-ui" in copied[0]
        assert teams.member_of(Session(session_id="A", path=None, cwd=team.cwd, title="shop-api")) is None


async def test_g_changes_the_goal(fake_home, tmp_path, monkeypatch):
    from tabmancer import teamview

    copied = []
    monkeypatch.setattr(teamview, "copy_text", lambda app, text: copied.append(text))
    team = a_team(tmp_path)
    teams.save({"shop": team})
    screen = TeamScreen(team, "#d97757")
    async with Viewer(screen).run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.2)
        screen.sessions = {"lead": None, "api": None, "ui": None}  # nobody open: the role says it
        await pilot.press("g")
        await box_has_focus(pilot, pilot.app)
        box = pilot.app.screen.query_one(Input)
        assert box.value == "Add a checkout"  # the goal as it is, to change
        box.value = ""
        await pilot.press(*"Add PayPal too", "enter")
        await pilot.pause(0.1)
        assert teams.load()["shop"].goal == "Add PayPal too" and screen.team.goal == "Add PayPal too"
        assert "Team goal: Add PayPal too." in teams.role(teams.load()["shop"], team.members[1])
        assert not copied  # the lead is closed: nothing to paste
