"""The wider stats: time at work, hours of the week, tools and files, limits, export."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tabmancer import clis, stats, usage
from tabmancer import tokens as T
from tabmancer.sessions import Session


def at(hour, minute=0, days_ago=0):
    moment = datetime.combine(date.today() - timedelta(days=days_ago), datetime.min.time()).replace(
        hour=hour, minute=minute)
    return moment


def stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reply(message_id, moment, tools=(), output=10, cwd="C:/work/demo"):
    content = [{"type": "tool_use", "id": f"t{i}", "name": name,
                "input": {"file_path": f"{cwd}/src/{i}.py"}} for i, name in enumerate(tools)]
    return {"type": "assistant", "cwd": cwd, "timestamp": stamp(moment),
            "message": {"id": message_id, "model": "claude-opus-5-5", "content": content or
                        [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 100, "output_tokens": output}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude"


def write(home, name, records):
    path = home / "projects" / "C--work-demo" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact like Claude Code's own transcripts, which the scan relies on.
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")


def test_time_at_work_skips_breaks_and_counts_side_by_side_sessions_once(home):
    write(home, "a", [reply("a1", at(9)), reply("a2", at(9, 4)), reply("a3", at(9, 30))])  # break before 9:30
    write(home, "b", [reply("b1", at(9, 1)), reply("b2", at(9, 3))])  # alongside a
    activity = T.collect(14).activity
    # a: 1 min + 4 min + 1 min after the break = 6 min; b inside a's span adds nothing on the clock.
    assert activity.day_seconds[-1] == 6 * 60
    assert activity.session_seconds == {"a": 6 * 60, "b": 3 * 60}
    assert activity.sessions_per_day[-1] == 2
    assert activity.slot_seconds[at(9).weekday()][9] == 6 * 60 - 60  # the first minute was before 9:00
    assert activity.slot_seconds[at(9).weekday()][8] == 60
    report = T.collect(14)
    assert report.wall_seconds({"a", "b"}) == 6 * 60 and report.wall_seconds({"b"}) == 3 * 60


def test_tools_and_edited_files_come_from_every_block_of_a_reply(home):
    first = reply("m1", at(10), tools=["Edit"])
    second = reply("m1", at(10), tools=["mcp__kicad__run_drc"])  # the same reply, its next block
    write(home, "a", [first, second, reply("m2", at(10, 1), tools=["Bash", "Write"])])
    report = T.collect(14)
    assert dict(stats.tools(report)) == {"Edit": 1, "kicad (MCP)": 1, "Bash": 1, "Write": 1}
    sessions = {"a": Session(session_id="a", path=None, cwd="C:/work/demo")}
    files = stats.edited_files(report, sessions)
    assert {(p, f.replace("\\", "/")) for p, f, _ in files} == {("demo", "src/0.py"), ("demo", "src/1.py")}


def test_this_week_against_the_last(home):
    write(home, "a", [reply("a1", at(9), output=30), reply("a2", at(9, days_ago=8), output=10)])
    now, before = T.collect(14).week_against_last()
    assert now["output"] == 30 and before["output"] == 10 and now["sessions"] == 1
    assert stats.change(now["output"], before["output"]) == "+200%"
    assert stats.change(5, 0) == "new" and stats.change(0, 0) == "–"
    assert T.collect(7).week_against_last() is None  # a week is too short to compare


def test_codex_tokens_come_from_its_running_totals(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    clis.set_enabled(["claude", "codex"])
    folder = tmp_path / "codex" / "sessions" / "2026" / "10" / "16"
    folder.mkdir(parents=True)
    sid = "01a13ee9-1c80-70e1-90ff-8b47b399d7bd"

    def count(total_in, cached, out, minute):
        return {"timestamp": stamp(at(11, minute)), "type": "event_msg",
                "payload": {"type": "token_count", "info": {"total_token_usage": {
                    "input_tokens": total_in, "cached_input_tokens": cached, "output_tokens": out}}}}

    records = [{"timestamp": stamp(at(11)), "type": "session_meta", "payload": {"id": sid, "cwd": "C:/work/api"}},
               {"timestamp": stamp(at(11)), "type": "turn_context", "payload": {"model": "gpt-5.5"}},
               count(1000, 800, 50, 1), count(1000, 800, 50, 2),  # repeated: nothing new
               {"timestamp": stamp(at(11, 3)), "type": "response_item",
                "payload": {"type": "custom_tool_call", "name": "apply_patch",
                            "input": "*** Update File: src/app.py\n"}},
               count(3000, 2500, 120, 4)]
    (folder / f"rollout-2026-10-16T11-00-00-{sid}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    report = T.collect(14)
    use = report.session(sid)
    assert (use.input, use.cache_read, use.output) == (500, 2500, 120)
    assert report.clis[sid] == "codex" and report.folders[sid] == "C:/work/api"
    assert set(report.models) == {"gpt-5.5"}
    assert dict(stats.tools(report)) == {"apply_patch": 1}


# --- plan limits ----------------------------------------------------------------------------

def test_readings_are_kept_and_forecast(monkeypatch):
    now = datetime(2026, 10, 16, 12, 0)
    resets = datetime(2026, 10, 18, 9, 0)
    for hours, percent in ((3, 40), (2, 46), (1, 52), (0, 58)):
        usage.record([usage.Limit("week", percent, resets, ""), usage.Limit("session", 10, None, "")],
                     now=now - timedelta(hours=hours))
    readings = usage.history(7, now=now)
    assert len(readings) == 8
    guess = usage.forecast(readings, "week", now=now)
    assert guess.percent == 58 and guess.per_hour == pytest.approx(6.0)
    assert guess.full_at == now + timedelta(hours=7) and guess.fills_first
    assert usage.forecast(readings, "session", now=now).full_at is None  # flat: not filling
    assert usage.forecast(readings, "nothing", now=now) is None


def test_a_reset_starts_the_forecast_over():
    now = datetime(2026, 10, 16, 12, 0)
    readings = [usage.Reading(now - timedelta(hours=3), "session", 90, None),
                usage.Reading(now - timedelta(hours=2), "session", 5, None),
                usage.Reading(now - timedelta(minutes=30), "session", 20, None)]
    guess = usage.forecast(readings, "session", now=now)
    assert guess.per_hour == pytest.approx(10.0)  # only the readings after the drop


def test_too_few_readings_give_no_forecast():
    now = datetime(2026, 10, 16, 12, 0)
    readings = [usage.Reading(now - timedelta(minutes=5), "week", 10, None), usage.Reading(now, "week", 11, None)]
    assert usage.forecast(readings, "week", now=now) is None


# --- export, the screen and the command -----------------------------------------------------

def test_export_and_csv(home, tmp_path):
    write(home, "a", [reply("a1", at(9), tools=["Edit"]), reply("a2", at(9, 2))])
    report = T.collect(7)
    data = stats.export(report, {})
    assert data["days"] == 7 and len(data["daily"]) == 7 and data["daily"][-1]["minutes"] == 3
    assert data["sessions"][0]["session_id"] == "a" and data["tools"] == {"Edit": 1}
    lines = stats.to_csv(report).splitlines()
    assert lines[0].startswith("date,sessions,minutes,input") and len(lines) == 8
    as_json, as_csv = stats.write_files(report, {}, folder=tmp_path)
    assert json.loads(as_json.read_text(encoding="utf-8"))["days"] == 7 and as_csv.exists()


async def test_the_stats_screen_has_tabs_periods_and_export(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker
    from tabmancer.statsview import StatsScreen

    saved = []
    monkeypatch.setattr(stats, "write_files", lambda report, sessions: saved.append(report.days) or (
        fake_home / "s.json", fake_home / "s.csv"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("u")
        screen = app.screen
        assert isinstance(screen, StatsScreen) and screen.report.days == 14
        await pilot.press("right")
        assert screen.query_one("TabbedContent").active == "work"
        await pilot.press("left", "left")
        assert screen.query_one("TabbedContent").active == "report"  # round the end
        await pilot.press("left")
        assert screen.query_one("TabbedContent").active == "limits"
        await pilot.press("3")
        for _ in range(80):
            await pilot.pause(0.05)
            if screen.report.days == 30:
                break
        assert screen.report.days == 30 and not screen.counting
        await pilot.press("x")
        for _ in range(40):
            await pilot.pause(0.05)
            if saved:
                break
        assert saved == [30]


def test_stats_command(home, capsys):
    from tabmancer.cli import main

    write(home, "a", [reply("a1", at(9), tools=["Bash"])])
    main(["stats", "--json", "--days", "7"])
    assert json.loads(capsys.readouterr().out)["tools"] == {"Bash": 1}
    main(["stats", "--csv", "--days", "7"])
    assert capsys.readouterr().out.startswith("date,")
    main(["stats", "--days", "7"])
    shown = capsys.readouterr().out
    assert "WHEN YOU WORK" in shown and "TOOLS AND FILES" in shown and "PLAN LIMITS" in shown
