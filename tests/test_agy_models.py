"""The model an agy session runs on: Argon while its quota lasts, then Hydrogen, then Flash; never Claude."""

import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from tabmancer import agy

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
ARGON, HYDROGEN, FLASH = agy.CHAIN
REAL_RUN_USAGE = agy._run_usage  # conftest puts a stub in its place for every test


@pytest.fixture(autouse=True)
def fresh_reads(monkeypatch):
    """Every test reads agy's quota afresh (a read under REREAD_AFTER old would serve it otherwise),
    on a machine with agy whether or not this one has it."""
    monkeypatch.setattr(agy, "_last_read", None)
    monkeypatch.setattr(agy, "installed", lambda: True)


# As agy 1.5.13 printed it on 2026-11-20 (remaining shares changed per test).
USAGE = (
    "Gemini Models\tWeekly Limit Remaining\t{gemini_week}\t2026-11-25T08:53:36Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t{gemini_5h}\t2026-11-20T12:53:15Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t0%\t2026-11-26T14:48:38Z\n"
    "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-11-20T13:08:48Z\n"
    "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t{argon}\t2026-11-25T07:02:59Z\n"
)


def quota(argon="37%", gemini_week="54%", gemini_5h="42%", read_at=NOW, out=None) -> dict:
    rows = agy.parse_usage(USAGE.format(argon=argon, gemini_week=gemini_week, gemini_5h=gemini_5h))
    return {"read_at": read_at.isoformat() if read_at else "", "rows": rows, "out": out or {}}


def test_the_table_is_read_as_agy_prints_it():
    rows = agy.parse_usage(USAGE.format(argon="37%", gemini_week="54%", gemini_5h="42%") + "a stray line\n")
    assert len(rows) == 5
    assert rows[-1] == ["Gemini 4 Argon", "Gemini 4 Argon Weekly Limit", "37%", "2026-11-25T07:02:59Z"]


def test_argon_while_its_quota_lasts():
    model, why = agy.pick_model(quota(), NOW)
    assert model == ARGON
    assert "37%" in why and "week" in why


def test_hydrogen_when_argon_is_out():
    model, why = agy.pick_model(quota(argon="0%"), NOW)
    assert model == HYDROGEN
    assert "Argon out of quota" in why


def test_flash_only_when_hydrogen_is_out_too():
    model, why = agy.pick_model(quota(argon="0%", gemini_5h="0%"), NOW)
    assert model == FLASH
    assert "Argon" in why and "Hydrogen" in why


def test_argon_comes_back_at_its_reset():
    later = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)  # past Argon's reset, read still fresh
    assert agy.pick_model(quota(argon="0%", read_at=later - timedelta(minutes=5)), later)[0] == ARGON


def test_unknown_quota_tries_argon_first():
    for usage in ({}, {"read_at": "", "rows": [], "out": {}}, quota(argon="0%", read_at=NOW - timedelta(hours=3))):
        model, why = agy.pick_model(usage, NOW)
        assert model == ARGON
        assert "not read" in why


def test_a_noted_failure_holds_until_its_reset():
    noted = {ARGON: (NOW + timedelta(hours=1)).isoformat(), HYDROGEN: (NOW + timedelta(hours=2)).isoformat()}
    assert agy.pick_model({"out": noted}, NOW)[0] == FLASH
    assert agy.pick_model({"out": noted}, NOW + timedelta(hours=1, minutes=1))[0] == ARGON


def test_the_whole_chain_out_is_flash_all_the_same_and_says_when_argon_is_back():
    noted = {model: (NOW + timedelta(hours=i + 1)).isoformat() for i, model in enumerate(agy.CHAIN)}
    model, why = agy.pick_model({"out": noted}, NOW)
    assert model == FLASH
    assert "the last" in why and "Argon is back" in why
    assert agy.resolve("", {"out": noted}, NOW) == (model, why)  # the two agree; never agy's own default


def test_argon_and_hydrogen_out_is_flash_for_both():
    usage = quota(argon="0%", gemini_week="0%")
    assert agy.pick_model(usage, NOW)[0] == agy.resolve("", usage, NOW)[0] == FLASH


def test_no_model_only_without_agy(monkeypatch):
    monkeypatch.setattr(agy, "installed", lambda: False)
    assert agy.pick_model(quota(), NOW) == ("", "agy is not installed")


def test_a_broken_quota_never_raises():
    assert agy.pick_model({"read_at": 5, "rows": [[None, None, None]], "out": {"x": 3}}, NOW)[0] == ARGON
    assert agy.pick_model({"rows": "nonsense"}, NOW)[0] == ARGON


