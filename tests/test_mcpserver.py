"""tabmancer mcp: the team bus over MCP (stdio JSON-RPC), as Claude Code, Antigravity and Codex speak it."""

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tabmancer import cli, mcpserver, teambus, teams
from tabmancer.mcpserver import Identity, Server


def _team(tmp_path: Path, decide: int = 0, lineup: str = "lead: plans\napi: endpoints\nui: screens @agy") -> teams.Team:
    folder = tmp_path / "shop"
    folder.mkdir(exist_ok=True)
    team = teams.new_team("shop", str(folder), "Build checkout", teams.parse_members(lineup, lead_ok=True),
                          decide=decide)
    teams.save({"shop": team})
    return team


def _server(member: str = "api", team: str = "shop", run=None) -> tuple[Server, io.StringIO]:
    out = io.StringIO()
    return Server(Identity(team, member), out, **({"run": run} if run else {})), out


def _replies(out: io.StringIO) -> list:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _call(server: Server, out: io.StringIO, name: str, arguments: dict | None = None, msg_id: int = 7) -> dict:
    """One tools/call through the wire, waited for: its result."""
    server.receive(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments or {}}}))
    for thread in list(server.threads):
        thread.join(timeout=30)
    found = [r for r in _replies(out) if isinstance(r, dict) and r.get("id") == msg_id]
    assert found, out.getvalue()
    return found[-1]["result"]


def _text(result: dict) -> str:
    return "\n".join(part["text"] for part in result["content"])


# --- the protocol ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("asked", "answered"), [
    ("2025-11-25", "2025-11-25"),
    ("2025-06-18", "2025-06-18"),  # Codex (rmcp)
    ("2025-03-26", "2025-03-26"),
    ("2024-11-05", "2024-11-05"),
    ("2099-01-01", mcpserver.PROTOCOL_VERSIONS[0]),  # unknown: the newest we speak, the client decides
])
def test_initialize_echoes_a_known_version_else_offers_the_newest(asked, answered):
    server, out = _server()
    server.receive(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": asked, "capabilities": {},
                                          "clientInfo": {"name": "x", "version": "1"}}}))
    reply = _replies(out)[0]
    assert reply["id"] == 1 and reply["result"]["protocolVersion"] == answered
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert reply["result"]["serverInfo"]["name"] == "tabmancer"


def test_notifications_get_no_reply_and_ping_gets_an_empty_result():
    server, out = _server()
    server.receive('{"jsonrpc":"2.0","method":"notifications/initialized"}')
    server.receive('{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":99}}')
    assert out.getvalue() == ""
    server.receive('{"jsonrpc":"2.0","id":"p","method":"ping"}')
    assert _replies(out) == [{"jsonrpc": "2.0", "id": "p", "result": {}}]


def test_bad_json_unknown_methods_and_unknown_tools_are_errors():
    server, out = _server()
    server.receive("{not json")
    server.receive('{"jsonrpc":"2.0","id":2,"method":"sampling/createMessage"}')
    server.receive('{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"rm_rf"}}')
    server.receive('{"id":4,"method":"ping"}')  # no jsonrpc
    server.receive('{"jsonrpc":"2.0","id":5,"result":{}}')  # a reply to nothing of ours: ignored
    codes = [(r["id"], r["error"]["code"]) for r in _replies(out)]
    assert codes == [(None, -32700), (2, -32601), (3, -32602), (4, -32600)]


def test_empty_lists_for_resources_and_prompts_some_clients_ask_for():
    server, out = _server()
    server.receive('{"jsonrpc":"2.0","id":1,"method":"resources/list"}')
    server.receive('{"jsonrpc":"2.0","id":2,"method":"prompts/list"}')
    assert [r["result"] for r in _replies(out)] == [{"resources": []}, {"prompts": []}]


def test_a_batch_is_answered_in_one_line(tmp_path):
    _team(tmp_path)
    server, out = _server()
    server.receive(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "team_who", "arguments": {}}},
    ]))
    for thread in list(server.threads):
        thread.join(timeout=30)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    batch = json.loads(lines[0])
    assert [r["id"] for r in batch] == [1, 2] and "api (you)" in batch[1]["result"]["content"][0]["text"]


