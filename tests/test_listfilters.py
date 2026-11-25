"""The list's filters with no UI (viewmodel, search): the cards, the words, X, one project, pins, Esc
and the row the cursor keeps. The dashboard's own tests (test_cards, test_marks, test_app ...) show
the same through its keys."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import search
from tabmancer import viewmodel as VM
from tabmancer.sessions import Exit, Session, Status, folder_key

NOW = datetime(2026, 11, 25, 14, 0)


def s(sid, cwd="C:/work/shop", title="", exit=Exit.CLEAN, status=Status.DONE, state="", ago=0, **kw) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=cwd, title=title or sid, exit=exit,
                   status=status, live_state=state, last_active=NOW - timedelta(minutes=ago), **kw)


OPEN_BUSY = s("o1", exit=Exit.RUNNING, status=Status.RUNNING, state="busy")
OPEN_WAITS = s("o2", "C:/work/blog", "İstanbul", exit=Exit.RUNNING, status=Status.RUNNING, state="idle")
CUT = s("c1", "C:/work/wiki", status=Status.CUT_OFF, ago=10, last_prompt="the basket")
DONE = s("c2", ago=60)
ALL = [DONE, CUT, OPEN_WAITS, OPEN_BUSY]


def test_what_each_card_counts_and_the_same_card_again():
    assert [x.session_id for x in ALL if VM.counted_by(x, "open")] == ["o2", "o1"]
    assert [x.session_id for x in ALL if VM.counted_by(x, "waiting")] == ["o2"]
    assert [x.session_id for x in ALL if VM.counted_by(x, "continue")] == ["c1"]
    assert VM.toggle_card(None, "open") == "open" and VM.toggle_card("open", "open") is None
    assert VM.toggle_card("open", "waiting") == "waiting"
    assert {key for key, _ in VM.CARDS.values()} == {"1", "2", "3"}


def ids(shown):
    return [x.session_id for x in shown]


def test_the_order_open_first_then_newest_and_live_by_state():
    assert ids(VM.visible(ALL, lambda _: False)) == ["o2", "o1", "c1", "c2"]  # open by project; closed newest first
    assert ids(VM.visible(ALL, lambda _: False, live=True)) == ["o2", "o1"]  # waiting for you before working


def test_the_words_the_project_the_card_and_x():
    hidden = {"c2"}
    is_hidden = lambda x: x.session_id in hidden
    assert ids(VM.visible(ALL, is_hidden)) == ["o2", "o1", "c1"]
    assert ids(VM.visible(ALL, is_hidden, show_hidden=True)) == ["o2", "o1", "c1", "c2"]
    assert ids(VM.in_sight(ALL, is_hidden, False)) == ["c1", "o2", "o1"]
    asked = []
    said = lambda: asked.append(1) or {"o1"}
    assert ids(VM.visible(ALL, is_hidden, text="İSTANBUL", said_in=said)) == ["o2", "o1"]
    assert ids(VM.visible(ALL, is_hidden, text="BASKET")) == ["c1"]
    assert VM.visible(ALL, is_hidden, said_in=lambda: 1 / 0) and asked == [1]  # no words: said_in never asked
    assert ids(VM.visible(ALL, is_hidden, project=folder_key("C:/work/wiki"))) == ["c1"]
    assert ids(VM.visible(ALL, is_hidden, card="waiting")) == ["o2"]


def test_pins_and_esc_and_the_row_kept():
    assert VM.toggle_pins({"a"}, {"b"}) == ({"a", "b"}, True)
    assert VM.toggle_pins({"a", "b"}, {"a", "b"}) == (set(), False)
    assert VM.toggle_pins({"a"}, {"a", "b"}) == ({"a", "b"}, True)  # not all pinned: all are now
    keys = ["h", "x", "y", "z"]
    assert VM.kept_row(keys, "y", 0, True) == 2
    assert VM.kept_row(keys, "gone", 9, True) == 3 and VM.kept_row(keys, "gone", 1, True) == 1
    assert VM.kept_row(keys, "gone", 1, False) is None and VM.kept_row([], "gone", 1, True) is None
    assert VM.kept_row(keys, None, 2, True) is None
    assert [VM.back_step(True, "open"), VM.back_step(False, "open"), VM.back_step(False, None)] == [
        "search", "card", "quit"]


def test_the_finder_asks_the_index_once_per_words_and_reading():
    class Index:
        calls = 0

        def find(self, needle, ids):
            Index.calls += 1
            return {"o1"}

    finder = search.Finder(Index())
    assert finder.said_in("deep", ["o1"]) == {"o1"} and finder.said_in("deep", ["o1"]) == {"o1"}
    assert Index.calls == 1
    finder.renew()
    finder.said_in("deep", ["o1"])
    assert Index.calls == 2 and finder.version == 1
    assert search.matches(OPEN_WAITS, search.fold("İSTANBUL")) and not search.matches(DONE, "basket")
