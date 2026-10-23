"""How the dashboard looks: one meaning per colour, bold numbers with what they count beside
them, project counts lined up, and a preview whose words are indented beside their labels."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import date, datetime, timedelta

from rich.console import Console

from tabmancer import sessions
from tabmancer.app import LIVE_STATES, STATUS_LABEL, SessionPicker
from tabmancer.sessions import Exit, Status
from tabmancer.theme import AMBER, BLUE, RED
from tabmancer.tokens import Event, Report, Tokens
from tabmancer.widgets import INTERRUPTED, HourBars, big, project_line, tally


def plain(renderable, width: int = 120) -> str:
    console = Console(width=width, file=io.StringIO(), record=True)
    console.print(renderable)
    return console.export_text()


def coloured(renderable, width: int = 40) -> str:
    console = Console(width=width, file=io.StringIO(), record=True, color_system="truecolor")
    console.print(renderable)
    return console.export_text(styles=True)


def rgb(colour: str) -> str:
    return ";".join(str(int(colour[i:i + 2], 16)) for i in (1, 3, 5))


async def wait_for(pilot, condition, tries=60):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


def test_each_colour_means_one_thing():
    for status, (_, style) in STATUS_LABEL.items():
        assert (AMBER in style) == (status in INTERRUPTED), status  # amber: what TO CONTINUE counts
        assert RED not in style, status  # red is kept for what waits for you
    for raw, (label, style, rank) in LIVE_STATES.items():
        assert (RED in style) == (rank < 2), raw  # red: what NEED YOU counts
        assert (BLUE in style) == (label == "working"), raw


def test_numbers_are_drawn_three_rows_high_in_blocks():
    assert big("1") == "▄█ \n █ \n▀▀▀"
    assert big("7") == "▀▀█\n  █\n  ▀"
    two = big("2.5").split("\n")
    assert len(two) == 3 and len({len(row) for row in two}) == 1  # rows line up
    assert two[2][4] == "▀"  # the decimal point sits low, between the digits


def test_beside_a_number_more_than_three_lines_end_in_how_many_more():
    lines = [(name, "") for name in ("a", "b", "c", "d", "e")]
    assert plain(tally(lines)).split() == ["a", "b", "+3", "more"]
    assert plain(tally(lines[:3])).split() == ["a", "b", "c"]


def test_the_hour_bars_show_the_latest_hours_when_not_all_fit():
    hours = [0] * 9 + [4, 287, 50]
    rows = plain(HourBars(hours), width=5).rstrip("\n").split("\n")
    assert len(rows) == 3 and all(len(row.rstrip()) <= 5 for row in rows)
    assert rows[0][3] == "█" and rows[2][4] != " "  # the busiest hour fills all three rows; now shows
    assert rows[2][:2] == "▁▁"  # hours with nothing keep a baseline


def test_hourly_adds_up_today_by_the_hour():
    now = datetime.now()
    midnight = datetime.combine(date.today(), datetime.min.time())
    events = [Event("a", int((midnight - timedelta(hours=23)).timestamp()), "m", Tokens(output=99), [], []),
              Event("a", int(midnight.timestamp()) + 60, "m", Tokens(output=5), [], []),
              Event("b", int(now.timestamp()) - 1, "m", Tokens(input=7), [], [])]
    report = Report(first=date.today(), days=1, events=events)
    hours = report.hourly()
    assert len(hours) == now.hour + 1 and sum(hours) == 12  # yesterday's 99 left out
    assert hours[now.hour] >= 7


def test_project_counts_line_up_on_the_right():
    rows = [plain(project_line(("● ", RED), name, count), width=24).rstrip("\n")
            for name, count in (("ccrestore", 6), ("checkout-mobile", 15))]
    assert [len(row) for row in rows] == [24, 24]
    assert rows[0].endswith(" 6") and rows[1].endswith("15")


async def test_cards_say_what_they_count_and_the_sidebar_marks_who_waits(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "one", "kind": "interactive", "status": "idle"},
        {"sessionId": "two", "kind": "interactive", "status": "blocked"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: all(app.sessions.get(s) and app.sessions[s].live_state for s in ("one", "two")))
        await pilot.pause(0.3)
        # Needs you is waiting for you too: one line, so working and the rest still fit.
        assert plain(app.cards["card-open"].beside.content).split() == ["2", "waiting", "for", "you"]
        assert plain(app.cards["card-waiting"].beside.content).strip() == "demo ×2"
        project = app.projects.get_option_at_index(1).prompt
        assert f"38;2;{rgb(RED)}m●" in coloured(project)  # red: a session there waits for you
        assert str(app.cells(app.sessions["one"])[0]) == ""  # open: nothing to tick
        closed = replace(app.sessions["two"], exit=Exit.CLEAN, status=Status.CUT_OFF)
        assert str(app.cells(closed)[0]) == "[ ]"
        assert AMBER in str(app.status_cell(closed).style)


async def test_the_preview_indents_the_words_beside_their_labels(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions)
        session = app.current()
        session.first_prompt = "a first prompt long enough to wrap " * 4
        app.update_detail()
        lines = plain(app.detail.content, width=60).split("\n")
        start = next(i for i, line in enumerate(lines) if line.startswith("First prompt"))
        column = lines[start].index("a first")
        assert lines[start + 1][:column].strip() == "" and lines[start + 1][column] != " "
        # In a narrow window each part is cut shorter, so the reply below still shows.
        session.first_prompt = "word " * 200
        await pilot.resize_terminal(100, 40)
        await pilot.pause(0.3)
        app.update_detail()
        shown = plain(app.detail.content, width=app.detail.content_size.width).split("\n")
        assert sum("word" in line for line in shown) <= 3
        assert any(line.startswith("Claude") for line in shown)
