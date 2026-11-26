"""The team bus for mixed-CLI teams: a message is marked read only once it is out, one listener per
member, a listener follows its agent (not the shell between them), the log stays bounded, and a
message from an agent always names who sent it."""

from __future__ import annotations

import builtins
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tabmancer import cli, teambus, teams


def _team(tmp_path: Path) -> teams.Team:
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    lineup = "lead: orchestrate @opus\napi: endpoints [src/api] @sonnet\nui: screens [src/ui] @agy @high"
    team = teams.new_team("shop", str(folder), "Build checkout", teams.parse_members(lineup, lead_ok=True))
    teams.save({"shop": team})
    return team


def _in_thread(fn):
    """fn run in a thread; the box holds what it returned, or what it raised."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as problem:  # noqa: BLE001 - the test looks at what came out
            box["error"] = problem

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def _until(check, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


# --- (1) read only once it is out ------------------------------------------------------------

def test_listen_without_ack_leaves_the_message_unread_until_acked():
    sent = teambus.send("shop", "lead", "ui", "build the form")
    got = teambus.listen("shop", "ui", timeout=0.5, poll=0.02, ack=False)
    assert [m["id"] for m in got] == [sent["id"]]
    assert [m["id"] for m in teambus.unread("shop", "ui")] == [sent["id"]]  # not yet out: still unread
    teambus.ack("shop", "ui", [m["id"] for m in got])
    assert teambus.unread("shop", "ui") == []


def test_a_listen_that_dies_before_printing_loses_nothing(tmp_path, monkeypatch, capsys):
    _team(tmp_path)
    teambus.send("shop", "lead", "ui", "build the form")

    def broken_print(*args, **kwargs):
        raise OSError("the pipe closed")  # the agent stopped reading before the message was out

    with monkeypatch.context() as patched, pytest.raises(OSError):  # only print: the bus stays the test's own
        patched.setattr(builtins, "print", broken_print)
        cli.team_listen("shop", ["ui", "--timeout", "0.3"])
    assert [m["text"] for m in teambus.unread("shop", "ui")] == ["build the form"]
    # The next listen delivers it, and then it is read.
    assert cli.team_listen("shop", ["ui", "--timeout", "0.3"]) == 0
    assert "build the form" in capsys.readouterr().out
    assert teambus.unread("shop", "ui") == []


def test_the_listen_output_is_what_the_prompts_expect(tmp_path, capsys):
    _team(tmp_path)
    teambus.send("shop", "lead", "ui", "build the form")
    assert cli.team_listen("shop", ["ui", "--timeout", "0.3"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[") and "from lead]: build the form" in out
    assert cli.team_listen("shop", ["ui", "--timeout", "0.1"]) == 0
    assert "[timeout] No messages for ui" in capsys.readouterr().out


# --- (2) one listener per member ---------------------------------------------------------------

def test_a_second_listener_takes_over_and_the_first_leaves_without_marking():
    first, first_box = _in_thread(lambda: teambus.listen("shop", "ui", timeout=10, poll=0.02))
    _until(lambda: teambus.listener("shop", "ui") is not None)
    owner = teambus.listener("shop", "ui")
    second, second_box = _in_thread(lambda: teambus.listen("shop", "ui", timeout=10, poll=0.02))
    _until(lambda: teambus.listener("shop", "ui") not in (None, owner))
    first.join(5)
    assert not first.is_alive() and isinstance(first_box.get("error"), teambus.Superseded)
    # The first one left the second's claim and presence alone.
    assert teambus.listener("shop", "ui") is not None
    assert teambus.read_presence("shop")["ui"]["listening"]
    sent = teambus.send("shop", "lead", "ui", "build the form")
    second.join(5)
    assert [m["id"] for m in second_box["value"]] == [sent["id"]]
    assert teambus.listener("shop", "ui") is None  # gone with the listener
    assert teambus.unread("shop", "ui") == []


def test_a_superseded_listen_says_not_to_start_another(tmp_path, monkeypatch, capsys):
    _team(tmp_path)

    def superseded(*args, **kwargs):
        raise teambus.Superseded("ui")

    monkeypatch.setattr(teambus, "listen", superseded)
    assert cli.team_listen("shop", ["ui", "--timeout", "0.1"]) == 0
    out = capsys.readouterr().out
    assert "[superseded]" in out and "do not start another" in out


def test_a_dead_listeners_claim_is_no_listener():
    teambus._write_claim("shop", "ui", {"pid": 999_999_999, "procStart": "1", "token": "old"})
    assert teambus.listener("shop", "ui") is None
    assert "ui" not in teambus.read_presence("shop")


def test_the_listener_claim_is_its_own_file_beside_the_runners_presence():
    teambus.write_presence("shop", "ui", cli="agy", session_id="conv-1")
    _, box = _in_thread(lambda: teambus.listen("shop", "ui", timeout=0.4, poll=0.02))
    _until(lambda: teambus.listener("shop", "ui") is not None)
    teambus.write_presence("shop", "ui", cli="agy", session_id="conv-1", state="working")  # the runner writes on
    assert teambus.read_presence("shop")["ui"]["listening"]  # the runner's write did not drop the listener
    _until(lambda: "value" in box)
    assert not teambus.read_presence("shop")["ui"]["listening"]


# --- (3) the agent, not the shell --------------------------------------------------------------

def test_the_agent_pid_is_found_up_the_parent_chain(monkeypatch):
    monkeypatch.delenv(teambus.AGENT_PID_ENV, raising=False)
    chain = {100: (90, "bash.exe"), 90: (80, "cmd.exe"), 80: (70, "claude.exe"), 70: (1, "explorer.exe")}
    monkeypatch.setattr(teambus, "_parent", lambda pid: chain.get(pid))
    assert teambus.agent_pid(100) == 80
    chain[80] = (70, "node.exe")  # Claude Code from npm
    assert teambus.agent_pid(100) == 80
    chain[80] = (70, "agy")
    assert teambus.agent_pid(100) == 80


def test_no_agent_up_the_chain_means_the_parent(monkeypatch):
    monkeypatch.delenv(teambus.AGENT_PID_ENV, raising=False)
    chain = {100: (90, "bash"), 90: (1, "sshd")}
    monkeypatch.setattr(teambus, "_parent", lambda pid: chain.get(pid))
    assert teambus.agent_pid(100) == 90
    monkeypatch.setattr(teambus, "_parent", lambda pid: None)  # nothing readable: as before
    assert teambus.agent_pid(100) is None


def test_the_runner_names_the_agent_pid(monkeypatch):
    monkeypatch.setenv(teambus.AGENT_PID_ENV, "4242")
    monkeypatch.setattr(teambus, "_parent", lambda pid: pytest.fail("no walk when the runner said"))
    assert teambus.agent_pid(100) == 4242


def test_team_run_gives_its_member_the_agent_pid_and_name(monkeypatch):
    seen = {}

    class Proc:
        pid = 31337

        def wait(self):
            return 0

    def popen(argv, env=None):
        seen.update(env or {})
        return Proc()

    monkeypatch.setattr(teambus.subprocess, "Popen", popen)
    assert teambus.run_member("shop", "ui", ["agy", "-i", "hi"]) == 0
    assert seen["TABMANCER_MEMBER"] == "ui" and seen[teambus.AGENT_PID_ENV] == str(os.getpid())


def test_listen_follows_the_agent_pid(tmp_path, monkeypatch):
    _team(tmp_path)
    watched = []
    monkeypatch.setattr(teambus, "agent_pid", lambda pid=None: 4242)
    monkeypatch.setattr(teambus, "listen", lambda *a, **k: watched.append(k.get("watch_pid")) or [])
    cli.team_listen("shop", ["ui", "--timeout", "0.1"])
    assert watched == [4242]


# --- (4) a bounded log ------------------------------------------------------------------------

def test_the_log_rotates_by_size_and_keeps_a_few(monkeypatch):
    monkeypatch.setattr(teambus, "ROTATE_BYTES", 2_000)
    for i in range(120):
        teambus.send("shop", "lead", "api", f"message {i:03d} " + "x" * 40)
    folder = teambus.team_dir("shop")
    logs = sorted(p.name for p in folder.glob("messages*.jsonl"))
    assert len(logs) <= teambus.KEEP_ROTATED + 1
    assert all(p.stat().st_size <= 2_000 + 200 for p in folder.glob("messages*.jsonl"))
    said = [m["text"] for m in teambus.messages("shop")]
    assert said[-1].startswith("message 119") and said == sorted(said)  # oldest first, across the files


def test_rotation_neither_drops_an_unread_message_nor_brings_back_a_read_one(monkeypatch):
    monkeypatch.setattr(teambus, "ROTATE_BYTES", 1_000)
    read = teambus.send("shop", "lead", "api", "read before the rotation")
    teambus.ack("shop", "api", [read["id"]])
    waiting = teambus.send("shop", "lead", "ui", "still waiting")
    for i in range(12):  # two rotations: the first two messages are in a rotated file, still kept
        teambus.send("shop", "lead", "lead", f"filler {i} " + "x" * 60)
    assert (teambus.team_dir("shop") / "messages.1.jsonl").is_file()  # it did rotate
    assert [m["id"] for m in teambus.unread("shop", "ui")] == [waiting["id"]]
    assert teambus.unread("shop", "api") == []


def test_a_rotation_another_process_holds_waits_its_turn(monkeypatch):
    monkeypatch.setattr(teambus, "ROTATE_BYTES", 500)
    lock = teambus.team_dir("shop") / teambus.ROTATE_LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("someone", encoding="utf-8")  # held, and fresh
    for i in range(20):
        teambus.send("shop", "lead", "api", f"m{i} " + "x" * 60)
    assert not (teambus.team_dir("shop") / "messages.1.jsonl").exists()  # not rotated under another's lock
    assert len(teambus.messages("shop")) == 20  # and nothing lost
    old = time.time() - teambus.STALE_LOCK_SECONDS - 5
    os.utime(lock, (old, old))  # a lock left by a process that died
    teambus.send("shop", "lead", "api", "one more")
    assert (teambus.team_dir("shop") / "messages.1.jsonl").is_file() and not lock.exists()


# --- (5) who sent it ----------------------------------------------------------------------------

def test_an_agent_sending_without_a_name_is_told_to_give_one(tmp_path, monkeypatch, capsys):
    _team(tmp_path)
    monkeypatch.delenv("TABMANCER_MEMBER", raising=False)
    monkeypatch.setattr(teambus, "agent_caller", lambda: True)
    assert cli.team_send("shop", ["ui", "hello"]) == 1
    assert "--from" in capsys.readouterr().err
    assert teambus.messages("shop") == []


def test_you_from_a_terminal_and_a_member_by_its_environment(tmp_path, monkeypatch):
    _team(tmp_path)
    monkeypatch.delenv("TABMANCER_MEMBER", raising=False)
    monkeypatch.setattr(teambus, "agent_caller", lambda: False)
    assert cli.team_send("shop", ["ui", "from", "the", "terminal"]) == 0
    monkeypatch.setenv("TABMANCER_MEMBER", "api")
    monkeypatch.setattr(teambus, "agent_caller", lambda: pytest.fail("the name is known"))
    assert cli.team_send("shop", ["ui", "from", "api"]) == 0
    assert [m["from"] for m in teambus.messages("shop")] == [teams.YOU, "api"]


def test_every_bus_member_is_given_its_name_claude_ones_too(tmp_path):
    team = _team(tmp_path)
    for member in team.members:
        env = dict(teams.bus_env(team, member))
        assert env["TABMANCER_MEMBER"] == member.name and env["TABMANCER_TEAM"] == "shop"
    for member in team.members:
        launch = teams.launch(team, member)
        assert dict(launch.env).get("TABMANCER_MEMBER") == member.name, launch.title
        assert dict(teams.background_start(team, launch).env).get("TABMANCER_MEMBER") == member.name


# --- (6) a copy agy's sandbox can run ----------------------------------------------------------

def test_the_bus_needs_nothing_but_the_standard_library():
    import ast

    tree = ast.parse(Path(teambus.__file__).read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(not (isinstance(node, ast.ImportFrom) and node.level) for node in top)  # no `from .x import`
    assert all(not (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tabmancer")) for node in top)
    assert teambus.YOU == teams.YOU


def _run_copy(command: str, args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """The installed command as a shell runs it, from a folder with nothing in it."""
    return subprocess.run(f"{command} {args}", shell=True, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=60, check=False)


def test_the_copy_in_the_bus_folder_sends_listens_and_acks_on_its_own(tmp_path):
    command = teambus.install("shop", created="2026-11-20T10:00:00+03:00", members=["lead", "ui"])
    copy = teambus.team_dir("shop") / teambus.CLI_FILE
    assert copy.is_file() and " -I " in command and command.endswith(teambus.CLI_FILE)
    empty = tmp_path / "sandbox-cwd"
    empty.mkdir()
    # Nothing of tabmancer on the path, and the bus folder found from where the copy lies.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", teambus.BUS_ENV, "TABMANCER_MEMBER")}
    sent = _run_copy(command, 'send shop ui "build the form" --from lead', empty, env)
    assert sent.returncode == 0, sent.stderr
    assert "Sent to ui" in sent.stdout
    assert [m["text"] for m in teambus.unread("shop", "ui")] == ["build the form"]  # the same bus
    heard = _run_copy(command, "listen shop ui --timeout 5", empty, env)
    assert heard.returncode == 0, heard.stderr
    assert "from lead]: build the form" in heard.stdout
    assert teambus.unread("shop", "ui") == []  # acked once printed
    wrong = _run_copy(command, 'send shop nobody "hi" --from lead', empty, env)
    assert wrong.returncode == 1 and "No member nobody" in wrong.stderr  # the members it was told


def test_the_copy_keeps_to_its_teams_generation():
    teambus.send("shop", "lead", "ui", "from the team before")  # the same name, an older team
    teambus.install("shop", created="2999-01-01T00:00:00+00:00", members=["lead", "ui"])
    assert teambus.main(["listen", "shop", "ui", "--timeout", "0.2"]) == 0  # nothing of the old team


def test_install_rewrites_only_what_changed():
    teambus.install("shop", created="2026-11-20T10:00:00+03:00", members=["lead", "ui"])
    copy, info = teambus.team_dir("shop") / teambus.CLI_FILE, teambus.team_dir("shop") / teambus.TEAM_FILE
    before = (copy.stat().st_mtime_ns, info.stat().st_mtime_ns)
    time.sleep(0.02)
    teambus.install("shop", created="2026-11-20T10:00:00+03:00", members=["lead", "ui"])
    assert (copy.stat().st_mtime_ns, info.stat().st_mtime_ns) == before
    copy.write_text("tampered", encoding="utf-8")  # a member edited it: put back on the next start
    teambus.install("shop", created="2026-11-20T10:00:00+03:00", members=["lead", "ui", "api"])
    assert copy.read_text(encoding="utf-8").startswith("# Generated by tabmancer")
    assert teambus._read_team_file("shop")[1] == ["lead", "ui", "api"]


def test_a_member_started_in_the_background_gets_its_name_too(tmp_path, monkeypatch, real_launch):
    """claude --bg ran with tabmancer's own environment only, so a background Claude member sent as 'you'."""
    from tabmancer import launch as launch_module

    team = _team(tmp_path)
    member = next(m for m in team.members if m.name == "api")
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "started api (bg 1a2b)\n", ""

    def run(command, env=None, **kwargs):
        seen.update(env or {})
        return Done()

    monkeypatch.setattr(launch_module.shutil, "which", lambda name: "C:/bin/claude.exe")
    monkeypatch.setattr(launch_module.claudeupdate, "before_opening", lambda: None)
    monkeypatch.setattr(launch_module.subprocess, "run", run)
    launch_module.run_in_background(teams.background_start(team, teams.launch(team, member)))
    assert seen.get("TABMANCER_MEMBER") == "api" and seen.get("TABMANCER_TEAM") == "shop"


