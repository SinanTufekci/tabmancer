"""Claude Code Wrapped: the last seven days counted (side by side sessions, the ones tabmancer
brought back), drawn as a card in a pixel font and saved as a PNG; said on Fridays, once a week."""

from __future__ import annotations

import struct
import time
import zlib
from datetime import date, datetime, timedelta

import pytest
from test_stats import at, reply, write

from tabmancer import card, wrapped
from tabmancer import tokens as T
from tabmancer.wrapped import due as real_due  # the conftest keeps it quiet for every other test

ACCENT = (217, 119, 87)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude"


def test_the_font_draws_every_character_the_card_writes_and_a_letter_as_it_looks():
    week = wrapped.Week(date(2026, 12, 29), date(2027, 1, 4), 12_345_678, 1_000_000, 50_000.0, 6, 31, 12, 7,
                        (1, 9000.0), ("mcp__kicad__run_drc (MCP)", 1234), 9, 1, [5, 0, 3, 9, 1, 7, 2])
    written = [wrapped.span(week), "CLAUDE CODE WRAPPED", "MY WEEK · ", "TOKENS A DAY", "MTWTFSS",
               "TABMANCER · GITHUB.COM/SINANTUFEKCI/TABMANCER", *(part for tile in wrapped.tiles(week) for part in tile)]
    missing = {char for words in written for char in words.upper()} - set(card.FONT)
    assert not missing
    canvas = card.Canvas(20, 20, (0, 0, 0))
    assert canvas.text(1, 1, "a", (255, 255, 255), 2) == 10  # capitals, two pixels a font pixel
    drawn = ["".join("1" if canvas.rows[1 + 2 * y][1 + 2 * x] else "0" for x in range(5)) for y in range(7)]
    assert drawn == card.FONT["A"] and canvas.rows[2][2] == canvas.rows[1][1]
    assert card.text_width("ABC", 1) == 17


def test_the_card_is_a_png_of_its_own_pixels():
    canvas = card.Canvas(7, 3, (10, 20, 30))
    canvas.rect(1, 1, 3, 1, (200, 100, 50))
    data = canvas.png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, at_ = {}, 8
    while at_ < len(data):
        size, kind = struct.unpack(">I4s", data[at_:at_ + 8])
        body = data[at_ + 8:at_ + 8 + size]
        assert struct.unpack(">I", data[at_ + 8 + size:at_ + 12 + size])[0] == zlib.crc32(kind + body)
        chunks[kind] = body
        at_ += 12 + size
    assert struct.unpack(">IIBB", chunks[b"IHDR"][:10]) == (7, 3, 8, 3)  # 8 bits, a palette
    assert chunks[b"PLTE"] == bytes((10, 20, 30, 200, 100, 50))
    rows = zlib.decompress(chunks[b"IDAT"])
    assert rows == b"\x00" + bytes(7) + b"\x00" + bytes((0, 1, 1, 1, 0, 0, 0)) + b"\x00" + bytes(7)
    picture = card.dib(canvas)
    assert struct.unpack("<IiiHH", picture[:16]) == (40, 7, 3, 1, 32)
    assert picture[40 + 7 * 4 + 4:40 + 7 * 4 + 8] == bytes((50, 100, 200, 0))  # the middle row, bottom up, BGR


def test_the_week_counts_seven_days_the_sessions_at_once_and_the_tool_called_most(home):
    write(home, "a", [reply("a1", at(10), tools=["Bash"]), reply("a2", at(10, 3)), reply("a3", at(10, 6))])
    write(home, "b", [reply("b1", at(10, 2), tools=["mcp__kicad__run_drc"]),
                      reply("b2", at(10, 5), tools=["mcp__kicad__run_erc", "mcp__kicad__add_via"])])
    write(home, "c", [reply("c1", at(15))])  # alone, later: two at once at most
    write(home, "old", [reply("o1", at(9, days_ago=9))])  # the week before
    report = T.collect(14)
    week = wrapped.week(report, {}, opened=[])
    one = report.total(last_days=1).total
    assert week.tokens == one and week.before == report.between(date.today() - timedelta(days=13),
                                                                  date.today() - timedelta(days=7)).total > 0
    assert week.sessions == 3 and week.at_once == 2 and week.days_worked == 1
    assert week.tool == ("kicad (MCP)", 3) and week.daily[-1] == one and sum(week.daily) == one
    assert week.first == date.today() - timedelta(days=6) and week.busiest[0] == date.today().weekday()


def test_the_sessions_brought_back_are_kept_two_months_and_counted_in_their_week():
    now = time.time()
    wrapped.note_opened([("old", "resume")], now=now - 70 * 86400)
    wrapped.note_opened([("a", "resume"), ("b", "auto")], now=now - 3600)
    wrapped.note_opened([("a", "resume"), ("c", "limit")], now=now)
    wrapped.note_opened([])  # nothing opened: nothing written
    kept = wrapped.opened_sessions()
    assert {e["session"] for e in kept} == {"a", "b", "c"}  # the one two months old is gone
    week = wrapped.week(T.Report(date.today() - timedelta(days=13), 14), {})
    assert (week.brought_back, week.carried_on) == (3, 2)
    assert ("BROUGHT BACK", "3", "2 ON THEIR OWN") in wrapped.tiles(week)


