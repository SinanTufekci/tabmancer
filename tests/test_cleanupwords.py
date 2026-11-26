"""The clean-up screen's words and picks, with no UI (cleanupwords): one copy for the terminal's screen and the
window's dialog. Made-up items only: nothing is measured or moved."""

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import cleanupwords as CW
from tabmancer.actions import Note
from tabmancer.diskclean import BIN, Item
from tabmancer.sessions import Exit, Session

NOW = datetime(2026, 11, 27, 12, 0)


def item(sid: str, size: int, days_old: float = 0, is_open: bool = False, prompt: str = "") -> Item:
    session = Session(session_id=sid, path=Path(f"C:/c/projects/p/{sid}.jsonl"), cwd="C:/work/shop",
                      title=f"Fix {sid}", last_active=NOW - timedelta(days=days_old), first_prompt=prompt,
                      exit=Exit.RUNNING if is_open else Exit.CLEAN)
    return Item(session, [], size)


def listed(*items: Item) -> dict[str, Item]:
    return {i.session.session_id: i for i in items}


def test_the_line_counts_all_and_the_picked(monkeypatch):
    monkeypatch.setattr(CW, "claude_cleanup_days", lambda: 30)
    items = [item("a", 2048), item("b", 1024)]
    assert CW.status(items, {"a"}) == ("2 sessions use 3 KB · 1 selected (2 KB) · Claude Code deletes sessions "
                                       "older than 30 days on its own")


def test_a_row_marks_the_picked_and_dims_an_open_one():
    picked, open_one = item("a", 512, days_old=3), item("b", 1024, is_open=True)
    assert CW.row_parts(picked, {"a"}) == [("[x]", ""), ("shop", ""), ("Fix a", ""), ("2026-11-24", "dim"),
                                           ("512 B", "bold")]
    assert CW.row_parts(picked, set())[0] == ("[ ]", "")
    assert CW.row_parts(open_one, {"b"})[:3] == [(" · ", "dim"), ("shop", "dim"), ("Fix b", "dim")]


def test_the_detail_says_what_it_keeps_and_that_an_open_one_stays():
    assert CW.detail_parts(None) == [(CW.NONE_ON_DISK, "")]
    words = "".join(w for w, _ in CW.detail_parts(item("a", 1, prompt="p" * 300)))
    assert words.startswith("Fix a\nC:/work/shop\nLast active 27 Nov 2026 12:00\nFirst prompt  " + "p" * 200 + "\n")
    assert "On disk:" in words and CW.OPEN_NOW not in words
    assert CW.detail_parts(item("b", 1, is_open=True))[-1] == (f"\n\n{CW.OPEN_NOW}", "yellow")


def test_the_picks_never_take_an_open_one():
    items = listed(item("old", 1, days_old=20), item("mid", 1, days_old=8), item("new", 1),
                   item("busy", 1, days_old=30, is_open=True))
    assert CW.toggled(items, set(), "old") == {"old"} and CW.toggled(items, {"old"}, "old") == set()
    assert CW.toggled(items, set(), "busy") == set() and CW.toggled(items, set(), "gone") == set()
    assert CW.with_older(items, {"new"}, NOW) == {"new", "old", "mid"}
    assert CW.all_or_none(items, set()) == {"old", "mid", "new"}
    assert CW.all_or_none(items, {"old", "mid", "new"}) == set()


def test_enter_asks_first_or_says_to_pick():
    items = listed(item("a", 1024), item("b", 1024))
    assert CW.ask(items, set()) == CW.SELECT_FIRST and CW.SELECT_FIRST.severity == "warning"
    asked = CW.ask(items, {"a", "b", "gone"})
    assert isinstance(asked, CW.Ask) and {i.session.session_id for i in asked.chosen} == {"a", "b"}
    assert asked.question == (f"Move 2 sessions (2 KB) to the {BIN}?\n"
                              "They disappear from Claude Code's /resume until restored.")


def test_what_moving_came_to_and_the_list_after():
    a, b, c = item("a", 1024), item("b", 2048), item("c", 1)
    problems = [f"p{n} [/b]" for n in range(7)]
    notes = CW.moved_notes([a, b], problems)
    assert notes[0] == Note(f"Moved 2 sessions (3 KB) to the {BIN}.", seconds=8)
    assert notes[1:] == [Note(p, "error", 10) for p in problems[:5]]  # the first five, as plain words
    rest, picked = CW.after_move(listed(a, b, c), {"a", "c"}, [a])
    assert rest == [b, c] and picked == {"c"}
