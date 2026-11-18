"""A session waiting with a keep-warm sleep running in the background is waiting, not at work.

Claude Code calls a session "busy" while a command it started in the background runs, though its
turn is over. The roles ask a team's members to keep their cache warm so while they wait (teams.py),
so every waiting member looked at work: the list, the bell, j, the notes and the handover all
took it for busy. The transcript says what runs: the background tool calls and the
<task-notification> that reports each one done.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tabmancer import sessions, teamview
from tabmancer.sessions import mark_live, read_session


def stamp(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def prompt(text: str, minutes_ago: float = 10) -> dict:
    return {"type": "user", "timestamp": stamp(minutes_ago), "cwd": "C:/work/demo", "sessionId": "s",
            "message": {"role": "user", "content": text}}


def background(call: str, command: str, minutes_ago: float = 5) -> dict:
    return {"type": "assistant", "timestamp": stamp(minutes_ago), "message": {
        "role": "assistant", "stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": call, "name": "Bash",
             "input": {"command": command, "description": "Wait", "run_in_background": True}}]}}


def started(call: str, minutes_ago: float = 5) -> dict:
    return {"type": "user", "timestamp": stamp(minutes_ago), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call, "content": "Command running in background with ID: b1."}]}}


def reply(text: str = "Done: the fix is in.", minutes_ago: float = 4) -> dict:
    return {"type": "assistant", "timestamp": stamp(minutes_ago), "message": {
        "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


def finished(call: str, minutes_ago: float = 1) -> dict:
    return {"type": "user", "timestamp": stamp(minutes_ago), "message": {"role": "user", "content": (
        f"<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>{call}</tool-use-id>\n"
        "<status>completed</status>\n<summary>Background command completed</summary>\n</task-notification>")}}


def transcript(tmp_path, *records):
    path = tmp_path / "s.jsonl"  # written as Claude Code writes it: no spaces between the parts
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    return path


def live(path, status="busy"):
    session = read_session(path)
    mark_live(session, {"sessionId": "s", "kind": "interactive", "status": status})
    return session


@pytest.mark.parametrize("command,seconds", [
    ("sleep 3300", 3300), ("sleep 55m", 3300), ("sleep 1h", 3600), ("Start-Sleep -Seconds 3300", 3300),
    ("start-sleep 60", 60), ("sleep 3300;", 3300), ("sleep 3300; echo done", 3300), ("sleep 3300 && echo ok", 3300),
    ("sleep 60 && npm test", None), ("sleep 60; echo $(rm -rf x)", None),
    ("sleep 5; echo starting\nnpm run dev", None), ("sleep 5 && echo up\r\nnpm test", None), ("until grep -q done f; do sleep 60; done", None), ("npm test", None)])
def test_a_keep_warm_sleep_is_told_from_other_commands(command, seconds):
    assert sessions.keep_warm_seconds(command) == seconds


def test_its_turn_over_and_a_sleep_running_it_waits(tmp_path):
    path = transcript(tmp_path, prompt("fix the bug"), background("t1", "sleep 3300"), started("t1"), reply())
    session = live(path)
    assert session.live_state == "idle"
    assert session.warm_until is not None and session.warm_until > datetime.now() + timedelta(minutes=45)


def test_another_command_in_the_background_is_aside_not_work(tmp_path):
    path = transcript(tmp_path, prompt("run the tests"), background("t1", "npm test"), started("t1"), reply())
    assert live(path).live_state == "aside"


def test_a_sleep_and_another_command_together_wait_for_the_command(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "npm test"), started("t1"),
                      background("t2", "sleep 3300", 4.5), started("t2", 4.5), reply())
    assert live(path).live_state == "aside"


def test_still_at_work_it_stays_busy_whatever_runs_beside(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "sleep 3300"), started("t1"))  # no reply yet
    assert live(path).live_state == "busy"


def test_a_command_reported_done_no_longer_counts(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "npm test"), started("t1"), reply(),
                      finished("t1"), reply("Tests pass.", 0.5))
    session = live(path)
    assert session.aside == []
    assert session.live_state == "busy"  # Claude Code's word stands when nothing explains it


def test_a_report_mid_turn_comes_as_a_queued_command(tmp_path):
    queued = {"type": "attachment", "timestamp": stamp(1), "attachment": {
        "type": "queued_command", "prompt": finished("t1")["message"]["content"]}}
    path = transcript(tmp_path, prompt("go"), background("t1", "npm test"), started("t1"), queued, reply(minutes_ago=0.5))
    assert live(path).aside == []


def test_a_sleep_long_past_its_end_is_taken_for_gone(tmp_path):
    path = transcript(tmp_path, prompt("go", 200), background("t1", "sleep 3300", 120), started("t1", 120),
                      reply(minutes_ago=119))
    session = live(path)
    assert session.warm_until is None
    assert session.live_state == "busy"


def test_idle_stays_idle(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "sleep 3300"), started("t1"), reply())
    assert live(path, status="idle").live_state == "idle"


def test_a_followed_transcript_keeps_track_across_reads(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "npm test"), started("t1"), reply())
    assert read_session(path, follow=True).aside == ["npm test"]
    with path.open("a", encoding="utf-8") as f:
        for record in (finished("t1", 0.5), reply("Tests pass.", 0.2), background("t2", "sleep 3300", 0.1),
                       started("t2", 0.1), reply("Waiting.", 0)):
            f.write(json.dumps(record) + "\n")
    session = read_session(path, follow=True)
    mark_live(session, {"sessionId": "s", "kind": "interactive", "status": "busy"})
    assert session.aside == [] and session.live_state == "idle"


def test_the_cache_keeps_it(tmp_path):
    """A session open when the dashboard starts, its transcript unchanged since, comes from the cache
    and is not read again: what it runs in the background has to come with it."""
    path = transcript(tmp_path, prompt("go"), background("t1", "sleep 3300"), started("t1"), reply())
    session = read_session(path)
    back = sessions._from_cache(json.loads(json.dumps(sessions._to_cache(session))))
    assert back == session and back.warm_until is not None
    mark_live(back, {"sessionId": "s", "kind": "interactive", "status": "busy"})
    assert back.live_state == "idle"


def test_a_members_card_says_until_when_its_cache_is_warm(tmp_path):
    path = transcript(tmp_path, prompt("go"), background("t1", "sleep 3300"), started("t1"), reply())
    session = live(path)
    session.live_state = "resting"
    label, _, spinning = teamview.status_of(session)
    assert label == f"idle · warm to {session.warm_until:%H:%M}" and not spinning
    session.live_state = "aside"
    assert teamview.status_of(session)[0] == "background command"


async def test_the_dashboard_rings_when_its_turn_ends_with_a_sleep_running(fake_home, monkeypatch):
    """The seam: the poll reads the transcript, mark_live tells the sleep apart, and the dashboard
    sees a session that was working now waiting for you (the bell, j), as with an idle one."""
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    path = fake_home / "projects" / "C--work-demo" / "one.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(prompt("fix the bug", 0.2)) + "\n")  # at work: its new message has no reply yet
    state = {"value": [{"sessionId": "one", "kind": "interactive", "status": "busy"}]}
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: state["value"])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)
    app = SessionPicker(days=14, live=True)
    bells = []
    monkeypatch.setattr(app, "bell", lambda: bells.append(1))
    async with app.run_test() as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions.get("one") and app.sessions["one"].live_state == "busy":
                break
        assert app.sessions["one"].live_state == "busy"
        with path.open("a", encoding="utf-8") as f:  # it ends its turn and keeps its cache warm
            for record in (background("t1", "sleep 3300", 0.1), started("t1", 0.1), reply(minutes_ago=0)):
                f.write(json.dumps(record) + "\n")
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions["one"].live_state == "idle":
                break
        assert app.sessions["one"].live_state == "idle"  # Claude Code still says busy
        assert bells == [1]


def stopped(call: str, task: str = "b1", minutes_ago: float = 2) -> list[dict]:
    """The session stopping a command itself (TaskStop): Claude Code reports no end of it then."""
    return [{"type": "assistant", "timestamp": stamp(minutes_ago), "message": {
                "role": "assistant", "stop_reason": "tool_use", "content": [
                    {"type": "tool_use", "id": f"stop-{call}", "name": "TaskStop", "input": {"task_id": task}}]}},
            {"type": "user", "timestamp": stamp(minutes_ago), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"stop-{call}", "content": json.dumps(
                    {"message": f"Successfully stopped task: {task} (sleep 3300)", "task_id": task})}]}}]


def test_a_command_the_session_stopped_itself_no_longer_counts(tmp_path):
    """A member stops its old sleep before it starts a new one (or before its work): no
    <task-notification> follows a TaskStop, and the stopped command looked as if it still ran."""
    path = transcript(tmp_path, prompt("go"), background("t1", "npm test", 8), started("t1", 8),
                      *stopped("t1", minutes_ago=6), background("t2", "sleep 3300"), started("t2"), reply())
    session = live(path)
    assert session.aside == [] and session.live_state == "idle"


def test_a_sleep_started_before_the_tail_window_is_found(tmp_path, monkeypatch):
    """A member's requests are logged whole, so its keep-warm sleep, started a few minutes ago, can sit
    further back than the two megabytes a first read takes from the end (seen: 30 MB transcripts)."""
    monkeypatch.setattr(sessions, "TAIL_BYTES", 4096)
    chatter = [record for n in range(40) for record in (
        prompt(f"peer note {n} " + "x" * 200, 3), reply("Noted. " + "y" * 200, 3))]
    path = transcript(tmp_path, prompt("go", 30), background("t1", "npm test", 25), started("t1", 25),
                      finished("t1", 20), background("t2", "sleep 3300", 10), started("t2", 10), reply(minutes_ago=10),
                      *chatter)
    assert path.stat().st_size > 4 * 4096
    session = live(path)
    assert session.aside == [] and session.warm_until is not None and session.live_state == "idle"


def test_the_look_back_reads_only_a_recent_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "TAIL_BYTES", 4096)
    chatter = [record for n in range(40) for record in (prompt("x" * 200, 3), reply("y" * 200, 3))]
    path = transcript(tmp_path, prompt("go", 30), background("t2", "sleep 3300", 10), started("t2", 10),
                      reply(minutes_ago=10), *chatter)
    old = (datetime.now() - timedelta(hours=3)).timestamp()
    os.utime(path, (old, old))
    assert read_session(path).warm_until is None  # not written to for hours: nothing of it runs


@pytest.mark.parametrize("command,state", [("sleep 3300", "idle"), ("npm test", "aside")])
def test_the_pid_files_shell_means_the_same(tmp_path, command, state):
    """Claude Code's pid file says "shell" where `claude agents --json` says "busy": the quick poll
    reads one, the full poll the other, and the state went back and forth between them."""
    path = transcript(tmp_path, prompt("go"), background("t1", command), started("t1"), reply())
    assert live(path, status="shell").live_state == state == live(path, status="busy").live_state


def test_the_pid_files_shell_for_a_command_out_of_sight(tmp_path):
    """A command its transcript does not show (started further back than is read): Claude Code says
    its turn is over and something runs, so it waits for that, not for you."""
    path = transcript(tmp_path, prompt("go"), reply())
    assert live(path, status="shell").live_state == "aside"


def resumed_notice(*tasks, minutes_ago: float = 3) -> dict:
    """What a resumed session is told of the commands still running when the one before ended, as
    Claude Code 2.1.322 writes it: their own ids only, and no reply ever comes to it."""
    ids = "".join(f"<task-id>{task}</task-id>\n" for task in tasks)
    return {"type": "user", "timestamp": stamp(minutes_ago), "queueTranscriptOnly": True,
            "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": (
                f"<task-notification>\n{ids}<task-id>__orphan_summary__:shell</task-id>\n<status>stopped</status>\n"
                f"<summary>{len(tasks)} background shell command tasks didn't finish before the previous session "
                "ended.</summary>\n</task-notification>")}}


def started_as(call: str, task: str, minutes_ago: float = 5) -> dict:
    return {"type": "user", "timestamp": stamp(minutes_ago), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call, "content": f"Command running in background with ID: {task}."}]}}


def test_the_commands_a_resumed_session_is_told_are_stopped_are_gone(tmp_path):
    # Two dev servers, the session closed, resumed: the notice names them by their own ids. They stayed
    # "background command" for good, and the member's keep-warm sleep after it read aside, not waiting.
    path = transcript(tmp_path, prompt("start the servers", 60), background("t1", "npm run dev", 50),
                      started_as("t1", "bAAA", 50), background("t2", "npm run api", 50), started_as("t2", "bBBB", 50),
                      reply("up", 49), resumed_notice("bAAA", "bBBB", minutes_ago=10), prompt("now wait", 8),
                      background("t3", "sleep 3300", 5), started_as("t3", "bCCC", 5), reply("waiting", 4))
    session = live(path, "shell")
    assert session.aside == [] and session.live_state == "idle" and session.warm_until is not None


def test_a_finished_session_resumed_and_closed_is_still_done(tmp_path):
    # The notice is the last record, and nothing answers it: it read as cut off, and c sent
    # "This session was interrupted" to a session that was done.
    path = transcript(tmp_path, prompt("fix it", 60), reply("fixed", 59), resumed_notice("bAAA", minutes_ago=1))
    session = read_session(path)
    assert session.status is sessions.Status.DONE and session.turn_over


def test_a_background_command_refused_never_runs(tmp_path):
    refused = {"type": "user", "timestamp": stamp(5), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": "Permission for this command was denied by a built-in Claude Code safeguard."}]}}
    path = transcript(tmp_path, prompt("serve it"), background("t1", "npm run dev"), refused, reply())
    assert live(path, "shell").aside == []


def test_a_sleep_at_the_very_edge_of_the_tail_window_is_seen(tmp_path, monkeypatch):
    # The window's first line was dropped as cut even when the window began right on it, and the look
    # back stopped short of a line the window cut: either way the sleep was in neither read.
    monkeypatch.setattr(sessions, "TAIL_BYTES", 4096)
    def line(record: dict) -> bytes:
        return (json.dumps(record, separators=(",", ":")) + "\n").encode()

    def filler(size: int) -> bytes:
        return line({"type": "progress", "data": "x" * (size - len(line({"type": "progress", "data": ""})))})

    sleep, after = line(background("t1", "sleep 3300", 5)), line(started("t1", 5)) + line(reply("waiting", 4))
    edge = sessions.TAIL_BYTES
    # From the end, where the sleep begins: on the edge, cut by it, ending on it, well inside.
    for back in (edge, edge + len(sleep) // 2, edge + len(sleep), edge - 300):
        path = tmp_path / f"edge{back}.jsonl"
        path.write_bytes(line(prompt("wait for the lead", 30)) + filler(9000) + sleep
                         + filler(back - len(sleep) - len(after)) + after)
        session = live(path, "shell")
        assert session.warm_until is not None and session.live_state == "idle", back


def test_a_background_command_refused_before_the_tail_window_never_runs(tmp_path, monkeypatch):
    # The look back read the call but not its refusal, which carries none of the marks it looked for.
    monkeypatch.setattr(sessions, "TAIL_BYTES", 4096)
    refused = {"type": "user", "timestamp": stamp(25), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
         "content": "Permission for this command was denied by a built-in Claude Code safeguard."}]}}
    chatter = [record for n in range(40) for record in (
        prompt(f"peer note {n} " + "x" * 200, 3), reply("Noted. " + "y" * 200, 3))]
    path = transcript(tmp_path, prompt("serve it", 30), background("t1", "npm run dev", 25), refused,
                      reply(minutes_ago=25), *chatter)
    assert path.stat().st_size > 4 * 4096
    assert live(path, "shell").aside == []