@pytest.mark.parametrize("asked", [
    "", "opus", "sonnet", "haiku", "fable", "claude-opus-5-5", "claude-sonnet-5-5-high", "claude-opus-5-5-low",
    "Claude Opus 5.5 (High)", "CLAUDE-SONNET-5-5-MEDIUM", "gpt-oss-120b-medium", "auto",
])
@pytest.mark.parametrize("state", ["fresh", "argon out", "both out", "all out", "unknown"])
def test_no_claude_model_ever_comes_out_for_agy(asked, state):
    usage = {
        "fresh": quota(),
        "argon out": quota(argon="0%"),
        "both out": quota(argon="0%", gemini_week="0%"),
        "all out": {"out": {m: (NOW + timedelta(hours=1)).isoformat() for m in agy.CHAIN}},
        "unknown": {},
    }[state]
    model, note = agy.resolve(asked, usage, NOW)
    assert model in agy.CHAIN
    assert not agy.barred(model)
    if agy.barred(asked):
        assert "never runs through agy" in note


def test_a_chain_model_asked_for_is_kept_and_goes_on_down_the_chain():
    assert agy.resolve("gemini-4-hydrogen-high", quota(), NOW) == (HYDROGEN, "")
    assert agy.resolve("Gemini 4 Hydrogen (High)", quota(), NOW) == (HYDROGEN, "")
    model, note = agy.resolve(ARGON, quota(argon="0%"), NOW)
    assert model == HYDROGEN and "Argon out of quota" in note


def test_claude_is_recognised_by_every_name():
    assert all(agy.is_claude(m) for m in ("opus", "Sonnet", "claude-haiku-4-5", "Claude Opus 5.5 (High)", "fable"))
    assert not any(agy.is_claude(m) for m in (*agy.CHAIN, "gemini-4-neon-high", "gpt-oss-120b-medium", ""))
    assert agy.barred("gpt-oss-120b-medium") and agy.barred("GPT-OSS 120B (Medium)") and agy.barred("opus")
    assert not any(agy.barred(m) for m in (*agy.CHAIN, "gemini-4-neon-high", ""))


def test_any_other_model_asked_for_is_kept_as_it_is():
    assert agy.resolve("gemini-4-neon-high", quota(argon="0%"), NOW) == ("gemini-4-neon-high", "")
    assert agy.resolve(" gemini-4-neon ", {}, NOW) == ("gemini-4-neon", "")


def test_quota_is_read_once_for_callers_at_the_same_moment(monkeypatch):
    reads = []
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: reads.append(1) or USAGE.format(
        argon="37%", gemini_week="54%", gemini_5h="42%"))
    assert agy.refresh_quota(now=NOW) and agy.refresh_quota(now=NOW)
    assert len(reads) == 1
    monkeypatch.setattr(agy.time, "monotonic", lambda: 10_000_000.0)  # long after
    assert agy.refresh_quota(now=NOW)
    assert len(reads) == 2


def test_refresh_keeps_the_table_and_drops_old_notes(monkeypatch):
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: USAGE.format(argon="0%", gemini_week="54%",
                                                                         gemini_5h="42%"))
    agy.note_out_of_quota(FLASH, NOW - timedelta(minutes=1), now=NOW - timedelta(hours=1))  # over by now
    agy.note_out_of_quota("Gemini 4 Hydrogen (High)", NOW + timedelta(hours=1), now=NOW)
    assert agy.refresh_quota(now=NOW)
    saved = agy.load_quota()
    assert saved["read_at"] and len(saved["rows"]) == 5
    assert saved["out"] == {}  # Flash's note was over; Hydrogen's family shows some left: back
    assert agy.pick_model(now=NOW)[0] == HYDROGEN  # Argon at 0% in the table


def test_a_noted_failure_stays_while_its_family_is_at_nothing_or_unseen(monkeypatch):
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: USAGE.format(argon="0%", gemini_week="54%",
                                                                         gemini_5h="0%"))
    agy.note_out_of_quota(HYDROGEN, NOW + timedelta(hours=1), now=NOW)
    agy.note_out_of_quota(FLASH, NOW + timedelta(hours=1), now=NOW)  # no family of its own in /usage
    assert agy.refresh_quota(now=NOW)
    assert set(agy.load_quota()["out"]) == {HYDROGEN, FLASH}


