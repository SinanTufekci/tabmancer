"""Token counts from transcript usage records: each reply once, read incrementally."""

from __future__ import annotations

import io
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from tabmancer import tokens as T


def stamp(days_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return moment.isoformat().replace("+00:00", "Z")


def reply(message_id: str, output: int = 10, read: int = 1000, days_ago: int = 0, model="claude-opus-5-5"):
    return {"type": "assistant", "cwd": "C:/work/demo", "timestamp": stamp(days_ago),
            "message": {"id": message_id, "model": model, "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 50,
                                  "cache_read_input_tokens": read, "output_tokens": output}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects" / "C--work-demo").mkdir(parents=True)
    return tmp_path


def write(home, name, records, mode="w", end="\n"):
    path = home / "projects" / "C--work-demo" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + end)
    return path


def test_a_reply_split_over_records_counts_once(home):
    write(home, "s1", [reply("msg_a"), reply("msg_a"), reply("msg_b", output=5)])
    use = T.collect(14).session("s1")
    assert use.output == 15 and use.cache_read == 2000 and use.input == 4 and use.cache_write == 100
    assert use.total == 15 + 2000 + 4 + 100


def test_a_fork_does_not_count_its_copied_history_again(home):
    write(home, "parent", [reply("msg_a"), reply("msg_b")])
    time.sleep(0.05)  # the fork is created later
    write(home, "fork", [reply("msg_a"), reply("msg_b"), reply("msg_c", output=7)])
    report = T.collect(14)
    assert report.session("parent").output == 20
    assert report.session("fork").output == 7  # only what the fork itself added
    assert report.total().output == 27


def test_only_appended_lines_are_read_again(home, monkeypatch):
    path = write(home, "s1", [reply("msg_a")])
    assert T.collect(14).session("s1").output == 10
    write(home, "s1", [reply("msg_b", output=3)], mode="a", end="")  # no newline: still being written
    assert T.collect(14).session("s1").output == 10
    with path.open("a", encoding="utf-8") as f:
        f.write("\n")
    seen = []
    real = T._scan
    monkeypatch.setattr(T, "_scan", lambda p, entry: (seen.append(entry["offset"]), real(p, entry)))
    assert T.collect(14).session("s1").output == 13
    assert seen and seen[0] > 0  # carried on from where it stopped


def test_a_transcript_rewritten_shorter_is_read_from_the_start(home):
    write(home, "s1", [reply("msg_a"), reply("msg_b"), reply("msg_c")])
    assert T.collect(14).session("s1").output == 30
    write(home, "s1", [reply("msg_z", output=1)])
    assert T.collect(14).session("s1").output == 1


def test_the_window_limits_totals_but_not_a_sessions_whole_life(home):
    write(home, "s1", [reply("msg_old", output=100, days_ago=20), reply("msg_new", output=1)])
    report = T.collect(14)
    assert report.total().output == 1
    assert report.session("s1").output == 101
    assert report.daily()[-1].output == 1 and len(report.daily()) == 14
    assert report.total(last_days=1).output == 1
    assert report.first == date.today() - timedelta(days=13)


def test_subagents_count_for_their_session(home):
    write(home, "s1", [reply("msg_a")])
    write(home, "s1/subagents/agent-x", [dict(reply("msg_sub", output=4, model="claude-haiku-5-5"),
                                              isSidechain=True)])
    report = T.collect(14)
    assert report.session("s1").output == 14
    assert set(report.models) == {"claude-opus-5-5", "claude-haiku-5-5"}
    assert report.folders["s1"] == "C:/work/demo"


def test_odd_records_are_skipped(home):
    path = write(home, "s1", [reply("msg_a"), {"type": "assistant", "message": "not a dict"},
                              {"type": "user", "message": {"usage": {"output_tokens": 9}}},
                              dict(reply("msg_zero"), message={"id": "msg_zero", "usage": {}})])
    with path.open("ab") as f:
        f.write(b'{"type":"assistant","usage" broken\n\x00\x00\n')
    assert T.collect(14).session("s1").output == 10


def test_human_numbers():
    assert [T.human(n) for n in (0, 950, 1000, 1234, 999_960, 5_300_000, 152_000_000, 8_264_000_000)] == \
        ["0", "950", "1k", "1.2k", "1M", "5.3M", "152M", "8.3B"]


# --- in the dashboard and on the command line ---------------------------------------

async def counted(pilot, app):
    for _ in range(80):
        await pilot.pause(0.05)
        if app.sessions and app.token_report is not None:
            return
    raise AssertionError("tokens were never counted")


def add_reply(home, folder, name, records):
    path = home / "projects" / folder / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))


async def test_the_dashboard_shows_where_tokens_went(fake_home):
    from rich.console import Console

    from tabmancer.app import SessionPicker
    from tabmancer.statsview import StatsScreen as TokensScreen
    from tabmancer.statsview import render
    from tabmancer.widgets import folder_key

    add_reply(fake_home, "C--work-demo", "one", [reply("msg_a", output=2_000_000, read=0)])
    other = dict(reply("msg_b", output=500_000, read=0), cwd="C:/work/other")
    add_reply(fake_home, "C--work-other", "three", [{**other, "type": "user", "entrypoint": "cli",
                                                      "message": {"content": "go"}}, other])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await counted(pilot, app)
        assert app.cards["card-tokens"].caption_text == "million · 2.5M output"
        one = app.sessions["one"]
        labels = [str(cell) for cell in app.meta_table(one).columns[0]._cells]
        assert "Tokens" in labels
        assert app.activity_target[-1] == 2_500_000 + 2 * 52  # both projects, today (output + 2 fresh + 50 written)
        app.project_filter = folder_key("C:/work/demo")
        app.update_chart()
        assert app.activity_target[-1] == 2_000_052
        await pilot.press("u")
        assert isinstance(app.screen, TokensScreen)
        console = Console(record=True, width=120, file=io.StringIO())
        console.print(render(app.token_report, app.sessions, "#d97757"))
        shown = console.export_text()
        assert "TOP SESSIONS" in shown and "First task" in shown and "other" in shown
        assert "claude-opus-5-5" in shown
        await pilot.press("escape")
        assert not isinstance(app.screen, TokensScreen)


def test_tokens_command_prints_json(home, capsys):
    from tabmancer.cli import main

    write(home, "s1", [reply("msg_a", output=7)])
    main(["tokens", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["sessions"][0]["session_id"] == "s1" and data["sessions"][0]["output"] == 7
    assert len(data["days"]) == 14 and data["days"][-1]["output"] == 7
    assert data["models"]["claude-opus-5-5"]["total"] == 7 + 1000 + 2 + 50


def test_a_report_from_before_midnight_still_compares_its_weeks():
    # The dashboard open overnight: the report collected yesterday, the stats asked for just after
    # midnight. Counted from today, the week ran past the window's end (IndexError).
    from datetime import date, timedelta

    from tabmancer import tokens

    report = tokens.Report(date.today() - timedelta(days=14), 14)
    assert report.last == date.today() - timedelta(days=1)
    assert report.week_against_last() is not None
