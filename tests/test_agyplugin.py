"""tabmancer's agy plugin: the Stop hook (quota notes, messages handed over) and install/remove."""

import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

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
    assert noted is not None and noted > datetime.now(timezone.utc) + timedelta(hours=4)


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
    teambus.install("shop", (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), ["lead", "ui"])
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


def _transcript(conversation: str, first_prompt: str, *later: str, age: float = 0) -> None:
    """An agy transcript as agy writes one (JSON lines, a step's text JSON-escaped)."""
    path = agy.transcript_path(conversation)
    path.parent.mkdir(parents=True, exist_ok=True)
    steps = [{"step_index": 0, "type": "USER_INPUT", "content": f"<USER_REQUEST>\n{first_prompt}"}]
    steps += [{"step_index": i, "type": "RUN_COMMAND", "content": text} for i, text in enumerate(later, 1)]
    path.write_text("".join(json.dumps(step) + "\n" for step in steps), encoding="utf-8")
    if age:
        moment = time.time() - age
        os.utime(path, (moment, moment))


def _listen_output(monkeypatch, capsys) -> str:
    """What the member's listen printed inside agy's sandbox: the bus folder read-only, so [acks off]."""
    def refused(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(teambus, "agent_pid", lambda *a: None)
    with monkeypatch.context() as m:
        m.setattr(teambus, "_atomic_write_json", refused)
        m.setattr(teambus, "_append_jsonl", refused)
        assert teambus.listen_command("shop", "ui", timeout=0.5) == 0
    out = capsys.readouterr().out
    assert teambus.ACKS_OFF in out
    return out


def test_what_the_listen_already_showed_is_marked_read_not_handed_over_again(monkeypatch, capsys):
    # The 2026-11-23 Stop run: TASK-2 came through the listen, MIDTURN during the turn; the hook
    # handed over both, and a model could have done TASK-2 twice.
    task = teambus.send("shop", "lead", "ui", "TASK-2: reply DONE-2")
    shown = _listen_output(monkeypatch, capsys)
    teambus.send("shop", "lead", "ui", "MIDTURN: reply MID-OK")
    _transcript("conv-ui", "[tabmancer:shop-ui] Read your full team role", shown)
    answer = json.loads(_hook({"terminationReason": "END", "executionNum": 0}, TEAM))
    assert "MIDTURN: reply MID-OK" in answer["reason"] and "TASK-2" not in answer["reason"]
    assert "may have shown" not in answer["reason"]  # it could tell
    assert teambus.unread("shop", "ui") == []
    assert task["id"] not in [m["id"] for m in teambus.unread("shop", "ui")]


def test_when_the_listen_showed_them_all_the_turn_ends(monkeypatch, capsys):
    teambus.send("shop", "lead", "ui", "TASK-2: reply DONE-2")
    _transcript("conv-ui", "[tabmancer:shop-ui] go", _listen_output(monkeypatch, capsys))
    assert _hook({"terminationReason": "END", "executionNum": 0}, TEAM) == ""
    assert teambus.unread("shop", "ui") == []


def test_a_shown_line_needs_its_text_too_and_survives_json_escaping():
    at = datetime(2026, 11, 23, 12, 41, 59)
    first = {"id": "a", "at": at, "from": "lead", "text": 'endpoint "pay" is /pay ü <b>'}
    second = {"id": "b", "at": at, "from": "lead", "text": "MIDTURN: reply MID-OK"}  # the same second
    printed = json.dumps({"content": f"{teambus.header(first)}: {first['text']}"}).encode()  # as escaped
    assert agyplugin._printed(first, teambus.header(first)) in printed
    assert agyplugin._printed(second, teambus.header(second)) not in printed  # same header, not shown


@pytest.mark.parametrize("whose", ["another member's", "an old one", "none"])
def test_without_the_members_own_recent_transcript_everything_goes_with_a_caution(monkeypatch, capsys, whose):
    teambus.send("shop", "lead", "ui", "TASK-2: reply DONE-2")
    shown = _listen_output(monkeypatch, capsys)
    if whose == "another member's":
        _transcript("conv-api", "[tabmancer:shop-api] go", shown)
    elif whose == "an old one":
        _transcript("conv-ui", "[tabmancer:shop-ui] go", shown, age=agyplugin.SHOWN_WITHIN + 60)
    answer = json.loads(_hook({"terminationReason": "END", "executionNum": 0}, TEAM))
    assert "TASK-2: reply DONE-2" in answer["reason"]
    assert "Your listen may have shown you some of them already" in answer["reason"]
    assert teambus.unread("shop", "ui") == []


def test_the_hook_command_as_written_runs_through_cmd_and_hands_messages_over(tmp_path):
    """The seam: hooks.json's command string, run the way agy runs it (one string to the shell)."""
    command = json.loads(agyplugin.files()["hooks.json"])["tabmancer-stop"]["Stop"][0]["command"]
    teambus.send("shop", "lead", "ui", "from the real hook")
    # A home of its own: the hook looks through agy's transcripts, and never the real ones here.
    env = {**os.environ, **TEAM, "USERPROFILE": str(tmp_path / "home"), "HOME": str(tmp_path / "home")}
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
        # As _run calls agy: no stdin, its output into a file (a pipe hung on agy 1.5.15), errors too.
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["stderr"] is subprocess.STDOUT
        assert "capture_output" not in kwargs and kwargs["stdout"] not in (None, subprocess.PIPE)
        self.calls.append(list(argv[1:]))
        verb = argv[2]
        if verb == self.fail:
            kwargs["stdout"].write(f"{verb} broke".encode())
            return subprocess.CompletedProcess(argv, 1)
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
        kwargs["stdout"].write(b"ok")
        return subprocess.CompletedProcess(argv, 0)


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
    assert 'Send a message with the team_send tool (to: "<member>", message: "..."), and optionally summary' in role
    assert "send shop <member>" not in role and "--summary" not in role and "who shop" not in role
    assert 'report to it with the team_send tool (to: "lead"' in role
    assert "make it alone" in role and "[acks off], call team_inbox with ack: true" in role
    # agy allows the tools (install put them in its allow list): no first call to make alone.
    agyplugin.allow_tools()
    role = teams.role(team, ui)
    assert "make it alone" not in role and "may ask for approval" not in role
    assert "[acks off], call team_inbox with ack: true" in role


@pytest.mark.parametrize("decide", [0, 3])
def test_an_agy_lead_with_the_plugin_adds_and_brings_back_members_with_team_request(tmp_path, monkeypatch, decide):
    from tabmancer import teams

    folder = tmp_path / "shop"
    folder.mkdir()
    team = teams.new_team("shop", str(folder), "Build checkout",
                          teams.parse_members("lead: plans @agy\nui: screens", lead_ok=True))
    team.decide = decide
    lead = team.members[0]
    bare = teams.role(team, lead)
    assert "team start shop <member>" in bare and "team_request" not in bare  # the shell command, as before
    monkeypatch.setattr(agyplugin, "status", lambda: "installed")
    role = teams.role(team, lead)
    # From agy's sandbox a shell command may not reach tabmancer: the tools do, asked of the user.
    assert 'team_request tool (action: "start", members: ["<member>"])' in role
    assert "team start shop" not in role and "send shop <member>" not in role
    assert "first task with the team_send tool" in role
    if decide:
        assert 'team_request tool (action: "add", lineup:' in role and "team add shop" not in role
    # A Claude lead is not touched by agy's plugin.
    claude = teams.new_team("shop2", str(folder), "Build checkout",
                            teams.parse_members("lead: plans\nui: screens @agy", lead_ok=True))
    assert "team_request" not in teams.role(claude, claude.members[0])


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


# --- agy's allow list: the team's tools without a prompt ------------------------------------------
# (agy 1.5.15 asks before every MCP call whatever a PreToolUse hook answers; its allow list is
# what skips the prompt.) All on the tests' agy home, never the real ~/.gemini.

OURS = [f"mcp(tabmancer_tabmancer/{tool})" for tool in
        ("team_send", "team_inbox", "team_wait", "team_who", "team_status")]
THEIRS = {
    "allowNonWorkspaceAccess": True,
    "model": "Gemini 4 Neon (High)",
    "permissions": {"allow": ["mcp(trello/list_boards)", "mcp(tabmancer_tabmancer/team_inbox)",
                              "command(C:\\Program Files\\KiCad\\kicad-cli.exe)"],
                    "deny": ["mcp(x/y)"]},
    "trustedWorkspaces": ["C:\\Users\\me\\Desktop\\proj", "ü-folder"],
}


def _settings() -> dict:
    return json.loads(agyplugin.settings_file().read_text(encoding="utf-8"))


def _put_settings(value) -> None:
    agyplugin.settings_file().parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    agyplugin.settings_file().write_text(text, encoding="utf-8")


def test_the_allow_list_lives_in_agys_settings_under_the_tests_home():
    assert agyplugin.settings_file() == agy.home() / "settings.json"
    assert "no-agy-home" in str(agyplugin.settings_file())  # conftest's, never the real one


def test_install_adds_only_the_missing_entries_and_keeps_everything_else_in_order(fake_agy):
    _put_settings(THEIRS)
    assert agyplugin.asked_tools() == ["team_send", "team_wait", "team_who", "team_status"]
    done, said = agyplugin.install(fake_agy)
    assert done and "4 added" in said and "team_request still asks" in said
    now = _settings()
    assert list(now) == list(THEIRS)  # every key, in its order
    assert {k: v for k, v in now.items() if k != "permissions"} == {k: v for k, v in THEIRS.items()
                                                                     if k != "permissions"}
    assert now["permissions"]["deny"] == ["mcp(x/y)"]
    allow = now["permissions"]["allow"]
    assert allow[:3] == THEIRS["permissions"]["allow"]  # what was there, first and as it was
    assert sorted(allow[3:]) == sorted(e for e in OURS if e != "mcp(tabmancer_tabmancer/team_inbox)")
    assert len(allow) == len(set(allow)) and not any("team_request" in e for e in allow)
    assert agyplugin.asked_tools() == []
    text = agyplugin.settings_file().read_bytes()
    assert b"\r" not in text and text.endswith(b"}\n") and "ü-folder".encode() in text  # as agy writes it
    before = agyplugin.settings_file().stat().st_mtime_ns
    agyplugin.install(fake_agy)  # nothing missing: the file is not written again
    assert agyplugin.settings_file().stat().st_mtime_ns == before and _settings() == now


def test_remove_takes_away_exactly_what_install_added(fake_agy):
    _put_settings(THEIRS)
    agyplugin.install(fake_agy)
    done, said = agyplugin.remove(fake_agy)
    assert done and "4 entries from agy's allow list" in said
    assert _settings() == THEIRS  # team_inbox was there before (a tab's "always allow"): it stays


def test_a_missing_settings_file_is_made_with_the_entries_alone(fake_agy):
    assert not agyplugin.settings_file().exists()
    assert agyplugin.install(fake_agy)[0]
    assert _settings() == {"permissions": {"allow": OURS}}
    assert not list(agyplugin.settings_file().parent.glob(".settings-*"))  # no temporary file left
    agyplugin.remove(fake_agy)
    assert _settings() == {"permissions": {"allow": []}}


@pytest.mark.parametrize("text", ["{not json", "[]", '{"permissions": []}', '{"permissions": {"allow": "x"}}',
                                  '// agy\n{"permissions": {}}'])
def test_settings_it_cannot_add_to_are_refused_and_nothing_is_installed(fake_agy, text):
    _put_settings(text)
    done, said = agyplugin.install(fake_agy)
    assert not done and said.startswith("Nothing installed:") and "will not rewrite" in said
    assert fake_agy.calls == [] and agyplugin.status() == "missing"
    assert agyplugin.settings_file().read_text(encoding="utf-8") == text  # untouched
    assert agyplugin.asked_tools() == list(agyplugin.TEAM_TOOLS)


def test_a_settings_file_broken_after_install_keeps_remove_from_touching_it(fake_agy):
    agyplugin.install(fake_agy)
    _put_settings("{broken")
    done, said = agyplugin.remove(fake_agy)
    assert done and "Its tools stay in agy's allow list" in said
    assert agyplugin.settings_file().read_text(encoding="utf-8") == "{broken"


def test_remove_without_a_record_takes_the_teams_entries_only(fake_agy):
    agyplugin.install(fake_agy)
    agyplugin._state_path().unlink()  # an install from before tabmancer kept what it added
    _put_settings({"permissions": {"allow": ["mcp(trello/list_boards)", *OURS, "mcp(tabmancer_tabmancer/team_request)"]}})
    agyplugin.remove(fake_agy)
    assert _settings() == {"permissions": {"allow": ["mcp(trello/list_boards)", "mcp(tabmancer_tabmancer/team_request)"]}}


def test_status_says_which_tools_agy_will_ask_for(fake_agy, capsys):
    agyplugin.install(fake_agy)
    _put_settings({"permissions": {"allow": OURS[:3]}})
    assert agyplugin.main(["status"]) == 0
    assert "agy will ask for team_who, team_status" in capsys.readouterr().out


def test_the_team_tools_are_the_ones_claude_members_run_unasked():
    from tabmancer import teams

    assert agyplugin.TEAM_TOOLS == teams.MCP_ALLOWED and "team_request" not in agyplugin.TEAM_TOOLS


def test_the_plugin_has_no_pretool_hook_any_more():
    """agy 1.5.15 asked all the same after a PreToolUse "allow": the allow list does it instead."""
    assert set(json.loads(agyplugin.files()["hooks.json"])) == {"tabmancer-stop"}
    assert not hasattr(agyplugin, "pretool")


def test_agys_output_is_read_from_a_file_even_when_a_child_keeps_its_pipe_open(tmp_path):
    """agy 1.5.15 leaves a process holding its output open: read from a pipe, validate and install
    waited out the 60 s timeout. Here a child that lives on 30 s after its parent exits."""
    import time

    script = tmp_path / "leaves_a_child.py"
    script.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('validated')\n", encoding="utf-8")
    started = time.monotonic()
    code, said = agyplugin._run([sys.executable, "-I", str(script)])
    assert (code, said) == (0, "validated")
    assert time.monotonic() - started < 15
