import json
import os
import time

import pytest

from tabmancer import cleanup, sessions, summarize
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
    monkeypatch.setattr(cleanup, "_trash_one", lambda path: trashed.append(path.name))
    items = {i.session.session_id: i for i in cleanup.collect()}
    moved, problems = cleanup.trash(list(items.values()))
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
    monkeypatch.setattr(cleanup, "_trash_one", lambda p: trashed.append(p))
    moved, problems = cleanup.trash([item])
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
    monkeypatch.setattr(cleanup, "_trash_one", lambda path: trashed.append(path.name))
    app = cleanup.CleanupApp()
    async with app.run_test() as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.items:
                break
        assert app.theme == DEFAULT_THEME  # the dashboard's black theme, not Textual's default
        await pilot.press("o")  # older than 7 days
        assert app.selected == {"old"}
        await pilot.press("enter", "n")  # asked, then declined
        assert trashed == []
        await pilot.press("enter", "y")
        assert sorted(trashed) == ["old", "old.jsonl"]
        assert set(app.items) == {"new"} and app.selected == set()
