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
        dialog.query_one("#new-first").text = "check the build"
        await pilot.press("enter")
        await until(pilot, lambda: len(opened) == 3)
        assert {launch.cwd for launch in opened} == {str(api), str(web), str(other.resolve())}
        assert all(launch.argv == ["claude", "check the build"] for launch in opened)


async def test_tab_moves_between_the_boxes_and_long_lines_wrap(fake_home):
    # A dialog on top keeps the dashboard's keys to itself, but not Textual's own: Tab once
    # stopped working here because every app action was switched off under a dialog.
    from textual.widgets import TextArea

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
        assert isinstance(dialog.focused, TextArea) and dialog.focused.id == "new-first"
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


def test_each_line_of_the_message_is_a_session_of_its_own_in_every_folder():
    opened = newsessions.launches(["C:/a", "C:/b"], "claude", "fix the login redirect\n\n  write the docs  ")
    assert [(launch.cwd, launch.prompt) for launch in opened] == [
        ("C:/a", "fix the login redirect"), ("C:/a", "write the docs"),
        ("C:/b", "fix the login redirect"), ("C:/b", "write the docs")]
    assert opened[0].title == "a: fix the login redirect"  # the tab says which is which
    assert newsessions.launches(["C:/a"], "claude", "")[0].title == "a: new"


def test_linked_lines_are_a_team_of_peers_named_by_their_lines(tmp_path):
    team = newsessions.linked_team(str(tmp_path / "Docs Site"), "api: the API reference @opus\nthe site's pages",
                                   newsessions.picks.Pick("sonnet", "high"), {"docs-site"})
    assert team.name == "docs-site-2" and team.peers  # the name is the folder's, made unique
    assert [(m.name, m.focus, m.model, m.effort) for m in team.members] == [
        ("api", "the API reference", "opus", "high"), ("second", "the site's pages", "sonnet", "high")]
    assert team.goal == "the API reference; the site's pages"


async def test_enter_in_the_message_adds_a_line_and_ctrl_l_links_them(fake_home, tmp_path, monkeypatch):
    from tabmancer import teams
    from tabmancer.teamview import TeamScreen

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    folder = tmp_path / "docs"
    folder.mkdir()
    monkeypatch.setattr(newsessions, "projects", lambda sessions: [newsessions.Project(str(folder))])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await box_has_focus(pilot, app)
        dialog = app.screen
        await pilot.press("space", "tab")  # the folder ticked, the message box next
        assert dialog.focused is dialog.message
        await pilot.press(*"the API reference", "enter", *"the pages")  # Enter here: a line more, not a start
        assert dialog.message.text == "the API reference\nthe pages" and not opened
        assert "2 sessions apart" in str(dialog.query_one("#new-link").render())
        await pilot.press("ctrl+l")
        assert "● linked" in str(dialog.query_one("#new-link").render())
        await pilot.press("ctrl+s")
        await until(pilot, lambda: len(opened) == 2)
        await until(pilot, lambda: isinstance(app.screen, TeamScreen))
    team = teams.load()["docs"]
    assert team.peers and [m.name for m in team.members] == ["first", "second"]
    assert all("--name" in launch.argv for launch in opened) and opened[0].argv[-1].startswith("Goal: ")


async def test_with_many_projects_everything_stays_on_a_120x30_screen(fake_home, tmp_path, monkeypatch):
    found = []
    for k in range(25):
        (tmp_path / f"p{k}").mkdir()
        found.append(newsessions.Project(str(tmp_path / f"p{k}"), sessions=1))
    monkeypatch.setattr(newsessions, "projects", lambda sessions: found)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await pilot.pause(0.2)
        dialog = app.screen
        # The list once took 18 rows and pushed the message box, the picks and the keys out of sight.
        assert dialog.query_one(".hint").region.bottom <= 29 and dialog.message.region.height >= 1
        assert dialog.folders.region.height >= 3


def test_new_command_takes_several_prompts_and_links_them(tmp_path, monkeypatch):
    from tabmancer import cli, teams

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    (tmp_path / "proj").mkdir()
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "one", "--prompt", "two"])
    assert [launch.prompt for launch in opened] == ["one", "two"]
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "api: one", "--prompt", "two", "--linked"])
    assert teams.load()["proj"].peers and len(opened) == 4
