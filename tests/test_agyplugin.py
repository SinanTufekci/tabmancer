"""tabmancer's agy plugin: the Stop hook (quota notes, messages handed over) and install/remove."""

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from tabmancer import agy, agyplugin, teambus

TEAM = {"TABMANCER_TEAM": "shop", "TABMANCER_MEMBER": "ui"}


def _hook(event, environ=None) -> str:
    out = io.StringIO()
    text = event if isinstance(event, str) else json.dumps(event)
    assert agyplugin.hook(io.StringIO(text), out, environ if environ is not None else {}) == 0
    return out.getvalue()


@pytest.fixture
def quota_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(agy, "note_quota_error", lambda text, model, now=None: calls.append((text, model)))
    return calls


# --- the hook -------------------------------------------------------------------------------------

def test_outside_a_team_a_turn_that_ends_is_left_alone(quota_calls):
    teambus.send("shop", "lead", "ui", "for a member, not this session")
    assert _hook({"hookEventName": "Stop", "terminationReason": "END", "executionNum": 0}) == ""
    assert quota_calls == [] and len(teambus.unread("shop", "ui")) == 1


def test_a_failed_turn_notes_its_quota_and_is_not_sent_round_again(quota_calls):
    teambus.send("shop", "lead", "ui", "waiting")
    error = "RESOURCE_EXHAUSTED (429): Individual quota reached. Resets in 4h7m"
    event = {"terminationReason": "ERROR", "error": error, "modelName": "Gemini 4 Hydrogen (High)",
             "executionNum": 0}
    assert _hook(event, TEAM) == ""
    assert quota_calls == [(error, "Gemini 4 Hydrogen (High)")]
    assert len(teambus.unread("shop", "ui")) == 1  # handed over by a turn that works, not this one
    _hook({**event, "error": ""}, {})
    assert len(quota_calls) == 1  # no text, nothing to read


def test_a_quota_note_that_fails_never_breaks_the_hook(monkeypatch):
    def broken(text, model, now=None):
        raise RuntimeError("bad usage file")

    monkeypatch.setattr(agy, "note_quota_error", broken)
    assert _hook({"terminationReason": "ERROR", "error": "RESOURCE_EXHAUSTED"}) == ""


def test_the_real_quota_note_runs_from_the_hook():
    _hook({"terminationReason": "ERROR", "modelName": "gemini-4-hydrogen-high",
           "error": "RESOURCE_EXHAUSTED (429): Individual quota reached. Resets in 4h7m"})
    noted = agy.note_quota_error("RESOURCE_EXHAUSTED (429): Individual quota reached. Resets in 4h7m",
                                 "gemini-4-hydrogen-high")
    assert noted is not None and noted > datetime.now(UTC) + timedelta(hours=4)


def test_messages_waiting_send_the_member_round_again_with_them(quota_calls):
    teambus.send("shop", "lead", "ui", "make the button blue", summary="button")
    teambus.send("shop", "api", "ui", "endpoint is /pay — ünïcode")
    said = _hook({"terminationReason": "END", "executionNum": 0}, TEAM)
    assert said.isascii()  # whatever the console's code page
    answer = json.loads(said)
    assert answer["decision"] == "continue"
    assert "from lead]: make the button blue" in answer["reason"]
    assert "from api]: endpoint is /pay — ünïcode" in answer["reason"]
    assert teambus.unread("shop", "ui") == []
    assert _hook({"terminationReason": "END", "executionNum": 1}, TEAM) == ""  # nothing left


def test_the_stop_chain_ends_after_max_continues():
    teambus.send("shop", "lead", "ui", "ignored again")
    assert _hook({"executionNum": agyplugin.MAX_CONTINUES, "terminationReason": "END"}, TEAM) == ""
    assert len(teambus.unread("shop", "ui")) == 1  # still there for its listen or team_wait


def test_messages_from_before_the_team_started_are_not_handed_over():
    teambus.send("shop", "lead", "ui", "an older team's")
    teambus.install("shop", (datetime.now(UTC) + timedelta(minutes=10)).isoformat(), ["lead", "ui"])
    assert _hook({"executionNum": 0}, TEAM) == ""


def test_bad_or_missing_events_do_nothing():
    teambus.send("shop", "lead", "ui", "x")
    for event in ("", "{not json", "[1, 2]", "null"):
        assert _hook(event, TEAM) == ""
    assert len(teambus.unread("shop", "ui")) == 1


