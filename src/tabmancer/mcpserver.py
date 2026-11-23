"""`tabmancer mcp`: the team bus as an MCP server (stdio), for Claude Code, Antigravity and Codex.

One JSON-RPC 2.0 message per line on stdin and stdout, written by hand (no MCP library). A member
reaches its team through tools instead of shell commands, so it works where a shell cannot reach
tabmancer (agy's terminal sandbox: agy starts its MCP servers outside it).

Who calls is never a tool argument: it is --team/--member, else the TABMANCER_TEAM and
TABMANCER_MEMBER environment a member is launched with. Without one, every tool says so, so a
server registered for every session (agy's plugin) is harmless outside a team.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import IO, Any

from . import __version__, teambus

SERVER_NAME = "tabmancer"
# Newest first: the client's version is echoed when known, else the newest is offered.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
WAIT_DEFAULT = 300.0
PROGRESS_EVERY = 20.0  # seconds between progress notifications while a wait runs (when asked for)
WAIT_MOST = 3300.0  # 55 minutes, as `team listen`: also keeps Claude's 1h prompt cache warm
REQUEST_TIMEOUT = 120
REQUESTS_FILE = "requests.jsonl"
RECENT = 8  # messages team_status shows
HEADLINE = 90

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "tabmancer's team bus: message the other members of your team, whatever CLI they run on. "
    "team_wait blocks until a message for you arrives (use it when you have nothing else to do); "
    "team_inbox reads what is waiting without blocking.")


def _schema(properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


TOOLS: list[dict] = [
    {"name": "team_send",
     "description": "Send a message to another member of your team (or 'all'). You are the sender.",
     "inputSchema": _schema({
         "to": {"type": "string", "description": "a member's name, or 'all'"},
         "message": {"type": "string"},
         "summary": {"type": "string", "description": "optional short gist"}}, ("to", "message"))},
    {"name": "team_inbox",
     "description": "The messages waiting for you, without blocking. ack=true marks the ones returned as read.",
     "inputSchema": _schema({"ack": {"type": "boolean", "default": False}})},
    {"name": "team_wait",
     "description": ("Block until a message for you arrives (or the timeout passes), return it and mark it "
                     "read. Use it whenever you wait with nothing else to do; never poll in a loop."),
     "inputSchema": _schema({"timeout": {"type": "number", "minimum": 0, "maximum": WAIT_MOST,
                                         "default": WAIT_DEFAULT, "description": "seconds"}})},
    {"name": "team_who",
     "description": "Your team's members, and which of them are running and listening.",
     "inputSchema": _schema()},
    {"name": "team_status",
     "description": "What each member has waiting, the team's latest messages and its open requests.",
     "inputSchema": _schema()},
    {"name": "team_request",
     "description": ("Lead only: add members to the team (action 'add', a line-up such as "
                     "\"api: endpoints @sonnet; ui: screens @agy\"; only when the team is yours to make) or "
                     "bring closed members back (action 'start', with their names; none: every closed one)."),
     "inputSchema": _schema({
         "action": {"type": "string", "enum": ["add", "start"]},
         "lineup": {"type": "string", "description": "for add: 'name: area @model @effort; ...'"},
         "members": {"type": "array", "items": {"type": "string"}, "description": "for start"}},
         ("action",))},
]


class Call(threading.Event):
    """A running tools/call: set when the client cancels it; carries its progress token, if any."""

    def __init__(self, progress: Any = None) -> None:
        super().__init__()
        self.progress = progress


class ToolError(Exception):
    """A tool that cannot do what was asked: its text goes back as an isError result."""


@dataclass
class Identity:
    team: str = ""
    member: str = ""

    @classmethod
    def find(cls, team: str = "", member: str = "") -> Identity:
        return cls(team or os.environ.get("TABMANCER_TEAM", ""), member or os.environ.get("TABMANCER_MEMBER", ""))

    def __bool__(self) -> bool:
        return bool(self.team and self.member)


@dataclass
class TeamInfo:
    members: list[str] = field(default_factory=list)
    lead: str = ""
    decide: int = 0
    since: datetime | None = None
    saved: Any = None  # the teams.Team, when the team is saved


def team_info(team: str) -> TeamInfo:
    """What the team's saved file says (teams.load), else what the bus folder knows (team.json)."""
    try:
        from . import teams

        saved = teams.load().get(team)
    except Exception:  # noqa: BLE001  (a broken teams file still leaves the bus usable)
        saved = None
    if saved is not None:
        from .teams import _made

        lead = saved.lead
        return TeamInfo([m.name for m in saved.members], lead.name if lead else "", saved.decide,
                        _made(saved.created), saved)
    created, members = teambus._read_team_file(team)
    return TeamInfo(members, since=created)


