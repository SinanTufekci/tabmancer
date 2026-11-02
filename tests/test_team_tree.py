"""Teams in the dashboard's list: a team's one row in OPEN NOW with its members as chips, unfolded
into a tree under it; a closed team's row in EARLIER; the team's preview with its wires and
messages, and the members' own rows keeping every key."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.text import Text

from tabmancer import app as app_module
from tabmancer import sessions, teams, teamtree
from tabmancer.app import HEADING, TEAM_ROW, SessionPicker
from tabmancer.handoverview import HandoverDialog
from tabmancer.sessions import Exit, Session
from tabmancer.teamview import TeamScreen


def stamp(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def transcript(home, folder, sid, name, records):
    project = home / "projects" / "shop"
    project.mkdir(parents=True, exist_ok=True)
    head = [{"type": "user", "cwd": str(folder), "entrypoint": "cli", "timestamp": stamp(30),
             "message": {"content": f"work as {name}"}},
            {"type": "custom-title", "customTitle": name},
            {"type": "assistant", "cwd": str(folder), "timestamp": stamp(20),
             "message": {"content": [{"type": "text", "text": "On it."}], "stop_reason": "end_turn"}}]
    (project / f"{sid}.jsonl").write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in head + records), encoding="utf-8")


def send(to, text, minutes):
    return {"type": "assistant", "timestamp": stamp(minutes), "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "SendMessage", "input": {"to": to, "message": text, "summary": text}}]}}


def peer(sender, text, minutes):
    return {"type": "user", "isMeta": True, "timestamp": stamp(minutes),
            "origin": {"kind": "peer", "from": "uds:x", "name": sender, "body": text},
            "message": {"content": text}}


def shop(home, tmp_path, monkeypatch, api_open=True):
    """A team "shop" (lead, api, ui) at work: the lead and api open, ui closed; and one other
    open session that is in no team."""
    folder = tmp_path / "shop"
    folder.mkdir()
    made = teams.new_team("shop", str(folder), "Add a checkout", teams.parse_members("api: endpoints; ui: screens"))
    teams.save({"shop": made})
    transcript(home, folder, "lead-1", "shop-lead", [send("shop-api", "make the endpoint", 9),
                                                    peer("shop-api", "endpoint done", 2)])
    transcript(home, folder, "api-1", "shop-api", [peer("shop-lead", "make the endpoint", 8),
                                                  send("shop-lead", "endpoint done", 3)])
    transcript(home, folder, "ui-1", "shop-ui", [{"type": "cost-state", "totalLinesAdded": 1, "totalLinesRemoved": 0}])
    live = [{"sessionId": "lead-1", "kind": "interactive", "status": "idle"},
            {"sessionId": "one", "kind": "interactive", "status": "busy"}]
    if api_open:
        live.append({"sessionId": "api-1", "kind": "interactive", "status": "busy"})
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    return made


async def ready(pilot, app, unfold=False):
    """Until the team "shop" is in the list, open (a team may show closed until the live poll lands),
    and with unfold, its members under its row."""
    for _ in range(100):
        await pilot.pause(0.05)
        state = app.team_states.get("shop")
        if state is not None and state.open_count() and f"{TEAM_ROW}shop" in app.table.rows and app.team_open("shop"):
            if unfold and "shop" not in app.unfolded:
                app.toggle_folded("shop")
            return
    raise AssertionError("the team never showed up")


def plain(renderable) -> str:
    console = Console(width=160, file=io.StringIO(), record=True)
    console.print(renderable)
    return console.export_text()


async def test_a_team_is_one_row_of_chips_till_unfolded(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        keys = [row.key.value for row in app.table.ordered_rows]
        assert keys[keys.index(f"{TEAM_ROW}shop") + 1] == "one"  # its members are folded into its row
        assert not {"lead-1", "api-1", "ui-1"} & set(keys)
        heading = app.table.get_row(f"{TEAM_ROW}shop")
        assert heading[0].plain == "▸" and heading[1].plain == "TEAM shop"
        chips = heading[2].plain  # the lead waits for you, api works (its star turns), ui is closed
        assert chips.startswith("● ★lead  ") and chips.split("  ")[1].endswith(" api") and chips.endswith("  ui")
        assert "✶ 1" in heading[3].plain
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("e")
        assert app.table.get_row(f"{TEAM_ROW}shop")[0].plain == "▾" and "lead-1" in app.table.rows


async def test_a_team_heads_its_members_in_open_now(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        keys = [row.key.value for row in app.table.ordered_rows]
        team = keys.index(f"{TEAM_ROW}shop")
        # the lead first, then the members, the closed one too; then the open session in no team
        assert keys[team + 1:team + 4] == ["lead-1", "api-1", "ui-1"] and keys[team + 4] == "one"
        cell = {sid: app.table.get_row(sid) for sid in ("lead-1", "api-1", "ui-1")}
        assert [cell[sid][1].plain for sid in cell] == ["★ lead", "├ api", "└ ui"]
        assert cell["lead-1"][2].plain == "← api: endpoint done"  # the latest word, in or out
        assert cell["api-1"][2].plain == "→ lead: endpoint done"
        assert cell["ui-1"][2].plain == "← you: work as shop-ui"  # what you told it counts too
        heading = app.table.get_row(f"{TEAM_ROW}shop")
        assert heading[1].plain == "TEAM shop" and "2 messages" in heading[2].plain
        assert "● 1" in heading[3].plain and "✶ 1" in heading[3].plain  # the lead waits, api works
        assert app.table.get_row(f"{HEADING}open")[1].plain.strip().endswith("· 3")  # open ones only


async def test_an_idle_member_and_one_at_the_limit_have_signs_of_their_own(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        lead, api = app.sessions["lead-1"], app.sessions["api-1"]
        lead.resets = datetime.now() + timedelta(hours=2)  # the lead, idle, stopped at a usage limit
        api.live_state = "resting"  # api done for now: idle, waiting for its lead
        status = app.team_cells(f"{TEAM_ROW}shop")[3]
        assert status.plain == "◐ 1  ● 1"  # they shared ◐ once: an idle member looked stopped at a limit
        assert [str(span.style) for span in status.spans] == [app_module.AMBER, app_module.MUTED]
        assert app.member_look(lead)[0] == "◐ at the limit" and app.member_look(api)[0] == "● idle"


async def test_the_team_preview_shows_every_key_each_whole(fake_home, tmp_path, monkeypatch):
    """Beside the tree, the keys wrapped as words ('s start the' / 'rest'); taking the lines they
    need, the last of them was cut off under the messages."""
    shop(fake_home, tmp_path, monkeypatch)
    # A busy team: its messages fill all the room they are given.
    monkeypatch.setattr(teamtree, "latest", lambda state, colours, room, width: Text(
        "\n".join(f"message {n}" for n in range(room))))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))  # its preview, not a session's

        def shown() -> str:
            visible = app.inspector.content_region.bottom - app.meta.region.y  # the preview cuts the rest off
            lines = app.meta.render_lines(app.meta.region.reset_offset)[:visible]
            return "\n".join("".join(segment.text for segment in line) for line in lines)

        for _ in range(100):
            await pilot.pause(0.05)
            if "MESSAGES" in shown() and "message 0" in shown():
                break
        text = shown()
        assert "start the rest" in text and "T  teams" in text
        assert not any(line.strip().startswith("rest") for line in text.split("\n"))


async def test_e_folds_a_team_and_its_row_opens_the_team_view(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    # A second team, listed first, so that the shop's row is not the first one.
    blog = teams.new_team("blog", str(tmp_path / "shop"), "Write posts", [])
    teams.save({"blog": blog, **teams.load()})
    transcript(fake_home, tmp_path / "shop", "blog-1", "blog-lead", [])
    live = sessions._agents_json()
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        *live, {"sessionId": "blog-1", "kind": "interactive", "status": "idle"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        assert app.table.get_row_index(f"{TEAM_ROW}blog") < app.table.get_row_index(f"{TEAM_ROW}shop")
        app.selected.clear()  # nothing ticked: Enter is about the row
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("e")
        assert "lead-1" in app.table.rows and "api-1" in app.table.rows
        assert app.cursor_key() == f"{TEAM_ROW}shop"  # the cursor stays on the team
        await pilot.press("e")
        assert "lead-1" not in app.table.rows and app.cursor_key() == f"{TEAM_ROW}shop"
        await pilot.press("enter")  # folded or not, Enter opens the team view
        await pilot.pause(0.1)
        assert isinstance(app.screen, TeamScreen) and app.screen.team.name == "shop"


async def test_the_team_preview_shows_the_wires_and_the_messages(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.pause(0.1)
        tree = plain(app.detail.content)
        assert "├──1▶──1◀── api" in tree and "└──0▶──0◀── ui" in tree and "Goal  Add a checkout" in tree
        assert "MESSAGES" in plain(app.meta.content) and "endpoint done" in plain(app.meta.content)
        # a member's own row keeps its own preview, with its messages and its team
        app.table.move_cursor(row=app.table.get_row_index("api-1"))
        await pilot.pause(0.1)
        assert "2 in · 1 out" in plain(app.detail.content)  # yours, and the lead's
        assert "shop · api" in plain(app.meta.content)


async def test_a_members_row_keeps_the_keys_of_a_session(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        app.table.move_cursor(row=app.table.get_row_index("lead-1"))
        assert app.current().session_id == "lead-1"
        await pilot.press("h")
        await pilot.pause(0.1)
        assert isinstance(app.screen, HandoverDialog) and app.screen.session.session_id == "lead-1"


async def test_a_member_in_the_background_looks_open_like_the_lead_not_closed(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "lead-1", "kind": "interactive", "status": "idle"},
        {"sessionId": "api-1", "kind": "background", "status": "busy"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        marks = {key: app.table.get_row(key)[0].plain for key in ("lead-1", "api-1", "ui-1")}
        assert marks == {"lead-1": "", "api-1": "", "ui-1": "[ ]"}  # only the closed one has a box
        app.table.move_cursor(row=app.table.get_row_index("api-1"))
        await pilot.press("space")  # still ticked to attach to it
        assert app.table.get_row("api-1")[0].plain == "[x]"


async def test_a_team_with_nobody_open_is_one_row_that_s_starts(fake_home, tmp_path, monkeypatch):
    from tabmancer import app as app_module

    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "one", "kind": "interactive", "status": "busy"}])  # in no team: OPEN NOW, then EARLIER
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if f"{TEAM_ROW}shop" in app.table.rows:
                break
        keys = [row.key.value for row in app.table.ordered_rows]
        assert keys.index(f"{HEADING}rest") < keys.index(f"{TEAM_ROW}shop")  # in EARLIER, where it last worked
        assert not {"lead-1", "api-1", "ui-1"} & set(keys)
        row = app.table.get_row(f"{TEAM_ROW}shop")
        assert row[0].plain == "[ ]" and row[3].plain == "▲ 2 to continue"  # the lead and api were cut off
        app.selected.clear()
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("space")  # ticks the whole team
        assert {"lead-1", "api-1", "ui-1"} <= app.selected
        assert app.table.get_row(f"{TEAM_ROW}shop")[0].plain == "[x]"
        app.selected.clear()
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("enter")  # its view, closed as it is: nothing opens
        await pilot.pause(0.1)
        assert isinstance(app.screen, TeamScreen) and not opened
        await pilot.press("escape")
        await pilot.pause(0.1)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("s")  # starts it: every member as itself, the lead last
        await pilot.pause(0.1)
    assert [launch.session_id for launch in opened] == ["api-1", "ui-1", "lead-1"]
    assert all(launch.options[:2] == ("--name", f"shop-{name}") for launch, name in zip(opened, ("api", "ui", "lead")))


async def test_a_new_message_flies_and_history_does_not(fake_home, tmp_path, monkeypatch):
    made = shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        assert not app.team_flights  # the first look only takes note
        state = app.team_states["shop"]
        news = teams.Message(datetime.now(), "lead", "ui", "draw the form")
        app.show_teams({"shop": teamtree.TeamState(made, state.sessions, [*state.said, news])})
        assert [m.text for m, _ in app.team_flights["shop"]] == ["draw the form"]


def test_a_message_flies_out_along_the_receivers_wire_and_back_along_the_senders():
    made = teams.new_team("shop", "C:/shop", "goal", teams.parse_members("api: a; ui: b"))
    state = teamtree.TeamState(made, {}, [])
    looks = {"lead": ("● waiting", ""), "api": ("✶ working", ""), "ui": ("✶ working", "")}
    colours = {"lead": "#ffffff", "api": "#00ff00", "ui": "#0000ff"}

    def drawn(message, progress):
        start = 100.0 - progress * teamtree.FLIGHT_SECONDS
        return [line.plain for line in teamtree.tree(state, 100, looks, colours, [(message, start)], 100.0)]

    down = teams.Message(datetime.now(), "lead", "ui", "x")
    up = teams.Message(datetime.now(), "api", "lead", "x")
    across = teams.Message(datetime.now(), "api", "ui", "x")
    assert drawn(down, 0.05)[2].startswith("└●") and drawn(down, 0.95)[2].startswith("└──0▶──0◀─●")
    assert drawn(up, 0.05)[1].startswith("├──0▶──0◀─●") and drawn(up, 0.95)[1].startswith("├●")
    assert "●" in drawn(across, 0.25)[1] and "●" not in drawn(across, 0.25)[2]  # back along api's wire
    assert "●" in drawn(across, 0.75)[2] and "●" not in drawn(across, 0.75)[1]  # then out along ui's
    assert not any("●" in line.split(" ")[0] for line in drawn(down, 1.2)[1:])  # landed: no wire carries it


def test_closed_members_are_dim_and_unstarted_ones_say_so():
    made = teams.new_team("shop", "C:/shop", "goal", teams.parse_members("api: a; ui: b"))
    closed = Session(session_id="api-1", path=None, cwd="C:/shop", exit=Exit.CLEAN)
    state = teamtree.TeamState(made, {"api": closed, "ui": None}, [])
    lines = [line.plain for line in teamtree.tree(state, 100, {"api": ("✓ done", "")}, {}, (), time.monotonic())]
    assert "not started" in lines[2] and "✓ done" in lines[1]


# --- the team view (T) ---------------------------------------------------------------------------


async def test_t_lists_the_teams_anywhere_picking_the_one_at_hand(fake_home, tmp_path, monkeypatch):
    from textual.widgets import OptionList

    from tabmancer.teamview import TeamsDialog

    shop(fake_home, tmp_path, monkeypatch)
    blog = teams.new_team("blog", str(tmp_path / "shop"), "Write posts", [])
    teams.save({"blog": blog, **teams.load()})  # listed first: the shop is not picked by chance
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app, unfold=True)
        for row, picked in ((f"{TEAM_ROW}shop", "shop"), ("ui-1", "shop"), ("one", "blog")):
            app.table.move_cursor(row=app.table.get_row_index(row))
            await pilot.press("T")
            await pilot.pause(0.1)
            assert isinstance(app.screen, TeamsDialog)
            options = app.screen.query_one(OptionList)
            assert options.get_option_at_index(options.highlighted).id == picked
            await pilot.press("escape")
            await pilot.pause(0.05)
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("T", "enter")  # and from the list, the team's view
        await pilot.pause(0.1)
        assert isinstance(app.screen, TeamScreen) and app.screen.team.name == "shop"


async def test_the_team_view_shows_traffic_tokens_and_whole_focus(fake_home, tmp_path, monkeypatch):
    made = shop(fake_home, tmp_path, monkeypatch)
    made.members[1].focus = "the endpoints, the webhooks and the data model behind the checkout"
    teams.save({"shop": made})
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 50)) as pilot:
        await ready(pilot, app)
        app.session_tokens["api-1"] = type("Use", (), {"total": 341_000})()
        app.push_screen(TeamScreen(made, "#d97757"))
        await pilot.pause(0.5)
        graph = app.screen.graph
        places, _ = graph.layout_boxes(graph.size.width)
        assert places["api"][2] > 26  # the cards grow with the room
        await pilot.press("right")  # api picked
        drawn = graph.render().plain
        assert "1▼ 1▲" in drawn  # sent down to api and back up, on its wire
        assert "341k" in drawn  # api's tokens, on its card
        assert "api  the endpoints, the webhooks and the data model behind the checkout" in drawn  # its focus


async def test_left_and_right_light_one_members_lane_then_everyones(fake_home, tmp_path, monkeypatch):
    made = shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        app.push_screen(TeamScreen(made, "#d97757"))
        await pilot.pause(0.5)
        screen = app.screen

        def log():
            return plain(screen.query_one("#team-log").content)

        assert plain(screen.query_one("#team-lanes").content).split() == ["you", "lead", "api", "ui"]
        assert "make the endpoint" in log() and "endpoint done" in log() and "work as shop-ui" in log()
        await pilot.press("right", "right")  # api, then ui
        assert screen.graph.selected_member().name == "ui"
        assert "CONVERSATION · ui" in plain(screen.query_one("#team-log-head").content)
        assert "make the endpoint" in log()  # the others' messages stay, gone quiet
        await pilot.press("right")  # past the last member: everyone again, no box picked
        assert screen.graph.selected_member() is None
        assert "CONVERSATION · everyone" in plain(screen.query_one("#team-log-head").content)
        await pilot.press("enter")  # nobody to open
        await pilot.pause(0.1)
        assert isinstance(app.screen, TeamScreen)


def test_the_conversation_is_drawn_as_arrows_between_lanes():
    made = teams.new_team("shop", "C:/shop", "goal", teams.parse_members("api: a; ui: b"))
    now = datetime.now()
    said = [teams.Message(now - timedelta(days=1), "you", "lead", "start"),
            teams.Message(now, "lead", "api", "make it"),
            teams.Message(now, "ui", "lead", "is it done?", delivered=False)]
    head, rows = teamtree.sequence(made, said, {"lead": "#ff0000"}, 100)
    names = head.plain.split()
    assert names == ["you", "lead", "api", "ui"]
    lane = {name: head.plain.index(name) + len(name) // 2 for name in names}
    assert [rows[0].plain.strip()[:2], rows[2].plain.strip()[:2]] == ["──", "──"]  # yesterday, then today
    assert rows[1].plain.index("●") == lane["you"] and rows[1].plain.index("▶") == lane["lead"]
    assert rows[3].plain.index("●") == lane["lead"] and rows[3].plain.index("▶") == lane["api"]
    on_its_way = rows[4].plain  # back from ui to the lead, dashed from a hollow dot, not read yet
    assert on_its_way.index("◀") == lane["lead"] and on_its_way.index("○") == lane["ui"] and "╌" in on_its_way
    assert "on its way" in on_its_way

    def style_at(row, words):
        at = row.plain.index(words)
        return [str(span.style) for span in row.spans if span.start <= at < span.end][-1]

    _, picked = teamtree.sequence(made, said, {"lead": "#ff0000"}, 100, picked="api")
    assert style_at(picked[3], "make it") != style_at(picked[1], "start")  # api's message lit, yours quiet
    assert "#ff0000" in style_at(picked[3], "●") and "#ff0000" not in style_at(picked[1], "●")


def test_tabmancers_own_prompts_are_not_taken_for_yours(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    made = teams.new_team("shop", str(folder), "Add a checkout", teams.parse_members("api: endpoints"))
    lead_log, api_log = tmp_path / "lead.jsonl", tmp_path / "api.jsonl"
    joined = teams.joining(made, [made.members[1]])[0].prompt
    started = teams.launch(made, made.lead).prompt

    def prompt(text, minutes):
        return {"type": "user", "timestamp": stamp(minutes), "message": {"content": text}}

    for path, records in ((lead_log, [prompt(started, 9), prompt("also add tests", 5)]), (api_log, [prompt(joined, 8)])):
        path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    found = {"lead": Session(session_id="l", path=lead_log, cwd=made.cwd),
             "api": Session(session_id="a", path=api_log, cwd=made.cwd)}
    said = teams.conversation(made, found, {})
    assert [(m.sender, m.receiver) for m in said] == [("tabmancer", "lead"), ("tabmancer", "api"), ("you", "lead")]


async def test_a_member_done_for_now_is_idle_and_one_at_a_permission_prompt_needs_you(fake_home, tmp_path,
                                                                                       monkeypatch):
    from dataclasses import replace

    from tabmancer import viewmodel as VM

    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        told = []
        monkeypatch.setattr(app, "notify", lambda message, *args, **kwargs: told.append(message))
        celebrated = []
        monkeypatch.setattr(app.mascot, "celebrate", lambda project="": celebrated.append(project))
        api = app.sessions["api-1"]
        app.apply_live_updates({"api-1": replace(api, live_state="busy")})
        app.apply_live_updates({"api-1": replace(api, live_state="idle")})  # a member done: no "for you"
        assert app.sessions["api-1"].live_state == "resting" and not told and not celebrated
        assert VM.state_of(app.sessions["api-1"])[:2] == ("idle", "done")
        app.apply_live_updates({"api-1": replace(api, live_state="busy", status=sessions.Status.BACKGROUND)})
        app.apply_live_updates({"api-1": replace(api, live_state="permission", status=sessions.Status.BACKGROUND)})
        assert told and "needs your OK" in told[-1] and "Enter on its row attaches" in told[-1]
        lead = app.sessions["lead-1"]
        app.apply_live_updates({"lead-1": replace(lead, live_state="busy")})
        app.apply_live_updates({"lead-1": replace(lead, live_state="idle")})  # the lead: it waits for you
        assert app.sessions["lead-1"].live_state == "idle" and "waiting for you" in told[-1]