def test_the_week_is_told_on_friday_from_noon_or_the_weekend_after_once_a_week():
    friday = datetime(2026, 11, 6, 12, 0)
    assert not real_due(friday - timedelta(minutes=1)) and not real_due(friday - timedelta(days=1))
    assert real_due(friday) and real_due(friday + timedelta(days=2, hours=10))  # Sunday, not told yet
    wrapped.told(friday)
    assert not real_due(friday + timedelta(hours=3)) and not real_due(friday + timedelta(days=1))
    assert real_due(friday + timedelta(days=7))  # the next week
    wrapped.set_enabled(False)
    assert not real_due(friday + timedelta(days=7))


def test_the_numbers_say_what_they_compare_with():
    week = wrapped.Week(date(2026, 10, 28), date(2026, 11, 3), 200, None, 60.0, 1, 1, 1, 1, None, None, 0, 0,
                        [0] * 7)
    tiles = wrapped.tiles(week)
    assert tiles[0] == ("TOKENS", "200", "") and tiles[3] == ("SIDE BY SIDE", "1", "AT WORK AT ONCE")
    assert tiles[4] == ("BUSIEST DAY", "-", "") and tiles[5] == ("TOP TOOL", "-", "")
    week.before, week.brought_back, week.carried_on = 0, 2, 1
    assert wrapped.tiles(week)[0][2] == "NEW THIS WEEK" and wrapped.tiles(week)[4][2] == "1 ON ITS OWN"
    week.before = 100
    assert wrapped.tiles(week)[0][2] == "+100% ON LAST WEEK"


def test_the_card_is_drawn_at_a_size_of_units(home):
    week = wrapped.week(T.collect(14), {}, opened=[])
    small, large = wrapped.draw(week, ACCENT, 1), wrapped.draw(week, ACCENT, 3)
    assert (small.width, small.height) == (400, 225) and (large.width, large.height) == (1200, 675)
    assert large.rgb(16 * 3 + 1, 12 * 3 + 1) == wrapped.TEXT  # the top of the title's C, three times larger
    assert large.rgb(0, 0) == wrapped.GROUND and len(large.palette) <= 256


async def test_w_shows_the_card_in_pixels_saves_and_copies_it(fake_home, monkeypatch):
    from tabmancer import bitart, sixel
    from tabmancer.app import SessionPicker
    from tabmancer.wrappedview import CardPicture, WrappedScreen

    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    saved, copied = [], []
    monkeypatch.setattr(card, "save", lambda canvas, name, folder=None: saved.append((canvas.width, name)) or name)
    monkeypatch.setattr(card, "copy_picture", lambda canvas: copied.append(canvas.height) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("W")
        await pilot.pause(0.3)
        assert isinstance(app.screen, WrappedScreen)
        shown = app.screen.query_one(CardPicture)
        assert shown.unit() >= 2 and shown.picture.data.startswith("\x1bP") and str(shown.render()) == ""
        await pilot.press("s", "c")
        await pilot.pause(0.1)
        assert saved == [(1200, f"claude-code-wrapped-{date.today().isoformat()}.png")] and copied == [675]
        await pilot.press("escape")
        assert not isinstance(app.screen, WrappedScreen)


async def test_without_pixels_the_card_is_its_numbers_in_words(fake_home):
    from tabmancer.app import SessionPicker
    from tabmancer.wrappedview import CardPicture

    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("W")
        await pilot.pause(0.3)
        words = app.screen.query_one(CardPicture).render().plain
        assert "CLAUDE CODE WRAPPED" in words and "side by side" in words and "s saves it" in words


async def test_on_friday_the_dashboard_says_the_week_is_ready_once(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    told = []
    monkeypatch.setattr(wrapped, "due", lambda now=None: not told)
    monkeypatch.setattr(wrapped, "told", lambda now=None: told.append(True))
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.2)
        app.tell_wrapped()
        app.tell_wrapped()
        await pilot.pause(0.1)
        notes = [n.message for n in app._notifications if "Wrapped" in n.message]
        assert told == [True] and len(notes) == 1 and "W shows it" in notes[0]


async def test_a_session_opened_again_from_the_dashboard_is_noted_for_the_card(fake_home):
    from tabmancer.app import SessionPicker
    from tabmancer.launch import Launch, Mode

    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause(0.2)
        app.open_sessions([Launch("one", str(fake_home), Mode.AUTO, "one"),
                           Launch("fresh", str(fake_home), Mode.NEW, "")])
        await pilot.pause(0.1)
    assert [(e["session"], e["how"]) for e in wrapped.opened_sessions()] == [("one", "auto")]  # not a new one


def test_tabmancer_wrapped_saves_the_picture_and_turns_the_friday_word_off(home, tmp_path, capsys):
    from tabmancer.cli import main

    write(home, "a", [reply("a1", at(9), tools=["Bash"])])
    main(["wrapped", "--out", str(tmp_path)])
    saved = tmp_path / f"claude-code-wrapped-{date.today().isoformat()}.png"
    assert saved.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and "saved as" in capsys.readouterr().out
    main(["wrapped", "--out", str(tmp_path / "mine.png")])
    assert (tmp_path / "mine.png").exists()
    main(["wrapped", "off"])
    assert not wrapped.enabled()
    main(["wrapped", "on"])
    assert wrapped.enabled()