def _standings(info: TeamInfo) -> dict[str, str]:
    """name -> ' (agy, gemini-4-hydrogen-high, listening since 14:02, quota 40% of 5 hours)': the
    words the team monitor shows (viewmodel's bus and model, agy.standing's quota)."""
    from . import agy, viewmodel

    team = info.saved
    presence = teambus.read_presence(team.name)
    out = {}
    for member in team.members:
        model = viewmodel.member_model(member, None)[0]
        bus = viewmodel._presence_of(presence, team, member)
        parts = [member.cli] + ([model] if model else [])
        if bus is not None:
            state = "listening" if bus["listening"] else "running" if bus["runner"] else ""
            if state:
                parts.append(state + (f" since {bus['since']:%H:%M}" if bus["since"] else ""))
        delivery = teambus.delivery_words(presence.get(member.name)
                                          or presence.get(teambus.normalize_member(team.name, member.name)))
        if delivery:
            parts.append(delivery)
        if member.cli == "agy" and model:
            quota = agy.standing(model)
            if quota["back"] is not None:
                parts.append(f"out of quota until {quota['back']:%H:%M}")
            elif quota["left"] is not None:
                parts.append(f"quota {quota['left']:.0f}% of {quota['span'] or 'its limit'}")
        out[member.name] = f" ({', '.join(parts)})"
    return out


def _good_id(msg_id: object) -> bool:
    """A request id MCP takes: a string or an integer (never null, a bool, a float or an object)."""
    return isinstance(msg_id, str) or (isinstance(msg_id, int) and not isinstance(msg_id, bool))


def _stamp(moment: object) -> str:
    return moment.strftime("%H:%M:%S") if isinstance(moment, datetime) else ""


def _render(messages: list[dict]) -> str:
    return "\n".join(f"[{_stamp(m.get('at'))} from {m.get('from')}]: {m.get('text')}" for m in messages)


def _headline(message: dict) -> str:
    text = " ".join(str(message.get("summary") or message.get("text") or "").split())
    if len(text) > HEADLINE:
        text = text[:HEADLINE - 1] + "…"
    return f"{_stamp(message.get('at'))} {message.get('from')} → {message.get('to')}: {text}"


def _requests_path(team: str):
    return teambus.team_dir(team) / REQUESTS_FILE