def test_a_stdin_never_closed_does_not_hold_the_hook(monkeypatch):
    monkeypatch.setattr(agyplugin, "STDIN_WAIT", 0.3)
    release = threading.Event()

    class Open(io.StringIO):
        def read(self, *args):
            release.wait(30)  # a writer that never closes
            return ""

    try:
        began = time.monotonic()
        assert agyplugin.hook(Open(), io.StringIO(), TEAM) == 0
        assert time.monotonic() - began < 3
    finally:
        release.set()


def test_a_message_not_written_out_stays_unread():
    teambus.send("shop", "lead", "ui", "x")

    class Closed(io.StringIO):
        def write(self, text):
            raise OSError("pipe closed")

    assert agyplugin.hook(io.StringIO('{"executionNum": 0}'), Closed(), TEAM) == 0
    assert len(teambus.unread("shop", "ui")) == 1


def test_the_hook_command_as_written_runs_through_cmd_and_hands_messages_over():
    """The seam: hooks.json's command string, run the way agy runs it (one string to the shell)."""
    command = json.loads(agyplugin.files()["hooks.json"])["tabmancer-stop"]["Stop"][0]["command"]
    teambus.send("shop", "lead", "ui", "from the real hook")
    env = {**os.environ, **TEAM}
    done = subprocess.run(command, shell=True, input=json.dumps({"executionNum": 0, "terminationReason": "END"}),
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert "from lead]: from the real hook" in json.loads(done.stdout)["reason"]
    assert teambus.unread("shop", "ui") == []


# --- the plugin's files ---------------------------------------------------------------------------

def test_the_plugin_files_are_what_agy_reads():
    made = {name: json.loads(text) for name, text in agyplugin.files().items()}
    assert set(made) == {"plugin.json", "mcp_config.json", "hooks.json"}  # at the root: hooks/ is not read
    assert made["plugin.json"]["name"] == agyplugin.NAME
    server = made["mcp_config.json"]["mcpServers"]["tabmancer"]
    assert server == {"command": sys.executable, "args": ["-m", "tabmancer", "mcp"]}  # no team: from agy's env
    [stop] = made["hooks.json"]["tabmancer-stop"]["Stop"]
    assert stop["type"] == "command" and isinstance(stop["command"], str)  # one string, run through cmd.exe
    assert stop["command"].endswith("-I -m tabmancer.agyplugin hook") and stop["timeout"] == agyplugin.HOOK_TIMEOUT


def test_the_mcp_server_as_the_plugin_starts_it_takes_its_member_from_agys_env(tmp_path):
    server = json.loads(agyplugin.files()["mcp_config.json"])["mcpServers"]["tabmancer"]
    hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "antigravity", "version": "1"}}})
    ask = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "team_send", "arguments": {"to": "lead", "message": "hi from agy"}}})
    done = subprocess.run([server["command"], *server["args"]], input=f"{hello}\n{ask}\n", capture_output=True,
                          text=True, encoding="utf-8", env={**os.environ, **TEAM}, cwd=tmp_path, timeout=60,
                          check=False)
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert replies[1]["result"]["isError"] is False, done.stdout + done.stderr
    assert [(m["from"], m["text"]) for m in teambus.unread("shop", "lead")] == [("ui", "hi from agy")]


# --- install and remove ---------------------------------------------------------------------------

class FakeAgy:
    def __init__(self, fail: str = ""):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv[1:]))
        verb = argv[2]
        if verb == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", f"{verb} broke")
        if verb == "install":  # agy copies the folder in, and makes its manifest and data folder
            target = agyplugin.installed_dir()
            target.mkdir(parents=True, exist_ok=True)
            for name, text in agyplugin.files().items():
                (target / name).write_text(text, encoding="utf-8")
            agyplugin.manifest_file().write_text('{"imports": null}', encoding="utf-8")
            agyplugin.data_dir().mkdir(parents=True, exist_ok=True)
        if verb == "uninstall":
            import shutil

            shutil.rmtree(agyplugin.installed_dir())
        return subprocess.CompletedProcess(argv, 0, "ok", "")


@pytest.fixture
def fake_agy(monkeypatch):
    monkeypatch.setattr(agyplugin, "_agy", lambda: "agy")
    return FakeAgy()


