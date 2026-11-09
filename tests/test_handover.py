"""How full a context is, the warning, and handing a session over to a fresh one."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tabmancer import app as app_module
from tabmancer import context, focus, handover, sessions, summarize, teams
from tabmancer.launch import Mode
from tabmancer.sessions import Exit, Session

WRITE_ITSELF = handover.write_itself  # the real one: conftest keeps tests off claude -p


@pytest.mark.parametrize(("model", "size"), [
    ("claude-opus-5-5", 1_000_000), ("claude-opus-4-7", 1_000_000), ("claude-opus-4-6", 200_000),
    ("claude-sonnet-4-6", 200_000), ("claude-sonnet-4-6[1m]", 1_000_000), ("claude-sonnet-5-5", 1_000_000),
    ("claude-haiku-4-5", 200_000), ("claude-haiku-5-5", 1_000_000), ("claude-fable-5-1", 1_000_000),
    ("something-else", 200_000),
])
def test_the_window_follows_the_model(model, size):
    assert context.window(model) == size


def test_share_and_crowded():
    session = Session(session_id="s", path=None, model="claude-opus-5-5", context=730_000)
    assert context.share(session) == pytest.approx(0.73) and context.crowded(session)
    assert context.window("claude-opus-4-6", seen=300_000) == 1_000_000  # it is past 200K: it has 1M
    assert not context.crowded(Session(session_id="t", path=None, model="claude-opus-5-5", context=100_000))
    assert context.share(Session(session_id="u", path=None)) == 0


def stamp(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def reply(text, used, minutes=0):
    return {"type": "assistant", "cwd": "C:/work/demo", "timestamp": stamp(minutes),
            "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                  "cache_read_input_tokens": used - 1002, "output_tokens": 50}}}


def write(path, records, mode="w"):
    with path.open(mode, encoding="utf-8") as f:
        f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))


def test_the_parser_keeps_the_context_of_the_last_reply(tmp_path):
    path = tmp_path / "s.jsonl"
    write(path, [{"type": "user", "cwd": "C:/work/demo", "entrypoint": "cli", "message": {"content": "go"}},
                 reply("one", 300_000), reply("two", 650_000)])
    session = sessions.read_session(path)
    assert session.context == 650_000 and session.model == "claude-opus-5-5"


def test_the_fresh_session_starts_from_the_handover(tmp_path):
    old = Session(session_id="old", path=None, cwd="C:/work/demo", title="Checkout")
    launch = handover.fresh(old, "## Goal\nShip it.")
    assert launch.mode is Mode.NEW and launch.prompt.startswith("You are taking over")
    assert launch.prompt.endswith("## Goal\nShip it.") and "“Checkout”" in launch.prompt
    assert "<handover>" in handover.REQUEST
    long = handover.fresh(old, "a line of the handover\n" * 2000)  # 46K: too long for a command line
    assert len(long.prompt) < handover.MAX_CHARS + 500 and long.prompt.endswith("if you need it.)")


def test_a_team_member_keeps_its_name_and_role(tmp_path):
    folder = tmp_path / "shop"
    folder.mkdir()
    made = teams.new_team("shop", str(folder), "Checkout", teams.parse_members("api: endpoints"))
    teams.save({"shop": made})
    member = Session(session_id="a1", path=None, cwd=str(folder), title="shop-api")
    launch = handover.fresh(member, "text")
    assert launch.argv[:3] == ["claude", "--name", "shop-api"] and launch.prompt.endswith("text")


def test_the_session_writes_its_own_handover_without_a_tab(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout="Sure.\n<handover>\n## Goal\nShip it.\n</handover>\n",
                                           stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "claude.exe")
    monkeypatch.setattr(subprocess, "run", run)
    session = Session(session_id="s1", path=None, cwd=str(tmp_path), model="claude-opus-5-5")
    assert WRITE_ITSELF(session) == "## Goal\nShip it."
    argv, kw = calls[0]
    assert argv == ["claude.exe", "-p", "--resume", "s1", "--model", "claude-opus-5-5"]
    # The request goes in on stdin, never on the command line, and from the session's own folder.
    assert kw["input"] == handover.REQUEST and kw["cwd"] == str(tmp_path)
    assert "CLAUDECODE" not in kw["env"]

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "limit"))
    with pytest.raises(handover.HandoverError, match="limit"):
        WRITE_ITSELF(session)


def test_only_an_open_session_that_waits_for_you_is_closed(monkeypatch):
    def open_one(sid, title, state="idle"):
        return Session(session_id=sid, path=None, title=title, exit=Exit.RUNNING, live_state=state)

    waiting, busy = open_one("a", "Checkout"), open_one("b", "Billing", "busy")
    assert handover.cannot_close(waiting, [waiting, busy]) is None
    assert "working" in handover.cannot_close(busy, [waiting, busy])
    twin = open_one("c", "Checkout")  # two tabs of that name: never guess which one to close
    assert "same title" in handover.cannot_close(waiting, [waiting, twin])

    closed, still_open = [], [{"sessionId": "a"}]
    monkeypatch.setattr(focus, "close_tab", lambda title: closed.append(title) or True)
    monkeypatch.setattr(handover, "_registry_sessions", lambda: [still_open.pop()] if still_open else [])
    monkeypatch.setattr(handover.time, "sleep", lambda seconds: None)
    handover.close(waiting, [waiting, busy])  # waits until its pid file is gone
    assert closed == ["Checkout"] and not still_open
    with pytest.raises(handover.HandoverError, match="working"):
        handover.close(busy, [waiting, busy])
    monkeypatch.setattr(focus, "close_tab", lambda title: False)
    with pytest.raises(handover.HandoverError, match="not found"):
        handover.close(waiting, [waiting])


def test_the_commits_a_session_made_are_found(tmp_path):
    def shell(command):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": command}}]}}

    path = tmp_path / "s.jsonl"
    write(path, [shell('git add -A && git commit -q -m "Add the parser" -m "Longer body"'),
                 shell("git commit -m 'Fix the cache'"), shell("git status"),
                 shell('git commit -q -m "Add the parser"')])  # the same one twice counts once
    assert handover.commits(path) == ["Add the parser", "Fix the cache"]


def test_from_the_transcript_is_one_call(tmp_path, monkeypatch):
    path = tmp_path / "s.jsonl"
    # Conversation records start with parentUuid, as in Claude Code's own transcripts.
    write(path, [{"parentUuid": None, "type": "user", "cwd": "C:/work/demo", "entrypoint": "cli",
                  "message": {"content": "go"}}, {"parentUuid": "a", **reply("done", 1000)}])
    seen = []
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180: seen.append(prompt) or " ## Goal\nX ")
    session = sessions.read_session(path)
    assert handover.from_transcript(session) == "## Goal\nX"
    assert handover.INSTRUCTIONS in seen[0] and "- go" in seen[0]


async def until(pilot, check, tries=100):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def test_the_dashboard_warns_once_and_hands_over_both_ways(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker
    from tabmancer.handoverview import HandoverDialog

    opened, done = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)
    project = fake_home / "projects" / "C--work-demo"
    write(project / "big.jsonl", [{"type": "user", "cwd": "C:/work/demo", "entrypoint": "cli",
                                   "timestamp": stamp(300), "message": {"content": "a long job"}},
                                  reply("working on it", 800_000)])
    live = {"big": "busy"}  # the open session and its state; closing its tab ends it
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": sid, "kind": "interactive", "status": state} for sid, state in live.items()])

    def close_tab(title):
        done.append(("close", title))
        live.clear()
        return True

    monkeypatch.setattr(focus, "close_tab", close_tab)
    monkeypatch.setattr(handover, "_registry_sessions", list)
    monkeypatch.setattr(handover, "write_itself",
                        lambda s: done.append(("write", s.session_id)) or f"## Goal\nFinish {s.session_id}.")
    app = SessionPicker(days=14)
    notes = []
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: "big" in app.told_crowded)
        await pilot.pause(0.5)
        crowded = [n for n in notes if "its context is 800k" in n]
        assert len(crowded) == 1 and "has been going for" in crowded[0]  # once, however many polls
        assert "/compact" in crowded[0]  # the other way to go on: the same session, shrunk
        assert str(app.table.get_cell("big", "exit")) == "▰▰▰▱ 80%"

        # A session at work: the dialog says it waits for it to finish; Esc leaves it be.
        app.table.move_cursor(row=app.table.get_row_index("big"))
        await pilot.pause(0.1)
        facts = [strip.text for strip in app.meta.render_lines(app.meta.region.reset_offset)]
        hint = [line for line in facts if "/compact" in line]
        assert len(hint) == 1 and "h: new phase" in hint[0]  # both ways on, in one line
        await pilot.press("h")
        what = str(app.screen.query_one("#handover-what").render())
        assert "as soon as it finishes" in what and "/compact" in what
        await pilot.press("escape")
        assert not isinstance(app.screen, HandoverDialog) and not app.queued

        # A closed session writes its own, resumed in the background; there is nothing to close.
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("h")
        assert isinstance(app.screen, HandoverDialog)
        await pilot.press("enter")  # the first choice: let the session write it
        await until(pilot, lambda: len(opened) == 1)
        assert done == [("write", "one")]
        assert opened[0].mode is Mode.NEW and opened[0].prompt.endswith("## Goal\nFinish one.")

        # From the transcript: one claude -p call, then the fresh session.
        monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180: "## Goal\nFinish two.")
        app.table.move_cursor(row=app.table.get_row_index("two"))
        await pilot.press("h", "down", "enter")
        await until(pilot, lambda: len(opened) == 2)
        assert opened[1].prompt.endswith("## Goal\nFinish two.") and opened[1].cwd == "C:/work/demo"

        # An open session that waits for you: its tab closes, then it writes, then the fresh one opens.
        live["big"] = "idle"
        await until(pilot, lambda: app.sessions["big"].live_state == "idle")
        app.table.move_cursor(row=app.table.get_row_index("big"))
        await pilot.press("h")
        assert "closed first" in str(app.screen.query_one("#handover-what").render())
        await pilot.press("enter")
        await until(pilot, lambda: len(opened) == 3)
        assert done[-2:] == [("close", app.sessions["big"].title), ("write", "big")]
        assert opened[2].prompt.endswith("## Goal\nFinish big.")

        # Its tab cannot be found: it writes nothing and stays open. From the transcript, the
        # fresh session still opens, and you are told the old one is open.
        live["big"] = "idle"
        await until(pilot, lambda: app.sessions["big"].exit is Exit.RUNNING)
        monkeypatch.setattr(focus, "close_tab", lambda title: False)
        app.table.move_cursor(row=app.table.get_row_index("big"))
        await pilot.press("h", "enter")
        await until(pilot, lambda: "not found" in notes[-1])
        assert "was not handed over" in notes[-1] and len(done) == 3 and len(opened) == 3
        app.table.move_cursor(row=app.table.get_row_index("big"))
        await pilot.press("h", "down", "enter")
        await until(pilot, lambda: len(opened) == 4)
        await until(pilot, lambda: "still open" in notes[-1])


async def test_h_on_a_busy_session_hands_it_over_once_it_finishes(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker
    from tabmancer.handoverview import HandoverDialog

    opened, done = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)
    project = fake_home / "projects" / "C--work-demo"
    write(project / "big.jsonl", [{"type": "user", "cwd": "C:/work/demo", "entrypoint": "cli",
                                   "timestamp": stamp(300), "message": {"content": "a long job"}},
                                  reply("working on it", 800_000)])
    live = {"big": "busy"}
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": sid, "kind": "interactive", "status": state} for sid, state in live.items()])

    def close_tab(title):
        done.append(("close", title))
        live.clear()
        return True

    monkeypatch.setattr(focus, "close_tab", close_tab)
    monkeypatch.setattr(handover, "_registry_sessions", list)
    monkeypatch.setattr(handover, "write_itself",
                        lambda s: done.append(("write", s.session_id)) or f"## Goal\nFinish {s.session_id}.")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: app.sessions.get("big") and app.sessions["big"].live_state == "busy")
        app.table.move_cursor(row=app.table.get_row_index("big"))
        await pilot.press("h", "enter")  # let the session write it: queued, nothing closed yet
        assert app.queued == {"big": "self"} and not done
        assert str(app.mode_cell(app.sessions["big"])) == "▶ hand over"
        await pilot.press("h")  # again: not after all
        assert not app.queued and not isinstance(app.screen, HandoverDialog)
        await pilot.press("h", "enter")
        await pilot.pause(0.5)
        assert not done and not opened  # still working: it waits
        live["big"] = "idle"  # it finished
        await until(pilot, lambda: len(opened) == 1)
        assert done == [("close", app.sessions["big"].title), ("write", "big")]
        assert opened[0].prompt.endswith("## Goal\nFinish big.") and not app.queued
