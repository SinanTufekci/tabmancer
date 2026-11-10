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
    week.models, week.output, week.files, week.calls = [("opus", 0.86), ("sonnet", 0.11), ("haiku", 0.03)], 991_000, 35, 432
    personas = [persona for sample in persona_weeks() for persona in wrapped.persona(sample)]
    written = [wrapped.span(week), "CLAUDE CODE WRAPPED", "I'M ", "TOKENS A DAY", "MTWTFSS", "WHEN I WORKED",
               "0 6 12 18", wrapped.TAG, "TABMANCER · " + wrapped.REPO, *personas,
               *(part for tile in wrapped.tiles(week) for part in tile)]
    missing = {char for words in written for char in words.upper() + words.lower()} - set(card.FONT)
    assert not missing  # in capitals and in small letters, as the card writes its labels
    canvas = card.Canvas(20, 20, (0, 0, 0))
    assert canvas.text(1, 1, "A", (255, 255, 255), 2) == 10  # two pixels a font pixel
    drawn = ["".join("1" if canvas.rows[1 + 2 * y][1 + 2 * x] else "0" for x in range(5)) for y in range(7)]
    assert drawn == card.FONT["A"] and canvas.rows[2][2] == canvas.rows[1][1]
    small = card.Canvas(8, 12, (0, 0, 0))
    small.text(1, 1, "g", (255, 255, 255))  # a small letter as it is written, its tail under the line
    assert ["".join("1" if small.rows[1 + y][1 + x] else "0" for x in range(5)) for y in range(9)] == card.FONT["g"]
    assert len(card.FONT["g"]) == 9 and card.FONT.get("é") is None
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
    clear = card.png(canvas.width, canvas.height, canvas.palette, canvas.rows, clear=True)
    assert b"tRNS\x00" in clear and clear.index(b"PLTE") < clear.index(b"tRNS") < clear.index(b"IDAT")  # the ground
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
    assert tiles[1] == ("AT WORK", "1 MIN", "OVER 1 DAY")  # not 1M, which beside the tokens reads as a million
    week.seconds = 3 * 3600 + 5 * 60
    assert wrapped.tiles(week)[1][1] == "3H 05M"
    assert tiles[4] == ("CLAUDE WROTE", "-", "") and tiles[6] == ("MODELS", "-", "")
    assert tiles[7] == ("BUSIEST DAY", "-", "") and tiles[9] == ("TOP TOOL", "-", "")
    week.before, week.brought_back, week.carried_on = 0, 2, 1
    assert wrapped.tiles(week)[0][2] == "NEW THIS WEEK" and wrapped.tiles(week)[7][2] == "1 ON ITS OWN"
    week.before = 100
    assert wrapped.tiles(week)[0][2] == "+100% ON LAST WEEK"
    week.brought_back, week.streak = 0, 5  # nothing brought back: a streak, when there is one
    assert wrapped.tiles(week)[7] == ("STREAK", "5 DAYS", "AT WORK IN A ROW")
    week.output, week.files, week.calls = 991_000, 35, 432
    week.models = [("opus", 0.86), ("sonnet", 0.11), ("haiku", 0.004)]
    tiles = wrapped.tiles(week)
    assert tiles[4] == ("CLAUDE WROTE", "991K", "~8 NOVELS") and tiles[5] == ("FILES EDITED", "35", "432 TOOL CALLS")
    assert tiles[6] == ("MODELS", "OPUS 86%", "SONNET 11%")  # under one percent is left out
    assert wrapped.worth(30_000) == "~75 PAGES" and wrapped.worth(0) == "" and wrapped.worth(100_000) == "~1 NOVEL"


def persona_weeks() -> list[wrapped.Week]:
    """A week for each name, and the builder's."""
    def plain(**more) -> wrapped.Week:
        week = wrapped.Week(date(2026, 10, 28), date(2026, 11, 3), 1000, None, 3600.0, 2, 3, 2, 1, None, None, 0, 0,
                            [0] * 7)
        for name, value in more.items():
            setattr(week, name, value)
        return week

    def hours(**at) -> list[float]:
        out = [0.0] * 24
        for hour, seconds in at.items():
            out[int(hour[1:])] = seconds
        return out

    return [plain(at_once=5), plain(hours=hours(h23=3 * 3600, h14=3600)), plain(hours=hours(h6=2 * 3600, h14=3600)),
            plain(hours=hours(h14=3 * 3600), weekend=2 * 3600), plain(longest=5 * 3600), plain(brought_back=4),
            plain(streak=9), plain(calls=12_500), plain(), plain(sessions=0)]


