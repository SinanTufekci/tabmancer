import json
import os
import time

import pytest
from conftest import wait_until

from tabmancer import cleanup, diskclean, sessions, summarize
from tabmancer.theme import DEFAULT_THEME


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    project = tmp_path / "projects" / "-work-demo"
    project.mkdir(parents=True)

    def add(sid, prompt="hello", days_old=0, history_bytes=0, subagents=False):
        records = [{"type": "user", "cwd": "/work/demo", "entrypoint": "cli", "message": {"content": prompt}},
                   {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}],
                                                     "stop_reason": "end_turn"}},
                   {"type": "cost-state"}]
        path = project / f"{sid}.jsonl"
        path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                        encoding="utf-8")
        if history_bytes:
            (tmp_path / "file-history" / sid).mkdir(parents=True)
            (tmp_path / "file-history" / sid / "snap").write_bytes(b"x" * history_bytes)
        if subagents:
            (project / sid / "subagents").mkdir(parents=True)
            (project / sid / "subagents" / "a.jsonl").write_text("{}", encoding="utf-8")
        stamp = time.time() - days_old * 86400
        os.utime(path, (stamp, stamp))
        return path

    return tmp_path, add


def test_collect_measures_every_file_of_a_session(home):
    _, add = home
    add("big", history_bytes=10_000, subagents=True)
    add("small")
    items = cleanup.collect()
    assert [i.session.session_id for i in items] == ["big", "small"]  # biggest first
    big = items[0]
    assert {p.name for p in big.files} == {"big.jsonl", "big"}  # transcript, its folder, file-history
    assert len(big.files) == 3
    assert big.size > 10_000


def test_collect_lists_sessions_with_nothing_to_resume(home):
    _, add = home
    add("cleared", prompt="<command-name>/clear</command-name>")
    (item,) = cleanup.collect()
    assert "nothing to resume" in item.session.title


def test_trash_skips_open_sessions_and_forgets_summaries(home, monkeypatch):
    _, add = home
    add("closed", history_bytes=10)
    add("open")
    monkeypatch.setattr(sessions, "_agents_json", lambda: [{"sessionId": "open", "kind": "interactive"}])
    summarize._store("closed", {"summary": "x", "size": 1})
    trashed = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: trashed.append(path.name))
    items = {i.session.session_id: i for i in cleanup.collect()}
    moved, problems = diskclean.trash(list(items.values()))
    assert [i.session.session_id for i in moved] == ["closed"]
    assert sorted(trashed) == ["closed", "closed.jsonl"]
    assert problems == ["demo: open right now, skipped"]
    assert "closed" not in summarize.load_cache()


def test_trash_refuses_paths_outside_claude_dir(home, tmp_path_factory, monkeypatch):
    _, add = home
    path = add("s")
    outside = tmp_path_factory.mktemp("elsewhere") / "x.txt"
    outside.write_text("keep me", encoding="utf-8")
    item = cleanup.Item(sessions.read_session(path), [path, outside], 1)
    trashed = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda p: trashed.append(p))
    moved, problems = diskclean.trash([item])
    assert trashed == [path] and moved == []
    assert "outside" in problems[0]


def test_claude_cleanup_days(home):
    root, _ = home
    assert cleanup.claude_cleanup_days() == 30
    (root / "settings.json").write_text('{"cleanupPeriodDays": 7}', encoding="utf-8")
    assert cleanup.claude_cleanup_days() == 7


def test_human_sizes():
    assert cleanup.human(512) == "512 B"
    assert cleanup.human(2048) == "2 KB"
    assert cleanup.human(5 * 1024 ** 2) == "5.0 MB"
    assert cleanup.human(3 * 1024 ** 3) == "3.0 GB"


async def test_cleanup_app_select_confirm_and_delete(home, monkeypatch):
    _, add = home
    add("old", days_old=20, history_bytes=100)
    add("new")
    trashed = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: trashed.append(path.name))
    app = cleanup.CleanupApp()
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: app.items)
        assert app.theme == DEFAULT_THEME  # the dashboard's black theme, not Textual's default
        await pilot.press("o")  # older than 7 days
        assert app.selected == {"old"}
        await pilot.press("enter", "n")  # asked, then declined
        assert trashed == []
        await pilot.press("enter", "y")
        assert sorted(trashed) == ["old", "old.jsonl"]
        assert set(app.items) == {"new"} and app.selected == set()


async def test_a_problem_shows_as_plain_words_not_markup(home, monkeypatch):
    """An OS error's words reach a note as they are: a '[/b]' in one is shown, not parsed as markup."""
    _, add = home
    add("old", days_old=20)

    def refuse(path):
        raise OSError("denied [/b] [red]x")

    monkeypatch.setattr(diskclean, "_trash_one", refuse)
    app = cleanup.CleanupApp()
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: app.items)
        await pilot.press("o", "enter", "y")
        await pilot.pause()
        said = [note.message for note in app._notifications]
        assert any("denied [/b] [red]x" in message for message in said), said
        assert all(not note.markup for note in app._notifications)


