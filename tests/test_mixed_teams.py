"""Mixed-CLI teams: Claude Code and Antigravity (agy) working together in the same team."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tabmancer import agy, cli, handover, sessions, summarize, teambus, teams, teamview, tokens
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session

# Every bus tool but team_request, which asks the user each time
ALLOWED = ("--allowedTools=Bash(tabmancer team send shop:*),Bash(tabmancer team listen shop api:*),"
           "Bash(tabmancer team who shop:*),mcp__tabmancer__team_send,mcp__tabmancer__team_inbox,"
           "mcp__tabmancer__team_wait,mcp__tabmancer__team_who,mcp__tabmancer__team_status")

WRITE_ITSELF = handover.write_itself  # the real one: conftest keeps tests off claude/agy -p


def _agy_index(home: Path, rows: list[tuple]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / agy.INDEX)
    con.execute(
        "CREATE TABLE IF NOT EXISTS conversation_summaries ("
        "conversation_id text, title text, preview text, step_count integer, "
        "last_modified_time datetime, workspace_uris text, status text, "
        "nesting_depth integer, not_fully_idle numeric, killed numeric)"
    )
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _write_agy_transcript(home: Path, sid: str, records: list[dict]) -> Path:
    path = home / "brain" / sid / ".system_generated" / "logs" / "transcript.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    return path


def _mixed_team(tmp_path: Path, lineup: str = "lead: orchestrate @opus @xhigh\napi: endpoints [src/api] @sonnet\nui: screens [src/ui] @agy @high", **kwargs) -> teams.Team:
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    members = teams.parse_members(lineup, lead_ok=True)
    return teams.new_team("shop", str(folder), "Build Stripe checkout", members, **kwargs)


def test_parse_members_supports_cli_tags_in_any_order_and_roundtrips(tmp_path):
    text = (
        "lead: plans @agy @xhigh\n"
        "api: endpoints [src/api] @sonnet @medium\n"
        "ui: screens [src/ui] @high @agy @gemini-4-neon\n"
        "review: checks code @gemini-4-carbon\n"
        "docs: guide @claude @haiku"
    )
    members = teams.parse_members(text, lead_ok=True)
    assert [(m.name, m.cli, m.model, m.effort, m.owns, m.lead) for m in members] == [
        ("lead", "agy", "", "xhigh", "", True),
        ("api", "claude", "sonnet", "medium", "src/api", False),
        ("ui", "agy", "gemini-4-neon", "high", "src/ui", False),
        ("review", "agy", "gemini-4-carbon", "", "", False),
        ("docs", "claude", "haiku", "", "", False),
    ]
    assert members[0].line() == "lead: plans @agy @xhigh"
    assert members[1].line() == "api: endpoints [src/api] @sonnet @medium"  # default claude omitted
    assert members[2].line() == "ui: screens [src/ui] @agy @gemini-4-neon @high"

    team = teams.new_team("mix", str(tmp_path), "Ship feature", members)
    assert team.uses_bus is True
    assert team.clis == {"claude", "agy"}
    teams.save({"mix": team})
    reloaded = teams.load()["mix"]
    assert [(m.name, m.cli, m.model, m.effort) for m in reloaded.members] == [
        (m.name, m.cli, m.model, m.effort) for m in team.members
    ]


def test_mixed_team_launches_claude_and_agy_with_bus_and_auto_mode(tmp_path):
    team = _mixed_team(tmp_path)
    lead, api, ui = team.members
    assert (lead.cli, api.cli, ui.cli) == ("claude", "claude", "agy")

    # Claude worker gets bus allowedTools, bus env, and startup listen prompt
    api_launch = teams.launch(team, api)
    assert api_launch.cli == "claude"
    assert ALLOWED in api_launch.argv
    assert ("TABMANCER_TEAM", "shop") in api_launch.env
    assert ("TABMANCER_MEMBER", "api") in api_launch.env
    assert "tabmancer team listen shop api" in (api_launch.prompt or "")
    assert teams.from_tabmancer(team, api_launch.prompt or "")

    # Antigravity worker runs via `team run`, `--mode auto`, `--add-dir <bus>`, `[tabmancer:shop-ui]` tag
    ui_launch = teams.launch(team, ui)
    assert ui_launch.cli == "agy"
    assert "team" in ui_launch.wrapper and "run" in ui_launch.wrapper and "ui" in ui_launch.wrapper
    argv = ui_launch.argv
    agy_idx = argv.index("agy")
    agy_args = argv[agy_idx:]
    assert "--mode" in agy_args and agy_args[agy_args.index("--mode") + 1] == "auto"
    assert "--effort" not in agy_args and agy_args[agy_args.index("--model") + 1] == "gemini-4-neon-high"
    assert "--add-dir" in agy_args and agy_args[agy_args.index("--add-dir") + 1] == str(teambus.team_dir("shop"))
    assert "-i" in agy_args
    prompt = agy_args[agy_args.index("-i") + 1]
    assert prompt.startswith("[tabmancer:shop-ui]")
    # agy's sandbox cannot import tabmancer: it runs the team's stdlib copy of the bus, put in the
    # folder --add-dir opens, with the plain interpreter
    bus = teambus.command("shop")
    assert f"{bus} listen shop ui" in prompt and "tabmancer team listen" not in prompt
    assert (teambus.team_dir("shop") / teambus.CLI_FILE).is_file()
    assert teambus._read_team_file("shop")[1] == ["lead", "api", "ui"]
    assert teams.from_tabmancer(team, prompt)
    # Role file written inside bus directory so sandboxed agy can read it
    bus_role = teambus.roles_dir("shop") / "shop-ui.md"
    assert bus_role.is_file()
    role_text = bus_role.read_text(encoding="utf-8")
    assert f"{bus} send shop" in role_text and "WaitMsBeforeAsync: 500" in role_text
    assert f"{bus} who shop" in role_text and "`tabmancer team send" not in role_text

    # Background mode applies only to Claude members, never agy
    bg_team = _mixed_team(tmp_path, background=True)
    assert teams.in_background(bg_team, teams.launch(bg_team, bg_team.members[1])) is True   # api (claude)
    assert teams.in_background(bg_team, teams.launch(bg_team, bg_team.members[2])) is False  # ui (agy)


def test_agy_member_creates_and_reuses_git_worktree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    team = teams.new_team("shop", str(repo), "Build UI",
                          teams.parse_members("lead @opus\nui: screens @agy", lead_ok=True), worktrees=True)
    ui = team.members[1]
    first = teams.launch(team, ui)
    expected = repo / ".claude" / "worktrees" / "shop-ui"
    assert Path(first.cwd) == expected
    assert (expected / ".git").exists()
    # Second launch reuses the existing worktree cleanly
    second = teams.launch(team, ui)
    assert Path(second.cwd) == expected


def test_teambus_send_listen_wakeup_and_receipts():
    # Immediate return when unread message already queued
    sent = teambus.send("shop", "shop-lead", "shop-ui", "build the checkout form", summary="checkout form")
    assert sent["from"] == "lead" and sent["to"] == "ui"
    all_before = teambus.messages("shop")
    assert len(all_before) == 1 and all_before[0]["delivered"] is False

    got = teambus.listen("shop", "ui", timeout=1.0)
    assert len(got) == 1 and got[0]["text"] == "build the checkout form"
    all_after = teambus.messages("shop")
    assert all_after[0]["delivered"] is True

    # Concurrent wakeup: listener starts before message is sent
    received: list[dict] = []

    def waiter():
        received.extend(teambus.listen("shop", "lead", timeout=5.0, poll=0.05))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.15)
    teambus.send("shop", "ui", "lead", "form done!")
    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert len(received) == 1 and received[0]["from"] == "ui" and received[0]["text"] == "form done!"

    # Timeout returns cleanly with []
    assert teambus.listen("shop", "api", timeout=0.1, poll=0.03) == []


def test_agy_transcript_parsing_tokens_digest_and_team_matching(tmp_path, monkeypatch):
    home = tmp_path / "agy"
    monkeypatch.setattr(agy, "home", lambda: home)
    folder = tmp_path / "shop"
    folder.mkdir()
    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y-%m-%d %H:%M:%S.1234567+00:00")
    iso = now_utc.isoformat().replace("+00:00", "Z")
    uri = json.dumps([folder.as_uri()])
    _agy_index(home, [("conv-ui-1", "Checkout UI", "[tabmancer:shop-ui] Goal: Build checkout", 3, ts, uri,
                       "CASCADE_RUN_STATUS_IDLE", 0, 0, 0)])
    _write_agy_transcript(home, "conv-ui-1", [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE", "created_at": iso,
         "content": "<USER_REQUEST>\n[tabmancer:shop-ui] Read your full team role in x.md.\n</USER_REQUEST>"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": iso,
         "model": "gemini-4-neon-high", "input_tokens": 12000, "cache_read_tokens": 3000, "output_tokens": 500,
         "content": "Working on checkout.tsx",
         "tool_calls": [{"name": "edit_file", "args": {"Path": '"src/ui/checkout.tsx"'}}]},
    ])

    found = agy.find(14)
    assert len(found) == 1
    sess = found[0]
    assert sess.session_id == "conv-ui-1"
    assert sess.title == "shop-ui"
    assert sess.model == "gemini-4-neon-high"
    assert sess.context == 15000
    assert "src/ui/checkout.tsx" in sess.files
    assert sess.last_reply == "Working on checkout.tsx"

    # Digest works for summarize / transcript handover
    dig = summarize.session_digest(sess)
    assert "Working on checkout.tsx" in dig and "src/ui/checkout.tsx" in dig

    # Team matching + live presence + conversation merging
    team = _mixed_team(tmp_path)
    teams.save({"shop": team})
    teambus.write_presence("shop", "ui", cli="agy", session_id="conv-ui-1")
    teambus.update_presence("shop", "ui", listener_pid=teambus.os.getpid(), listening=True)
    matched = teams.member_sessions(team, found, live={})
    assert matched["ui"] is not None
    assert matched["ui"].session_id == "conv-ui-1"
    assert matched["ui"].exit is Exit.RUNNING
    assert matched["ui"].live_state == "resting"

    # Bus messages appear in conversation with delivered status
    teambus.send("shop", "lead", "ui", "build checkout", summary="checkout")
    conv_before = teams.conversation(team, matched, live={})
    bus_msgs = [m for m in conv_before if m.sender == "lead" and m.receiver == "ui"]
    assert len(bus_msgs) == 1 and bus_msgs[0].delivered is False and bus_msgs[0].summary == "checkout"

    teambus.unread("shop", "ui", mark_read=True)
    conv_after = teams.conversation(team, matched, live={})
    bus_msgs2 = [m for m in conv_after if m.sender == "lead" and m.receiver == "ui"]
    assert len(bus_msgs2) == 1 and bus_msgs2[0].delivered is True

    # Token counting includes agy transcript when a team uses agy
    report = tokens.collect(7)
    per = report.per_session(whole_life=True)
    assert "conv-ui-1" in per and per["conv-ui-1"].total == 15500

    # Clean up presence and verify resume via as_member
    teambus.clear_presence("shop", "ui")
    resumed = teams.as_member(sess, Launch("t", str(folder), Mode.RESUME, "conv-ui-1", cli="agy"))
    assert resumed.cli == "agy" and "--mode" in resumed.options and "run" in resumed.wrapper


def test_claude_bus_listener_counts_as_keep_warm_idle():
    assert sessions.keep_warm_seconds("tabmancer team listen shop api") == 3300.0
    assert sessions.keep_warm_seconds("tabmancer team listen shop api --timeout 1800") == 1800.0
    assert sessions.keep_warm_seconds("python -m tabmancer team listen shop api") == 3300.0
    assert sessions.keep_warm_seconds("pytest -q") is None


def test_cli_team_bus_commands_and_add_start(tmp_path, monkeypatch, capsys):
    opened: list[Launch] = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    team = _mixed_team(tmp_path)
    teams.save({"shop": team})

    # send + listen
    assert cli.team_send("shop", ["ui", "implement", "cart", "--from", "lead", "--summary", "cart"]) == 0
    assert "Sent to ui" in capsys.readouterr().out
    assert cli.team_listen("shop", ["ui", "--timeout", "0.2"]) == 0
    out = capsys.readouterr().out
    assert "from lead" in out and "implement cart" in out

    # who
    assert cli.team_who("shop") == 0
    who_out = capsys.readouterr().out
    assert "lead (shop-lead) · claude" in who_out and "ui (shop-ui) · agy" in who_out
    assert "delivery" not in who_out
    teambus.write_presence("shop", "ui", cli="agy", pid=os.getpid())
    teambus.update_presence("shop", "ui", delivery="queued")  # as codexbus notes it for a Codex member
    assert cli.team_who("shop") == 0
    assert "· delivery: queued" in capsys.readouterr().out
    teambus.clear_presence("shop", "ui")

    # add @agy member
    assert cli.add_to_team("shop", "docs: write guide @agy @medium") == 0
    add_out = capsys.readouterr().out
    assert "tabmancer team send" in add_out
    assert any(l.cli == "agy" and l.title == "shop: docs" for l in opened)


def test_handover_supports_agy_lead_and_preserves_mixed_lineup(tmp_path, monkeypatch):
    team = _mixed_team(tmp_path, lineup="lead: orchestrate @agy @xhigh\napi: endpoints @sonnet\nui: screens @agy @high")
    req = handover.team_request(team)
    assert "ui: screens @agy @high" in req and "Keep @agy" in req

    # write_itself invokes `agy --conversation <id> ... -p <request>` for an agy lead
    seen_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        seen_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, "<handover>State summary</handover>\n<lineup>api: endpoints @sonnet\nui: screens @agy @high</lineup>", "")

    monkeypatch.setattr(handover, "write_itself", WRITE_ITSELF)
    monkeypatch.setattr(handover.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(handover.subprocess, "run", fake_run)
    lead_session = Session(session_id="agy-lead-1", path=tmp_path / "t.jsonl", cwd=team.cwd, cli="agy", model="gemini-4-carbon")
    written, lineup = handover.write_team(lead_session, team)
    assert seen_cmd[:3] == ["/bin/agy", "--conversation", "agy-lead-1"] and seen_cmd[-2:] == ["-p", req]
    assert "State summary" in written
    assert "@agy" in lineup

    fresh_team = handover.next_team(team, lineup)
    assert fresh_team.lead is not None and fresh_team.lead.cli == "agy"
    assert {m.name: m.cli for m in fresh_team.members} == {"lead": "agy", "api": "claude", "ui": "agy"}
    launches = handover.team_launches(fresh_team, lead_session, written)
    assert launches[-1].cli == "agy" and launches[-1].title == "shop: lead"
    assert "State summary" in (launches[-1].prompt or "")


def test_teamview_card_model_and_lineup_graph_show_agy_badge(tmp_path):
    team = _mixed_team(tmp_path)
    ui = team.members[2]
    label, fell = teamview.card_model(None, ui.pick, ui.cli)
    assert label.startswith("agy") and "high" in label and not fell

    graph = teamview.LineupGraph(team, "#d97757")
    rendered = graph.render().plain
    assert "agy" in rendered and "ui" in rendered and "api" in rendered


def test_the_bus_command_in_an_agy_role_runs_without_tabmancer(tmp_path):
    """The command an agy member is told to run works with no tabmancer on the path and no bus env:
    the seam between the role text and the copy teams.options() installs."""
    import os
    import subprocess

    team = _mixed_team(tmp_path)
    ui = team.members[2]
    teams.launch(team, ui)
    bus = teams.bus_cmd(team, ui)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", teambus.BUS_ENV, "TABMANCER_MEMBER", "VIRTUAL_ENV")}
    empty = tmp_path / "empty"
    empty.mkdir()
    sent = subprocess.run(f'{bus} send shop api "hello from ui" --from ui', shell=True, cwd=empty, env=env,
                          capture_output=True, text=True, timeout=60, check=False)
    assert sent.returncode == 0, sent.stderr
    assert [m["text"] for m in teambus.unread("shop", "api")] == ["hello from ui"]
    who = subprocess.run(f"{bus} who shop", shell=True, cwd=empty, env=env,
                         capture_output=True, text=True, timeout=60, check=False)
    assert who.returncode == 0 and "api" in who.stdout, who.stderr


def test_claude_members_of_a_bus_team_get_the_bus_as_mcp_tools(tmp_path):
    """--mcp-config names a file per member that starts `tabmancer mcp` for that member; the tools are
    allowed without asking. agy members and Claude-only teams get none (agy: its plugin; Claude-only:
    SendMessage)."""
    team = _mixed_team(tmp_path)
    _lead, api, ui = team.members
    argv = teams.launch(team, api).argv
    [flag] = [a for a in argv if a.startswith("--mcp-config=")]
    path = Path(flag.split("=", 1)[1])
    assert path.parent == teambus.team_dir("shop") / "mcp" and path.name == "shop-api.json"
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["tabmancer"]
    assert entry["type"] == "stdio" and entry["args"][-5:] == ["mcp", "--team", "shop", "--member", "api"]
    assert ALLOWED in argv
    assert argv.index(flag) < argv.index(ALLOWED)
    assert "mcp__tabmancer__team_send" in teams.role(team, api)

    assert not any(a.startswith("--mcp-config") for a in teams.launch(team, ui).argv)
    plain = teams.new_team("solo", str(tmp_path / "shop"), "x", teams.parse_members("lead: a\nb: c", lead_ok=True))
    assert not any(a.startswith("--mcp-config") for a in teams.launch(plain, plain.members[1]).argv)

    # The seam: the command in that file is a working MCP server for that member.
    hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "claude-code", "version": "2"}}})
    ask = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "team_send", "arguments": {"to": "ui", "message": "from the config"}}})
    teams.save({"shop": team})
    done = subprocess.run([entry["command"], *entry["args"]], input=f"{hello}\n{ask}\n", capture_output=True,
                          text=True, encoding="utf-8", timeout=60, check=False)
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2] and replies[1]["result"]["isError"] is False, done.stderr
    assert [(m["from"], m["text"]) for m in teambus.unread("shop", "ui")] == [("api", "from the config")]


@pytest.mark.parametrize(("text", "said"), [
    ("Build checkout", "Build checkout."), ("Build checkout.", "Build checkout."), ("Ship it!", "Ship it!"),
    ("Is it fast?", "Is it fast?"), ("Fix the bug (the one in api.)", "Fix the bug (the one in api.)"),
    ('Say "done."', 'Say "done."'), ("Wait…", "Wait…"), ("Build checkout.  ", "Build checkout."), ("", ""),
    ("Use v1.2", "Use v1.2."), ("Fix it (soon)", "Fix it (soon)."),
])
def test_a_goal_ends_one_sentence_whether_written_with_its_period_or_not(text, said):
    assert teams.sentence(text) == said


@pytest.mark.parametrize("cli", ["claude", "agy"])
def test_a_goal_written_with_its_period_gets_no_second_one_in_the_role(tmp_path, cli):
    folder = tmp_path / "shop"
    folder.mkdir()
    team = teams.new_team("shop", str(folder), "Build checkout.",
                          teams.parse_members(f"lead: plans\nui: screens @{cli}", lead_ok=True))
    role = teams.role(team, team.members[1])
    assert "Team goal: Build checkout. " in role and "checkout.." not in role


def test_a_claude_members_unasked_commands_are_for_its_own_team_only(tmp_path):
    """Bash(tabmancer team:*) let a member of shop send on, listen to, start or add to any team."""
    team = _mixed_team(tmp_path)
    lead, api, _ui = team.members
    assert teams.bash_rules(team, api) == ["tabmancer team send shop", "tabmancer team listen shop api",
                                           "tabmancer team who shop"]  # listens as itself; no start, no add
    assert teams.bash_rules(team, lead)[-1] == "tabmancer team start shop"
    team.decide = 3
    assert teams.bash_rules(team, lead)[-2:] == ["tabmancer team start shop", "tabmancer team add shop"]
    for member in (lead, api):
        tools = next(a for a in teams.launch(team, member).argv if a.startswith("--allowedTools="))
        assert "Bash(tabmancer team:*)" not in tools and "Bash(tabmancer team " in tools
        rules = [r for r in tools.split("=", 1)[1].split(",") if r.startswith("Bash(")]
        assert all(r.startswith(("Bash(tabmancer team send shop:", "Bash(tabmancer team listen shop ",
                                 "Bash(tabmancer team who shop:", "Bash(tabmancer team start shop:",
                                 "Bash(tabmancer team add shop:")) for r in rules), rules
