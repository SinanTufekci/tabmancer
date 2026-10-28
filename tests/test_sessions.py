"""Parser and status tests on small synthetic transcripts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tabmancer import sessions as S
from tabmancer.sessions import Exit, Status
from tabmancer.shutdowns import open_at_shutdown

CWD = str(Path("/work/demo"))


def user(text, **extra):
    return {"type": "user", "cwd": CWD, "entrypoint": "cli", "gitBranch": "main",
            "timestamp": "2026-10-01T09:00:00Z", "message": {"role": "user", "content": text}, **extra}


def assistant(blocks, stop="end_turn", **extra):
    return {"type": "assistant", "cwd": CWD, "entrypoint": "cli",
            "message": {"role": "assistant", "content": blocks, "stop_reason": stop}, **extra}


def text(t):
    return {"type": "text", "text": t}


def tool_use(name="Edit", path=None):
    return {"type": "tool_use", "id": "t1", "name": name,
            "input": {"file_path": path or str(Path(CWD) / "app.py")}}


def tool_result():
    return user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])


COST = {"type": "cost-state", "totalLinesAdded": 12, "totalLinesRemoved": 3, "totalCostUSD": 0.5}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects" / "-work-demo").mkdir(parents=True)
    return tmp_path


def write(home, records, name="s1"):
    path = home / "projects" / "-work-demo" / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")
    return path


def read(home, records, name="s1"):
    return S.read_session(write(home, records, name))


def test_finished_reply_closed_cleanly(home):
    s = read(home, [{"type": "mode"}, user("fix the login bug"), assistant([text("Fixed it.")]),
                    {"type": "ai-title", "aiTitle": "Login bug"}, COST])
    assert (s.status, s.exit) == (Status.DONE, Exit.CLEAN)
    assert s.title == "Login bug"
    assert s.first_prompt == "fix the login bug"
    assert s.last_reply == "Fixed it."
    assert (s.lines_added, s.lines_removed) == (12, 3)
    assert s.git_branch == "main" and s.cwd == CWD


def test_killed_while_tool_was_running(home):
    s = read(home, [user("refactor"), assistant([tool_use()], stop="tool_use")])
    assert (s.status, s.exit) == (Status.CUT_OFF, Exit.KILLED)
    assert s.files == ["app.py"]


def test_tool_finished_but_claude_never_continued(home):
    s = read(home, [user("refactor"), assistant([tool_use()], stop="tool_use"), tool_result(), COST])
    assert (s.status, s.exit) == (Status.CUT_OFF, Exit.CLEAN)


def test_user_pressed_escape(home):
    s = read(home, [user("long task"), assistant([tool_use()], stop="tool_use"),
                    user([text("[Request interrupted by user for tool use]")]), COST])
    assert s.status is Status.STOPPED


def test_message_without_reply(home):
    s = read(home, [user("first"), assistant([text("ok")]), user("and now this?"), COST])
    assert s.status is Status.UNANSWERED
    assert s.last_prompt == "and now this?"


def test_local_command_counts_as_idle(home):
    s = read(home, [user("hi"), assistant([text("hello")]),
                    user("<command-name>/context</command-name>"), COST])
    assert s.status is Status.DONE


def test_api_error(home):
    s = read(home, [user("hi"), assistant([text("API Error: overloaded")], stop="stop_sequence",
                                          isApiErrorMessage=True), COST])
    assert s.status is Status.ERROR


def test_resumed_then_killed_is_not_clean(home):
    # An old cost-state from an earlier exit must not make a later crash look clean.
    s = read(home, [user("hi"), assistant([text("hello")]), COST, {"type": "mode"},
                    user("continue"), assistant([text("sure")])])
    assert s.exit is Exit.KILLED


def test_custom_title_wins_and_sidechains_are_ignored(home):
    s = read(home, [user("hi"), user("subagent prompt", isSidechain=True),
                    {"type": "ai-title", "aiTitle": "AI title"},
                    {"type": "custom-title", "customTitle": "My name"}, assistant([text("x")]), COST])
    assert s.title == "My name"
    assert s.first_prompt == "hi"


def test_session_without_real_prompt_is_skipped(home):
    assert read(home, [user("<command-name>/clear</command-name>"), COST]) is None
    assert S.read_session(write(home, [], name="empty")) is None


def test_tail_window_and_backward_search(home, monkeypatch):
    monkeypatch.setattr(S, "TAIL_BYTES", 300)
    filler = [assistant([text("x" * 200)]) for _ in range(20)]
    s = read(home, [user("start"), {"type": "ai-title", "aiTitle": "Far back"}, *filler, COST])
    assert s.title == "Far back"
    assert s.status is Status.DONE and s.exit is Exit.CLEAN


def test_find_sessions_filters_non_terminal_and_old(home):
    write(home, [user("terminal"), assistant([text("a")]), COST], "cli")
    write(home, [user("headless", entrypoint="sdk-cli"), assistant([text("b")]), COST], "sdk")
    old = write(home, [user("old"), assistant([text("c")]), COST], "old")
    stamp = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old, (stamp, stamp))
    assert [s.session_id for s in S.find_sessions(days=14)] == ["cli"]
    assert {s.session_id for s in S.find_sessions(days=60, include_all=True)} == {"cli", "sdk", "old"}


def test_open_at_shutdown_matches_window(home):
    now = datetime.now()
    shutdown = now - timedelta(hours=10)

    def session(name, ended, exit=Exit.CLEAN):
        s = S.Session(session_id=name, path=Path(name), last_active=ended)
        s.exit = exit
        return s

    sessions = [
        session("at-shutdown", shutdown - timedelta(seconds=40)),
        session("also", shutdown - timedelta(seconds=5)),
        session("closed-earlier", shutdown - timedelta(hours=2)),
        session("running", now, Exit.RUNNING),
    ]
    assert open_at_shutdown(sessions, [shutdown]) == {"at-shutdown", "also"}
    # Without shutdown records: sessions that ended together.
    assert open_at_shutdown(sessions, []) == {"at-shutdown", "also"}


def test_a_killed_session_is_ticked_only_when_the_machine_went_down_after_it(home):
    now = datetime.now()

    def killed(name, ended):
        s = S.Session(session_id=name, path=Path(name), last_active=ended)
        s.exit = Exit.KILLED
        return s

    shutdown = now - timedelta(hours=10)
    by_shutdown = killed("by-shutdown", shutdown + timedelta(seconds=20))  # written to while it went down
    by_hand = killed("by-hand", now - timedelta(hours=1))  # Task Manager, with the machine running on
    long_ago = killed("long-ago", now - timedelta(days=3))
    sessions = [by_shutdown, by_hand, long_ago]
    assert open_at_shutdown(sessions, [shutdown]) == {"by-shutdown"}
    # A crash or a power cut since: the boot after it (Kernel-Power 41, EventLog 6008) stands for it.
    crash_boot = now - timedelta(minutes=30)
    assert open_at_shutdown(sessions, [crash_boot, shutdown]) == {"by-shutdown", "by-hand"}
    # No shutdown can be read at all: every session killed lately.
    assert open_at_shutdown(sessions, []) == {"by-shutdown", "by-hand"}


def test_files_outside_the_project_are_left_out(home):
    s = read(home, [user("edit"), assistant([tool_use(path=str(Path(CWD) / "src" / "a.py")),
                                             tool_use(path=str(Path("/tmp/scratch/notes.txt")))],
                                            stop="tool_use"), tool_result(), COST])
    assert s.files == [str(Path("src") / "a.py")]


def test_live_sessions_from_claude_agents(home, monkeypatch):
    write(home, [user("terminal"), assistant([text("a")]), COST], "open-here")
    write(home, [user("bg"), assistant([text("b")]), COST], "in-background")
    write(home, [user("closed"), assistant([text("c")]), COST], "closed")
    monkeypatch.setattr(S, "_agents_json", lambda: [
        {"sessionId": "open-here", "kind": "interactive", "status": "busy", "pid": 1},
        {"sessionId": "in-background", "kind": "background", "state": "blocked"},
    ])
    found = {s.session_id: s for s in S.find_sessions()}
    assert (found["open-here"].status, found["open-here"].live_state) == (Status.RUNNING, "busy")
    assert (found["in-background"].status, found["in-background"].exit) == (Status.BACKGROUND, Exit.RUNNING)
    assert found["closed"].status is Status.DONE


def test_falls_back_to_pid_files_without_the_cli(home, monkeypatch):
    monkeypatch.setattr(S, "_agents_json", lambda: None)
    monkeypatch.setattr(S, "_alive", lambda info: info["pid"] == 1)
    (home / "sessions").mkdir()
    for pid, sid in [(1, "alive"), (2, "stale")]:
        (home / "sessions" / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "sessionId": sid, "kind": "interactive"}), encoding="utf-8")
    assert set(S.live_sessions()) == {"alive"}


def test_agents_json_handles_missing_cli_and_bad_output(monkeypatch):
    monkeypatch.undo()  # drop the autouse stub; exercise the real function
    monkeypatch.setattr(S.shutil, "which", lambda name: None)
    assert S._agents_json() is None

    class Result:
        returncode, stdout = 0, "not json"

    monkeypatch.setattr(S.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(S.subprocess, "run", lambda *a, **k: Result())
    assert S._agents_json() is None
    Result.stdout = '[{"sessionId": "a"}, {"no": "id"}, 3]'
    assert S._agents_json() == [{"sessionId": "a"}]


def test_a_background_jobs_turn_says_whether_it_works_and_a_permission_prompt_is_named():
    def live(info):
        session = S.Session(session_id="x", path=S.Path("x.jsonl"))
        S.mark_live(session, dict(info, sessionId="x"))
        return session.live_state

    assert live({"kind": "background", "state": "working", "status": "busy"}) == "busy"
    assert live({"kind": "background", "state": "working", "status": "idle"}) == "idle"  # its turn is over
    assert live({"kind": "background", "state": "blocked", "status": "waiting",
                 "waitingFor": "permission prompt"}) == "permission"
    assert live({"kind": "background", "state": "blocked"}) == "blocked"  # whatever it waits for
    assert live({"kind": "interactive", "status": "idle"}) == "idle"
