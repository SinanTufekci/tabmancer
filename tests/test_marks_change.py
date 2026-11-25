"""Pins and hides as every dashboard and the desktop window make them (marks.change, pin, hide,
hide_project): read from marks.json as it is now, so what another one changed meanwhile stays. No UI
here; the dashboard's own tests (test_marks) and the window's (test_desktop_main) press the keys.
Made-up data only."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

import pytest

from tabmancer import config, marks
from tabmancer.config import config_dir, read_json
from tabmancer.sessions import Exit, Session, Status, folder_key

NOW = datetime(2026, 11, 25, 14, 0)


def s(sid, cwd="C:/work/shop", title="", exit=Exit.CLEAN, status=Status.DONE) -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=cwd, title=title or sid, exit=exit,
                   status=status, last_active=NOW - timedelta(minutes=5))


OPEN = s("o1", title="The open one", exit=Exit.RUNNING, status=Status.RUNNING)
DONE = s("d1", title="Tidy the docs")
CUT = s("c1", "C:/work/wiki", status=Status.CUT_OFF)
WIKI = folder_key("C:/work/wiki")


def test_a_change_keeps_what_another_dashboard_saved_since(fake_home):
    mine = marks.Marks.load()  # this dashboard read the marks...
    marks.Marks(pinned={"theirs"}, projects={WIKI}).save()  # ...then another one pinned and hid
    done = marks.change(partial(marks.pin, chosen=[DONE]))
    assert not isinstance(done, marks.Refused)
    now, said = done
    assert mine.pinned == set() and now.pinned == {"theirs", "d1"} and now.projects == {WIKI}
    assert read_json(marks.FILE) == {"hidden": [], "projects": [WIKI], "pinned": ["d1", "theirs"]}
    assert said.words == "Pinned “Tidy the docs” above the rest." and said.hid == set()


def test_with_no_marks_json_yet_a_change_starts_one(fake_home):
    assert marks.Marks.read() == marks.Marks() and not (config_dir() / marks.FILE).exists()
    assert not isinstance(marks.change(partial(marks.pin, chosen=[DONE])), marks.Refused)
    assert read_json(marks.FILE) == {"hidden": [], "projects": [], "pinned": ["d1"]}


@pytest.mark.parametrize("there", [b'{"hidden": [], "projects": [], "pinned": ["d1", "the',  # cut off
                                   b'["d1"]', b"\xff\xfe not text"])  # no dict; not even text
def test_a_marks_json_that_cannot_be_read_is_never_saved_over(fake_home, there):
    path = config_dir() / marks.FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(there)
    asked = []
    assert marks.change(asked.append) is marks.UNREAD  # refused: the caller says its words
    assert asked == [] and path.read_bytes() == there  # how never called, nothing saved
    assert marks.Marks.read() is None and marks.Marks.load() == marks.Marks()  # at start-up: none shown


def test_a_marks_json_another_process_holds_too_long_is_never_saved_over(fake_home, monkeypatch):
    marks.Marks(pinned={"theirs"}, projects={WIKI}).save()
    path = config_dir() / marks.FILE
    kept = path.read_bytes()
    real, tries = Path.read_text, []

    def held(self, *args, **kwargs):
        if self == path:
            tries.append(self)
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", held)
    monkeypatch.setattr(config, "BUSY_WAIT", 0)
    assert marks.change(partial(marks.pin, chosen=[DONE])) is marks.UNREAD
    assert len(tries) == config.BUSY_TRIES and path.read_bytes() == kept  # waited it out, then left it
    tries.clear()
    assert marks.Marks.read(tries=1) is None and len(tries) == 1  # a UI thread's read: tried once, no wait
    monkeypatch.setattr(Path, "read_text", real)
    assert marks.Marks.read() == marks.Marks(pinned={"theirs"}, projects={WIKI})


def test_a_change_that_cannot_be_written_leaves_the_file_as_it_was(fake_home, monkeypatch):
    marks.Marks(pinned={"theirs"}).save()
    path = config_dir() / marks.FILE
    kept, real = path.read_bytes(), os.replace

    def held(source, target):  # another process holds marks.json all the while
        if Path(target) == path:
            raise PermissionError(13, "The process cannot access the file", str(target))
        return real(source, target)

    monkeypatch.setattr(os, "replace", held)
    monkeypatch.setattr(config, "BUSY_WAIT", 0)
    assert marks.change(partial(marks.pin, chosen=[DONE])) is marks.UNSAVED
    assert path.read_bytes() == kept and list(path.parent.glob("*.tmp")) == []  # as it was, no half file
    assert marks.UNSAVED.words == "marks.json could not be saved: nothing changed."


def test_f_pins_and_again_unpins_one_by_its_title_more_by_how_many():
    kept = marks.Marks()
    assert marks.pin(kept, [DONE, CUT]).words == "Pinned 2 sessions above the rest."
    assert kept.pinned == {"d1", "c1"}
    assert marks.pin(kept, [DONE]).words == "Unpinned “Tidy the docs”." and kept.pinned == {"c1"}
    assert marks.pin(kept, [DONE, CUT]).words == "Pinned 2 sessions above the rest."  # not all were: all are now
    assert marks.pin(kept, [DONE, CUT]).words == "Unpinned 2 sessions." and kept.pinned == set()


def test_x_hides_the_closed_ones_brings_them_back_and_never_hides_an_open_one():
    kept = marks.Marks(projects={WIKI})
    assert marks.hide(kept, [OPEN]).words == marks.OPEN_ALWAYS_SHOW and kept.hidden == set()
    hid = marks.hide(kept, [OPEN, DONE])
    assert hid.hid == {"d1"} and kept.hidden == {"d1"} and hid.words.startswith("Hid “Tidy the docs”. Nothing")
    back = marks.hide(kept, [DONE, CUT])  # both out of sight (CUT by its project): both back, the project too
    assert back.words == "Back in sight: 2 sessions." and back.hid == set()
    assert kept.hidden == set() and kept.projects == set()


def test_x_on_a_project_hides_it_and_again_brings_it_back():
    kept = marks.Marks()
    there = [s("w1", "C:/work/wiki", "Notes"), s("w2", "C:/work/wiki", exit=Exit.RUNNING, status=Status.RUNNING)]
    hid = marks.hide_project(kept, WIKI, there)
    assert kept.projects == {WIKI} and hid.hid == {"w1"}  # the open one still shows, ticked or not
    assert hid.words.startswith("Hid the project wiki (2 sessions; open ones still show).")
    assert marks.hide_project(kept, WIKI, there).words == "The project wiki is back in sight."
    assert kept.projects == set()
