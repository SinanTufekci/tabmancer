"""What a bug sweep of the whole project found, each pinned so it stays found: two processes on one
transcript, a member started twice, a session missing from the list, a crash, a wrong number."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

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
    assert ran[0][-1] == "rename the 'Save & Close' button"  # inside one pair of quotes for cmd.exe


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
    # list has it now, hiding a project drops its ticks, and the footer's context counts the ticks.
    source = Path(__file__).resolve().parent.parent / "src" / "tabmancer" / "app.py"
    text = source.read_text(encoding="utf-8")
    assert "self.hand_over(now, how)" in text
    assert 'return ("ticked", len(self.selected), waiting)' in text
    assert os.path.exists(source)


def test_a_background_rows_enter_attaches_to_it():
    from tabmancer.app import SessionPicker

    app = SessionPicker(days=14)
    app.background_live = {"bg-1": {"sessionId": "bg-1", "id": "7f3a", "kind": "background"}}
    session = Session("bg-1", None, cwd="C:/w/api", status=Status.BACKGROUND, exit=Exit.RUNNING,
                      last_active=datetime.now() - timedelta(minutes=1))
    made = app.launch_for(session)
    assert made.mode is Mode.ATTACH and made.argv == ["claude", "attach", "7f3a"]