def test_the_week_gets_a_name_from_its_numbers_and_the_post_says_it():
    names = [wrapped.persona(week) for week in persona_weeks()]
    assert names == [
        ("THE CONDUCTOR", "5 SESSIONS AT WORK AT ONCE"), ("THE NIGHT OWL", "75% OF THE WORK AT NIGHT"),
        ("THE EARLY BIRD", "67% OF THE WORK BEFORE 9:00"), ("THE WEEKEND WARRIOR", "67% OF THE WORK ON THE WEEKEND"),
        ("THE MARATHONER", "ONE SESSION AT WORK FOR 5H 00M"), ("THE COMEBACK KID", "4 SESSIONS BROUGHT BACK"),
        ("THE STEADY HAND", "9 DAYS IN A ROW"), ("THE TOOLSMITH", "12,500 TOOL CALLS"),
        ("THE BUILDER", "3 SESSIONS IN 2 PROJECTS"), ("THE WELL RESTED", "NO SESSIONS THIS WEEK")]
    week = persona_weeks()[0]
    week.tokens, week.seconds = 55_757_442, 51_266.0
    post = wrapped.words(week)
    assert post.startswith("My week with Claude Code: 55.8M tokens, 14h 14m at work, 5 sessions side by side. "
                           "I'm The Conductor. #ClaudeCodeWrapped")
    assert post.endswith("https://github.com/SinanTufekci/tabmancer") and len(post) <= 280  # X's limit; Bluesky's is 300


def test_time_at_work_by_hour_counts_side_by_side_sessions_once():
    start = datetime(2026, 11, 1, 23, 0).timestamp()  # a Sunday night
    spans = {"a": [[start, start + 3600]], "b": [[start + 1800, start + 5400]]}
    hours, weekend, longest = wrapped.at_work(spans)
    assert hours[23] == 3600 and hours[0] == 1800 and sum(hours) == 5400  # the half hour both worked, once
    assert weekend == 3600 and longest == 3600  # Monday's half hour is not the weekend
    assert wrapped.most_at_once(spans) == 2
    assert wrapped.in_a_row([0, 5, 5, 5, 0], 4) == 3  # today not begun: the run to yesterday
    assert wrapped.in_a_row([5, 0, 5, 5, 5], 4) == 3 and wrapped.in_a_row([5, 5, 0], 2) == 2
    assert wrapped.in_a_row([0, 0, 5, 0, 0], 4) == 0


def test_the_card_is_drawn_at_a_size_of_units(home):
    week = wrapped.week(T.collect(14), {}, opened=[])
    small, large = wrapped.draw(week, ACCENT, 1), wrapped.draw(week, ACCENT, 3)
    assert (small.width, small.height) == (400, 225) and (large.width, large.height) == (1200, 675)
    assert large.rgb(152 * 3 + 1, 12 * 3 + 1) == wrapped.TEXT  # the top of the title's C, three times larger
    assert large.rgb(0, 0) == wrapped.GROUND and len(large.palette) <= 256  # the sky at its top...
    assert large.rgb(1, large.height - 1) == wrapped.DUSK  # ...and at its foot
    story = wrapped.draw(week, ACCENT, shape="story")
    assert (story.width, story.height) == (1080, 1920) and len(story.palette) <= 256
    assert wrapped.filename(week, "story").endswith("-story.png") and not wrapped.filename(week).endswith("-story.png")


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
        today = date.today().isoformat()
        assert app.screen.slide == "you"  # the slides start with who you were
        await pilot.press("s", "c")
        await pilot.pause(0.1)
        assert saved == [(1200, f"claude-code-wrapped-{today}-you.png")] and copied == [675]
        await pilot.press("left")  # round to the last slide: the card that sums it up
        await pilot.pause(0.1)
        assert app.screen.slide == "summary" and shown.slide == "summary"
        await pilot.press("s")
        await pilot.pause(0.1)
        assert saved[-1] == (1200, f"claude-code-wrapped-{today}.png")
        await pilot.press("right", "right")
        assert app.screen.slide == "tokens"
        await pilot.press("escape")
        assert not isinstance(app.screen, WrappedScreen)


