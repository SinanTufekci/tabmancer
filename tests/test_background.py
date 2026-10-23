"""Background sessions: listing, stopping, removing, and the dialog's confirmations."""

from __future__ import annotations

import time

import pytest

from tabmancer import background as B
from tabmancer import launch as L
from tabmancer import sessions

NOW_MS = time.time() * 1000
ENTRIES = [
    {"kind": "interactive", "sessionId": "terminal", "pid": 1, "status": "busy"},
    {"kind": "background", "id": "idle1", "sessionId": "s-idle", "pid": 10, "state": "done", "status": "idle",
     "name": "Finished task", "cwd": "C:/work/web-app", "startedAt": NOW_MS - 3_600_000},
    {"kind": "background", "id": "busy1", "sessionId": "s-busy", "pid": 11, "state": "working",
     "status": "busy", "name": "Long refactor", "cwd": "C:/work/api", "startedAt": NOW_MS - 60_000},
    {"kind": "background", "id": "ask1", "sessionId": "s-ask", "pid": 12, "state": "blocked",
     "name": "Needs a decision", "cwd": "C:/work/api", "startedAt": NOW_MS - 120_000},
    {"kind": "background", "id": "gone1", "sessionId": "s-gone", "state": "done", "name": "Old one",
     "cwd": "C:/work/docs", "startedAt": NOW_MS - 40 * 86_400_000},
]


@pytest.fixture
def cli(monkeypatch):
    """A fake `claude`: agents --json --all lists ENTRIES; stop and rm edit them."""
    entries = [dict(e) for e in ENTRIES]
    calls: list[tuple[str, ...]] = []

    def agents(include_finished=False):
        return [dict(e) for e in entries if include_finished or e.get("pid")]

    def run(*args):
        calls.append(args)
        target = next(e for e in entries if e.get("id") == args[1])
        if args[0] == "stop":
            target.pop("pid", None)
        else:
            entries.remove(target)
        return f"{args[0]} {args[1]}"

    monkeypatch.setattr(sessions, "_agents_json", agents)
    monkeypatch.setattr(B, "_run", run)
    return calls


def test_jobs_lists_background_sessions_most_urgent_first(cli):
    found = B.jobs()
    assert [job.id for job in found] == ["ask1", "busy1", "idle1", "gone1"]
    assert [B.kind(job) for job in found] == ["needs", "working", "idle", "stopped"]
    assert [job.tidy for job in found] == [False, False, True, True]
    assert found[2].project == "web-app" and found[2].session_id == "s-idle"
    assert B.ago(found[3].started) == "40d ago"


def test_jobs_is_none_without_the_cli(monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: None)
    assert B.jobs() is None


def test_commands_go_to_the_cli_with_a_clean_environment(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "stopped idle1\n", ""

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"])
        return Done()

    monkeypatch.setattr(B.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert B.stop("idle1") == "stopped idle1"
    assert seen["argv"] == ["claude", "stop", "idle1"] and "CLAUDECODE" not in seen["env"]
    Done.returncode, Done.stdout, Done.stderr = 1, "", "has unpushed commits"
    with pytest.raises(OSError, match="unpushed"):
        B.remove("idle1")


def test_attach_mode_runs_claude_attach():
    launch = L.Launch(title="t", cwd="", mode=L.Mode.ATTACH, session_id="idle1")
    assert launch.argv == ["claude", "attach", "idle1"]


async def until(pilot, check, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def test_the_dialog_stops_removes_and_cleans_up(fake_home, cli, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("B")
        await until(pilot, lambda: isinstance(app.screen, B.BackgroundDialog))
        dialog = app.screen
        assert dialog.current().id == "ask1"

        await pilot.press("x")  # it waits for an answer: a first press only asks
        assert cli == [] and dialog.armed == ("stop", "ask1")
        await pilot.press("x")
        await until(pilot, lambda: cli == [("stop", "ask1")])
        await until(pilot, lambda: not dialog.busy and not next(j for j in dialog.jobs if j.id == "ask1").running)

        await pilot.press("down")  # moving on drops a pending confirmation
        await pilot.press("d")
        assert dialog.armed is not None
        await pilot.press("up")
        assert dialog.armed is None

        cli.clear()
        await pilot.press("c", "c")  # every idle or stopped one: stopped first if it still runs
        await until(pilot, lambda: len(cli) == 4 and not dialog.busy)
        assert cli == [("stop", "idle1"), ("rm", "idle1"), ("rm", "ask1"), ("rm", "gone1")]
        await until(pilot, lambda: [job.id for job in dialog.jobs] == ["busy1"])

        await pilot.press("enter")  # attaches in a new tab
        await until(pilot, lambda: not isinstance(app.screen, B.BackgroundDialog))
        assert [launch.argv for launch in opened] == [["claude", "attach", "busy1"]]
