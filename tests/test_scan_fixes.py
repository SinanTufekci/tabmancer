"""What a bug sweep of the whole project found, each pinned so it stays found: two processes on one
transcript, a member started twice, a session missing from the list, a crash, a wrong number."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tabmancer import (
    background,
    context,
    handover,
    launch,
    newsessions,
    picks,
    sessions,
    teams,
    tokens,
    workspaces,
)
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status


def test_a_first_message_that_begins_with_a_dash_is_not_taken_for_an_option():
    # claude: "error: unknown option '- fix the login'" (tried with 2.1.318); after a "--" it is the prompt.
    fresh = Launch("x", "C:/w", Mode.NEW, "", prompt="- fix the login")
    assert fresh.argv[-2:] == ["--", "- fix the login"]
    assert Launch("x", "C:/w", Mode.RESUME, "id", prompt="-v please").argv[-2:] == ["--", "-v please"]
    assert Launch("x", "C:/w", Mode.NEW, "", prompt="fix it").argv == ["claude", "fix it"]  # as before otherwise


def test_a_background_start_through_npms_claude_cmd_keeps_a_quoted_message_whole(monkeypatch):
    ran = []
    monkeypatch.setattr(launch.shutil, "which", lambda name: r"C:\npm\claude.CMD")
    monkeypatch.setattr(launch.subprocess, "run", lambda argv, **kw: ran.append(argv) or
                        subprocess.CompletedProcess(argv, 0, "Started bg-1\n", ""))
    launch.run_in_background(Launch("x", "C:/w", Mode.RESUME, "id", prompt='rename the "Save & Close" button'))
    assert ran[0].endswith(' "rename the \'Save & Close\' button"')  # inside one pair of quotes for cmd.exe


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe runs a .cmd shim on Windows only")
def test_a_background_start_through_a_cmd_shim_reaches_claude_as_written(tmp_path, monkeypatch):
    # cmd.exe read the line first: %USERNAME% came out as the name, a word with & in it and no space
    # went bare and the rest ran as a command, a line break ended the message there.
    echo = tmp_path / "echo.py"
    echo.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    shim = tmp_path / "claude.cmd"
    shim.write_text(f'@ECHO off\r\n"{sys.executable}" "%~dp0echo.py" %*\r\n', encoding="ascii")
    monkeypatch.setattr(launch.shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(launch.claudeupdate, "before_opening", lambda: None)
    monkeypatch.setattr(launch, "_note", lambda launches: None)
    for prompt, arrives in (("We hit 100% at %USERNAME%, fix it", "We hit 100% at %USERNAME%, fix it"),
                            ("tests&calc", "tests&calc"), ("a ^ b | c > d < (e)", "a ^ b | c > d < (e)"),
                            ("line one\nline two", "line one line two"), ('say "hi"', "say 'hi'"),
                            ("ends in a folder\\", "ends in a folder\\")):
        said = json.loads(launch.run_in_background(Launch("x", str(tmp_path), Mode.RESUME, "id", prompt=prompt)))
        assert said == ["--bg", "--resume", "id", arrives]


def test_a_workspace_takes_a_background_session_for_open(tmp_path):
    folder = tmp_path / "api"
    folder.mkdir()
    running = Session("bg-1", None, cwd=str(folder), status=Status.BACKGROUND, exit=Exit.RUNNING)
    space = workspaces.Workspace("w", [workspaces.Entry(str(folder), workspaces.CONTINUE)])
    launches, notes = workspaces.resolve(space, [running])
    assert not launches and any("already open" in note for note in notes)


def test_a_second_linked_team_does_not_take_the_first_ones_name(tmp_path):
    folder = tmp_path / "my-shop-frontends-app"  # cut to 18, it ended in a dash
    first = newsessions.linked_team(str(folder), "a\nb", picks.Pick(), set())
    second = newsessions.linked_team(str(folder), "c\nd", picks.Pick(), {first.name})
    assert first.name != second.name and second.name == teams.clean(second.name)


def test_a_background_session_still_starting_is_not_cleared_away():
    starting = background.Job(id="b1", session_id="s", name="", cwd="", state="working", running=False, started=None)
    stopped = background.Job(id="b2", session_id="t", name="", cwd="", state="stopped", running=False, started=None)
    assert background.kind(starting) == "working" and not starting.tidy and stopped.tidy


def test_an_idle_member_waiting_for_its_lead_can_be_handed_over():
    assert "resting" in handover.IDLE  # the list calls an idle member that waits for its lead resting


def test_older_model_ids_with_a_date_are_not_read_as_a_1m_window():
    assert context.window("claude-opus-4-20250514") == context.SMALL
    assert context.window("claude-3-7-sonnet-20250219") == context.SMALL
    assert context.window("claude-opus-4-7") == context.LARGE and context.window("claude-sonnet-5-5") == context.LARGE


def test_a_prompt_that_begins_with_markup_is_a_prompt():
    def said(text):
        return sessions._is_real_prompt({"type": "user", "message": {"content": text}})

    assert said('<div class="card"> why is this not centred?')
    assert not said("<command-name>/clear</command-name>") and not said("<local-command-stdout>x</local-command-stdout>")
    assert not said("<system-reminder>x</system-reminder>") and not said("<ide_opened_file>x")


def test_a_transcript_that_could_not_be_read_a_moment_is_not_kept_out_of_the_list(fake_home, monkeypatch):
    paths = sorted((fake_home / "projects").glob("*/*.jsonl"))
    real = sessions.read_session
    held = {paths[0]}

    def flaky(path, strict=False):
        if path in held and strict:
            raise PermissionError("held by a backup")
        return real(path, strict)

    monkeypatch.setattr(sessions, "read_session", flaky)
    assert [s.session_id for s in sessions._read_cached(paths)] == [paths[1].stem]
    held.clear()  # read again next time: not cached as nothing to resume
    assert sorted(s.session_id for s in sessions._read_cached(paths)) == sorted(p.stem for p in paths)


def test_where_there_is_no_birth_time_a_transcript_is_born_at_its_first_record(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"type":"custom-title"}\n{"type":"user","timestamp":"2026-11-01T10:00:00.000Z"}\n',
                    encoding="utf-8")

    class LinuxStat:  # no st_birthtime; st_ctime moves with every write
        st_ctime = st_mtime = datetime(2026, 11, 4).timestamp()

    born = tokens._born(path, LinuxStat())
    assert born == datetime.fromisoformat("2026-11-01T10:00:00+00:00").timestamp()


async def test_s_twice_starts_a_team_once(fake_home, tmp_path, monkeypatch):
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        app.open_team(team, teams.start(team, {}))
        app.open_team(team, teams.start(team, {}))  # again, before the first ones show as open
        await pilot.pause(0.1)
    assert sorted(launch.title for launch in opened) == ["shop: api", "shop: lead"]


def test_the_handover_and_the_tick_count_follow_the_list_as_it_is_now():
    # Read from the code, as a guard against going back: the handover is given the session as the
    # list has it now, hiding a project drops its ticks, and the footer's context counts the ticks
    # (footer.row is given how many, and says them).
    source = Path(__file__).resolve().parent.parent / "src" / "tabmancer" / "app.py"
    text = source.read_text(encoding="utf-8")
    flows = source.with_name("engine.py").read_text(encoding="utf-8")  # handing over: Engine.start_handover
    assert "current = state.sessions.get(session.session_id, session)" in flows
    assert "front.hand_over(current, how)" in flows
    assert text.count("len(self.selected), self.member_rows)") == 2
    rules = source.with_name("footer.py").read_text(encoding="utf-8")
    assert 'return Row("ticked", ticked, anyone)' in rules
    assert os.path.exists(source)


def test_a_background_rows_enter_attaches_to_it():
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    app.background_live = {"bg-1": {"sessionId": "bg-1", "id": "7f3a", "kind": "background"}}
    session = Session("bg-1", None, cwd="C:/w/api", status=Status.BACKGROUND, exit=Exit.RUNNING,
                      last_active=datetime.now() - timedelta(minutes=1))
    made = app.launch_for(session)
    assert made.mode is Mode.ATTACH and made.argv == ["claude", "attach", "7f3a"]


async def test_hiding_a_row_leaves_the_cursor_where_it_was(tmp_path, monkeypatch):
    # The list is built again on each change, and its cursor went to the top: on the sixth row, x hid
    # it and the cursor landed on the newest session, so a second x hid that one, never picked.
    from conftest import _records

    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "recent_shutdowns", list)
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: set())
    project = tmp_path / "projects" / "C--work-demo"
    project.mkdir(parents=True)
    now = datetime.now().timestamp()
    for i in range(8):
        path = project / f"s{i}.jsonl"
        path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n"
                                for r in _records(f"do {i}", "done", f"Task {i}")), encoding="utf-8")
        os.utime(path, (now - i * 600, now - i * 600))
    app = SessionPicker(days=14)
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.press("down", "down", "down", "down", "down")
        row, first = app.table.cursor_row, app.current().session_id
        await pilot.press("x")
        await pilot.pause(0.2)
        assert app.table.cursor_row == row and app.current().session_id not in (first, "s0")
        second = app.current().session_id
        await pilot.press("x")
        await pilot.pause(0.2)
        assert sorted(app.marks.hidden) == sorted([first, second])


async def test_a_filter_starts_at_its_newest_match(tmp_path, monkeypatch):
    # Keeping the cursor's place when its row went (above) also kept it for a filter: on the seventh
    # row, /alpha landed on the third match, not the newest.
    from conftest import _records

    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "recent_shutdowns", list)
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: set())
    project = tmp_path / "projects" / "C--work-demo"
    project.mkdir(parents=True)
    now = datetime.now().timestamp()
    for i in range(8):
        word = "alpha" if i < 3 else "beta"
        path = project / f"s{i}.jsonl"
        path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n"
                                for r in _records(f"do {word} {i}", "done", f"{word} task {i}")), encoding="utf-8")
        os.utime(path, (now - i * 600, now - i * 600))
    app = SessionPicker(days=14)
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.press(*["down"] * 6)
        assert app.current().session_id.startswith("s") and "beta" in app.current().title
        await pilot.press("slash", *"alpha")
        await pilot.pause(0.3)
        assert app.current().session_id == "s0"


def test_the_hint_to_look_further_back_stays_in_range(tmp_path, monkeypatch):
    # With --days 30000 and nothing found, it suggested --days 60000, which --days refuses.
    from tabmancer import sessions

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects").mkdir()
    assert "--days 36500 " in sessions.nothing_found(30000, include_all=True)
    assert "--days 60 " in sessions.nothing_found(14, include_all=True)


def test_days_must_be_a_number_of_days(capsys):
    # inf, nan and 1e9 ended in a traceback (OverflowError in timedelta), -1 listed "the last -1 days".
    from tabmancer import cli

    for wrong in ("inf", "nan", "1e9", "-1", "0", "x"):
        try:
            cli.main(["list", "--days", wrong])
        except SystemExit as stop:
            assert stop.code == 2, wrong
        else:
            raise AssertionError(wrong)
    assert "argument --days" in capsys.readouterr().err
    assert cli.days_back("2.5") == 2.5 and cli.days_back(str(cli.MOST_DAYS)) == cli.MOST_DAYS