def test_tools_list_schemas_are_objects_and_never_ask_who_calls():
    server, out = _server()
    server.receive('{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    tools = _replies(out)[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["team_send", "team_inbox", "team_wait", "team_who", "team_status",
                                          "team_request"]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object" and tool["description"]
        assert not {"from", "sender", "member", "team"} & set(tool["inputSchema"]["properties"])


# --- who calls ------------------------------------------------------------------------------------

def test_no_team_identity_makes_every_tool_say_so(monkeypatch):
    monkeypatch.delenv("TABMANCER_TEAM", raising=False)
    monkeypatch.delenv("TABMANCER_MEMBER", raising=False)
    out = io.StringIO()
    server = Server(Identity.find(), out)
    result = _call(server, out, "team_send", {"to": "ui", "message": "hi"})
    assert result["isError"] and "not a tabmancer team session" in _text(result)
    assert teambus.messages("shop") == []


def test_identity_comes_from_arguments_else_the_environment(monkeypatch):
    monkeypatch.setenv("TABMANCER_TEAM", "shop")
    monkeypatch.setenv("TABMANCER_MEMBER", "ui")
    assert Identity.find() == Identity("shop", "ui")
    assert Identity.find("other", "api") == Identity("other", "api")


# --- the tools ------------------------------------------------------------------------------------