async def test_f_turns_the_card_to_a_story_and_p_takes_it_where_it_is_shared(fake_home, monkeypatch):
    from tabmancer import bitart, share, sixel
    from tabmancer.app import SessionPicker
    from tabmancer.wrappedview import CardPicture, ShareMenu, WrappedScreen

    monkeypatch.setattr(sixel, "_terminal", sixel.Terminal(bitart.WT_CELL))
    saved, copied, pages, shown = [], [], [], []
    monkeypatch.setattr(card, "save", lambda canvas, name, folder=None: saved.append((canvas.height, name)) or
                        fake_home / name)
    monkeypatch.setattr(card, "copy_picture", lambda canvas: copied.append(canvas.height) or True)
    monkeypatch.setattr(share, "open_page", pages.append)
    monkeypatch.setattr(share, "reveal", shown.append)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("W")
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, WrappedScreen)
        await pilot.press("left")  # the card that sums it up
        await pilot.press("f")  # the story: 640 design pixels down, too tall for 50 rows at three a pixel
        picture = screen.query_one(CardPicture)
        assert screen.shape == "story" and picture.shape == "story" and 0 < picture.unit() < 3
        await pilot.press("s")
        await pilot.pause(0.1)
        assert saved[-1] == (1920, f"claude-code-wrapped-{date.today().isoformat()}-story.png")
        await pilot.press("p")
        await pilot.pause(0.2)
        assert isinstance(app.screen, ShareMenu) and "#ClaudeCodeWrapped" in str(app.screen.query_one("#share-words").render())
        await pilot.press("x")  # X: the wide card, whatever the screen shows, and a new post with the words
        await pilot.pause(0.2)
        assert app.screen is screen and saved[-1][0] == 675 and copied[-1] == 675
        assert pages[-1].startswith("https://x.com/intent/post?text=My+week+with+Claude+Code") and not shown
        await pilot.press("p", "i")  # Instagram: the story card, its folder shown, instagram.com
        await pilot.pause(0.2)
        assert saved[-1][0] == 1920 and pages[-1] == "https://www.instagram.com/" and shown


def test_each_place_opens_its_new_post_with_the_words_in_it():
    from urllib.parse import parse_qs, urlparse

    from tabmancer import share

    words = "My week: 5 sessions & more #ClaudeCodeWrapped"
    for key, host in (("x", "x.com"), ("b", "bsky.app"), ("t", "www.threads.com")):
        place = next(p for p in share.PLACES if p.key == key)
        address = urlparse(share.page(place, words))
        assert address.netloc == host and parse_qs(address.query) == {"text": [words]}
    assert share.page(next(p for p in share.PLACES if p.key == "i"), words) == "https://www.instagram.com/"


def test_tabmancer_wrapped_saves_a_story_and_shares(home, tmp_path, capsys, monkeypatch):
    from tabmancer import cli, share

    pages = []
    monkeypatch.setattr(share, "open_page", pages.append)
    cli.main(["wrapped", "--story", "--out", str(tmp_path)])
    assert (tmp_path / f"claude-code-wrapped-{date.today().isoformat()}-story.png").exists()
    assert "I'm The" in capsys.readouterr().out
    cli.main(["wrapped", "--share", "bluesky", "--out", str(tmp_path)])
    assert pages[-1].startswith("https://bsky.app/intent/compose?text=") and "Bluesky is open" in capsys.readouterr().out


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


def test_the_folder_shown_picks_the_card_even_with_a_space_in_its_path(monkeypatch):
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path

    from tabmancer import share

    # The tests put a stand-in in place of reveal: this is a copy of the module's own.
    spec = importlib.util.spec_from_file_location("tabmancer._share_as_shipped", share.__file__)
    shipped = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, shipped)  # its dataclasses look themselves up there
    spec.loader.exec_module(shipped)
    started = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: started.append(args))
    card = Path("C:/Users/John Smith/Downloads/card.png")
    shipped.reveal(card)
    assert started == [f'explorer /select,"{card}"']  # the switch outside the quotes, the path inside
