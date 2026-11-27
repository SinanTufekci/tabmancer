"""The Wrapped screen's words and doings, with no UI (wrappedwords): one copy for the terminal's W screen and the
desktop window's Wrapped dialog. An empty made-up week; saving, copying, drawing it moving and the pages faked."""

from datetime import date, timedelta

from tabmancer import card, history, share, wrapped
from tabmancer import tokens as T
from tabmancer import wrappedwords as WW
from tabmancer.actions import Note
from tabmancer.palette import TEXT

ACCENT = "#d97757"
RGB = (217, 119, 87)


def week() -> wrapped.Week:
    return wrapped.week(T.Report(date.today() - timedelta(days=13), 14), {})


def test_the_slides_turn_round_the_ends_and_the_shape_turns():
    first, last = wrapped.SLIDES[0], wrapped.SLIDES[-1]
    assert WW.stepped(first, -1) == last and WW.stepped(last, 1) == first and WW.stepped(first, 1) == wrapped.SLIDES[1]
    assert WW.turned("wide") == "story" and WW.turned("story") == "wide" and list(WW.SHAPES) == ["wide", "story"]
    assert list(WW.PERIODS) == [7, 30, 0]


def test_the_words_around_the_card():
    shown = week()
    assert WW.title_parts(shown, ACCENT) == [("Claude Code ", f"bold {TEXT}"), ("Wrapped", f"bold {ACCENT}"),
                                             (f"   {wrapped.span(shown)}   ", WW.MUTED)]
    about = WW.about_parts(wrapped.SLIDES[0], False, ACCENT)
    assert about[0] == ("●" + "·" * (len(wrapped.SLIDES) - 1) + "  ", ACCENT)
    assert "no project names (n puts the top one on)" in about[2][0]
    assert "n leaves it off" in WW.about_parts(wrapped.SLIDES[0], True, ACCENT)[2][0]
    choices = WW.share_choices()
    assert [key for key, _, _ in choices] == [p.key for p in share.PLACES] + [WW.WORDS]


def test_a_period_counted_again(monkeypatch):
    asked = []
    monkeypatch.setattr(history, "collect", lambda days=None: asked.append(days) or
                        T.Report(date.today() - timedelta(days=(days or 60) - 1), days or 60, whole=days is None))
    assert WW.recount(7).days == 7 and WW.recount(30).days == 30 and WW.recount(0).whole
    assert asked == [14, 60, None]


def test_saving_copying_and_sharing_are_said_as_notes(monkeypatch, tmp_path):
    shown = week()
    canvas = wrapped.draw(shown, RGB)
    monkeypatch.setattr(card, "save", lambda canvas, name, folder=None: tmp_path / name)
    assert WW.saved(canvas, shown, "wide", "summary") == Note(f"Saved as {tmp_path / wrapped.filename(shown)}",
                                                              seconds=10)

    def refused(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(card, "save", refused)
    assert WW.saved(canvas, shown, "wide", "summary") == Note("The card was not saved: read-only", "error", 10)
    monkeypatch.setattr(wrapped, "moving", lambda week, accent, shape, slide, names=False: b"GIF89a")
    monkeypatch.setattr(card, "save_data", lambda data, name, folder=None: tmp_path / name)
    moving = WW.saved_moving(shown, RGB, "wide", "tokens", False)
    assert moving.words.endswith(".gif: a GIF, for a post that takes one (X does).") and moving.seconds == 10
    monkeypatch.setattr(card, "save_data", refused)
    assert WW.saved_moving(shown, RGB, "wide", "tokens", False).severity == "error"

    assert WW.shared("nowhere", shown, RGB, "summary", False) is None
    monkeypatch.setattr(share, "share", lambda place, week, accent, slide="summary", names=False:
                        share.Shared(tmp_path / "card.png", True, "page", f"{place.label} is open."))
    assert WW.shared("x", shown, RGB, "summary", False) == ("Wrapped for X", Note("X is open.", seconds=14))
    monkeypatch.setattr(share, "share", refused)
    assert WW.shared("x", shown, RGB, "summary", False) == ("", Note("The card was not saved: read-only", "error", 10))
