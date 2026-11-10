"""The stats' overview: the big numbers against the period before, all time, one project, the
records, and the days grouped by week or month over a long period."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tabmancer import history, stats
from tabmancer.sessions import folder_key
from tabmancer.statsview import Card, Kpis, in_text, overview_parts
from tabmancer.tokens import Day, Report, Tokens


def stamp(days_ago: int, hour: int = 10, minute: int = 30) -> str:
    day = date.today() - timedelta(days=days_ago)
    return datetime(day.year, day.month, day.day, hour, minute).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z")


def reply(message_id: str, days_ago: int, output: int = 10, cwd: str = "C:/work/alpha", minute: int = 30,
          tool: str = "") -> dict:
    content = [{"type": "tool_use", "id": f"t{message_id}", "name": tool, "input": {}}] if tool else \
        [{"type": "text", "text": "ok"}]
    return {"type": "assistant", "cwd": cwd, "timestamp": stamp(days_ago, minute=minute),
            "message": {"id": message_id, "model": "claude-opus-5-5", "content": content,
                        "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0, "output_tokens": output}}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude"


def write(home, name: str, records: list[dict]) -> None:
    path = home / "projects" / "C--work" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")


def kpis(parts: list) -> dict[str, object]:
    return {kpi.title: kpi for kpi in next(p for p in parts if isinstance(p, Kpis)).cards}


def test_the_overview_sets_the_period_against_the_one_before(home):
    write(home, "old", [reply("o1", 10, output=100), reply("o2", 10, output=100, minute=32)])
    write(home, "new", [reply("n1", 2, output=300), reply("n2", 2, output=300, minute=33)])
    write(home, "other", [reply("x1", 1, output=50, cwd="C:/work/beta")])
    report, longer = history.collect(7), history.collect(14)
    before = stats.before(longer, 7, {})
    assert before.tokens.output == 200 and before.sessions == 1 and before.days == 1 and before.timed
    assert before.projects == {folder_key("C:/work/alpha"): 200} and before.seconds > 0
    found = kpis(overview_parts(report, {}, "#ffffff", before))
    assert found["TOKENS"].change == "▲ 225%"  # 650 against 200
    assert found["SESSIONS"].change == "▲ 100%" and found["PROJECTS"].number == "2"
    assert found["PROJECTS"].change == "▲ 100%" and found["AT WORK"].change.startswith("▲")
    text = in_text(overview_parts(report, {}, "#ffffff", before), "#ffffff")
    assert any(isinstance(p, Card) and p.title == "TOP PROJECTS" for p in overview_parts(report, {}, "#fff", before))
    assert text  # renders


def test_nothing_before_is_said_once_not_as_new_on_every_figure(home):
    write(home, "new", [reply("n1", 1)])
    report = history.collect(7)
    parts = overview_parts(report, {}, "#fff", stats.before(history.collect(14), 7, {}))
    assert all(kpi.change == "" for kpi in kpis(parts).values())
    assert any("Nothing in the 7 days before" in getattr(p, "plain", "") for p in parts)


def test_days_from_claude_codes_record_are_set_against_for_tokens_only(home):
    (home / "stats-cache.json").parent.mkdir(parents=True, exist_ok=True)
    (home / "stats-cache.json").write_text(json.dumps({"dailyModelTokens": [
        {"date": (date.today() - timedelta(days=10)).isoformat(), "tokensByModel": {"claude-opus-5": 100}}],
        "dailyActivity": [{"date": (date.today() - timedelta(days=10)).isoformat(), "sessionCount": 2}]}),
        encoding="utf-8")
    write(home, "new", [reply("n1", 1, output=200)])
    before = stats.before(history.collect(14), 7, {})
    assert not before.timed and before.tokens.unsplit == 100 and before.sessions == 2
    found = kpis(overview_parts(history.collect(7), {}, "#fff", before))
    assert found["TOKENS"].change == "▲ 100%" and found["SESSIONS"].change == "▼ 50%"
    assert found["AT WORK"].change == "" and found["PROJECTS"].change == ""  # not known for those days


def test_all_time_reaches_back_to_the_first_day_kept(home):
    write(home, "new", [reply("n1", 0)])
    history.save({(date.today() - timedelta(days=50)).toordinal(): Day(
        use=Tokens(output=70), projects={"C:/work/gone": (Tokens(output=70), 1, 600.0)}, seconds=600.0,
        hours=[0.0] * 10 + [600.0] + [0.0] * 13, sessions=1)})
    report = history.collect(None)
    assert report.whole and report.days == 51 and report.total().output == 80
    assert stats.span(report).startswith("all time, since")
    rows = stats.per_project(report, {})
    assert {row["project"]: row["tokens"].output for row in rows} == {"alpha": 10, "gone": 70}
    gone = next(row for row in rows if row["project"] == "gone")
    assert gone["sessions"] == 1 and gone["seconds"] == 600.0
    assert stats.session_count(report) == 2


def test_one_project_alone_with_its_share_of_the_kept_days(home):
    write(home, "a", [reply("a1", 1, output=10)])
    write(home, "b", [reply("b1", 1, output=40, cwd="C:/work/beta")])
    history.save({(date.today() - timedelta(days=5)).toordinal(): Day(
        use=Tokens(output=300), projects={"C:/work/alpha": (Tokens(output=100), 1, 60.0),
                                          "C:/work/beta": (Tokens(output=200), 2, 120.0)},
        seconds=150.0, sessions=3)})
    report = history.collect(14)
    one = stats.only(report, {}, folder_key("C:/work/beta"))
    assert one.total().output == 240 and one.models["claude-opus-5-5"].output == 40
    assert set(one.per_session()) == {"b"} and stats.session_count(one) == 3
    assert [row["project"] for row in stats.per_project(one, {})] == ["beta"]


def test_long_periods_are_grouped_by_week_and_by_month():
    def heading(days: int) -> tuple[str, int]:
        found, rows = stats.grouped(Report(first=date(2026, 1, 1), days=days))
        return found, len(rows)

    assert heading(14) == ("BY DAY", 14)
    assert heading(90)[0] == "BY WEEK" and 13 <= heading(90)[1] <= 14
    assert heading(270) == ("BY MONTH", 9)


def test_the_records_of_a_period(home):
    write(home, "a", [reply("a1", 3, output=500, tool="Bash"), reply("a2", 3, minute=34, tool="Bash"),
                      reply("a3", 2, output=5), reply("a4", 1, output=5)])
    found = {what: (much, when) for what, much, when in stats.records(history.collect(7), {})}
    assert found["Busiest day"][0] == "510"
    assert found["Days in a row"][0] == "3"
    assert found["Top tool"] == ("Bash", "2 calls")
    assert "Busiest hour" in found and "Longest session" in found


def test_stats_all_time_command(home, capsys):
    from tabmancer.cli import main

    write(home, "a", [reply("a1", 2)])
    main(["stats", "--all-time"])
    shown = capsys.readouterr().out
    assert "OVERVIEW" in shown and "all time, since" in shown and "TOKENS" in shown


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    from tabmancer import demo

    monkeypatch.setenv("CODEX_HOME", "restored after the test")  # prepare() sets it too
    live = demo.prepare(tmp_path)
    app = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, app.tell):
        monkeypatch.setattr(module, name, value)
    return app


async def test_the_screen_looks_back_over_all_time_and_one_project(in_demo):
    from tabmancer.statsview import StatsScreen

    app = in_demo
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("u")
        screen = app.screen
        assert isinstance(screen, StatsScreen)

        async def counted():
            for _ in range(100):
                await pilot.pause(0.05)
                if not screen.counting:
                    return
            raise AssertionError("still counting")

        await counted()
        await pilot.press("5")
        await counted()
        assert screen.period == stats.ALL_TIME and screen.report.whole and screen.before is None
        await pilot.press("p")
        await counted()
        assert screen.project == screen.projects[0][0] and screen.shown is not screen.report
        assert screen.projects[0][1] in str(screen.query_one("#stats-head").render())
        await pilot.press("P")
        await counted()
        assert screen.project == "" and screen.shown is screen.report
