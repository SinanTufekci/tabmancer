"""A whole team handed over (h in its view): its lead writes the handover and the line-up for the rest,
you see both, and the team starts again as a new generation of itself: a fresh lead, fresh members."""

from __future__ import annotations

from datetime import datetime, timedelta

from test_team_tree import shop

from tabmancer import app as app_module
from tabmancer import handover, teams
from tabmancer.app import SessionPicker
from tabmancer.launch import Mode
from tabmancer.sessions import Session
from tabmancer.teamview import TeamHandoverDialog, TeamScreen

ANSWER = ("Here it is.\n<handover>\n## Goal\nA checkout.\n## Next steps\nWire the payment page.\n</handover>\n"
          "<lineup>\nTeam size: 2\n- api: the payment endpoint [src/api] @sonnet @medium\n"
          "lead: plans @opus\ntests: end to end tests [tests] @haiku @low\n</lineup>")


async def until(pilot, check, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


def lead_session(**changes) -> Session:
    return Session(session_id="lead-1", path=None, cwd="C:/shop", title="shop-lead",
                   last_active=datetime.now() - timedelta(minutes=5), **changes)


# --- what the lead is asked, and how its answer is read --------------------------------------------

def test_the_lead_is_asked_for_a_handover_and_a_line_up():
    team = teams.new_team("shop", "C:/shop", "A checkout", teams.parse_members("api: endpoints @sonnet @high; ui: screens"))
    asked = handover.team_request(team)
    assert "- api: endpoints @sonnet @high" in asked and "- ui: screens" in asked  # named as in a line-up
    assert handover.START in asked and handover.LINEUP_START in asked and "No lead line" in asked
    from tabmancer import router

    assert router.GUIDE in asked  # members picked the router's way: the cheapest that will do


def test_the_answer_is_a_handover_and_a_line_up_without_a_lead():
    written, lineup = handover.read_team(ANSWER)
    assert written == "## Goal\nA checkout.\n## Next steps\nWire the payment page."
    assert lineup == "api: the payment endpoint [src/api] @sonnet @medium\ntests: end to end tests [tests] @haiku @low"
    assert handover.read_team("<handover>\nonly this\n</handover>") == ("only this", "")
    # A lead knows its members by their session names, and a real one wrote the line-up so (shop-ui).
    team = teams.new_team("shop", "C:/shop", "A checkout", teams.parse_members("api: x; ui: y"))
    named = "<handover>h</handover><lineup>\nshop-ui: the success page @sonnet @medium\nshop-e2e: tests\n</lineup>"
    assert handover.read_team(named, team)[1] == "ui: the success page @sonnet @medium\ne2e: tests"


def test_the_next_generation_keeps_the_name_and_lead_and_takes_the_line_up(monkeypatch):
    monkeypatch.delenv("TABMANCER_CONTEXT_CAP", raising=False)
    old = teams.new_team("shop", "C:/shop", "A checkout", teams.parse_members("lead: leads @opus @xhigh; api: x; ui: y", lead_ok=True))
    old.created = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    fresh = handover.next_team(old, handover.read_team(ANSWER)[1])
    assert fresh.name == "shop" and fresh.goal == old.goal and fresh.lead.pick.model == "opus"
    assert [m.name for m in fresh.members] == ["lead", "api", "tests"] and fresh.cap == "300k"
    # Made now: the old generation's sessions are its members no more.
    old_lead = Session(session_id="was", path=None, cwd="C:/shop", title="shop-lead",
                       started=datetime.now() - timedelta(hours=2))
    assert teams.since_made(old, old_lead) and not teams.since_made(fresh, old_lead)


def test_the_new_generation_opens_its_members_then_the_lead_with_the_handover():
    fresh = handover.next_team(teams.new_team("shop", "C:/shop", "A checkout", []),
                               "api: endpoint @sonnet @medium\ntests: tests @haiku @low")
    launches = handover.team_launches(fresh, lead_session(), "## Goal\nA checkout.")
    assert [launch.title for launch in launches] == ["shop: api", "shop: tests", "shop: lead"]
    assert all(launch.mode is Mode.NEW for launch in launches)  # fresh sessions, not resumes
    prompt = launches[-1].argv[-1]
    assert prompt.startswith("You are the new lead of the team shop") and prompt.endswith("## Goal\nA checkout.")
    assert launches[0].argv[launches[0].argv.index("--model") + 1] == "sonnet"


# --- in the team's view ------------------------------------------------------------------------------

def stubs(monkeypatch, answer=ANSWER):
    done = {"closed": [], "written": 0, "opened": []}
    monkeypatch.setattr(app_module, "close_tabs", lambda names: done["closed"].extend(names) or ("ok", "information"))
    monkeypatch.setattr(handover, "close", lambda session, open_ones, again="": done["closed"].append(session.title))

    def write(session, request=handover.REQUEST):
        done["written"] += 1
        assert request.startswith("We are handing this team's work over")
        return answer

    monkeypatch.setattr(handover, "write_itself", write)
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: done["opened"].extend(launches) or [])
    return done


