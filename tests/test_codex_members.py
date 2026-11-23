"""Codex team members (@codex): launched with the bus as MCP tools for their session alone, their
thread found from ~/.codex, and their messages queued into it with `codex queue`. A fake codex
throughout: no test starts the real one (nor its app-server)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tabmancer import cli, codex, codexbus, router, summarize, teambus, teams


def _team(tmp_path: Path, lineup: str = "lead: orchestrate @opus\nrev: reviews diffs @codex") -> teams.Team:
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    team = teams.new_team("shop", str(folder), "Build checkout", teams.parse_members(lineup, lead_ok=True))
    teams.save({"shop": team})
    return team


def _rollout(home: Path, sid: str, cwd: str, first: str) -> Path:
    path = home / "sessions" / "2026" / "11" / "20" / f"rollout-2026-11-20T10-00-00-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records = [
        {"type": "session_meta", "payload": {"id": sid, "cwd": cwd, "timestamp": now, "originator": "codex-tui"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": first}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class Runs:
    """subprocess.run as codex queue would answer it, recording each call."""

    def __init__(self, code: int = 0, stderr: str = "") -> None:
        self.calls: list = []
        self.code, self.stderr = code, stderr

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.CompletedProcess(command, self.code, stdout="", stderr=self.stderr)


@pytest.fixture
def fake_codex(monkeypatch, tmp_path):
    exe = tmp_path / "bin" / "codex.exe"
    monkeypatch.setattr(codexbus.shutil, "which", lambda name: str(exe) if name == "codex" else None)
    return exe


# --- the tag and the launch ----------------------------------------------------------------

def test_codex_tag_parses_round_trips_and_makes_a_bus_team(tmp_path):
    team = _team(tmp_path)
    rev = team.members[1]
    assert rev.cli == "codex"
    assert rev.line() == "rev: reviews diffs @codex"
    assert team.uses_bus is True
    assert "codex" in team.clis
    assert [m.cli for m in teams.load()["shop"].members] == ["claude", "codex"]


def test_a_member_without_a_tag_is_never_codex(tmp_path):
    team = _team(tmp_path, "lead: orchestrate\napi: endpoints\nui: screens @agy")
    assert [m.cli for m in team.members] == ["claude", "claude", "agy"]


def test_the_router_never_picks_for_codex():
    assert "codex" not in router.CLIS
    with pytest.raises(summarize.SummaryError):
        router.suggest("review this diff", [], cli="codex")


def test_codex_member_launches_under_team_run_with_its_own_mcp_server(tmp_path):
    team = _team(tmp_path)
    rev = team.members[1]
    made = teams.launch(team, rev)
    assert made.cli == "codex"
    assert ("TABMANCER_TEAM", "shop") in made.env and ("TABMANCER_MEMBER", "rev") in made.env
    assert "team" in made.wrapper and "run" in made.wrapper and "rev" in made.wrapper
    argv = made.argv
    args = argv[argv.index("codex") + 1:]
    overrides = [args[i + 1] for i, a in enumerate(args) if a == "-c"]
    command = next(o for o in overrides if o.startswith("mcp_servers.tabmancer.command="))
    served = next(o for o in overrides if o.startswith("mcp_servers.tabmancer.args="))
    assert command.split("=", 1)[1].startswith("'") and command.endswith("'")
    assert served.endswith("'mcp', '--team', 'shop', '--member', 'rev']")
    assert "mcp_servers.tabmancer.tool_timeout_sec=3600" in overrides
    assert "-m" not in args  # no model asked: Codex's own
    prompt = args[-1]
    assert prompt.startswith("[tabmancer:shop-rev]")
    assert "team_send" in prompt or "team role in" in prompt
    # its role: no listen command to run, the tools instead
    role = teams.role(team, rev)
    assert "team_send" in role and "team_inbox" in role and "new turn" in role
    assert f"listen {team.name} rev" not in role
    # the bus knows the team (when it began, who is in it) for the deliverer
    assert teambus._read_team_file("shop")[1] == ["lead", "rev"]


def test_codex_overrides_parse_as_toml(tmp_path):
    tomllib = pytest.importorskip("tomllib")  # 3.11+: skipped on 3.10

    team = _team(tmp_path)
    flags = teams.codex_options(team, team.members[1])
    values = [flags[i + 1] for i, f in enumerate(flags) if f == "-c"]
    parsed = tomllib.loads("\n".join(values).replace("mcp_servers.tabmancer.", ""))
    assert parsed["args"][-4:] == ["--team", "shop", "--member", "rev"]
    assert parsed["tool_timeout_sec"] == 3600
    assert Path(parsed["command"]).name.lower().startswith(("python", "tabmancer"))
    assert teams._toml_literal("C:\\a'b") == json.dumps("C:\\a'b")
    assert tomllib.loads(f"x = {teams._toml_literal(chr(92) + 'it' + chr(39) + 's')}")["x"] == "\\it's"


@pytest.mark.parametrize(("model", "flag"), [("gpt-5.5-codex", True), ("opus", False), ("claude-sonnet-5-5", False),
                                             ("gemini-4-argon-high", False), ("", False),
                                             ("auto", False), ("o4-mini", True), ("default", False)])
def test_codex_gets_only_a_model_codex_runs(tmp_path, model, flag):
    team = _team(tmp_path, f"lead: orchestrate\nrev: reviews @codex{' @' + model if model else ''}")
    rev = team.members[1]
    assert rev.cli == "codex"
    flags = teams.codex_options(team, rev)
    assert (["-m", model] == list(flags[-2:])) is flag


def test_resuming_a_codex_member_resumes_its_thread(tmp_path):
    team = _team(tmp_path)
    rev = team.members[1]
    home = tmp_path / "codex-home"
    session = codex.read(_rollout(home, "019a-thread", team.cwd, "[tabmancer:shop-rev] hello"))
    assert session is not None
    made = teams.launch(team, rev, resume=session)
    args = made.argv[made.argv.index("codex") + 1:]
    assert args[0] == "resume" and "019a-thread" in args
    assert args.index("-c") < args.index("019a-thread")  # options before the session
    assert args[-1].startswith("[tabmancer:shop-rev] You are resuming")


# --- its thread -----------------------------------------------------------------------------

def test_codex_member_is_found_by_its_tag_and_bound(tmp_path, monkeypatch):
    team = _team(tmp_path)
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    _rollout(home, "019a-other", team.cwd, "something else")
    _rollout(home, "019a-rev", team.cwd, "[tabmancer:shop-rev] Read your full team role")
    found = teams.member_sessions(team, codex.find(days=2), live={})
    assert found["rev"] is not None and found["rev"].session_id == "019a-rev"
    assert teambus.read_bindings("shop")["rev"]["cli"] == "codex"
    assert codexbus.thread_of("shop", "rev") == "019a-rev"


def test_a_running_codex_member_shows_as_running(tmp_path, monkeypatch):
    team = _team(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    teambus.write_presence("shop", "rev", cli="codex", pid=os.getpid(), state="working")
    assert teambus.read_presence("shop")["rev"]["runner_alive"]
    found = teams.member_sessions(team, [], live={})["rev"]
    assert found is not None and found.cli == "codex" and found.title == "shop-rev"
    assert found.session_id == "codex:shop-rev" and found.live_state == "idle"


def test_no_thread_yet_is_empty(tmp_path, monkeypatch):
    _team(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert codexbus.thread_of("shop", "rev") == ""


# --- delivery -------------------------------------------------------------------------------

def test_a_message_is_queued_into_the_thread_and_marked_read(tmp_path, fake_codex):
    _team(tmp_path)
    teambus.install("shop", "", ["lead", "rev"])
    teambus.send("shop", "lead", "rev", "review PR 12 & tell me | now")
    runs = Runs()
    tried, note = codexbus.push("shop", "rev", "019a-rev", set(), runs)
    assert (tried, note) == (True, "queued")
    command = runs.calls[0]
    assert command[:6] == [str(fake_codex), "queue", "--thread", "019a-rev", "--message", command[5]]
    assert "review PR 12 & tell me | now" in command[5] and "from lead" in command[5]
    assert command[-1] == "--resume"
    assert teambus.unread("shop", "rev") == []
    assert codexbus.push("shop", "rev", "019a-rev", set(), runs) == (False, "")  # nothing new
    assert len(runs.calls) == 1


def test_a_failed_queue_marks_nothing(tmp_path, fake_codex):
    _team(tmp_path)
    teambus.send("shop", "lead", "rev", "hello")
    runs = Runs(code=1, stderr="Error: thread not found")
    tried, note = codexbus.push("shop", "rev", "nope", set(), runs)
    assert tried and note.startswith("codex queue failed (1)") and "thread not found" in note
    assert [m["text"] for m in teambus.unread("shop", "rev")] == ["hello"]


def test_a_resume_warning_still_counts_as_queued(tmp_path, fake_codex):
    _team(tmp_path)
    teambus.send("shop", "lead", "rev", "hello")
    runs = Runs(stderr="warning: could not resume the thread; the message is queued")
    tried, note = codexbus.push("shop", "rev", "019a-rev", set(), runs)
    assert tried and note.startswith("queued;") and "next one" in note
    assert teambus.unread("shop", "rev") == []


def test_a_long_batch_goes_as_a_note_to_call_team_inbox(tmp_path, fake_codex):
    _team(tmp_path)
    for n in range(3):
        teambus.send("shop", "lead", "rev", f"part {n}: " + "x" * 2000)
    runs, already = Runs(), set()
    tried, note = codexbus.push("shop", "rev", "019a-rev", already, runs)
    assert (tried, note) == (True, "told of 3 waiting")
    text = runs.calls[0][5]
    assert text.startswith("3 unread messages") and "team_inbox" in text and len(text) < codexbus.MAX_INLINE
    assert len(teambus.unread("shop", "rev")) == 3  # team_inbox marks them, not the note
    assert codexbus.push("shop", "rev", "019a-rev", already, runs) == (False, "")  # not told twice
    teambus.send("shop", "lead", "rev", "one more")
    assert codexbus.push("shop", "rev", "019a-rev", already, runs)[0] is True


def test_no_codex_on_path_leaves_the_messages_waiting(tmp_path, monkeypatch):
    _team(tmp_path)
    monkeypatch.setattr(codexbus.shutil, "which", lambda name: None)
    teambus.send("shop", "lead", "rev", "hello")
    runs = Runs()
    assert codexbus.push("shop", "rev", "019a-rev", set(), runs) == (
        True, "codex is not on PATH: the messages wait for team_inbox")
    assert runs.calls == [] and len(teambus.unread("shop", "rev")) == 1


def test_npm_codex_cmd_gets_a_line_cmd_reads_safely(tmp_path):
    from tabmancer.launch import _for_cmd

    exe = str(tmp_path / "npm" / "codex.cmd")
    line = codexbus._command(exe, ["queue", "--message", 'a & b | "c" 100%'])
    assert isinstance(line, str) and line.endswith(_for_cmd('a & b | "c" 100%'))
    assert codexbus._command(str(tmp_path / "codex.exe"), ["queue"]) == [str(tmp_path / "codex.exe"), "queue"]


def test_watch_waits_for_a_thread_then_delivers_and_notes_it(tmp_path, monkeypatch, fake_codex):
    _team(tmp_path)
    teambus.write_presence("shop", "rev", cli="codex", pid=os.getpid())
    threads = iter(["", "019a-rev"])
    monkeypatch.setattr(codexbus, "thread_of", lambda team, member: next(threads, "019a-rev"))
    teambus.send("shop", "lead", "rev", "hello")
    stop = threading.Event()
    runs = Runs()

    def run(command, **kwargs):
        stop.set()
        return runs(command, **kwargs)

    worker = threading.Thread(target=codexbus.watch, args=("shop", "rev", stop, run, 0.01))
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    assert len(runs.calls) == 1 and runs.calls[0][3] == "019a-rev"
    assert teambus.read_presence("shop")["rev"].get("delivery") == "queued"


def test_watch_survives_a_deliverer_error(tmp_path, monkeypatch):
    _team(tmp_path)
    teambus.write_presence("shop", "rev", cli="codex", pid=os.getpid())
    stop = threading.Event()

    def broken(team, member):
        stop.set()
        raise RuntimeError("disk gone")

    monkeypatch.setattr(codexbus, "thread_of", broken)
    codexbus.watch("shop", "rev", stop, Runs(), 0.01)
    assert "delivery failed: disk gone" in str(teambus.read_presence("shop")["rev"].get("delivery"))


# --- the wrapper --------------------------------------------------------------------------

def test_team_run_watches_beside_a_codex_member_only(tmp_path, monkeypatch):
    _team(tmp_path)
    got: list = []
    monkeypatch.setattr(teambus, "run_member",
                        lambda team, member, cmd, cli="agy", created="", beside=None: got.append((cli, beside)) or 0)
    assert cli.team_run("shop", ["rev", "--", "C:\\npm\\codex.cmd", "-c", "x=1"]) == 0
    assert cli.team_run("shop", ["lead", "--", "claude"]) == 0
    assert cli.team_run("shop", ["lead", "--", "agy", "-i", "hi"]) == 0
    (c1, b1), (c2, b2), (c3, b3) = got
    assert c1 == "codex" and b1.func is codexbus.watch and b1.args == ("shop", "rev")
    assert (c2, b2, c3, b3) == ("claude", None, "agy", None)


def test_run_member_runs_beside_and_stops_it_when_the_cli_exits(tmp_path):
    seen = threading.Event()
    stopped: list[bool] = []

    def beside(stop: threading.Event) -> None:
        seen.set()
        stopped.append(stop.wait(10))

    code = teambus.run_member("shop", "rev", [sys.executable, "-c", "import time; time.sleep(0.3)"], cli="codex",
                              beside=beside)
    assert code == 0 and seen.is_set() and stopped == [True]
    assert "rev" not in teambus.read_presence("shop")


class Waits:
    """stop for watch: records each wait, and stops after so many."""

    def __init__(self, most: int) -> None:
        self.rests: list[float] = []
        self.most = most

    def wait(self, rest: float) -> bool:
        self.rests.append(rest)
        return len(self.rests) > self.most


def test_the_budget_is_the_quoted_length(tmp_path):
    messages = [{"id": "1", "at": datetime.now(), "from": "lead", "text": "100% " * 500}]
    assert codexbus.text_for(messages, str(tmp_path / "codex.exe"))[1] is True
    text, inline = codexbus.text_for(messages, str(tmp_path / "codex.cmd"))  # each % goes as %%cd:~,% for cmd.exe
    assert inline is False and "team_inbox" in text
    assert codexbus.text_for(messages[:0] + [{**messages[0], "text": "hi"}], "codex", brief=True)[1] is False


def test_after_a_failure_summaries_go_and_the_tries_back_off(tmp_path, monkeypatch, fake_codex):
    _team(tmp_path)
    teambus.write_presence("shop", "rev", cli="codex", pid=os.getpid())
    monkeypatch.setattr(codexbus, "thread_of", lambda team, member: "019a-rev")
    answers = iter([1, 1, 0])
    sent: list[str] = []

    def run(command, **kwargs):
        sent.append(command[5])
        return subprocess.CompletedProcess(command, next(answers, 0), stdout="", stderr="nope")

    teambus.send("shop", "lead", "rev", "short")
    stop = Waits(4)
    codexbus.watch("shop", "rev", stop, run, poll=1.0)
    assert "short" in sent[0] and sent[1].startswith("1 unread") and sent[2].startswith("1 unread")
    assert stop.rests[:4] == [1.0, codexbus.AFTER_FAILURE, codexbus.AFTER_FAILURE * 2, 1.0]
    assert len(teambus.unread("shop", "rev")) == 1  # a summary marks nothing: team_inbox does
    assert teambus.read_presence("shop")["rev"]["delivery"] == "told of 1 waiting"


def test_the_look_for_a_thread_backs_off(tmp_path, monkeypatch):
    _team(tmp_path)
    looks: list[int] = []
    monkeypatch.setattr(codexbus, "thread_of", lambda team, member: looks.append(1) or "")
    stop = Waits(12)
    codexbus.watch("shop", "rev", stop, Runs(), poll=2.0)
    assert stop.rests[:5] == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert max(stop.rests) == codexbus.MOST_BACKOFF and len(looks) == 12


def test_a_thread_found_by_its_tag_is_bound_by_the_deliverer(tmp_path, monkeypatch):
    team = _team(tmp_path)
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    _rollout(home, "019a-rev", team.cwd, "[tabmancer:shop-rev] hello")
    found: list[int] = []
    real = codex.find
    monkeypatch.setattr(codex, "find", lambda days=14: found.append(1) or real(days))
    assert codexbus.thread_of("shop", "rev") == "019a-rev"
    assert teambus.read_bindings("shop")["rev"]["session_id"] == "019a-rev"
    assert codexbus.thread_of("shop", "rev") == "019a-rev" and found == [1]  # the binding, not ~/.codex again