class Server:
    """One client's session: answers its messages, runs each tool call on a thread of its own."""

    def __init__(self, identity: Identity, out: IO[str],
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.me = identity
        self.out = out
        self.run = run
        self.lock = threading.Lock()  # one line on the wire at a time
        # cancels and threads, touched by the reader, the call threads and serve's end. Not the
        # wire's lock: a write blocked on a full pipe must not stop the reader taking a cancel.
        self.calls_lock = threading.Lock()
        self.closed = threading.Event()
        self.cancels: dict[Any, threading.Event] = {}
        self.threads: list[threading.Thread] = []

    # --- the wire ----------------------------------------------------------------------------

    def write(self, message: dict | list) -> bool:
        """Send a message (or a batch's answers). False when it did not go out as it was."""
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            line.encode("utf-8")
        except UnicodeEncodeError:  # a lone surrogate (a \ud800 escape read in): no UTF-8 for it
            said = "The answer held text that cannot be sent as UTF-8."
            ids = [m.get("id") for m in (message if isinstance(message, list) else [message]) if isinstance(m, dict)]
            errors = [self.error(i, INTERNAL_ERROR, said) for i in ids] or [self.error(None, INTERNAL_ERROR, said)]
            self._send(json.dumps(errors if isinstance(message, list) else errors[0], separators=(",", ":")))
            return False
        return self._send(line)

    def _send(self, line: str) -> bool:
        with self.lock:
            try:
                self.out.write(line + "\n")
                self.out.flush()
            except (OSError, ValueError):  # the client went away
                return False
        return True

    @staticmethod
    def error(msg_id: Any, code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    @staticmethod
    def result(msg_id: Any, value: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": value}

    def serve(self, stdin: IO[str]) -> None:
        try:
            for line in stdin:
                if line.strip():
                    try:
                        self.receive(line)
                    except Exception as problem:  # noqa: BLE001  (one bad message never ends the server)
                        self.write(self.error(None, INTERNAL_ERROR, f"Internal error: {problem}"))
        finally:
            self.closed.set()
            with self.calls_lock:
                threads = list(self.threads)
            for thread in threads:
                thread.join(timeout=3)

    def receive(self, line: str) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self.write(self.error(None, PARSE_ERROR, "Parse error"))
            return
        if isinstance(message, list):  # a batch (2025-03-26): answered together
            if not message:
                self.write(self.error(None, INVALID_REQUEST, "Empty batch"))
                return
            # On a thread of its own, as a lone call is: a team_wait in it must not stop the reading
            # (of its cancel, say). Its answers go out together once all are in.
            thread = threading.Thread(target=self._batch_thread, args=(message,), daemon=True)
            with self.calls_lock:
                self.threads.append(thread)
            thread.start()
            return
        answer = self.answer(message, threaded=True)
        if answer is not None:
            self.write(answer)

    def _batch_thread(self, batch: list) -> None:
        try:
            answers = []
            for message in batch:
                try:
                    answer = self.answer(message, threaded=False)
                except Exception as problem:  # noqa: BLE001
                    answer = self.error(None, INTERNAL_ERROR, f"Internal error: {problem}")
                if answer is not None:
                    answers.append(answer)
            if answers:
                self.write(answers)
        finally:
            with self.calls_lock:
                if threading.current_thread() in self.threads:
                    self.threads.remove(threading.current_thread())

    def answer(self, message: object, threaded: bool) -> dict | None:
        """The reply to one message, None for a notification or a call answered on its thread."""
        if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
            return None  # a reply to a request of ours: we send none
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str) or ("id" in message and not _good_id(message["id"]))):
            msg_id = message.get("id") if isinstance(message, dict) else None
            return self.error(msg_id if _good_id(msg_id) else None, INVALID_REQUEST, "Invalid request")
        method = message.get("method")
        params = message.get("params") or {}
        if "id" not in message:  # a notification
            if method == "notifications/cancelled" and isinstance(params, dict):
                with self.calls_lock:
                    event = self.cancels.get(params.get("requestId"))
                if event is not None:
                    event.set()
            return None
        msg_id = message["id"]
        if not isinstance(params, dict):
            return self.error(msg_id, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return self.result(msg_id, self.initialize(params))
        if method == "ping":
            return self.result(msg_id, {})
        if method == "tools/list":
            return self.result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not any(t["name"] == name for t in TOOLS):
                return self.error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return self.error(msg_id, INVALID_PARAMS, "arguments must be an object")
            if not threaded:  # in a batch, on the batch's thread: still cancellable
                cancel = threading.Event()
                with self.calls_lock:
                    self.cancels[msg_id] = cancel
                try:
                    value, after = self.call(name, arguments, cancel)
                finally:
                    with self.calls_lock:
                        self.cancels.pop(msg_id, None)
                if cancel.is_set() and name == "team_wait":
                    return None  # cancelled: no answer, and nothing marked read
                # Any other tool answers even when cancelled: it is quick and what it did (a message
                # sent, a request made) is done, so its answer is the truth, which the client may drop.
                if after is not None:
                    after()
                return self.result(msg_id, value)
            meta = params.get("_meta")
            cancel = Call(meta.get("progressToken") if isinstance(meta, dict) else None)
            thread = threading.Thread(target=self._call_thread, args=(msg_id, name, arguments, cancel), daemon=True)
            with self.calls_lock:
                self.cancels[msg_id] = cancel
                self.threads.append(thread)
            thread.start()
            return None
        if method in ("resources/list", "prompts/list"):  # asked by some clients whatever we offer
            return self.result(msg_id, {method.split("/")[0]: []})
        return self.error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def initialize(self, params: dict) -> dict:
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": INSTRUCTIONS}

    def _call_thread(self, msg_id: Any, name: str, arguments: dict, cancel: threading.Event) -> None:
        try:
            value, after = self.call(name, arguments, cancel)
            if cancel.is_set() and name == "team_wait":
                return  # cancelled: the client wants no answer, and nothing it did not see is marked read
            # Any other tool answers even when cancelled (see answer).
            if self.write(self.result(msg_id, value)) and after is not None:
                after()  # marked read only once the answer is out
        finally:
            with self.calls_lock:
                self.cancels.pop(msg_id, None)
                if threading.current_thread() in self.threads:
                    self.threads.remove(threading.current_thread())

    # --- the tools -----------------------------------------------------------------------------

    def call(self, name: str, arguments: dict, cancel: threading.Event) -> tuple[dict, Callable[[], None] | None]:
        """(the tools/call result, what to do once it is written)."""
        try:
            if not self.me:
                raise ToolError("This is not a tabmancer team session: tabmancer starts each team member with its "
                                "team and name (TABMANCER_TEAM, TABMANCER_MEMBER), and this one has none.")
            text, after = getattr(self, f"tool_{name}")(arguments, cancel)
        except ToolError as problem:
            return {"content": [{"type": "text", "text": str(problem)}], "isError": True}, None
        except Exception as problem:  # noqa: BLE001  (a tool's failure is the caller's to read, not a dead server)
            return {"content": [{"type": "text", "text": f"tabmancer failed: {problem}"}], "isError": True}, None
        return {"content": [{"type": "text", "text": text}], "isError": False}, after

    @staticmethod
    def _text(arguments: dict, key: str, required: bool = False) -> str:
        value = arguments.get(key, "")
        if not isinstance(value, str):
            raise ToolError(f"{key} must be a string.")
        if required and not value.strip():
            raise ToolError(f"{key} is empty.")
        return value.strip()

    def tool_team_send(self, arguments: dict, cancel: threading.Event) -> tuple[str, None]:
        team, me = self.me.team, self.me.member
        to = teambus.normalize_member(team, self._text(arguments, "to", required=True))
        text = self._text(arguments, "message", required=True)
        members = team_info(team).members
        if members and to not in ("*", "all") and to not in members:
            raise ToolError(f"No member {to} on team {team}. Members: {', '.join(members)}.")
        if to == me:
            raise ToolError("That is you: name another member.")
        record = teambus.send(team, me, to, text, summary=self._text(arguments, "summary"))
        return f"Sent to {record['to']} (id {record['id']}).", None

    def tool_team_inbox(self, arguments: dict, cancel: threading.Event) -> tuple[str, Callable[[], None] | None]:
        team, me = self.me.team, self.me.member
        waiting = teambus.unread(team, me, since=team_info(team).since)
        if not waiting:
            return f"No messages for {me}.", None
        ack = arguments.get("ack", False) is True
        ids = [m["id"] for m in waiting]
        note = "" if ack else "\n(still unread: ack=true or team_wait marks them read)"
        return _render(waiting) + note, (lambda: teambus.mark_delivered(team, me, ids)) if ack else None

    def tool_team_wait(self, arguments: dict, cancel: threading.Event) -> tuple[str, Callable[[], None] | None]:
        team, me = self.me.team, self.me.member
        timeout = arguments.get("timeout", WAIT_DEFAULT)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ToolError("timeout must be a number of seconds.")
        token = getattr(cancel, "progress", None)
        began = last = time.monotonic()

        def stopped() -> bool:
            nonlocal last
            if token is not None and time.monotonic() - last >= PROGRESS_EVERY:  # a client that counts
                last = time.monotonic()  # progress as life does not time the wait out
                self.write({"jsonrpc": "2.0", "method": "notifications/progress",
                            "params": {"progressToken": token, "progress": round(last - began),
                                       "message": "waiting for a message"}})
            return cancel.is_set() or self.closed.is_set()

        try:  # one listen, holding the member's listener claim for the whole wait (who shows it listening)
            arrived = teambus.listen(team, me, timeout=min(max(0.0, float(timeout)), WAIT_MOST),
                                     since=team_info(team).since, ack=False, stop=stopped)
        except teambus.Superseded:
            return (f"[superseded] A newer listen for {me} on team {team} is waiting for your messages; it "
                    "delivers them. Do not start another wait."), None
        if arrived:
            ids = [m["id"] for m in arrived]
            return _render(arrived), lambda: teambus.mark_delivered(team, me, ids)
        if stopped():
            return "[cancelled]", None
        return f"[timeout] No messages for {me} in team {team} yet: call team_wait again to keep waiting.", None

    def tool_team_who(self, arguments: dict, cancel: threading.Event) -> tuple[str, None]:
        team = self.me.team
        info = team_info(team)
        live = teambus.read_presence(team)
        names = info.members or sorted(live)
        lines = []
        for name in names:
            state = live.get(name)
            where = ("listening" if state and state.get("listening") else
                     "running" if state and state.get("runner_alive") else "not seen")
            tags = [t for t, on in (("lead", name == info.lead), ("you", name == self.me.member)) if on]
            delivery = teambus.delivery_words(state)  # a Codex member's last `codex queue`
            lines.append(f"{name}{' (' + ', '.join(tags) + ')' if tags else ''}: {where}"
                         + (f", {delivery}" if delivery else ""))
        return f"Team {team}:\n" + "\n".join(lines) if lines else f"Team {team} has no members yet.", None

    def tool_team_status(self, arguments: dict, cancel: threading.Event) -> tuple[str, None]:
        team = self.me.team
        info = team_info(team)
        rows = teambus.messages(team, since=info.since)
        names = info.members or sorted({str(m.get("to")) for m in rows if m.get("to") not in ("*", "all")})
        out = [f"Team {team}: {len(rows)} message(s)."]
        standing = _standings(info) if info.saved is not None else {}
        for name in names:
            waiting = len(teambus.unread(team, name, since=info.since))
            last = next((m for m in reversed(rows) if m.get("from") == name), None)
            said = f", last sent {_stamp(last.get('at'))}" if last else ""
            out.append(f"  {name}{standing.get(name, '')}: {waiting} unread{said}")
        if rows:
            out.append("Latest:")
            out.extend(f"  {_headline(m)}" for m in rows[-RECENT:])
        pending = [r for r in teambus._read_jsonl(_requests_path(team)) if not r.get("done")]
        if pending:
            out.append("Requests still running:")
            out.extend(f"  {r.get('from')}: {r.get('action')} {r.get('what', '')}" for r in pending)
        return "\n".join(out), None

    def tool_team_request(self, arguments: dict, cancel: threading.Event) -> tuple[str, None]:
        team, me = self.me.team, self.me.member
        action = self._text(arguments, "action", required=True)
        info = team_info(team)
        if not info.lead or me != info.lead:
            raise ToolError(f"Only the team's lead ({info.lead or 'none: a team of peers'}) may ask for this.")
        if action == "add":
            if not info.decide:
                raise ToolError("This team's line-up was given by the user: ask them to add members.")
            what = self._text(arguments, "lineup", required=True)
            _no_new_codex(team, what)
            args = ["--", what]  # a line-up that begins with a dash is not an option of tabmancer's
        elif action == "start":
            names = arguments.get("members") or []
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ToolError("members must be a list of names.")
            unknown = [n for n in names if n not in info.members]
            if unknown:
                raise ToolError(f"No member {', '.join(unknown)} on team {team}.")
            args, what = list(names), " ".join(names)
        else:
            raise ToolError("action is add or start.")
        record = {"id": f"{int(time.time() * 1000)}-{os.getpid()}", "at": teambus._now_iso(), "from": me,
                  "action": action, "what": what}
        teambus._append_jsonl(_requests_path(team), record)
        try:
            from .teams import _tabmancer_cmd

            done = self.run([*_tabmancer_cmd(), "team", action, team, *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=REQUEST_TIMEOUT, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            code, said = done.returncode, ((done.stdout or "") + (done.stderr or "")).strip()
        except (OSError, subprocess.SubprocessError) as problem:
            code, said = -1, str(problem)
        teambus._append_jsonl(_requests_path(team), {**record, "done": True, "code": code})
        if code != 0:
            raise ToolError(f"tabmancer team {action} failed ({code}): {said or 'no output'}")
        return said or f"Done: team {action} {what}.", None


def _no_new_codex(team: str, lineup: str) -> None:
    """A lead adds a Codex member only to a team that has one already: Codex is never brought in
    unasked (and it costs a subscription of its own)."""
    from . import teams

    try:
        wanted = teams.parse_members(lineup)
    except Exception as problem:  # tabmancer team add would say what is wrong with it
        raise ToolError(f"That line-up cannot be read: {problem}") from problem
    saved = teams.load().get(team)
    if any(m.cli == "codex" for m in wanted) and not (saved is not None and "codex" in saved.clis):
        raise ToolError("This team has no Codex member, so none is added: ask the user to add one.")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tabmancer mcp", description="the team bus as an MCP server (stdio)")
    parser.add_argument("--team", default="")
    parser.add_argument("--member", default="")
    opts = parser.parse_args(argv)
    identity = Identity.find(opts.team, opts.member)
    if identity:
        identity.member = teambus.normalize_member(identity.team, identity.member)
    # Text on the wire is UTF-8 and \n, whatever the console's code page.
    # The wire is a copy of stdout's handle, and stdout itself goes to stderr from here: a stray
    # print (ours, a library's, a child's) cannot break the protocol.
    stdin = open(sys.stdin.fileno(), encoding="utf-8", errors="replace", closefd=False)  # noqa: SIM115
    wire = open(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")  # noqa: SIM115
    sys.stdout.flush()
    with contextlib.suppress(OSError):
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    Server(identity, wire).serve(stdin)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