async def team_view(pilot, app):
    await until(pilot, lambda: app.team_states.get("shop") is not None and app.team_states["shop"].open_count())
    app.push_screen(TeamScreen(teams.load()["shop"], "#d97757"))
    await until(pilot, lambda: isinstance(app.screen, TeamScreen) and app.screen.sessions.get("lead") is not None)
    return app.screen


async def test_h_twice_hands_the_whole_team_over(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)  # the lead open and waiting; api and ui closed
    done = stubs(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await team_view(pilot, app)
        await pilot.press("h")
        assert done["written"] == 0  # once: only what a second h does
        await pilot.press("h")
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        dialog = app.screen
        assert done["closed"] == ["shop-lead"] and done["written"] == 1
        assert "the lead's suggestion" in str(dialog.query_one("#handover-lineup-card").border_subtitle)
        preview = str(dialog.query_one("#handover-preview").render())
        assert "3 tabs" in preview and "api" in preview and "haiku/low" in preview
        await pilot.press("ctrl+s")
        await until(pilot, lambda: len(done["opened"]) == 3)
    team = teams.load()["shop"]
    assert [m.name for m in team.members] == ["lead", "api", "tests"]
    assert done["opened"][-1].title == "shop: lead" and "Wire the payment page." in done["opened"][-1].argv[-1]


async def test_a_handover_not_taken_is_shown_again_without_writing_again(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    done = stubs(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        view = await team_view(pilot, app)
        await pilot.press("h", "h")
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        await pilot.press("escape")
        await until(pilot, lambda: app.screen is view)
        view.sessions = {name: None if s is None else s.__class__(**{**s.__dict__, "exit": s.exit.CLEAN})
                         for name, s in view.sessions.items()}  # closed now, as the handover left them
        await pilot.press("h")
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        assert done["written"] == 1 and not done["opened"]


async def test_a_member_at_work_keeps_the_team_from_being_handed_over(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)  # api open and busy
    done = stubs(monkeypatch)
    app = SessionPicker(days=14)
    notes = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    async with app.run_test(size=(150, 44)) as pilot:
        view = await team_view(pilot, app)
        monkeypatch.setattr(view, "notify", lambda message, **kw: notes.append(message))
        await pilot.press("h", "h")
        await pilot.pause(0.2)
    assert any("api is working" in note for note in notes) and done["written"] == 0 and not done["closed"]


async def test_h_on_a_team_of_peers_says_to_hand_members_over_one_by_one(fake_home, tmp_path, monkeypatch):
    from tabmancer.teamview import team_handover_problem

    peers = teams.new_team("two", str(tmp_path), "two topics", teams.parse_members("a: one; b: two"), peers=True)
    assert "H hands the picked member over" in team_handover_problem(peers, {})


async def test_h_twice_on_the_teams_row_in_the_list_hands_it_over_too(fake_home, tmp_path, monkeypatch):
    from test_team_tree import ready

    from tabmancer.app import TEAM_ROW

    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    done = stubs(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.1)
        await pilot.press("h", "h")
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        assert done["written"] == 1


async def test_the_new_generations_cap_is_picked_in_the_dialog(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    done = stubs(monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await team_view(pilot, app)
        await pilot.press("h", "h")
        await until(pilot, lambda: isinstance(app.screen, TeamHandoverDialog))
        dialog = app.screen
        choice = dialog.query_one("#handover-cap")
        assert choice.value == "auto"  # the team's own (none, in the tests): there to see and change
        assert "no cap" in str(dialog.query_one("#handover-preview").render())
        choice.pick("400k")
        await pilot.pause(0.05)
        assert "400k context cap" in str(dialog.query_one("#handover-preview").render())
        assert "more of the work kept word for word" in str(dialog.query_one("#handover-cap-words").render())
        await pilot.press("ctrl+s")
        await until(pilot, lambda: len(done["opened"]) == 3)
    assert teams.load()["shop"].cap == "400k"
    assert all(launch.argv[launch.argv.index("--autocompact") + 1] == "400k" for launch in done["opened"])


async def test_the_new_team_form_picks_the_members_cap(tmp_path):
    from test_lead_decides import Host
    from textual.widgets import Input, TextArea

    from tabmancer.teamview import NewTeamDialog

    dialog = NewTeamDialog(str(tmp_path), set())
    host = Host(dialog)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        dialog.query_one("#team-name", Input).value = "shop"
        dialog.query_one("#team-goal", TextArea).text = "Add a checkout"
        cap = dialog.query_one("#team-cap")
        cap.focus()
        await pilot.press("right")  # auto (the tests' default) -> 200k, round the row
        assert cap.value == "200k" and "context cap every member starts under" in str(
            dialog.query_one("#team-help").render())
        assert dialog.query_one(".hint").region.bottom <= 30  # the keys line still on a 120x30 screen
        await pilot.press("ctrl+s")
        await pilot.pause(0.1)
    assert host.result.cap == "200k"
