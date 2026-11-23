"""agy out of quota: both windows of a family, the whole chain out (no model then), and a run's
quota error noted."""

from datetime import datetime, timedelta, timezone

import pytest

from tabmancer import agy

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
NEON, HYDROGEN = agy.DEFAULT_CHAIN
ARGON, FLASH = "gemini-4-argon-high", "gemini-3.8-flash-high"
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
    """Neon and Hydrogen both draw on "Gemini Models": either window at 0% takes both."""
    assert agy.pick_model(usage(week=week, five=five, argon="37%"), NOW)[0] == ""  # Argon's row is not theirs
    assert agy.standing(HYDROGEN, usage(week=week, five=five), NOW)["left"] == 0.0
    assert agy.standing(NEON, usage(week=week, five=five), NOW)["back"] is not None


def test_the_live_state_is_all_out_and_says_until_when():
    seen = usage()  # Gemini Models' five hours at 0%, Argon's week at 0%: as on 2026-11-20
    first_back = datetime(2026, 11, 20, 16, 7, tzinfo=timezone.utc)
    model, why = agy.pick_model(seen, NOW)
    assert model == ""  # none: never another model put in their place, never Claude
    assert why == f"Neon and Hydrogen out until {agy._when_back(first_back)}: pick one or wait"
    assert agy.all_out(seen, NOW) == first_back
    assert agy.resolve("", seen, NOW) == (model, why)
    assert agy.resolve("opus", seen, NOW) == ("", why)
    assert agy.resolve(ARGON, seen, NOW) == (ARGON, "")  # typed: the user's choice, out or not


def test_not_all_out_while_one_has_some():
    assert agy.all_out(usage(five="12%"), NOW) is None
    assert agy.pick_model(usage(five="12%"), NOW)[0] == NEON
    assert agy.all_out(usage(argon="5%"), NOW) is not None  # Argon has some, but it is not in the chain
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
    ("Gemini 4 Hydrogen (High)", HYDROGEN), ("Gemini 4 Hydrogen (Low)", HYDROGEN), ("gemini-4-neon-medium", NEON),
    ("Gemini 3.8 Flash (High)", FLASH), ("gemini-4-argon-high", ARGON), ("Gemini 4 Argon (Medium)", "gemini-4-argon-medium"),
])
def test_the_model_may_be_written_as_agy_names_it(written, noted):
    agy.note_quota_error(ERROR, written, NOW)
    assert list(agy.load_quota()["out"]) == [noted]


def test_one_models_429_takes_only_that_model():
    """"Individual quota reached" for Neon: Hydrogen is tried next, not taken with it."""
    agy.note_quota_error("Individual quota reached for gemini-4-neon-high, resets in 3h", "Gemini 4 Neon (High)", NOW)
    noted = agy.load_quota()
    assert list(noted["out"]) == [NEON]
    assert agy.pick_model(noted, NOW)[0] == HYDROGEN
    agy.note_quota_error(ERROR, HYDROGEN, NOW)
    noted = agy.load_quota()
    assert agy.pick_model(noted, NOW)[0] == ""
    assert agy.all_out(noted, NOW) == NOW + timedelta(hours=3)  # Neon back first
    assert agy.pick_model(noted, NOW + timedelta(hours=3, minutes=1))[0] == NEON


def test_a_fresh_read_clears_a_note_only_by_the_models_own_row(monkeypatch):
    """The shared family with some left says nothing of one model's own quota: Hydrogen's note
    holds until its time. Argon's own row with some left clears Argon's."""
    agy.note_quota_error(ERROR, HYDROGEN, NOW)
    agy.note_quota_error("Individual quota reached, resets in 3h", ARGON, NOW)
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: TABLE.format(week="40%", five="3%", argon="20%"))
    later = NOW + timedelta(minutes=10)
    assert agy.refresh_quota(now=later)
    assert list(agy.load_quota()["out"]) == [HYDROGEN]
    assert agy.pick_model(None, later)[0] == NEON
    assert agy.pick_model(None, NOW + timedelta(hours=4, minutes=8))[0] == NEON
