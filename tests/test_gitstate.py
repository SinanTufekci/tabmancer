"""Where each project's folder stands in git: changed files, commits to push, in the sidebar and
the preview."""

from __future__ import annotations

import io
import json
import subprocess

from rich.console import Console

from tabmancer import gitstate
from tabmancer.app import SessionPicker
from tabmancer.widgets import folder_key

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]


def git(folder, *args) -> None:
    subprocess.run([*GIT, "-C", str(folder), *args], check=True, capture_output=True)


def plain(renderable) -> str:
    console = Console(width=60, file=io.StringIO(), record=True)
    console.print(renderable)
    return console.export_text()


def test_the_status_lines_are_read():
    state = gitstate.parse("# branch.oid abc\n# branch.head main\n# branch.upstream origin/main\n"
                           "# branch.ab +2 -1\n1 .M N... 100644 100644 100644 a b src/x.py\n"
                           "2 R. N... 100644 100644 100644 a b R100 new.py\told.py\n? notes.txt\n! ignored.log\n")
    assert state == gitstate.GitState("main", changed=3, ahead=2, behind=1, upstream=True)
    assert state.words() == "3 changed · 2 to push · 1 to pull"
    assert gitstate.GitState("main", upstream=True).words() == "clean"
    assert gitstate.GitState("main").words() == "no upstream"


def test_a_real_repository_its_changes_and_what_is_not_pushed(tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "clone", str(remote), str(work))
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    git(work, "add", "a.txt")
    git(work, "commit", "-m", "first")
    git(work, "push", "-u", "origin", "HEAD")
    assert gitstate.read(str(work)) == gitstate.GitState("main", upstream=True)
    git(work, "commit", "--allow-empty", "-m", "second")
    (work / "a.txt").write_text("two\n", encoding="utf-8")
    (work / "b.txt").write_text("new\n", encoding="utf-8")
    assert gitstate.read(str(work)) == gitstate.GitState("main", changed=2, ahead=1, upstream=True)
    assert gitstate.read(str(tmp_path)) is None  # not a repository
    states, failed = gitstate.read_all([str(work), str(tmp_path), str(work)])
    assert list(states) == [folder_key(str(work))] and not failed


def test_git_that_does_not_answer_is_told_apart_from_no_repository(tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", gitstate.TIMEOUT)

    monkeypatch.setattr(gitstate.subprocess, "run", slow)
    states, failed = gitstate.read_all([str(tmp_path)])
    assert not states and failed == {folder_key(str(tmp_path))}


async def test_the_sidebar_and_the_preview_say_where_the_folder_stands(fake_home, tmp_path):
    work = tmp_path / "shop"
    work.mkdir()
    git(work, "init")
    (work / "app.py").write_text("print('hi')\n", encoding="utf-8")
    project = fake_home / "projects" / "C--shop"
    project.mkdir()
    records = [{"type": "user", "cwd": str(work), "entrypoint": "cli", "message": {"content": "start"}},
               {"type": "assistant", "cwd": str(work), "message": {"content": [{"type": "text", "text": "ok"}],
                                                                   "stop_reason": "end_turn"}}]
    (project / "s.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                                     encoding="utf-8")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if app.git:
                break
        key = folder_key(str(work))
        assert app.git[key].changed == 1
        line = next(app.projects.get_option_at_index(i).prompt for i in range(app.projects.option_count)
                    if app.projects.get_option_at_index(i).id == key)
        assert "±" in plain(line) and "↑" not in plain(line)
        app.table.move_cursor(row=app.table.get_row_index("s"))
        await pilot.pause(0.1)
        assert "1 changed · no upstream" in plain(app.meta_table(app.sessions["s"]))
        app.show_git({}, failed={key})  # git did not answer this time: what was known stays
        assert app.git[key].changed == 1


def test_a_long_name_gives_way_to_the_marks_and_the_count():
    from rich.text import Text

    from tabmancer.widgets import project_line

    console = Console(width=20, file=io.StringIO(), record=True)
    console.print(project_line(("● ", ""), Text("a-very-long-project-name"), 12, "±↑"))
    line = console.export_text().rstrip("\n")
    assert line.endswith("±↑ 12") and "…" in line