def test_install_validates_then_installs_and_remove_leaves_agy_as_it_was(fake_agy):
    assert agyplugin.status() == "missing"
    done, said = agyplugin.install(fake_agy)
    assert done and "tabmancer agy-plugin remove" in said
    folder = str(agyplugin.source_dir())
    assert fake_agy.calls == [["plugin", "validate", folder], ["plugin", "install", folder]]
    assert agyplugin.status() == "installed"

    done, said = agyplugin.remove(fake_agy)
    assert done and fake_agy.calls[-1] == ["plugin", "uninstall", "tabmancer"]
    assert agyplugin.status() == "missing"
    assert not agyplugin.data_dir().exists() and not agyplugin.manifest_file().exists()
    assert not agyplugin.source_dir().exists()


def test_remove_keeps_what_agy_had_before(fake_agy):
    agyplugin.manifest_file().parent.mkdir(parents=True, exist_ok=True)
    agyplugin.manifest_file().write_text('{"imports": null}', encoding="utf-8")  # another local plugin's
    agyplugin.install(fake_agy)
    agyplugin.remove(fake_agy)
    assert agyplugin.manifest_file().is_file()


def test_a_reinstall_remembers_the_first_installs_before(fake_agy):
    agyplugin.install(fake_agy)
    agyplugin.install(fake_agy)  # the manifest exists now, made by the first install
    agyplugin.remove(fake_agy)
    assert not agyplugin.manifest_file().exists()


def test_a_validate_that_fails_installs_nothing(monkeypatch):
    monkeypatch.setattr(agyplugin, "_agy", lambda: "agy")
    broken = FakeAgy(fail="validate")
    done, said = agyplugin.install(broken)
    assert not done and "validate failed (1): validate broke" in said
    assert [c[1] for c in broken.calls] == ["validate"]


def test_without_agy_nothing_is_touched(monkeypatch):
    monkeypatch.setattr(agyplugin, "_agy", lambda: None)
    assert agyplugin.install() == (False, "agy is not on PATH: install Antigravity's CLI first.")
    assert not agyplugin.source_dir().exists()


def test_status_says_when_the_installed_plugin_is_older(fake_agy):
    agyplugin.install(fake_agy)
    hooks = agyplugin.installed_dir() / "hooks.json"
    hooks.write_text(hooks.read_text(encoding="utf-8").replace("-I -m", "-m"), encoding="utf-8")
    assert agyplugin.status() == "outdated"


def test_the_plugin_folders_live_under_agys_home_never_the_real_one():
    assert agyplugin.installed_dir().is_relative_to(agy.home().parent)
    assert ".gemini" not in str(agy.home()) or "no-agy-home" in str(agy.home())


def test_an_agy_members_role_names_the_tools_only_when_the_plugin_is_there(tmp_path, monkeypatch):
    from tabmancer import teams

    folder = tmp_path / "shop"
    folder.mkdir()
    team = teams.new_team("shop", str(folder), "Build checkout",
                          teams.parse_members("lead: plans\nui: screens @agy", lead_ok=True))
    ui = team.members[1]
    bare = teams.role(team, ui)
    assert "team_inbox" not in bare
    assert "send shop <member>" in bare and "BypassSandbox: true" in bare  # the shell copy, out of the sandbox
    monkeypatch.setattr(agyplugin, "status", lambda: "installed")
    role = teams.role(team, ui)
    assert "team_send, team_inbox, team_who, team_status" in role
    assert "keep waiting with the background listen" in role and "listen shop ui" in role
    # Sends go through the tool (outside the sandbox), the first call alone, and acks when they are off.
    assert 'Send a message with `the team_send tool (to: "<member>"' in role and "send shop <member>" not in role
    assert "make it alone" in role and "[acks off], call team_inbox with ack: true" in role


@pytest.mark.parametrize("event", [{"terminationReason": "END"}, {"terminationReason": "END", "executionNum": None},
                                   {"terminationReason": "END", "executionNum": "x"},
                                   {"terminationReason": "END", "executionNum": True}])
def test_a_turn_count_agy_leaves_out_sends_no_turn_round(event):
    teambus.send("shop", "lead", "ui", "waits for the next turn")
    assert _hook(event, TEAM) == ""
    assert len(teambus.unread("shop", "ui")) == 1


