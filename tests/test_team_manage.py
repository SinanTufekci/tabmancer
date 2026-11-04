"""Managing a team from the dashboard: a member opened from the list comes back as that member,
names written in Turkish keep their letters, and a subagent's report is not a team message."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from conftest import box_has_focus
from test_team_tree import peer, ready, shop, stamp

from tabmancer import app as app_module
from tabmancer import focus, sessions, teams, teamtree
from tabmancer.app import TEAM_ROW, SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Session


def test_names_keep_their_letters_without_the_marks():
    assert teams.clean("Test takımı ve örnek") == "test-takimi-ve-ornek"
    assert teams.clean("Test takımı ve örnek proje") == "test-takimi-ve-ornek-pro"  # 24 at most
    assert teams.clean("ŞEHİR çiğ Ünlü") == "sehir-cig-unlu"
    assert teams.clean("  api / UI  ") == "api-ui"


def test_a_members_session_resumes_as_that_member(tmp_path, monkeypatch):
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    made = teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: endpoints"))
    saved = {"shop": made}
    member = Session(session_id="api-1", path=None, cwd=str(tmp_path), title="shop-api")
    plain = Launch(title="x", cwd=str(tmp_path), mode=Mode.RESUME, session_id="api-1")
    launch = teams.as_member(member, plain, saved)
    assert launch.title == "shop: api" and launch.session_id == "api-1" and launch.mode is Mode.RESUME
    assert launch.argv[:3] == ["claude", "--resume", "api-1"]
    assert launch.options[:2] == ("--name", "shop-api") and "--settings" in launch.options
    assert "--append-system-prompt-file" in launch.options
    # a fork is a session of its own; so is a session that only shares the name, in another folder
    assert teams.as_member(member, Launch("x", str(tmp_path), Mode.FORK, "api-1"), saved).options == ()
    stranger = Session(session_id="s", path=None, cwd=str(tmp_path.parent / f"{tmp_path.name}-elsewhere"),
                       title="shop-api")  # beside the team's folder, its name beginning with that one's
    assert teams.as_member(stranger, plain, saved).options == ()


async def test_enter_opens_ticked_members_as_members_with_the_lead_last(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [])  # all closed
    opened: list[Launch] = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if {"lead-1", "api-1", "ui-1"} <= app.sessions.keys():
                break
        app.selected = {"lead-1", "api-1", "ui-1", "one"}  # as after a restart: all were open
        await pilot.press("enter")
        await pilot.pause(0.1)
    by_id = {launch.session_id: launch for launch in opened}
    assert [launch.session_id for launch in opened][-1] == "lead-1"  # its members are up when it starts
    for sid, name in (("lead-1", "shop-lead"), ("api-1", "shop-api"), ("ui-1", "shop-ui")):
        assert by_id[sid].options[:2] == ("--name", name) and "--settings" in by_id[sid].options
    assert by_id["one"].options == ()  # in no team: as it always was


def test_a_subagents_report_is_not_a_message_of_the_team(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    made = teams.new_team("shop", str(folder), "Add a checkout", teams.parse_members("api: endpoints"))
    log = tmp_path / "api.jsonl"
    handback = {"type": "user", "isMeta": True, "timestamp": stamp(4),
                "origin": {"kind": "peer", "from": "a1b2", "senderTaskId": "a1b2", "name": "general-purpose",
                           "body": "[Subagent hand-back] The text below is the final report"},
                "message": {"content": "report"}}
    records = [peer("shop-lead", "make the endpoint", 8), handback]
    log.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    said = teams.conversation(made, {"api": Session(session_id="a", path=log, cwd=str(folder))}, {})
    assert [(m.sender, m.receiver) for m in said] == [("lead", "api")]


def test_a_summary_does_not_repeat_the_name_shown_beside_it():
    to_cad = teams.Message(datetime.now(), "lead", "cad", "the whole text", summary="cad: start once memory is free")
    assert teamtree.gist(to_cad, "cad") == "start once memory is free"
    assert teamtree.word(to_cad, "lead", {}).plain == "→ cad: start once memory is free"
    assert teamtree.gist(to_cad, "api") == "cad: start once memory is free"  # another name: left as it is



def test_chips_cut_long_names_before_they_count_the_quietest():
    members = [("lead", True, "●", "", "", 1), ("common-gcode-common-geom", False, "✶", "", "", 2),
               ("perf", False, "✶", "", "", 2), ("docs", False, "●", "", "", 1)]
    assert teamtree.chips(members, 80, "").plain == "● ★lead  ✶ common-gcode-common-geom  ✶ perf  ● docs"
    cut = teamtree.chips(members, 40, "").plain
    assert len(cut) <= 40 and cut.startswith("● ★lead  ✶ common") and "…  ✶ perf  ● docs" in cut
    # no room for all: the lead and whoever waits for you keep their names, in the team's order
    assert teamtree.chips(members, 20, "").plain == "● ★lead  ● docs  +2"


async def test_keys_on_an_open_teams_row(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    opened: list[Launch] = []
    closed: list[str] = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(focus, "close_tab", lambda title: closed.append(title) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        app.selected.clear()
        row = f"{TEAM_ROW}shop"
        app.table.move_cursor(row=app.table.get_row_index(row))
        await pilot.press("s")  # every member not open: ui, resumed as itself
        assert [(launch.session_id, launch.options[:2]) for launch in opened] == [("ui-1", ("--name", "shop-ui"))]
        assert app.cursor_key() == row
        await pilot.press("x")  # once: it asks
        assert not closed and app.closing_team == "shop"
        assert app.cursor_key() == row and app.team_open("shop")
        await pilot.press("x")
        assert app.closing_team is None  # the second press went to the team: its tabs are closing
        for _ in range(100):
            await pilot.pause(0.05)
            if len(closed) == 2:
                break
        assert sorted(closed) == ["shop-api", "shop-lead"]
        opened.clear()
        await pilot.press("a")  # a member for a team at work: it opens and says hello to the lead
        await box_has_focus(pilot, app)
        await pilot.press(*"docs: the readme", "enter")
        await pilot.pause(0.1)
        assert [m.name for m in teams.load()["shop"].members] == ["lead", "api", "ui", "docs"]
        assert [launch.title for launch in opened] == ["shop: docs"] and "Introduce yourself" in opened[0].prompt


async def test_j_unfolds_the_team_of_the_member_waiting_for_you(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        assert "lead-1" not in app.table.rows  # folded
        await pilot.press("j")  # the lead waits for you
        assert app.cursor_key() == "lead-1" and "shop" in app.unfolded


async def test_a_member_added_to_a_closed_team_waits_to_start_with_it(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [])
    opened: list[Launch] = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if f"{TEAM_ROW}shop" in app.table.rows:
                break
        app.table.move_cursor(row=app.table.get_row_index(f"{TEAM_ROW}shop"))
        await pilot.press("a")
        await box_has_focus(pilot, app)
        await pilot.press(*"docs: the readme", "enter")
        await pilot.pause(0.1)
        assert not opened and [m.name for m in teams.load()["shop"].members][-1] == "docs"


def test_team_start_brings_the_closed_members_back_and_leaves_the_open_ones(fake_home, tmp_path, monkeypatch, capsys):
    # What a lead runs when a member it needs has closed: `team add` takes only members new to the team.
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    shop(fake_home, tmp_path, monkeypatch, api_open=False)  # the lead open; api and ui closed
    assert cli.start_team("shop", "ui") == 0
    said = capsys.readouterr().out
    assert [(launch.title, launch.mode, launch.session_id) for launch in opened] == [("shop: ui", Mode.RESUME, "ui-1")]
    assert "--name" in opened[0].argv and "shop-ui (where it left off)" in said and "SendMessage" in said
    opened.clear()
    assert cli.start_team("shop", "") == 0  # every one that is not open: the lead is, and ui is opening
    assert [launch.title for launch in opened] == ["shop: api"]
    said = capsys.readouterr().out
    assert "Open already: shop-lead" in said and "Opening a moment ago, so not opened again: shop-ui" in said
    opened.clear()
    assert cli.start_team("shop", "shop-lead") == 0 and not opened  # by its session name too: open, left be
    assert "Open already: shop-lead" in capsys.readouterr().out
    assert cli.start_team("shop", "docs") == 1 and not opened
    assert "No member docs on the team shop. Its members: lead, api, ui." in capsys.readouterr().out
    assert cli.start_team("nosuch") == 1 and "no team called nosuch" in capsys.readouterr().out


def test_a_lead_may_start_its_closed_members_without_asking_and_is_told_how(tmp_path):
    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x; ui: y"))
    lead, api, _ = team.members
    tools = next(a for a in teams.launch(team, lead).argv if a.startswith("--allowedTools="))
    assert tools == "--allowedTools=SendMessage,ListAgents,Bash(tabmancer team start:*)"
    assert "tabmancer team start shop <member>" in teams.role(team, lead)
    assert next(a for a in teams.launch(team, api).argv if a.startswith("--allowedTools=")).endswith("ListAgents")
    assert "tabmancer team start" not in teams.role(team, api)  # a member asks its lead


def _member_transcript(home, folder, sid, name, minutes_ago, told_only=False):
    """A member's transcript: started a while ago, with a prompt typed into it, or (told_only) only
    the lead's message coming in, as a member that starts with no message of its own has."""
    project = home / "projects" / "shop"
    project.mkdir(parents=True, exist_ok=True)
    first = (peer("shop-lead", "build the cart", minutes_ago) if told_only else
             {"type": "user", "cwd": str(folder), "entrypoint": "cli", "timestamp": stamp(minutes_ago),
              "message": {"content": f"work as {name}"}})
    first = {**first, "cwd": str(folder), "entrypoint": "cli"}
    records = [first, {"type": "custom-title", "customTitle": name},
               {"type": "assistant", "cwd": str(folder), "timestamp": stamp(minutes_ago - 1),
                "message": {"content": [{"type": "text", "text": "On it."}], "stop_reason": "end_turn"}},
               {"type": "cost-state", "totalLinesAdded": 1, "totalLinesRemoved": 0}]
    (project / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_a_member_told_only_by_its_lead_is_listed(fake_home, tmp_path):
    # A member starts with no message of its own and waits for the lead's: never typed into, it was
    # taken for nothing to resume and missing from the list, so its team took it for never started.
    folder = tmp_path / "shop"
    folder.mkdir()
    _member_transcript(fake_home, folder, "ui-1", "shop-ui", 30, told_only=True)
    found = {s.session_id: s for s in sessions.find_sessions(14, include_all=True, quick=True)}
    assert "ui-1" in found and found["ui-1"].title == "shop-ui" and found["ui-1"].first_prompt == "build the cart"


def test_a_new_team_is_a_fresh_one_not_the_old_team_of_its_name(fake_home, tmp_path, monkeypatch, capsys):
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    folder = tmp_path / "shop"
    folder.mkdir()
    # Last week's team "shop" left its sessions; today's "shop", made an hour ago, has its own ui.
    _member_transcript(fake_home, folder, "old-ui", "shop-ui", 60 * 24 * 7)
    _member_transcript(fake_home, folder, "old-api", "shop-api", 60 * 24 * 7)
    _member_transcript(fake_home, folder, "new-ui", "shop-ui", 30, told_only=True)
    team = teams.new_team("shop", str(folder), "Add a checkout", teams.parse_members("api: endpoints; ui: screens"))
    team.created = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    teams.save({"shop": team})
    listed = sessions.find_sessions(14, include_all=True, quick=True)
    found = teams.member_sessions(team, listed, live={"shop-api": {"sessionId": "old-api"}})  # the old api, open still
    assert found["ui"].session_id == "new-ui" and found["api"] is None and found["lead"] is None
    old = next(s for s in listed if s.session_id == "old-ui")
    assert teams.member_of(old, {"shop": team}) is None  # resumed from the list, it is a session of its own
    assert cli.start_team("shop", "") == 0
    assert [(launch.title, launch.mode, launch.session_id) for launch in opened] == [
        ("shop: api", Mode.NEW, ""), ("shop: ui", Mode.RESUME, "new-ui"), ("shop: lead", Mode.NEW, "")]
    assert "old-" not in capsys.readouterr().out


def test_team_start_leaves_a_member_open_under_its_name_and_says_when_nothing_opened(fake_home, tmp_path,
                                                                                     monkeypatch, capsys):
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    shop(fake_home, tmp_path, monkeypatch, api_open=False)
    # api is up in its tab but has no transcript yet (it waits for the lead's first message): its pid
    # file names it. Opening it again would make two sessions answer to shop-api.
    monkeypatch.setattr(teams, "live_names", lambda: {"shop-api": {"sessionId": "not-written-yet", "name": "shop-api"}})
    assert cli.start_team("shop", "api") == 0 and not opened
    assert "Open already: shop-api" in capsys.readouterr().out
    # A long session name is matched whole, not cut to a name's length first.
    long = teams.new_team("checkout-redesign", str(tmp_path / "shop"), "Goal", teams.parse_members("frontend: x"))
    teams.save({**teams.load(), long.name: long})
    assert cli.start_team("checkout-redesign", "checkout-redesign-frontend") == 0
    assert [launch.title for launch in opened] == ["checkout-redesign: frontend"]
    # No terminal that opens tabs: nothing started, said so, and not 0 for a lead to wait on.
    monkeypatch.setattr(cli, "open_tabs", lambda launches: ["claude --resume ui-1"])
    assert cli.start_team("shop", "ui") == 1
    assert "none started" in capsys.readouterr().out and "shop-ui" not in teams.opening()  # not taken for opening


def test_a_team_made_in_another_time_zone_keeps_its_members(tmp_path):
    from datetime import timezone

    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x"))
    assert datetime.fromisoformat(team.created).tzinfo is not None  # kept with its offset
    team.created = datetime(2026, 11, 4, 14, 0, tzinfo=timezone(timedelta(hours=3))).isoformat()
    # Five minutes later, read on a machine now two hours west: the same moment, in another zone.
    later = datetime(2026, 11, 4, 11, 5, tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=1)))
    member = Session("s", None, cwd=str(tmp_path), title="shop-api", started=later)
    assert teams.since_made(team, member)
    early = Session("t", None, cwd=str(tmp_path), title="shop-api", started=later - timedelta(minutes=30))
    assert not teams.since_made(team, early)
