"""The history: every finished day kept as a summary, so the stats reach back past the transcripts
Claude Code deletes, and Claude Code's own record (stats-cache.json) for the days before."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tabmancer import history
from tabmancer import tokens as T
from tabmancer.config import write_json


def stamp(days_ago: int, hour: int = 12) -> str:
    day = date.today() - timedelta(days=days_ago)
    moment = datetime(day.year, day.month, day.day, hour, 30).astimezone(timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


def reply(message_id: str, days_ago: int, output: int = 10, read: int = 1000, hour: int = 12, tool: str = ""):
    content = [{"type": "text", "text": "ok"}]
    if tool:
        content.append({"type": "tool_use", "id": f"t{message_id}", "name": tool, "input": {}})
    return {"type": "assistant", "cwd": "C:/work/demo", "timestamp": stamp(days_ago, hour),
            "message": {"id": message_id, "model": "claude-opus-5-5", "role": "assistant", "content": content,
                        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 50,
                                  "cache_read_input_tokens": read, "output_tokens": output}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "projects" / "C--work-demo").mkdir(parents=True)
    return tmp_path


def write(home, name, records):
    path = home / "projects" / "C--work-demo" / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")
    return path


def ordinal(days_ago: int) -> int:
    return (date.today() - timedelta(days=days_ago)).toordinal()


def test_finished_days_are_kept_and_today_is_not(home):
    write(home, "s1", [reply("a", 3, hour=10, tool="Bash"), reply("b", 3, hour=10), reply("c", 0)])
    T.collect(14)  # every count keeps the days it has seen finish
    kept = history.load()
    assert set(kept) == {ordinal(3)}  # not today: it is not over
    day = kept[ordinal(3)]
    assert day.use.output == 20 and day.use.cache_read == 2000 and day.sessions == 1
    assert day.models["claude-opus-5-5"].output == 20 and day.tools == {"Bash": 1}
    assert sum(day.hours) == day.seconds > 0 and day.hours[10] > 0  # the hour it was worked in
    (folder, (use, sessions, seconds)), = day.projects.items()
    assert folder == "C:/work/demo" and use.output == 20 and sessions == 1 and seconds == day.seconds


def test_a_kept_day_stands_in_once_its_transcript_is_gone(home):
    gone = write(home, "old", [reply("a", 3, output=500), reply("b", 3, output=500)])
    write(home, "new", [reply("c", 0)])
    T.collect(14)
    gone.unlink()  # Claude Code's cleanup
    plain = T.collect(14)
    assert plain.daily()[-4].output == 0  # the files alone have lost it
    report = history.collect(14)
    assert report.daily()[-4].output == 1000 and report.total().output == 1010
    assert report.models["claude-opus-5-5"].output == 1010
    assert report.activity.day_seconds[-4] > 0 and report.activity.sessions_per_day[-4] == 1
    assert ordinal(3) in report.archive and ordinal(0) not in report.archive
    assert history.load()[ordinal(3)].use.output == 1000  # a smaller count never writes over a day kept


def test_a_day_is_kept_again_when_a_count_finds_more_in_it(home):
    write(home, "s1", [reply("a", 2)])
    T.collect(14)
    write(home, "s2", [reply("b", 2, output=90)])
    T.collect(14)
    assert history.load()[ordinal(2)].use.output == 100


def test_claude_codes_own_record_tells_the_days_before(home):
    (home / "stats-cache.json").write_text(json.dumps({
        "version": 5,
        "dailyActivity": [{"date": (date.today() - timedelta(days=40)).isoformat(), "messageCount": 9,
                           "sessionCount": 3, "toolCallCount": 70},
                          {"date": "not a date", "sessionCount": 1}, "odd"],
        "dailyModelTokens": [{"date": (date.today() - timedelta(days=40)).isoformat(),
                              "tokensByModel": {"claude-opus-5": 5000, "claude-haiku-5-5": "lots", "x": -3}}],
    }), encoding="utf-8")
    write(home, "s1", [reply("a", 0)])
    days = history.claude_days()
    assert set(days) == {ordinal(40)}
    day = days[ordinal(40)]
    assert day.source == "claude" and day.use.unsplit == 5000 and day.use.total == 5000
    assert day.sessions == 3 and day.calls == 70 and set(day.models) == {"claude-opus-5"}
    report = history.collect(None)  # all of it: from Claude Code's first day
    assert report.first == date.today() - timedelta(days=40) and report.days == 41
    assert report.total().unsplit == 5000 and report.total().total == 5000 + 1062
    assert report.activity.sessions_per_day[0] == 3 and report.calls() == 70


def test_odd_history_files_are_read_as_far_as_they_make_sense(home):
    write_json(history.FILE, {"format": history.FORMAT, "days": {
        "2026-01-02": {"use": [1, 2, 3, 4], "hours": ["x"], "projects": {"f": [[1], 2]}, "seconds": "long"},
        "nonsense": {}, "2026-01-03": "odd"}})
    kept = history.load()
    assert list(kept) == [date(2026, 1, 2).toordinal()]
    day = kept[date(2026, 1, 2).toordinal()]
    assert day.use.total == 10 and day.hours == [0.0] * 24 and day.projects == {} and day.seconds == 0
    write_json(history.FILE, {"format": 99, "days": {}})
    assert history.load() == {}