def test_send_goes_out_as_the_caller_and_only_to_a_member(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    result = _call(server, out, "team_send", {"to": "ui", "message": "endpoint ready", "summary": "ready"})
    assert not result["isError"] and "Sent to ui" in _text(result)
    [message] = teambus.messages("shop")
    assert (message["from"], message["to"], message["text"], message["summary"]) == (
        "api", "ui", "endpoint ready", "ready")
    nobody = _call(server, out, "team_send", {"to": "qa", "message": "x"}, msg_id=8)
    assert nobody["isError"] and "No member qa" in _text(nobody)
    me = _call(server, out, "team_send", {"to": "api", "message": "x"}, msg_id=9)
    assert me["isError"] and "That is you" in _text(me)
    empty = _call(server, out, "team_send", {"to": "ui", "message": "  "}, msg_id=10)
    assert empty["isError"] and len(teambus.messages("shop")) == 1


def test_inbox_peeks_unless_asked_to_ack(tmp_path):
    _team(tmp_path)
    teambus.send("shop", "lead", "api", "first task")
    server, out = _server("api")
    peek = _call(server, out, "team_inbox")
    assert "from lead]: first task" in _text(peek) and "still unread" in _text(peek)
    assert len(teambus.unread("shop", "api")) == 1
    taken = _call(server, out, "team_inbox", {"ack": True}, msg_id=8)
    assert "first task" in _text(taken)
    assert teambus.unread("shop", "api") == []
    assert "No messages for api" in _text(_call(server, out, "team_inbox", msg_id=9))


def test_wait_returns_a_message_that_arrives_and_marks_it_read_after_the_answer(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    timer = threading.Timer(0.6, lambda: teambus.send("shop", "ui", "api", "need a field"))
    timer.start()
    began = time.monotonic()
    result = _call(server, out, "team_wait", {"timeout": 20})
    assert time.monotonic() - began < 15
    assert "from ui]: need a field" in _text(result)
    assert teambus.unread("shop", "api") == []


def test_wait_times_out_and_rejects_a_bad_timeout(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    assert "[timeout]" in _text(_call(server, out, "team_wait", {"timeout": 0}))
    bad = _call(server, out, "team_wait", {"timeout": "soon"}, msg_id=8)
    assert bad["isError"]


def test_a_cancelled_wait_ends_without_an_answer_and_marks_nothing(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    server.receive(json.dumps({"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                               "params": {"name": "team_wait", "arguments": {"timeout": 60}}}))
    time.sleep(0.3)
    server.receive('{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":41}}')
    for thread in list(server.threads):
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in server.threads)
    assert out.getvalue() == ""
    assert teambus.listener("shop", "api") is None  # its claim went with it


def test_a_closed_stdin_ends_a_running_wait(tmp_path):
    _team(tmp_path)
    out = io.StringIO()
    server = Server(Identity("shop", "api"), out)
    call = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "team_wait", "arguments": {"timeout": 60}}}) + "\n"
    began = time.monotonic()
    server.serve(io.StringIO(call))  # EOF right after the call
    assert time.monotonic() - began < 10
    assert "[cancelled]" in out.getvalue()


def test_a_newer_listen_supersedes_the_wait(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    box = {}
    thread = threading.Thread(target=lambda: box.update(r=_call(server, out, "team_wait", {"timeout": 30})))
    thread.start()
    deadline = time.monotonic() + 15
    while not teambus._read_claim("shop", "api").get("token") and time.monotonic() < deadline:
        time.sleep(0.02)  # the wait has claimed the member: only then is a newer claim newer
    teambus._write_claim("shop", "api", {**teambus._pid_info(os.getpid()), "token": "newer", "since": ""})
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert "[superseded]" in _text(box["r"])


def test_who_and_status_name_the_members_and_what_waits(tmp_path):
    _team(tmp_path)
    teambus.send("shop", "lead", "ui", "build the screens", summary="screens")
    teambus.send("shop", "ui", "lead", "on it")
    server, out = _server("api")
    who = _text(_call(server, out, "team_who"))
    assert who.splitlines()[1:] == ["lead (lead): not seen", "api (you): not seen", "ui: not seen"]
    status = _text(_call(server, out, "team_status", msg_id=8))
    assert "2 message(s)" in status and "ui (agy, gemini-4-neon-high): 1 unread, last sent" in status
    assert "api (claude): 0 unread" in status
    assert "lead → ui: screens" in status and "ui → lead: on it" in status


def test_a_running_listener_shows_in_who(tmp_path):
    _team(tmp_path)
    teambus._write_claim("shop", "ui", {**teambus._pid_info(os.getpid()), "token": "t", "since": ""})
    server, out = _server("api")
    assert "ui: listening" in _text(_call(server, out, "team_who"))


# --- requests: an agy lead's add and start --------------------------------------------------------

class Recorder:
    def __init__(self, code: int = 0, said: str = "Started api."):
        self.calls: list[list[str]] = []
        self.code, self.said = code, said

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.code, self.said, "")


def test_only_the_lead_may_request(tmp_path):
    _team(tmp_path, decide=3)
    run = Recorder()
    server, out = _server("api", run=run)
    result = _call(server, out, "team_request", {"action": "start", "members": ["ui"]})
    assert result["isError"] and "Only the team's lead (lead)" in _text(result)
    assert run.calls == []


def test_a_lead_brings_members_back_and_the_request_is_logged(tmp_path):
    _team(tmp_path)
    run = Recorder()
    server, out = _server("lead", run=run)
    result = _call(server, out, "team_request", {"action": "start", "members": ["api", "ui"]})
    assert not result["isError"] and "Started api." in _text(result)
    assert run.calls[0][-5:] == ["team", "start", "shop", "api", "ui"]
    log = teambus._read_jsonl(teambus.team_dir("shop") / mcpserver.REQUESTS_FILE)
    assert [(r["from"], r["action"], r.get("done", False)) for r in log] == [
        ("lead", "start", False), ("lead", "start", True)]
    unknown = _call(server, out, "team_request", {"action": "start", "members": ["qa"]}, msg_id=8)
    assert unknown["isError"] and len(run.calls) == 1


def test_add_only_when_the_team_is_the_leads_to_make(tmp_path):
    _team(tmp_path)  # a line-up the user gave
    run = Recorder()
    server, out = _server("lead", run=run)
    given = _call(server, out, "team_request", {"action": "add", "lineup": "qa: tests"})
    assert given["isError"] and "ask them" in _text(given) and run.calls == []

    _team(tmp_path, decide=3)
    added = _call(server, out, "team_request", {"action": "add", "lineup": "qa: tests @agy"}, msg_id=8)
    assert not added["isError"]
    assert run.calls[0][-5:] == ["team", "add", "shop", "--", "qa: tests @agy"]


def test_a_failed_request_is_an_error_with_what_tabmancer_said(tmp_path):
    _team(tmp_path)
    server, out = _server("lead", run=Recorder(code=1, said="No member qa."))
    result = _call(server, out, "team_request", {"action": "start"})
    assert result["isError"] and "failed (1): No member qa." in _text(result)


def test_another_teams_request_is_impossible(tmp_path):
    """The team is the caller's identity, never an argument: a lead of shop cannot reach another team."""
    _team(tmp_path)
    run = Recorder()
    server, out = _server("lead", run=run)
    result = _call(server, out, "team_request", {"action": "start", "team": "other"})
    assert not result["isError"] and run.calls[0][-3:] == ["team", "start", "shop"]


# --- over real pipes, as each client opens ---------------------------------------------------------

CLIENTS = {
    "claude-code": {"protocolVersion": "2025-06-18",
                    "capabilities": {"roots": {}, "elicitation": {}},
                    "clientInfo": {"name": "claude-code", "version": "2.1.327"}},
    "antigravity": {"protocolVersion": "2025-03-26", "capabilities": {"roots": {"listChanged": True}},
                    "clientInfo": {"name": "antigravity-cli", "version": "1.5.0"}},
    "codex": {"protocolVersion": "2025-06-18",
              "capabilities": {"elicitation": {}},
              "clientInfo": {"name": "codex-mcp-client", "title": "Codex", "version": "0.9.0"}},
}


class Pipe:
    """A real `python -m tabmancer.mcpserver` on stdin/stdout, as a client starts it."""

    def __init__(self, member: str, env_identity: bool = False):
        env = {k: v for k, v in os.environ.items() if k not in ("TABMANCER_TEAM", "TABMANCER_MEMBER")}
        args = [sys.executable, "-m", "tabmancer.mcpserver"]
        if env_identity:
            env.update(TABMANCER_TEAM="shop", TABMANCER_MEMBER=member)
        else:
            args += ["--team", "shop", "--member", member]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, text=True, encoding="utf-8", bufsize=1)
        self.next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, **({"params": params} if params else {})})
        line = self.proc.stdout.readline()
        assert line, self.proc.stderr.read()
        reply = json.loads(line)
        assert reply["id"] == self.next_id
        return reply

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def close(self) -> int:
        self.proc.stdin.close()
        return self.proc.wait(timeout=20)


