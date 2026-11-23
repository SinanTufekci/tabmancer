"""agy's chain in the top bar and in Bit (agy.bar_view): from the saved quota only, never an agy run;
shown only while agy is in use, first when agy is the main CLI. Fakes only: no CLI is run."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import test_agy_picker
from test_agy_picker import BOTH_OUT, HYDROGEN, NEON, TABLE, neon_out

from tabmancer import agy, clis
from tabmancer.sessions import Exit, Session
from tabmancer.usage import Limit

usage = test_agy_picker.usage  # agy's /usage as a test sets it: no agy is run


@pytest.fixture
def here(monkeypatch, usage):
    """agy installed, with the /usage the test sets."""
    monkeypatch.setattr(agy, "installed", lambda: "/bin/agy")
    return usage


def read(here, table: str) -> None:
    here["table"] = table
    agy._last_read = None  # a fresh read, as one after REREAD_AFTER
    assert agy.refresh_quota()


def test_nothing_read_says_so_and_agy_missing_shows_nothing(here, monkeypatch):
    assert agy.bar_view() == {"parts": [("agy quota not read", "unknown")], "all_out": None, "said": ""}
    assert here["asked"] == 0  # the bar never runs agy itself
    monkeypatch.setattr(agy, "installed", lambda: None)
    assert agy.bar_view() == {}


def test_each_model_of_the_chain_with_what_is_left(here):
    read(here, TABLE)
    assert agy.bar_view()["parts"] == [("Neon 54% left", "ok"), ("Hydrogen 54% left", "ok")]
    read(here, TABLE.replace("54%", "9%", 1))
    assert agy.bar_view()["parts"] == [("Neon 9% left", "low"), ("Hydrogen 9% left", "low")]
    assert not any("Argon" in words for words, _ in agy.bar_view()["parts"])  # off the chain: not shown


def test_a_model_out_says_till_when_and_the_next_is_still_shown(here):
    read(here, TABLE)
    neon_out()
    (neon, neon_tone), hydrogen = agy.bar_view()["parts"]
    assert neon.startswith("Neon out till ") and neon_tone == "out" and hydrogen == ("Hydrogen 54% left", "ok")
    assert agy.bar_view()["all_out"] is None and agy.bar_view()["said"] == ""


def test_every_model_out_tells_bit_which_and_until_when(here):
    read(here, BOTH_OUT)
    bar = agy.bar_view()
    assert [tone for _, tone in bar["parts"]] == ["out", "out"]
    assert bar["all_out"] is not None and bar["all_out"].tzinfo is None  # local, as the bar shows it
    assert bar["said"].startswith("agy: Neon and Hydrogen out until ") and "pick one or wait" in bar["said"]
    assert all(words.split("till ")[1][:3].isalpha() for words, _ in bar["parts"])  # another day: its weekday
    stale = agy.bar_view(now=datetime.now(timezone.utc) + timedelta(hours=3))  # a read too old says nothing now
    assert [tone for _, tone in stale["parts"]] == ["unknown"] and stale["parts"][0][0].startswith("agy quota read ")
    assert here["asked"] == 1  # the one read the test made


async def test_the_top_bar_shows_agy_only_while_it_is_used_and_first_when_it_is_main(fake_home, here, monkeypatch):
    from tabmancer.app import SessionPicker

    read(here, TABLE)
    limits = [Limit("session", 30, None, "")]
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.1)
        shown = lambda: str(app.topbar.query_one("#usage").content)
        assert not app.uses_agy()
        app.refresh_usage()
        await app.workers.wait_for_complete()
        assert app.agy_bar == {}  # no agy session: agy's quota is not even read
        app.sessions["g"] = Session(session_id="g", path=None, cli="agy", model=NEON, exit=Exit.RUNNING)
        assert app.uses_agy()
        app.agy_bar = agy.bar_view()
        app.show_usage(limits)
        assert shown().startswith("session ") and shown().endswith("agy Neon 54% left · Hydrogen 54% left")
        monkeypatch.setattr(clis, "main_cli", lambda: "agy")
        app.show_usage(limits)
        assert shown().startswith("agy Neon 54% left · Hydrogen 54% left   session ")
        app.show_usage([])
        assert shown() == "agy Neon 54% left · Hydrogen 54% left"
        assert HYDROGEN  # the chain the bar names, as test_agy_picker has it


async def test_bit_says_every_agy_model_is_out_once(fake_home, here, monkeypatch):
    from tabmancer.app import SessionPicker

    read(here, BOTH_OUT)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(0.1)
        said = []
        monkeypatch.setattr(app.mascot, "say", lambda text, colour="", seconds=4: said.append(text))
        app.agy_bar = agy.bar_view()
        app.show_usage([])
        app.show_usage([])  # the next reading: still out, said already
        assert said == [app.agy_bar["said"]]
        app.agy_bar = {}  # back (or no agy any more), then out again: said again
        app.show_usage([])
        app.agy_bar = agy.bar_view()
        app.show_usage([])
        assert len(said) == 2
