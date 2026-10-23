"""Fresh sessions in several folders at once: the n dialog and tabmancer new."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from conftest import box_has_focus

from tabmancer import app as app_module
from tabmancer import clis, newsessions, workspaces
from tabmancer.app import SessionPicker
from tabmancer.newsessions import NewSessions
from tabmancer.sessions import Session


def session(sid, cwd, hours_ago, cli="claude"):
    return Session(session_id=sid, path=None, cwd=cwd, cli=cli,
                   last_active=datetime.now() - timedelta(hours=hours_ago))


def test_projects_are_the_folders_you_worked_in_most_recent_first(tmp_path):
    api, web, gone, spare = (tmp_path / n for n in ("api", "web", "gone", "spare"))
    for folder in (api, web, spare):
        folder.mkdir()
    workspaces.save({"w": workspaces.Workspace("w", [workspaces.Entry(str(spare))])})
    found = newsessions.projects([session("a", str(api), 5), session("b", str(web), 1),
                                  session("c", str(api), 2, cli="codex"), session("d", str(gone), 0)])
    assert [p.name for p in found] == ["web", "api", "spare"]  # a folder that is gone is left out
    assert found[1].sessions == 2 and found[1].used == {"claude", "codex"}
    assert newsessions.match("API", found).cwd == str(api)  # by project name
    assert newsessions.match(str(spare), []).cwd == str(spare.resolve())  # or by path
    assert newsessions.match("nothing", found) is None


def test_fresh_launches_carry_the_first_message_and_the_cli():
    first, second = newsessions.launches(["C:/a", "C:/b"], "codex", "plan the release")
    assert first.argv == ["codex", "plan the release"] and second.cwd == "C:/b"
    assert newsessions.launches(["C:/a"], "claude", "")[0].argv == ["claude"]


async def until(pilot, check, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


def transcript(home, name, cwd):
    folder = home / "projects" / name
    folder.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "cwd": cwd, "entrypoint": "cli", "message": {"content": f"work on {name}"}},
               {"type": "assistant", "cwd": cwd, "message": {"content": [{"type": "text", "text": "ok"}],
                                                             "stop_reason": "end_turn"}}]
    (folder / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


async def test_the_dialog_starts_fresh_sessions_in_the_ticked_folders(fake_home, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    api, web, other = tmp_path / "api", tmp_path / "web", tmp_path / "elsewhere"
    for folder in (api, web, other):
        folder.mkdir()
    transcript(fake_home, "api", str(api))
    transcript(fake_home, "web", str(web))
    now = datetime.now().timestamp()
    os.utime(fake_home / "projects" / "web" / "web.jsonl", (now - 3600, now - 3600))  # api is the newest
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: "api" in app.sessions)
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await box_has_focus(pilot, app)
        dialog = app.screen
        assert {p.name for p in dialog.found} >= {"api", "web"}
        assert dialog.found[0].name == "api"
        await pilot.press("space")  # the first folder: api
        await pilot.press(*"web")  # typing filters the list
        await until(pilot, lambda: list(dialog.shown) == [newsessions.folder_key(str(web))])
        await pilot.press("down", "space")
        assert len(dialog.ticked) == 2
        dialog.query_one("#new-filter").value = str(other)  # a folder that has no sessions yet
        dialog.query_one("#new-filter").focus()
        await pilot.press("enter")  # ticks it, does not start yet
        assert len(dialog.ticked) == 3 and not opened
        dialog.query_one("#new-first").value = "check the build"
        await pilot.press("enter")
        await until(pilot, lambda: len(opened) == 3)
        assert {launch.cwd for launch in opened} == {str(api), str(web), str(other.resolve())}
        assert all(launch.argv == ["claude", "check the build"] for launch in opened)


async def test_tab_moves_between_the_boxes_and_long_lines_wrap(fake_home):
    # A dialog on top keeps the dashboard's keys to itself, but not Textual's own: Tab once
    # stopped working here because every app action was switched off under a dialog.
    from textual.widgets import Input

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await box_has_focus(pilot, app)
        dialog = app.screen
        assert dialog.focused is dialog.folders
        # The sessions' folder is not on this machine, so there is no project to list: it says so.
        assert "No recent projects" in str(dialog.folders.get_option_at_index(0).prompt)
        await pilot.press("tab")
        assert isinstance(dialog.focused, Input) and dialog.focused.id == "new-first"
        await pilot.press("shift+tab", "shift+tab")
        assert dialog.focused.id == "new-filter"
        await pilot.press("n")  # a letter in a box is typed, not the dashboard's n
        assert dialog.query_one("#new-filter").value == "n" and app.screen is dialog
        note = dialog.query_one("#new-note")  # its own line above the keys, whole: wrapped, not cut off
        assert note.region.width * note.region.height >= len(str(note.render()).strip())
        assert dialog.query_one(".hint").region.y > note.region.y  # the keys come last


async def test_ctrl_o_picks_another_cli_when_more_are_on(fake_home, monkeypatch):
    clis.set_enabled(["claude", "codex"])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        app.push_screen(NewSessions([]))
        await pilot.pause(0.1)
        assert app.screen.cli == "claude"
        await pilot.press("ctrl+o")
        assert app.screen.cli == "codex"


def test_new_command_opens_tabs(tmp_path, monkeypatch):
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    (tmp_path / "proj").mkdir()
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "hello"])
    assert [launch.argv for launch in opened] == [["claude", "hello"]]