@pytest.mark.parametrize("client", sorted(CLIENTS))
def test_each_clients_handshake_and_a_message_over_real_pipes(tmp_path, client):
    _team(tmp_path)
    api = Pipe("api", env_identity=client == "antigravity")  # agy's plugin server: identity from the env
    ui = Pipe("ui")
    try:
        for pipe in (api, ui):
            hello = pipe.request("initialize", CLIENTS[client])
            assert hello["result"]["protocolVersion"] == CLIENTS[client]["protocolVersion"]
            pipe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert len(api.request("tools/list")["result"]["tools"]) == len(mcpserver.TOOLS)
        sent = api.request("tools/call", {"name": "team_send", "arguments": {"to": "ui", "message": "ünïcode ok"}})
        assert sent["result"]["isError"] is False
        got = ui.request("tools/call", {"name": "team_wait", "arguments": {"timeout": 10}})
        assert "from api]: ünïcode ok" in got["result"]["content"][0]["text"]
    finally:
        codes = (api.close(), ui.close())
    assert codes == (0, 0)
    assert teambus.unread("shop", "ui") == []


def test_a_waiting_member_shows_as_listening_the_whole_time(tmp_path):
    """team_wait holds the same listener claim as `team listen`, without a gap, and drops it after."""
    _team(tmp_path)
    server, _out = _server("api")
    server.receive(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                               "params": {"name": "team_wait", "arguments": {"timeout": 60}}}))
    time.sleep(0.4)
    seen = []
    for _ in range(30):  # 1.5 s: longer than any step a wait could have taken
        seen.append(teambus.listener("shop", "api") is not None
                    and teambus.read_presence("shop").get("api", {}).get("listening") is True)
        time.sleep(0.05)
    server.receive('{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":5}}')
    for thread in list(server.threads):
        thread.join(timeout=10)
    assert all(seen)
    assert teambus.listener("shop", "api") is None