def test_the_python_path_is_always_quoted_for_cmd():
    assert agyplugin._cmd_word(r"C:\py\python.exe") == r'"C:\py\python.exe"'
    assert agyplugin._cmd_word(r"C:\a&b\python.exe") == r'"C:\a&b\python.exe"'


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe")
def test_a_folder_name_cmd_would_read_runs_as_written(tmp_path):
    folder = tmp_path / "a&b(c)^%OS%"
    folder.mkdir()
    tool = folder / "tool.cmd"
    tool.write_text("@echo ran %1\r\n", encoding="ascii")
    done = subprocess.run(f"{agyplugin._cmd_word(str(tool))} ok", shell=True, capture_output=True, text=True,
                          timeout=30, check=False)
    assert done.stdout.strip() == "ran ok", done.stdout + done.stderr


# --- the PreToolUse hook: the team's own tools without a prompt -----------------------------------

def _pretool(event, environ=None) -> dict:
    out = io.StringIO()
    text = event if isinstance(event, str) else json.dumps(event)
    assert agyplugin.pretool(io.StringIO(text), out, environ if environ is not None else {}) == 0
    return json.loads(out.getvalue())


def _call(tool, server="tabmancer_tabmancer", quoted=False):
    args = {"ServerName": server, "ToolName": tool, "Arguments": {}}
    if quoted:  # as the transcript writes them: each value as JSON text
        args = {key: json.dumps(value) for key, value in args.items()}
    return {"toolCall": {"id": "c1", "name": "mcp_tool", "args": args}, "stepIdx": 14}


@pytest.mark.parametrize("tool", sorted(agyplugin.TEAM_TOOLS))
@pytest.mark.parametrize("server", ["tabmancer_tabmancer", "tabmancer"])
def test_a_members_team_tools_are_allowed(tool, server):
    assert _pretool(_call(tool, server), TEAM)["decision"] == "allow"
    assert _pretool(_call(tool, server, quoted=True), TEAM)["decision"] == "allow"


@pytest.mark.parametrize(("event", "environ"), [
    (_call("team_request"), TEAM),  # what a lead starts or adds is always asked of you
    (_call("team_who"), {}),  # not a team session
    (_call("team_who"), {"TABMANCER_TEAM": "shop"}),
    (_call("team_who", server="trello"), TEAM),  # another server's tool, even with our name
    (_call("list_boards", server="trello"), TEAM),
    (_call("team_who", server="tabmancer_other"), TEAM),
    ({"toolCall": {"name": "run_command", "args": {"CommandLine": "ping -n 20 127.0.0.1"}}}, TEAM),
])
def test_anything_else_gets_no_opinion(event, environ):
    assert _pretool(event, environ) == {"decision": ""}


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '{"toolCall": null}', '{"toolCall": {"args": 3}}',
                                  '{"toolCall": {"args": {"ServerName": 1, "ToolName": "team_who"}}}',
                                  '{"toolCall": {"args": "{broken"}}'])
def test_bad_input_gets_no_opinion(text):
    assert _pretool(text, TEAM) == {"decision": ""}


def test_a_decision_that_cannot_be_reached_is_no_opinion(monkeypatch):
    def boom(event, environ):
        raise RuntimeError("anything")

    monkeypatch.setattr(agyplugin, "tool_decision", boom)
    assert _pretool(_call("team_who"), TEAM) == {"decision": ""}


def test_the_team_tools_are_the_ones_claude_members_run_unasked():
    from tabmancer import teams

    assert set(teams.MCP_ALLOWED) == agyplugin.TEAM_TOOLS and "team_request" not in agyplugin.TEAM_TOOLS


def test_the_pretool_hook_is_in_the_plugin_for_mcp_calls_only():
    [group] = json.loads(agyplugin.files()["hooks.json"])["tabmancer-tools"]["PreToolUse"]
    assert group["matcher"] == "mcp_tool|call_mcp_tool"
    [handler] = group["hooks"]
    assert handler["command"].endswith("-I -m tabmancer.agyplugin hook pretool")
    assert handler["timeout"] == agyplugin.PRETOOL_TIMEOUT == 5


def test_the_pretool_command_as_written_runs_through_cmd():
    """The seam: hooks.json's command string, run the way agy runs it, with agy's env."""
    [group] = json.loads(agyplugin.files()["hooks.json"])["tabmancer-tools"]["PreToolUse"]
    command = group["hooks"][0]["command"]
    for environ, expected in (({**os.environ, **TEAM}, "allow"),
                              ({k: v for k, v in os.environ.items() if not k.startswith("TABMANCER_")}, "")):
        done = subprocess.run(command, shell=True, input=json.dumps(_call("team_send", quoted=True)),
                              capture_output=True, text=True, encoding="utf-8", env=environ, timeout=60,
                              check=False)
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout)["decision"] == expected