def test_refresh_without_an_answer_changes_nothing(monkeypatch):
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: None)
    assert not agy.refresh_quota(now=NOW)
    assert agy.load_quota() == {"read_at": "", "rows": [], "out": {}, "unreadable": ""}
    assert "quota not read yet" in agy.pick_model(now=NOW)[1]


def test_an_answer_that_is_no_table_is_said_where_the_pick_is(monkeypatch):
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: "Usage: something new\n")
    assert not agy.refresh_quota(now=NOW)
    model, why = agy.pick_model(now=NOW)
    assert model == ARGON and "couldn't read agy's quota" in why


@pytest.mark.parametrize("table", [
    "Gemini 4 Argon    Gemini 4 Argon Weekly Limit    0%    2099-11-25T07:02:59Z",  # spaces, not tabs
    "GEMINI 4 ARGON\tWeekly\t0%\t2099-11-25T07:02:59Z",  # another case
    "Gemini 4 Argon (Preview)\tWeekly\t0 %\t",  # a longer family name, a space before %
])
def test_a_table_that_drifted_is_still_read(table):
    usage = {"read_at": NOW.isoformat(), "rows": agy.parse_usage(table), "out": {}}
    assert len(usage["rows"]) == 1
    assert agy.pick_model(usage, NOW)[0] == HYDROGEN


@pytest.mark.parametrize(("share", "value"), [("37%", 37.0), ("37,5%", 37.5), ("<1%", 0.5), ("0%", 0.0),
                                              ("100", 100.0), ("n/a", None), ("", None)])
def test_shares_in_any_form(share, value):
    assert agy._percent(share) == value


def test_under_one_percent_is_not_out():
    usage = {"read_at": NOW.isoformat(), "rows": agy.parse_usage("Gemini 4 Argon\tWeekly\t<1%\t"), "out": {}}
    assert agy.pick_model(usage, NOW)[0] == ARGON


def test_a_failed_version_read_is_tried_again(monkeypatch):
    answers = iter([None, (0, "agy 1.5.13\n")])
    monkeypatch.setattr(agy.shutil, "which", lambda name: "agy")
    monkeypatch.setattr(agy, "_captured", lambda argv, timeout, env=None: next(answers))
    assert agy._agy_version() == ()
    assert agy._agy_version() == (1, 5, 13)


def test_a_caller_waits_for_another_read_no_longer_than_its_timeout():
    agy._reading.acquire()  # another read in flight
    try:
        started = time.monotonic()
        assert agy.refresh_quota(timeout=0.2, now=NOW) is False
        assert time.monotonic() - started < 2
    finally:
        agy._reading.release()


def test_usage_is_never_asked_of_an_agy_that_would_send_it_to_the_model(monkeypatch):
    calls = []
    monkeypatch.setattr(agy.shutil, "which", lambda name: "agy")
    monkeypatch.setattr(agy, "_agy_version", lambda: (1, 1, 10))
    monkeypatch.setattr(agy, "_captured", lambda *a, **k: calls.append(a))
    assert REAL_RUN_USAGE(5) is None
    assert calls == []


def test_usage_goes_without_a_shell_and_without_path_conversion(monkeypatch):
    seen = {}
    table = "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t37%\t\n"

    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        raise OSError("not started in tests")

    monkeypatch.setattr(agy.shutil, "which", lambda name: "agy")
    monkeypatch.setattr(agy, "_agy_version", lambda: (1, 5, 13))
    monkeypatch.setattr(agy.subprocess, "Popen", popen)
    assert REAL_RUN_USAGE(5) is None  # agy did not start: no table
    assert seen["argv"] == ["agy", "--print-timeout", "20s", "-p", "/usage"]
    assert seen["env"]["MSYS_NO_PATHCONV"] == "1"
    assert not seen.get("shell")
    monkeypatch.setattr(agy, "_captured", lambda argv, timeout, env=None: (0, table))
    assert REAL_RUN_USAGE(5) == table
    monkeypatch.setattr(agy, "_captured", lambda argv, timeout, env=None: (1, table))
    assert REAL_RUN_USAGE(5) is None  # a failed run is no table


def test_a_command_that_outlives_its_time_is_cut_off_with_its_children():
    """A hard limit: a child holding the pipe open does not keep the read waiting."""
    started = time.monotonic()
    child = ("import subprocess, sys, time; "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)")
    assert agy._captured([sys.executable, "-c", child], timeout=1) is None
    assert time.monotonic() - started < 10
    done = agy._captured([sys.executable, "-c", "print('1.5.13')"], timeout=20)
    assert done is not None and done[0] == 0 and done[1].strip() == "1.5.13"