def test_status_names_the_model_an_agy_member_runs_on_by_quota(tmp_path):
    from datetime import datetime, timedelta, timezone

    from tabmancer import agy

    _team(tmp_path)
    agy.note_out_of_quota("gemini-4-neon-high", datetime.now(timezone.utc) + timedelta(hours=2))
    server, out = _server("api")
    status = _text(_call(server, out, "team_status"))
    ui = next(line for line in status.splitlines() if line.strip().startswith("ui ("))
    assert ui.strip().startswith("ui (agy, gemini-4-hydrogen-high)")  # the model it would run on now: never Claude


def test_a_wait_reports_progress_when_the_client_asks_for_it(tmp_path, monkeypatch):
    """A client that resets its tool timeout on progress keeps a long wait alive."""
    monkeypatch.setattr(mcpserver, "PROGRESS_EVERY", 0.2)
    _team(tmp_path)
    server, out = _server("api")
    server.receive(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "team_wait", "arguments": {"timeout": 1},
                                          "_meta": {"progressToken": "tok"}}}))
    for thread in list(server.threads):
        thread.join(timeout=10)
    replies = _replies(out)
    progress = [r for r in replies if r.get("method") == "notifications/progress"]
    assert progress and all(r["params"]["progressToken"] == "tok" for r in progress)
    assert "[timeout]" in replies[-1]["result"]["content"][0]["text"]

    quiet, said = _server("api")  # no token, no progress
    _call(quiet, said, "team_wait", {"timeout": 0.5})
    assert not any(r.get("method") for r in _replies(said))


# --- what must never end the server ---------------------------------------------------------------

@pytest.mark.parametrize("bad_id", [None, 1.5, True, {"a": 1}, [1]])
def test_an_id_that_is_not_a_string_or_integer_is_an_invalid_request(bad_id):
    server, out = _server()
    server.receive(json.dumps({"jsonrpc": "2.0", "id": bad_id, "method": "ping"}))
    server.receive(json.dumps({"jsonrpc": "2.0", "id": bad_id, "method": "tools/call",
                               "params": {"name": "team_who", "arguments": {}}}))
    server.receive('{"jsonrpc":"2.0","id":9,"method":"ping"}')
    assert [(r["id"], r.get("error", {}).get("code")) for r in _replies(out)] == [
        (None, -32600), (None, -32600), (9, None)]
    assert server.threads == []


def test_a_message_that_breaks_the_server_is_an_internal_error_and_reading_goes_on(monkeypatch):
    server, out = _server()
    real = server.answer
    calls = iter([RuntimeError("boom")])

    def answer(message, threaded):
        problem = next(calls, None)
        if problem is not None:
            raise problem
        return real(message, threaded)

    monkeypatch.setattr(server, "answer", answer)
    server.serve(io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'))
    replies = _replies(out)
    assert replies[0]["error"]["code"] == mcpserver.INTERNAL_ERROR and "boom" in replies[0]["error"]["message"]
    assert replies[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_a_wait_in_a_batch_does_not_stop_the_reading_and_can_be_cancelled(tmp_path):
    _team(tmp_path)
    server, out = _server("api")
    began = time.monotonic()
    server.receive(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "team_wait", "arguments": {"timeout": 60}}},
    ]))
    assert time.monotonic() - began < 1  # receive came back at once
    time.sleep(0.3)
    server.receive('{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2}}')
    for thread in list(server.threads):
        thread.join(timeout=10)
    assert time.monotonic() - began < 10
    assert _replies(out) == [[{"jsonrpc": "2.0", "id": 1, "result": {}}]]  # the cancelled wait: no answer


def test_text_that_cannot_be_utf8_is_an_error_answer_not_a_gone_client():
    server, out = _server()
    assert server.write(server.result(3, {"content": [{"type": "text", "text": "a\ud800b"}]})) is False
    assert server.write(server.result(4, {})) is True  # still talking
    replies = _replies(out)
    assert replies[0]["id"] == 3 and replies[0]["error"]["code"] == mcpserver.INTERNAL_ERROR
    assert replies[1] == {"jsonrpc": "2.0", "id": 4, "result": {}}


