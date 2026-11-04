"""Teams: members, their roles and tabs, who is who, and what they said to each other."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from conftest import box_has_focus, role_of

from tabmancer import app as app_module
from tabmancer import teams
from tabmancer.launch import Mode
from tabmancer.sessions import Exit, Session


def team(tmp_path, *members):
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    return teams.new_team("Shop!", str(folder), "Add a checkout", list(members) or teams.parse_members(
        "api: endpoints; ui: screens"))


def test_members_come_from_a_line_and_the_orchestrator_comes_first(tmp_path):
    assert [(m.name, m.focus) for m in teams.parse_members("API: the endpoints; Ui Team:screens\ntests; api: dup")] == \
        [("api", "the endpoints"), ("ui-team", "screens"), ("tests", "")]
    made = team(tmp_path)
    assert made.name == "shop" and [m.name for m in made.members] == ["lead", "api", "ui"]
    assert made.lead.name == "lead" and made.session_name(made.members[1]) == "shop-api"
    assert made.member_of("shop-ui") == "ui" and made.member_of("other-session") == "other-session"
    for line in teams.TEMPLATES.values():
        assert len(teams.parse_members(line)) >= 2


def test_each_member_starts_with_its_name_role_and_the_inbound_setting(tmp_path):
    made = team(tmp_path)
    lead, api = made.members[0], made.members[1]
    argv = teams.launch(made, api).argv
    assert argv[:3] == ["claude", "--name", "shop-api"]
    settings = argv[argv.index("--settings") + 1]
    with open(settings, encoding="utf-8") as f:
        assert json.load(f) == {"crossSessionInbound": "accept"}
    assert "--allowedTools=SendMessage,ListAgents" in argv  # one piece, or it swallows the prompt
    prompt = role_of(argv)
    assert "shop-lead" in prompt and "shop-ui" in prompt and "endpoints" in prompt and "\n" not in prompt
    assert argv[-1].endswith("shop-api.md")  # members wait for their first task: no prompt of their own
    lead_argv = teams.launch(made, lead).argv
    assert lead_argv[-1].startswith("Goal: Add a checkout") and "orchestrator" in teams.role(made, lead)


def test_start_opens_the_closed_and_the_missing_with_the_orchestrator_last(tmp_path):
    made = team(tmp_path)
    closed = Session(session_id="api-1", path=tmp_path / "api.jsonl", cwd=made.cwd, exit=Exit.CLEAN)
    running = Session(session_id="ui-1", path=None, cwd=made.cwd, exit=Exit.RUNNING)
    launches = teams.start(made, {"api": closed, "ui": running})
    assert [launch.title for launch in launches] == ["shop: api", "shop: lead"]
    assert launches[0].mode is Mode.RESUME and launches[0].argv[:4] == ["claude", "--resume", "api-1", "--name"]
    assert launches[1].mode is Mode.NEW
    assert [launch.mode for launch in teams.start(made, {"api": closed}, fresh=True)] == [Mode.NEW] * 3


def test_members_are_found_by_name_open_or_closed(tmp_path):
    made = team(tmp_path)
    now = datetime.now()
    old = Session(session_id="a0", path=None, cwd=made.cwd, title="shop-api", last_active=now - timedelta(days=1))
    new = Session(session_id="a1", path=None, cwd=made.cwd, title="shop-api", last_active=now)
    elsewhere = Session(session_id="x", path=None, cwd="C:/other", title="shop-ui", last_active=now)
    open_lead = Session(session_id="l1", path=None, cwd=made.cwd, title="something else")
    found = teams.member_sessions(made, [old, new, elsewhere, open_lead], {"shop-lead": {"sessionId": "l1"}})
    assert found["api"] is new and found["lead"] is open_lead and found["ui"] is None


def stamp(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def write(path, records):
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")


def send(to, text, minutes):
    return {"type": "assistant", "timestamp": stamp(minutes), "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "SendMessage", "input": {"to": to, "message": text,
                                                                           "summary": f"about {text}"}}]}}


def peer(sender, text, minutes):
    return {"type": "user", "isMeta": True, "timestamp": stamp(minutes),
            "origin": {"kind": "peer", "from": "uds:x", "name": sender, "body": text},
            "message": {"content": f"Another Claude session sent a message: {text}"}}


def test_the_conversation_is_read_back_from_the_transcripts(tmp_path):
    made = team(tmp_path)
    lead_log, api_log = tmp_path / "lead.jsonl", tmp_path / "api.jsonl"
    write(lead_log, [
        {"type": "user", "timestamp": stamp(10), "message": {"content": "build the checkout"}},
        send("shop-api", "make the endpoint", 9),
        send("uds:\\\\.\\pipe\\LOCAL\\cc-msg-ui", "draw the form", 8),  # addressed by pipe
        peer("shop-api", "endpoint done", 2)])
    write(api_log, [peer("shop-lead", "make the endpoint", 8), send("shop-lead", "endpoint done", 3)])
    sessions = {"lead": Session(session_id="l", path=lead_log, cwd=made.cwd),
                "api": Session(session_id="a", path=api_log, cwd=made.cwd), "ui": None}
    live = {"shop-ui": {"sessionId": "u", "messagingSocketPath": "\\\\.\\pipe\\LOCAL\\cc-msg-ui"}}
    said = teams.conversation(made, sessions, live)
    shown = [(m.sender, m.receiver, m.text, m.delivered) for m in said]
    assert shown == [("you", "lead", "build the checkout", True),
                     ("lead", "api", "make the endpoint", True),       # read by api: once, as read
                     ("lead", "ui", "draw the form", False),           # ui has not read it yet
                     ("api", "lead", "endpoint done", True)]
    assert said[1].summary == "about make the endpoint"
    with lead_log.open("a", encoding="utf-8") as f:  # more is read incrementally
        f.write(json.dumps(send("shop-api", "now add tests", 1), separators=(",", ":")) + "\n")
    assert teams.conversation(made, sessions, live)[-1].text == "now add tests"


def mid_turn(sender, text, minutes, msg_id):
    """A message that came while the session was busy: taken in mid-turn as an attachment."""
    return {"type": "attachment", "timestamp": stamp(minutes), "attachment": {
        "type": "queued_command", "prompt": f"<cross-session-message>{text}</cross-session-message>",
        "origin": {"kind": "peer", "from": "uds:x", "msg_id": msg_id, "name": sender, "body": text}}}


def test_a_message_taken_in_mid_turn_counts_as_read(tmp_path):
    made = team(tmp_path)
    lead_log, api_log = tmp_path / "lead.jsonl", tmp_path / "api.jsonl"
    write(lead_log, [send("shop-api", "make the endpoint", 9), send("shop-api", "and its tests", 5)])
    received = peer("shop-lead", "and its tests", 4)
    received["origin"]["msg_id"] = "m2"
    write(api_log, [mid_turn("shop-lead", "make the endpoint", 8, "m1"),
                    mid_turn("shop-lead", "and its tests", 4, "m2"), received])  # the same one twice
    sessions = {"lead": Session(session_id="l", path=lead_log, cwd=made.cwd),
                "api": Session(session_id="a", path=api_log, cwd=made.cwd), "ui": None}
    said = teams.conversation(made, sessions, {})
    assert [(m.sender, m.receiver, m.text, m.delivered) for m in said] == [
        ("lead", "api", "make the endpoint", True), ("lead", "api", "and its tests", True)]


def test_a_message_flies_along_the_wires(tmp_path):
    from tabmancer.teamview import TeamGraph

    made = team(tmp_path)
    graph = TeamGraph(made, "#d97757")
    places, rows = graph.layout_boxes(100)
    assert rows == [["api", "ui"]] and places["lead"][1] == 0 and places["api"][1] == places["ui"][1]
    down = graph.path("lead", "api", places)
    up = graph.path("api", "lead", places)
    across = graph.path("api", "ui", places)
    assert down == up[::-1] and down[0][1] < down[-1][1]
    assert across[0][0] < across[-1][0] and len({y for _, y in across}) <= 3
    assert graph.path("you", "api", places) == []


async def test_the_dashboard_creates_a_team_and_shows_it(fake_home, tmp_path, monkeypatch):
    from tabmancer.app import SessionPicker
    from tabmancer.newsessions import NewSessions
    from tabmancer.teamview import NewTeamDialog, TeamScreen

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    folder = tmp_path / "shop"
    folder.mkdir()
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.press("T")  # no teams yet: straight to a new one
        assert isinstance(app.screen, NewTeamDialog)
        dialog = app.screen
        await box_has_focus(pilot, app)
        assert dialog.focused.id == "team-name"
        await pilot.press("s", "h")  # letters go into the box, not to the dashboard's s and h
        assert dialog.query_one("#team-name").value == "sh" and app.screen is dialog
        await pilot.press("enter")  # Enter goes on to the next box: nothing is created yet
        assert dialog.focused.id == "team-folder" and app.screen is dialog
        await pilot.press("tab", "tab")  # so does Tab
        assert dialog.focused.id == "team-members"
        await pilot.press("shift+tab")
        assert dialog.focused.id == "team-goal"
        dialog.query_one("#team-name").value = "shop"
        dialog.query_one("#team-folder").value = str(folder)
        dialog.query_one("#team-goal").text = "Add a checkout"
        await pilot.press("ctrl+s")
        for _ in range(60):  # a loaded machine takes a while
            await pilot.pause(0.05)
            if isinstance(app.screen, TeamScreen):
                break
        assert isinstance(app.screen, TeamScreen)
        assert [launch.title for launch in opened] == ["shop: api", "shop: ui", "shop: tests", "shop: lead"]
        assert "shop" in teams.load()
        await pilot.press("n")  # the dashboard's keys stay behind the team view
        assert not isinstance(app.screen, NewSessions)
        await pilot.press("right", "right")
        assert app.screen.graph.selected_member().name == "ui"
        opened.clear()
        await pilot.press("enter")  # not open: open it
        assert [launch.title for launch in opened] == ["shop: ui"]
        await pilot.press("escape")
        assert not isinstance(app.screen, TeamScreen)


def test_a_long_goal_of_several_lines_goes_in_the_role_file_and_the_tabs_still_open(tmp_path):
    import subprocess

    from tabmancer import launch as L

    plan = "## Status\n- master pushed\n\n\n\n## Build\n```\ngit checkout -- i18n/ ; gradlew --stop\n```\n" + (
        "A line of the plan, long enough to count. " * 90)
    lineup = "lead @opus @xhigh\n" + "\n".join(f"m{i}: part {i}; and more of it [src/m{i}] @sonnet" for i in range(5))
    team = teams.new_team("big", str(tmp_path), plan, teams.parse_members(lineup, lead_ok=True))
    assert [m.name for m in team.members] == ["lead", "m0", "m1", "m2", "m3", "m4"]
    assert team.members[1].focus == "part 0; and more of it" and team.members[1].owns == "src/m0"
    assert "\n\n\n" not in team.goal and "```\ngit checkout" in team.goal  # its lines kept
    launches = teams.start(team, {})
    role = role_of(launches[0].argv)
    assert "Team goal:\n\n## Status" in role and team.goal in role  # the whole goal, as written
    first = launches[-1].argv[-1]  # the lead's first message names the goal on one line, cut short
    assert first.startswith("Goal: ## Status - master pushed") and "the whole goal is in your role" in first
    assert len(first) < 500 and teams.from_tabmancer(team, first)
    for command in L.windows_terminal_commands(launches, "0"):
        assert len(subprocess.list2cmdline(command)) <= L.WT_MOST


def test_a_semicolon_inside_a_members_part_stays_in_it():
    members = teams.parse_members("art: drafts first; icons, splash and the store pictures [assets/] @sonnet @high\n"
                                  "ui: the screens; worktree _wt/ui; docs: the guide; tests @haiku; review")
    assert [(m.name, m.focus, m.owns, m.model) for m in members] == [
        ("art", "drafts first; icons, splash and the store pictures", "assets/", "sonnet"),
        ("ui", "the screens; worktree _wt/ui", "", ""), ("docs", "the guide", "", ""), ("tests", "", "", "haiku"),
        ("review", "", "", "")]
    assert teams.suggested_lines("- örnek: bir iş\nHere you go:") == "ornek: bir iş"  # a name in any letters
