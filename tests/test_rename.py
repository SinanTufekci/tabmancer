"""Renaming a session from the dashboard: /rename run once in a closed one, pasted into an open one."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace

import pytest
from conftest import box_has_focus
from conftest import wait_until as until

from tabmancer import app as app_module
from tabmancer import rename, sessions
from tabmancer.sessions import Exit, Session, Status

RENAME = rename.rename  # the real one: conftest keeps tests off the claude CLI


def test_a_closed_session_runs_rename_once_without_a_tab(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout="Session renamed to: Checkout v2\n", stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: "claude.exe")
    monkeypatch.setattr(subprocess, "run", run)
    RENAME(Session(session_id="s1", path=None, cwd=str(tmp_path)), "Checkout  v2")
    argv, kw = calls[0]
    assert argv == ["claude.exe", "-p", "--resume", "s1"]
    # The command goes in on stdin, never on the command line, and no model is named: it asks none.
    assert kw["input"] == "/rename Checkout v2" and kw["cwd"] == str(tmp_path)

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "Hi!", ""))
    with pytest.raises(OSError):  # an answer that is not the rename: it did not happen
        RENAME(Session(session_id="s1", path=None, cwd=str(tmp_path)), "x")


def test_what_r_says_is_one_copy_for_both_front_ends(tmp_path, monkeypatch):
    """Refused for another CLI, a team's member and a background session; the heading and Enter's words; the
    open one's command, on the clipboard and its tab brought forward; failed and done."""
    monkeypatch.setattr(rename.teams, "member_of", lambda session: None)
    closed = Session(session_id="s1", path=None, cwd=str(tmp_path / "shop"), title="x" * 70)
    assert rename.refusal(closed) is None
    assert rename.refusal(replace(closed, cli="codex")).startswith("Only Claude Code sessions can be renamed: Codex")
    assert rename.refusal(replace(closed, status=Status.BACKGROUND)).startswith("A background session renames itself")
    monkeypatch.setattr(rename.teams, "member_of", lambda session: ("team", "member"))
    assert rename.refusal(closed) == "Team members keep their names: the team finds them by it."
    assert rename.heading(closed) == f"Rename shop · “{'x' * 59}…”"
    assert rename.enter_words(closed) == "rename it, in Claude Code's own list too"
    running = replace(closed, exit=Exit.RUNNING)
    assert rename.enter_words(running) == "copy /rename and go to its tab, to paste"
    told = rename.open_told(running, "New  name")
    assert told.clipboard == "/rename New name" and told.go_to is running and told.title == "Rename"
    assert told.words.startswith("shop is open, so it renames itself")
    assert rename.failed_note(closed, "no claude").words == "shop was not renamed: no claude"
    assert rename.failed_note(closed, "x").severity == "error"
    assert rename.renamed_note("Fresh").words == "Renamed to “Fresh”."


async def test_r_renames_a_closed_session_and_hands_an_open_one_the_command(fake_home, monkeypatch):
    from tabmancer.app import NamePrompt, SessionPicker

    project = fake_home / "projects" / "C--work-demo"

    def fake_rename(session, name):  # what Claude itself writes
        with (project / f"{session.session_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": name}, separators=(",", ":")) + "\n")

    renamed, copied, notes = [], [], []
    monkeypatch.setattr(rename, "rename", lambda s, name: renamed.append(name) or fake_rename(s, name))
    monkeypatch.setattr(app_module, "copy_text", lambda app, text: copied.append(text))
    app = SessionPicker(days=14)
    monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: "one" in app.sessions)
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("r")
        assert isinstance(app.screen, NamePrompt)
        await box_has_focus(pilot, app)
        await pilot.press(*"Fresh name")  # the old name is selected: typing replaces it
        await pilot.press("enter")
        await until(pilot, lambda: app.sessions["one"].title == "Fresh name")
        assert renamed == ["Fresh name"] and "Fresh name" in str(app.table.get_cell("one", "title"))

        # Open: it has to rename itself, or its next turn writes the old name back.
        monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
            {"sessionId": "two", "kind": "interactive", "status": "idle"}])
        app.poll_live(full=True)
        await until(pilot, lambda: app.sessions["two"].live_state == "idle")
        app.table.move_cursor(row=app.table.get_row_index("two"))
        await pilot.press("r")
        await box_has_focus(pilot, app)
        await pilot.press(*"Other", "enter")
        await until(pilot, lambda: bool(copied))
        assert copied == ["/rename Other"] and renamed == ["Fresh name"]

        app.sessions["one"].cli = "codex"
        app.table.move_cursor(row=app.table.get_row_index("one"))
        await pilot.press("r")
        assert not isinstance(app.screen, NamePrompt) and "Only Claude Code" in notes[-1]