def test_a_stray_print_cannot_spoil_the_wire(tmp_path):
    _team(tmp_path)
    code = ("import sys; from tabmancer import mcpserver as m; real = m.Server.tool_team_who\n"
            "def noisy(self, *a):\n    print('stray print'); return real(self, *a)\n"
            "m.Server.tool_team_who = noisy\nraise SystemExit(m.main(['--team', 'shop', '--member', 'api']))\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8")
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "team_who", "arguments": {}}}
    out, err = proc.communicate(json.dumps(call) + "\n", timeout=30)
    assert [json.loads(line)["id"] for line in out.splitlines()] == [1]
    assert "stray print" in err


def test_a_lead_adds_no_codex_member_to_a_team_without_one(tmp_path):
    _team(tmp_path, decide=3)
    run = Recorder()
    server, out = _server("lead", run=run)
    refused = _call(server, out, "team_request", {"action": "add", "lineup": "rev: reviews @codex"})
    assert refused["isError"] and "no Codex member" in _text(refused) and run.calls == []

    _team(tmp_path, decide=3, lineup="lead: plans; api: endpoints; rev: reviews @codex")
    more = _call(server, out, "team_request", {"action": "add", "lineup": "rev2: reviews too @codex"}, msg_id=8)
    assert not more["isError"] and run.calls[0][-1] == "rev2: reviews too @codex"


def test_a_lineup_after_the_dashes_reaches_team_add_whole(monkeypatch, tmp_path):
    got: list = []
    monkeypatch.setattr(cli, "add_to_team", lambda team, spec: got.append((team, spec)) or 0)
    monkeypatch.setattr(cli.claudeupdate, "before_opening", lambda say: None)
    monkeypatch.setattr(sys, "argv", ["tabmancer", "team", "add", "shop", "--", "--help: x @agy"])
    with pytest.raises(SystemExit):
        cli.main()
    assert got == [("shop", "--help: x @agy")]


def test_who_and_status_say_how_a_codex_members_last_delivery_went(tmp_path, capsys):
    _team(tmp_path, lineup="lead: plans\napi: endpoints\nrev: reviews @codex")
    teambus.write_presence("shop", "rev", cli="codex", pid=os.getpid())
    teambus.update_presence("shop", "rev", delivery="codex queue failed (1): no thread")
    server, out = _server("api")
    who = _text(_call(server, out, "team_who"))
    assert "rev: running, delivery: codex queue failed (1): no thread" in who
    assert "api (you): not seen" in who  # no note where nothing was delivered
    status = _text(_call(server, out, "team_status", msg_id=8))
    assert "rev (codex" in status and "delivery: codex queue failed (1): no thread" in status
    teambus.who_command("shop", ["rev", "api"])
    printed = capsys.readouterr().out
    assert "rev (shop-rev) · codex · open · delivery: codex queue failed (1): no thread" in printed
    assert "api (shop-api) · ? · closed\n" in printed
    # Its own words kept, one line, and cut when long.
    assert teambus.delivery_words({"delivery": "queued"}) == "delivery: queued"
    assert teambus.delivery_words({"delivery": "delivery failed: disk gone"}) == "delivery failed: disk gone"
    assert teambus.delivery_words({"delivery": "a\nb"}) == "delivery: a b"
    assert len(teambus.delivery_words({"delivery": "x" * 500})) == teambus.DELIVERY_MOST
    assert teambus.delivery_words(None) == teambus.delivery_words({}) == ""


def test_a_codex_lead_may_request_and_adds_codex_only_where_there_is_one(tmp_path):
    _team(tmp_path, decide=3, lineup="lead: plans @codex")  # Codex is on the team already: the lead
    run = Recorder()
    server, out = _server("lead", run=run)
    added = _call(server, out, "team_request", {"action": "add", "lineup": "rev: reviews @codex; ui: screens @agy"})
    assert not added["isError"] and run.calls[0][-5:] == ["team", "add", "shop", "--",
                                                          "rev: reviews @codex; ui: screens @agy"]
    back = _call(server, out, "team_request", {"action": "start", "members": ["lead"]}, msg_id=8)
    assert not back["isError"] and run.calls[1][-3:] == ["start", "shop", "lead"]
