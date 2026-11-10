"""Claude Code Wrapped's slides: the week, the month or all of the time kept, each slide drawn in
both shapes, the numbers counting up into a GIF, Bit in the pose of the week's name, and the
screen's keys for all of it (← →, 1 2 3, n, g)."""

from __future__ import annotations

import io
from datetime import date

import pytest

from tabmancer import card, history, wrapped

ACCENT = (217, 119, 87)


@pytest.fixture
def in_demo(tmp_path, monkeypatch):
    from tabmancer import demo

    monkeypatch.setenv("CODEX_HOME", "restored after the test")  # prepare() sets it too
    live = demo.prepare(tmp_path)
    app = demo.picker()
    for module, name, value in demo.stand_ins(tmp_path, live, app.tell):
        monkeypatch.setattr(module, name, value)
    return app


def periods() -> dict[str, wrapped.Week]:
    return {"week": wrapped.week(history.collect(14), {}),
            "month": wrapped.week(history.collect(60), {}, days=30),
            "all": wrapped.week(history.collect(None), {}, days=None)}


def test_each_period_has_its_words_and_its_file_name(in_demo):
    weeks = periods()
    today = date.today().isoformat()
    assert weeks["week"].days == 7 and not weeks["week"].whole
    assert weeks["month"].days == 30 and weeks["all"].whole and 1 <= weeks["all"].days <= 30  # the demo keeps nine days
    assert wrapped.words(weeks["week"]).startswith("My week with Claude Code")
    assert wrapped.words(weeks["month"]).startswith("My month with Claude Code")
    assert wrapped.words(weeks["all"]).startswith("Me and Claude Code since ")
    assert wrapped.filename(weeks["week"]) == f"claude-code-wrapped-{today}.png"
    assert wrapped.filename(weeks["month"], "story", "tokens") == f"claude-code-wrapped-{today}-month-tokens-story.png"
    assert wrapped.filename(weeks["all"], slide="you", kind="gif") == f"claude-code-wrapped-{today}-all-time-you.gif"


def test_every_slide_is_drawn_in_both_shapes_for_every_period(in_demo):
    for week in periods().values():
        for shape, size in (("wide", (1200, 675)), ("story", (1080, 1920))):
            seen = set()
            for slide in wrapped.SLIDES:
                canvas = wrapped.draw(week, ACCENT, shape=shape, slide=slide)
                assert (canvas.width, canvas.height) == size
                seen.add(canvas.png())
            assert len(seen) == len(wrapped.SLIDES)  # no two slides alike


def test_the_project_name_is_on_the_card_only_when_asked(in_demo):
    week = periods()["week"]
    assert week.top_project is not None
    without = wrapped.draw(week, ACCENT, slide="tools").png()
    named = wrapped.draw(week, ACCENT, slide="tools", names=True).png()
    assert without != named
    assert wrapped.draw(week, ACCENT, slide="tools").png() == without  # names are off by default


def test_the_numbers_count_up_into_a_gif_that_loops(in_demo):
    week = periods()["week"]
    start = wrapped.draw(week, ACCENT, unit=1, slide="tokens", progress=0.0).png()
    assert start != wrapped.draw(week, ACCENT, unit=1, slide="tokens").png()  # at nought, the numbers are not there yet
    data = wrapped.moving(week, ACCENT, "wide", "tokens", unit=1, frames=4)
    assert data[:6] == b"GIF89a" and data[-1:] == b";" and b"NETSCAPE2.0" in data
    assert data.count(b"\x21\xf9\x04") == 4  # a graphic control block a frame


def test_the_gif_is_its_frames_pixel_for_pixel():
    pil = pytest.importorskip("PIL.Image")
    frames = []
    for k in range(3):
        canvas = card.Canvas(30, 20, (10, 20, 30))
        canvas.rect(2 + 5 * k, 3, 8, 6, (200, 100, 50))
        canvas.rect(0, 15, 30, 2, (k * 80, 255, 0))
        frames.append(canvas)
    image = pil.open(io.BytesIO(card.gif(frames, [6, 6, 100])))
    assert image.n_frames == 3
    for k, canvas in enumerate(frames):
        image.seek(k)
        rgb = image.convert("RGB")
        assert all(rgb.getpixel((x, y)) == canvas.rgb(x, y) for y in range(20) for x in range(30))


