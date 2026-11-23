"""agy out of quota: both windows of a family, the whole chain out, and a run's quota error noted."""

from datetime import datetime, timedelta, timezone

import pytest

from tabmancer import agy

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ARGON, HYDROGEN, FLASH = agy.CHAIN
# What research's retry met on 2026-11-20: Hydrogen failed with this, while /usage said the rows below.
ERROR = ("RESOURCE_EXHAUSTED (429): Individual quota reached for gemini-4-hydrogen-high. "
         "Resets in 4h7m. Upgrade for more.")
TABLE = ("Gemini Models\tWeekly Limit Remaining\t{week}\t2026-11-25T08:53:36Z\n"
         "Gemini Models\tFive Hour Limit Remaining\t{five}\t2026-11-20T16:07:00Z\n"
         "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t{argon}\t2026-11-25T07:02:59Z\n")


@pytest.fixture(autouse=True)
def with_agy(monkeypatch):
    monkeypatch.setattr(agy, "installed", lambda: True)


def usage(week="40%", five="0%", argon="0%", out=None) -> dict:
    return {"read_at": NOW.isoformat(), "rows": agy.parse_usage(TABLE.format(week=week, five=five, argon=argon)),
            "out": out or {}, "unreadable": ""}


def test_a_family_keeps_both_its_windows():
    rows = agy.parse_usage(TABLE.format(week="40%", five="0%", argon="37%"))
    assert [(r[0], r[2]) for r in rows] == [("Gemini Models", "40%"), ("Gemini Models", "0%"), ("Gemini 4 Argon", "37%")]


@pytest.mark.parametrize(("week", "five"), [("40%", "0%"), ("0%", "40%"), ("0%", "0%")])
def test_either_window_at_nothing_is_out(week, five):
    assert agy.pick_model(usage(week=week, five=five, argon="37%"), NOW)[0] == ARGON  # Argon has its own
    assert agy.standing(HYDROGEN, usage(week=week, five=five), NOW)["left"] == 0.0
    assert agy.standing(FLASH, usage(week=week, five=five), NOW)["back"] is not None  # Flash shares it


def test_the_live_state_is_all_out_and_says_until_when():
    seen = usage()  # Gemini Models' five hours at 0%, Argon's week at 0%: as on 2026-11-20
    model, why = agy.pick_model(seen, NOW)
    assert model == FLASH and not agy.barred(model)  # still never Claude, never empty
    first_back = datetime(2026, 11, 20, 16, 7, tzinfo=timezone.utc)
    assert why.startswith(f"all agy models out of quota until {agy._when_back(first_back)}")
    assert "Hydrogen back first" in why and "will likely fail" in why
    assert agy.all_out(seen, NOW) == first_back
    assert agy.resolve("", seen, NOW) == (model, why)
    assert agy.resolve("opus", seen, NOW)[0] == FLASH


def test_not_all_out_while_one_has_some():
    assert agy.all_out(usage(five="12%"), NOW) is None  # Hydrogen has some: Argon out, Hydrogen next
    assert agy.pick_model(usage(five="12%"), NOW)[0] == HYDROGEN
    assert agy.all_out(usage(argon="5%"), NOW) is None
    assert agy.all_out({}, NOW) is None  # nothing known: not out


def test_out_until_the_last_window_lifts():
    week_back = datetime(2026, 11, 25, 8, 53, 36, tzinfo=timezone.utc)
    assert agy.standing(HYDROGEN, usage(week="0%", five="0%"), NOW)["back"] == agy._local(week_back)


@pytest.mark.parametrize(("text", "after"), [
    (ERROR, timedelta(hours=4, minutes=7)),
    ("Individual quota reached, resets in ~75h", timedelta(hours=75)),
    ("quota exceeded; resets in 1d2h", timedelta(days=1, hours=2)),
    ("RESOURCE_EXHAUSTED: resets in 35m", timedelta(minutes=35)),
    ("Resource exhausted. Resets in 45 seconds", timedelta(seconds=45)),
    ("RESOURCE_EXHAUSTED (429): Individual quota reached", agy.NOTED_FOR),  # no time: the default
])
def test_a_quota_error_is_noted_until_it_resets(text, after):
    back = agy.note_quota_error(text, HYDROGEN, NOW)
    assert back == NOW + after
    assert agy.load_quota()["out"][HYDROGEN] == back.isoformat(timespec="seconds")


@pytest.mark.parametrize("text", [
    "429 Too Many Requests: the model is overloaded, try again later",
    "UNAVAILABLE (503): high demand",
    "rate limit hit, retry in 20s",
    "",
])
def test_other_errors_are_not_noted(text):
    assert agy.note_quota_error(text, HYDROGEN, NOW) is None
    assert agy.load_quota()["out"] == {}


@pytest.mark.parametrize(("written", "noted"), [
    ("Gemini 4 Hydrogen (High)", HYDROGEN), ("Gemini 4 Hydrogen (Low)", HYDROGEN), ("gemini-4-argon-medium", ARGON),
    ("Gemini 3.8 Flash (High)", FLASH), ("gemini-4-neon-high", "gemini-4-neon-high"),
])
def test_the_model_may_be_written_as_agy_names_it(written, noted):
    agy.note_quota_error(ERROR, written, NOW)
    assert list(agy.load_quota()["out"]) == [noted]


def test_hydrogen_noted_out_takes_flash_with_it_not_argon():
    agy.note_quota_error(ERROR, "Gemini 4 Hydrogen (High)", NOW)
    noted = agy.load_quota()
    assert agy.pick_model(noted, NOW)[0] == ARGON  # Argon's own week is untouched
    agy.note_quota_error("Individual quota reached, resets in 3h", ARGON, NOW)
    noted = agy.load_quota()
    assert agy.all_out(noted, NOW) == NOW + timedelta(hours=3)  # Argon back first
    assert agy.pick_model(noted, NOW + timedelta(hours=3, minutes=1))[0] == ARGON


def test_a_fresh_read_with_some_left_clears_the_note(monkeypatch):
    agy.note_quota_error(ERROR, HYDROGEN, NOW)
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: TABLE.format(week="40%", five="3%", argon="0%"))
    assert agy.refresh_quota(now=NOW + timedelta(minutes=10))
    assert agy.load_quota()["out"] == {}
    assert agy.pick_model(None, NOW + timedelta(minutes=10))[0] == HYDROGEN