# --- the bus folder on a shared /tmp ----------------------------------------------------------------

def _as_posix(monkeypatch, tmp_path, uid: int) -> list:
    monkeypatch.delenv(teambus.BUS_ENV, raising=False)
    monkeypatch.setattr(teambus.sys, "platform", "linux")
    monkeypatch.setattr(teambus.os, "getuid", lambda: uid, raising=False)
    monkeypatch.setattr(teambus.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    monkeypatch.setattr(teambus.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(teambus, "_PRIVATE", set())
    chmods: list = []
    monkeypatch.setattr(teambus.os, "chmod", lambda path, mode: chmods.append((str(path), mode)))
    return chmods


def test_on_linux_and_macos_the_bus_is_the_users_own_folder(monkeypatch, tmp_path):
    (tmp_path / "tmp").mkdir()
    owner = teambus.os.lstat(tmp_path / "tmp").st_uid  # what a folder made here is owned by
    _as_posix(monkeypatch, tmp_path, owner)
    root = teambus.bus_root()
    assert root == tmp_path / "tmp" / f"tabmancer-bus-{owner}" and root.is_dir()
    assert teambus.team_dir("shop").parent == root


def test_a_bus_folder_someone_else_made_is_not_used(monkeypatch, tmp_path):
    (tmp_path / "tmp").mkdir()
    owner = teambus.os.lstat(tmp_path / "tmp").st_uid
    _as_posix(monkeypatch, tmp_path, owner + 1000)  # the folder's owner is not this user
    assert teambus.bus_root() == tmp_path / "home" / ".cache" / "tabmancer-bus"


def test_a_bus_folder_others_can_read_is_closed_to_them(monkeypatch, tmp_path):
    (tmp_path / "tmp").mkdir()
    owner = teambus.os.lstat(tmp_path / "tmp").st_uid
    chmods = _as_posix(monkeypatch, tmp_path, owner)
    real = teambus.os.lstat
    monkeypatch.setattr(teambus.os, "lstat", lambda path: os.stat_result(
        (teambus.stat.S_IFDIR | 0o755, *real(path)[1:])))
    root = teambus.bus_root()
    assert chmods == [(str(root), 0o700)]


def test_windows_keeps_its_temp_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(teambus.BUS_ENV, raising=False)
    monkeypatch.setattr(teambus.sys, "platform", "win32")
    monkeypatch.setattr(teambus.tempfile, "gettempdir", lambda: str(tmp_path))
    assert teambus.bus_root() == tmp_path / "tabmancer-bus"


def test_windows_keeps_the_bus_in_temp():
    """Pinned: agy's sandbox refuses --add-dir writes outside %TEMP% too (2026-11-23 retest), and a flip
    moves every new team's bus, so it is a decision, not an edit."""
    assert teambus.WINDOWS_BUS_IN_TEMP is True


def test_windows_can_keep_the_bus_out_of_its_temp_folder(monkeypatch, tmp_path):
    """Ready for the retest's outcome: a sandbox that refuses writes under %TEMP% only."""
    monkeypatch.delenv(teambus.BUS_ENV, raising=False)
    monkeypatch.setattr(teambus.sys, "platform", "win32")
    monkeypatch.setattr(teambus, "WINDOWS_BUS_IN_TEMP", False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert teambus.bus_root() == tmp_path / "Local" / "tabmancer" / "bus"
    monkeypatch.setenv(teambus.BUS_ENV, str(tmp_path / "own"))  # a member started with its folder keeps it
    assert teambus.bus_root() == tmp_path / "own"


def test_a_session_name_loses_the_team_only_when_that_leaves_a_member():
    assert teambus.normalize_member("shop", "shop-api") == "api"  # no member list yet: as before
    teambus.install("shop", "", ["lead", "api", "shop-ui"])
    assert teambus.normalize_member("shop", "shop-api") == "api"
    assert teambus.normalize_member("shop", "Shop-API") == "API"
    assert teambus.normalize_member("shop", "shop-ui") == "shop-ui"  # a member of that very name
    assert teambus.normalize_member("shop", "shop-qa") == "shop-qa"  # no member qa: left whole
    assert teambus.normalize_member("shop", " lead ") == "lead"


# --- a bus folder it may read but not write (agy's terminal sandbox) ----------------------------

@pytest.fixture
def read_only_bus(monkeypatch):
    """Every write into the bus folder refused, as agy's sandbox refused the 2026-11-20 seam test's."""
    monkeypatch.setattr(teambus, "_atomic_write_json", _refused)
    monkeypatch.setattr(teambus, "_append_jsonl", _refused)
    monkeypatch.setattr(teambus, "agent_pid", lambda *a: None)


def _refused(*args, **kwargs):
    raise PermissionError(13, "Permission denied")


def test_a_listen_that_cannot_write_still_hands_the_message_over_once_and_says_acks_are_off(
        monkeypatch, capsys):
    sent = teambus.send("shop", "lead", "ui", "TASK-1: probe the bus")
    monkeypatch.setattr(teambus, "agent_pid", lambda *a: None)
    with monkeypatch.context() as m:  # the message is in; then the folder turns read-only
        m.setattr(teambus, "_atomic_write_json", _refused)
        m.setattr(teambus, "_append_jsonl", _refused)
        assert teambus.listen_command("shop", "ui", timeout=0.5) == 0
        out = capsys.readouterr().out
    assert out.count("TASK-1: probe the bus") == 1  # printed once in this run, no traceback
    assert teambus.ACKS_OFF in out and "team_inbox" in out and "BypassSandbox" in out
    # Still unread, so a member acks it with team_inbox (outside the sandbox) and it never comes again.
    assert [m["id"] for m in teambus.unread("shop", "ui")] == [sent["id"]]
    teambus.unread("shop", "ui", mark_read=True)
    assert teambus.listen_command("shop", "ui", timeout=0.2) == 0
    assert "TASK-1" not in capsys.readouterr().out


def test_a_listen_without_a_claim_waits_and_is_never_superseded(read_only_bus):
    report: dict = {}
    assert teambus.listen("shop", "ui", timeout=0.2, poll=0.02, ack=False, report=report) == []
    assert report == {"claimed": False}
    assert teambus.listener("shop", "ui") is None  # nobody sees it listening


def test_a_listen_that_could_ack_but_not_claim_says_presence_is_off(monkeypatch, capsys):
    teambus.send("shop", "lead", "ui", "hello")
    monkeypatch.setattr(teambus, "_write_claim", _refused)
    monkeypatch.setattr(teambus, "agent_pid", lambda *a: None)
    assert teambus.listen_command("shop", "ui", timeout=0.5) == 0
    out = capsys.readouterr().out
    assert "hello" in out and teambus.PRESENCE_OFF in out and teambus.ACKS_OFF not in out
    assert teambus.unread("shop", "ui") == []


# --- a process it may not ask about is not a dead one (agy's sandbox denies OpenProcess) ----------

def test_alive_means_running_as_the_same_process_and_a_refused_question_is_no_death(monkeypatch):
    def state(answer):
        monkeypatch.setattr(teambus, "_process_state", lambda pid: answer)

    state((teambus.GONE, None))
    assert not teambus._alive({"pid": 4242, "procStart": "7"})
    state((teambus.UNKNOWN, None))
    assert teambus._alive({"pid": 4242, "procStart": "7"})  # agy's sandbox: no answer, not dead
    state((teambus.RUNNING, 7))
    assert teambus._alive({"pid": 4242, "procStart": "7"}) and teambus._alive({"pid": 4242})
    state((teambus.RUNNING, 8))
    assert not teambus._alive({"pid": 4242, "procStart": "7"})  # the pid went to another process
    assert not teambus._alive({})


def _agent(monkeypatch, answers: list) -> list:
    """The watched agent (pid 4242) answers each question with the next of `answers` (the last one
    from then on); returns the list of questions asked about it. Other pids (the listen's own, for
    its claim) are running."""
    asked: list = []

    def state(pid):
        if pid != 4242:
            return teambus.RUNNING, 1
        asked.append(pid)
        return answers[min(len(asked), len(answers)) - 1]

    monkeypatch.setattr(teambus, "_process_state", state)
    return asked


def test_a_listen_whose_agent_it_may_not_ask_about_keeps_waiting(monkeypatch):
    # The 2026-11-23 Stop-hook run: in agy's sandbox every listen said [timeout] within seconds.
    asked = _agent(monkeypatch, [(teambus.UNKNOWN, None)])
    _, box = _in_thread(lambda: teambus.listen("shop", "ui", timeout=30, poll=0.01, watch_pid=4242))
    _until(lambda: len(asked) >= 4)  # the agent asked about several times over
    assert "value" not in box
    teambus.send("shop", "lead", "ui", "TASK-2")
    _until(lambda: "value" in box)
    assert [m["text"] for m in box["value"]] == ["TASK-2"]


@pytest.mark.parametrize("later", [(teambus.GONE, None), (teambus.RUNNING, 8)], ids=["gone", "pid reused"])
def test_a_listen_ends_with_its_agent(monkeypatch, later):
    _agent(monkeypatch, [(teambus.RUNNING, 7), later])  # the start time is taken first, then asked
    started = time.monotonic()
    assert teambus.listen("shop", "ui", timeout=30, poll=0.01, watch_pid=4242) == []
    assert time.monotonic() - started < 5


@pytest.mark.skipif(os.name != "nt", reason="Windows' own answers")
def test_windows_says_gone_for_an_ended_process_and_running_for_a_live_one():
    child = subprocess.Popen(["cmd", "/c", "exit 0"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    child.wait()
    assert teambus._windows_query(child.pid)[0] == teambus.GONE
    assert teambus._windows_query(999_999_999) == (teambus.GONE, None)
    state, started = teambus._windows_query(os.getpid())
    assert state == teambus.RUNNING and started == teambus._windows_process_start(os.getpid())