def test_a_long_run_of_pixels_restarts_the_gif_table():
    """More than 4096 codes: the LZW table is full and starts again, as a reader expects."""
    pil = pytest.importorskip("PIL.Image")
    canvas = card.Canvas(200, 200, (0, 0, 0))
    for y in range(200):
        for x in range(200):
            if (x * 7 + y * 13) % 11 < 4:
                canvas.rect(x, y, 1, 1, ((x * 3) % 256, (y * 5) % 256, 90))
    image = pil.open(io.BytesIO(card.gif([canvas], [10])))
    rgb = image.convert("RGB")
    assert all(rgb.getpixel((x, y)) == canvas.rgb(x, y) for y in range(0, 200, 3) for x in range(0, 200, 3))


def test_bit_takes_the_pose_of_the_weeks_name():
    from tabmancer import wrapped_poses

    drawn = {persona: repr(wrapped_poses.pose(persona, ACCENT)) for persona in wrapped_poses.POSES}
    assert len(set(drawn.values())) == len(drawn)  # each its own
    assert repr(wrapped_poses.pose("SOMEONE NEW", ACCENT)) == repr(wrapped_poses.pose("", ACCENT))  # glad, by default


async def test_the_screen_goes_through_slides_periods_and_saves_one_moving(in_demo, monkeypatch, tmp_path):
    from tabmancer.wrappedview import CardPicture, WrappedScreen

    saved = []
    monkeypatch.setattr(card, "save_data", lambda data, name, folder=None: saved.append((data[:6], name)) or
                        tmp_path / name)
    monkeypatch.setattr(wrapped, "moving", lambda week, accent, shape, slide, names=False, **_: b"GIF89a" + slide.encode())
    app = in_demo
    async with app.run_test(size=(160, 50)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("W")
        await pilot.pause(0.3)
        screen = app.screen
        assert isinstance(screen, WrappedScreen) and screen.period == 7 and screen.slide == "you"
        picture = screen.query_one(CardPicture)
        await pilot.press("right")
        assert screen.slide == "tokens" and picture.slide == "tokens"
        assert "the tokens" in screen.query_one("#wrapped-about").render().plain
        await pilot.press("n")
        assert screen.names and picture.names and "n leaves it off" in screen.query_one("#wrapped-about").render().plain
        await pilot.press("2")
        for _ in range(100):
            await pilot.pause(0.05)
            if not screen.counting:
                break
        assert screen.period == 30 and screen.week.days == 30 and picture.week is screen.week
        assert "month" in screen.query_one("#wrapped-title").render().plain
        await pilot.press("3")
        for _ in range(100):
            await pilot.pause(0.05)
            if not screen.counting:
                break
        assert screen.period == 0 and screen.week.whole
        await pilot.press("g")
        for _ in range(40):
            await pilot.pause(0.05)
            if saved:
                break
        assert saved == [(b"GIF89a", f"claude-code-wrapped-{date.today().isoformat()}-all-time-tokens.gif")]


def test_tabmancer_wrapped_saves_a_month_slide_as_a_gif(in_demo, tmp_path, capsys, monkeypatch):
    from tabmancer import cli

    monkeypatch.setattr(wrapped, "moving", lambda week, accent, shape, slide, names=False, **_: b"GIF89a;")
    cli.main(["wrapped", "--period", "month", "--slide", "time", "--gif", "--out", str(tmp_path)])
    saved = tmp_path / f"claude-code-wrapped-{date.today().isoformat()}-month-time.gif"
    assert saved.read_bytes() == b"GIF89a;" and "saved as" in capsys.readouterr().out
    cli.main(["wrapped", "--period", "all", "--story", "--out", str(tmp_path)])
    assert (tmp_path / f"claude-code-wrapped-{date.today().isoformat()}-all-time-story.png").exists()
