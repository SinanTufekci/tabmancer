"""Other CLIs: which are on, how their sessions are read, how they open."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tabmancer import agy, clis, codex, sessions
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Status


def test_only_claude_is_on_until_you_choose():
    assert clis.enabled() == ["claude"]
    assert clis.set_enabled(["agy", "codex"]) == ["codex", "agy"]  # kept in a fixed order
    assert clis.enabled() == ["codex", "agy"]
    assert clis.set_enabled([]) == ["claude"]  # never none at all


@pytest.mark.parametrize(("cli", "mode", "prompt", "argv"), [
    ("codex", Mode.RESUME, None, ["codex", "resume", "id1"]),
    ("codex", Mode.RESUME, "go on", ["codex", "resume", "id1", "go on"]),
    ("codex", Mode.FORK, None, ["codex", "fork", "id1"]),
    ("codex", Mode.NEW, "start here", ["codex", "start here"]),
    ("codex", Mode.CONTINUE, None, ["codex", "resume", "--last"]),
    ("agy", Mode.RESUME, None, ["agy", "--conversation", "id1"]),
    ("agy", Mode.RESUME, "go on", ["agy", "--conversation", "id1", "-i", "go on"]),
    ("agy", Mode.NEW, None, ["agy"]),
    ("agy", Mode.NEW, "start here", ["agy", "-i", "start here"]),
    ("claude", Mode.NEW, "start here", ["claude", "start here"]),
])
def test_each_cli_gets_its_own_command(cli, mode, prompt, argv):
    assert Launch("t", "", mode, "id1", prompt=prompt, cli=cli).argv == argv


def test_auto_continue_sends_the_continue_prompt_to_any_cli(monkeypatch):
    monkeypatch.setenv("TABMANCER_CONTINUE_PROMPT", "devam et")
    assert Launch("t", "", Mode.AUTO, "id1", cli="codex").argv == ["codex", "resume", "id1", "devam et"]
    assert Launch("t", "", Mode.AUTO, "id1", cli="agy").argv == ["agy", "--conversation", "id1", "-i", "devam et"]


# --- Codex ------------------------------------------------------------------------

def event(kind, **payload):
    return {"timestamp": "2026-10-16T09:00:00Z", "type": "event_msg", "payload": {"type": kind, **payload}}


def meta(sid="c1", source="cli", cwd="C:/work/api"):
    return {"timestamp": "2026-10-16T08:59:00Z", "type": "session_meta",
            "payload": {"id": sid, "cwd": cwd, "source": source,
                        "originator": "codex-tui" if source == "cli" else "codex_exec",
                        "git": {"branch": "main"}}}


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path / "codex"


def rollout(home, records, sid="c1", padding=0):
    folder = home / "sessions" / "2026" / "10" / "16"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-10-16T09-00-00-{sid}.jsonl"
    lines = [json.dumps(records[0])]
    if padding:  # a big reasoning blob between the start and the end
        lines.append(json.dumps({"type": "response_item", "payload": {"type": "reasoning", "x": "y" * padding}}))
    lines += [json.dumps(r) for r in records[1:]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_finished_codex_session(codex_home):
    path = rollout(codex_home, [
        meta(), event("task_started"), event("user_message", message="add rate limiting"),
        event("agent_message", message="Working on it."),
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                               "input": "*** Begin Patch\n*** Update File: src/limit.py\n"}},
        event("task_complete", last_agent_message="Done: 60 requests a minute per key.")])
    session = codex.read(path)
    assert (session.cli, session.session_id, session.cwd) == ("codex", "c1", "C:/work/api")
    assert session.title == "add rate limiting" and session.entrypoint == "cli"
    assert session.status is Status.DONE and session.exit is Exit.UNKNOWN
    assert session.last_reply == "Done: 60 requests a minute per key."
    assert session.git_branch == "main" and session.files == [str(__import__("pathlib").Path("src/limit.py"))]


@pytest.mark.parametrize(("tail", "status"), [
    ([event("task_started"), event("user_message", message="next")], Status.UNANSWERED),
    ([event("task_started"), event("user_message", message="next"), event("agent_message", message="hm")],
     Status.CUT_OFF),
    ([event("task_started"), event("user_message", message="next"), event("turn_aborted")], Status.STOPPED),
    ([event("task_started"), event("user_message", message="next"), event("error", message="x")], Status.ERROR),
])
def test_how_a_codex_session_ended(codex_home, tail, status):
    path = rollout(codex_home, [meta(), event("user_message", message="first"), event("task_complete"), *tail])
    assert codex.read(path).status is status


def item(kind, text, **extra):
    """The newer layout (Codex 0.153 on): messages arrive as completed items."""
    return event("item_completed", item={"type": kind, "id": "i", "content": [{"type": "text", "text": text}],
                                         **extra})


def test_newer_codex_writes_messages_as_completed_items(codex_home):
    path = rollout(codex_home, [meta(), event("task_started"), item("UserMessage", "add tests"),
                                event("item_completed", item={"type": "Reasoning", "id": "r"}),
                                item("AgentMessage", "Added four tests.", phase="final_answer"),
                                event("task_complete", last_agent_message="Added four tests.")])
    session = codex.read(path)
    assert session.title == "add tests" and session.status is Status.DONE
    assert session.last_reply == "Added four tests."
    assert "add tests" in codex.digest(session) and "Added four tests." in codex.digest(session)
    cut = rollout(codex_home, [meta("c3"), event("task_started"), item("UserMessage", "refactor"),
                               event("item_completed", item={"type": "Reasoning", "id": "r"})], sid="c3")
    assert codex.read(cut).status is Status.CUT_OFF


def test_the_end_of_a_big_codex_session_is_read(codex_home):
    # A file larger than the head but smaller than the tail window once read as its head only.
    path = rollout(codex_home, [meta(), event("user_message", message="first"), event("task_complete")],
                   padding=codex.HEAD_BYTES + 1000)
    assert codex.read(path).status is Status.DONE


def test_codex_sessions_join_the_list_only_when_codex_is_on(codex_home, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    rollout(codex_home, [meta(), event("user_message", message="by hand"), event("task_complete")])
    rollout(codex_home, [meta("c2", source="exec"), event("user_message", message="from a script")], sid="c2")
    assert sessions.find_sessions(14) == []
    clis.set_enabled(["claude", "codex"])
    assert [s.session_id for s in sessions.find_sessions(14)] == ["c1"]  # codex exec is like claude -p
    assert {s.session_id for s in sessions.find_sessions(14, include_all=True)} == {"c1", "c2"}


def test_codex_digest_for_a_summary(codex_home):
    path = rollout(codex_home, [meta(), event("user_message", message="add limits"),
                                event("agent_message", message="Added.")])
    text = codex.digest(codex.read(path))
    assert "add limits" in text and "Added." in text


# --- Antigravity --------------------------------------------------------------------

def agy_index(home, rows):
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / agy.INDEX)
    con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, "
                "step_count integer, last_modified_time datetime, workspace_uris text, status text, "
                "nesting_depth integer, not_fully_idle numeric, killed numeric)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_agy_conversations_come_from_its_index(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.1234567+00:00")
    agy_index(tmp_path / "agy", [
        ("a1", "Bolt analysis", "Bolt analysis", 4, now, '["file:///C:/work/parts"]', "CASCADE_RUN_STATUS_IDLE",
         0, 0, 0),
        ("a2", "", "Half done", 9, now, "", "", 0, 1, 0),
        ("a3", "Subagent", "", 2, now, "", "", 1, 0, 0),
        ("a4", "Never dated", "", 1, "0001-01-01 00:00:00+00:00", "", "", 0, 0, 0),
    ])
    found = {s.session_id: s for s in agy.find(14)}
    assert set(found) == {"a1", "a2"}  # no subagents, nothing without a date
    assert found["a1"].cwd == str(__import__("pathlib").Path("C:/work/parts")) and found["a1"].status is Status.DONE
    assert found["a2"].title == "Half done" and found["a2"].status is Status.CUT_OFF
    assert found["a1"].cli == "agy" and found["a1"].exit is Exit.UNKNOWN


def test_agy_without_an_index_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "missing")
    assert agy.find(14) == []


# --- the dashboard and the command line --------------------------------------------

async def test_the_dashboard_lists_and_opens_codex_sessions(fake_home, codex_home, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker
    from tabmancer.clisettings import CliSettings

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    rollout(codex_home, [meta(cwd="C:/work/demo"), event("user_message", message="codex task"),
                         event("task_complete")])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        assert "c1" not in app.sessions  # Codex is off
        await pilot.press("comma")
        assert isinstance(app.screen, CliSettings)
        await pilot.press("down", "space", "enter")  # turn Codex on
        for _ in range(60):
            await pilot.pause(0.05)
            if "c1" in app.sessions:
                break
        assert clis.enabled() == ["claude", "codex"]
        assert str(app.table.get_cell("c1", "exit")) == "codex"
        app.table.move_cursor(row=app.table.get_row_index("c1"))
        await pilot.press("m", "m")  # resume -> auto -> fork: Codex can fork
        assert app.mode(app.sessions["c1"]) is Mode.FORK
        await pilot.press("m", "enter")
        assert opened and opened[-1].argv == ["codex"] and opened[-1].cwd == "C:/work/demo"


def test_clis_command(capsys):
    from tabmancer.cli import main

    main(["clis", "enable", "codex"])
    out = capsys.readouterr().out
    assert "on   Codex" in out and "on   Claude Code" in out and "off  Antigravity" in out
    main(["clis", "disable", "codex"])
    assert clis.enabled() == ["claude"]
    with pytest.raises(SystemExit):
        main(["clis", "enable", "nope"])