def test_trash_asks_again_what_is_open(home, monkeypatch):
    # Listed while closed, then resumed while the clean-up screen stood open: still not moved.
    _, add = home
    add("later-open")
    trashed = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: trashed.append(path.name))
    items = cleanup.collect()  # nothing open yet
    assert not items[0].is_open
    monkeypatch.setattr(diskclean, "live_sessions", lambda: {"later-open": {"sessionId": "later-open"}})
    moved, problems = diskclean.trash(items)
    assert not moved and not trashed and problems == ["demo: open right now, skipped"]


def test_moving_to_the_recycle_bin_is_diskclean_alone():
    """A stand-in set on cleanup for trash or _trash_one would not reach the Recycle Bin: they are
    not there, so setting one fails."""
    assert not any(hasattr(cleanup, name) for name in ("trash", "_trash_one", "live_sessions"))
    assert cleanup.collect is diskclean.collect and cleanup.Item is diskclean.Item
    with pytest.raises(AttributeError):
        pytest.MonkeyPatch().setattr(cleanup, "_trash_one", lambda path: None)


# --- `tabmancer cleanup` in plain text: --list, --older, --yes, --json --------------------------------

@pytest.fixture
def moves(monkeypatch):
    """What would reach the Recycle Bin, kept here instead: no test moves a real file."""
    trashed = []
    monkeypatch.setattr(diskclean, "_trash_one", lambda path: trashed.append(path.name))
    return trashed


def cleanup_cli(capsys, *flags) -> tuple[int, str, str]:
    from tabmancer.cli import main

    with pytest.raises(SystemExit) as done:
        main(["cleanup", *flags])
    out = capsys.readouterr()
    return done.value.code, out.out, out.err


def test_cleanup_list_shows_every_session_and_moves_nothing(home, moves, monkeypatch, capsys):
    _, add = home
    add("old", prompt="the old cart", days_old=20, history_bytes=5000)
    add("open", prompt="still at work")
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "open", "kind": "interactive"}])
    code, out, _ = cleanup_cli(capsys, "--list")
    assert code == 0 and not moves
    lines = out.splitlines()
    assert lines[0].startswith("2 sessions use ") and "older than 30 days on its own" in lines[0]
    assert "the old cart" in lines[1] and "(open" not in lines[1]  # biggest first
    assert "still at work" in lines[2] and lines[2].endswith("(open, kept)")
    assert "--older DAYS" in lines[-1]


def test_cleanup_older_says_what_it_would_move_and_moves_nothing(home, moves, capsys):
    _, add = home
    add("old", prompt="the old cart", days_old=20)
    add("new", prompt="this week")
    code, out, _ = cleanup_cli(capsys, "--older", "7")
    assert code == 0 and not moves
    assert "the old cart" in out and "this week" not in out
    assert "Would move 1 sessions" in out and "more than 7 days ago" in out and "add --yes" in out


def test_cleanup_yes_moves_what_older_picks_and_keeps_open_ones(home, moves, monkeypatch, capsys):
    _, add = home
    add("old", days_old=20, history_bytes=100)
    add("old-open", days_old=20)
    add("new")
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "old-open", "kind": "interactive"}])
    code, out, _ = cleanup_cli(capsys, "--older", "7", "--yes")
    assert code == 0 and sorted(moves) == ["old", "old.jsonl"]
    assert out.startswith("Moved 1 sessions (") and "to the " in out


def test_cleanup_yes_says_each_problem_as_plain_words(home, monkeypatch, capsys):
    _, add = home
    add("old", days_old=20)

    def refuse(path):
        raise OSError("denied [/b]")

    monkeypatch.setattr(diskclean, "_trash_one", refuse)
    code, out, _ = cleanup_cli(capsys, "--older", "7", "--yes")
    assert code == 1 and "Moved 0 sessions" in out and "not moved: " in out and "denied [/b]" in out


def test_cleanup_yes_without_older_moves_nothing(home, moves, capsys):
    _, add = home
    add("old", days_old=20)
    code, out, err = cleanup_cli(capsys, "--yes")
    assert code == 2 and not moves and not out and "--older" in err


def test_cleanup_json_lists_and_marks_what_older_picks(home, moves, capsys):
    _, add = home
    add("old", prompt="the old cart", days_old=20)
    add("new")
    code, out, _ = cleanup_cli(capsys, "--json")
    listed = json.loads(out)
    assert code == 0 and sorted(entry["session_id"] for entry in listed) == ["new", "old"]
    assert all("picked" not in entry for entry in listed) and listed[0]["files"]
    code, out, _ = cleanup_cli(capsys, "--older", "7", "--json")
    assert {entry["session_id"]: entry["picked"] for entry in json.loads(out)} == {"old": True, "new": False}
    code, out, _ = cleanup_cli(capsys, "--older", "7", "--yes", "--json")
    done = json.loads(out)
    assert done["moved"] == ["old"] and done["freed"] > 0 and done["problems"] == [] and moves


def test_cleanup_with_no_flag_opens_the_screen(home, moves, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(cleanup.CleanupApp, "run", lambda self: ran.append(self))
    code, out, _ = cleanup_cli(capsys)
    assert code == 0 and len(ran) == 1 and not out and not moves
