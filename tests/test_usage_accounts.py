"""Plan limit readings are an account's: each one names the Claude account signed in while it was read,
the stats chart and forecast one account's, and where you choose to go on says how the other
accounts' limits stood when last read."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from test_accounts import MINE, MINE_NAME, TEAM_A, claude_in
from test_limits import stopped_at, wait_for, write

from tabmancer import accounts, usage
from tabmancer.accounts import Account
from tabmancer.config import config_dir
from tabmancer.usage import Limit

NOW = datetime(2026, 11, 23, 12, 0)
TEAM = Account("claude", "s@example.com", "Team", "acc-1", TEAM_A, "Example Labs", "claude_team")
PERSONAL = Account("claude", "s@example.com", "Max 5x", "acc-1", MINE, MINE_NAME, "claude_max")


def lines() -> list[dict]:
    path = config_dir() / usage.HISTORY_FILE
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_reading_names_its_account_and_the_history_is_filtered_by_it():
    usage.record([Limit("session", 10, None, "")], now=NOW - timedelta(hours=3))  # from before: no account
    usage.record([Limit("session", 20, None, "")], now=NOW - timedelta(hours=2), account=TEAM.key)
    usage.record([Limit("session", 70, None, "")], now=NOW - timedelta(hours=1), account=PERSONAL.key)
    assert [line.get("account") for line in lines()] == [None, TEAM.key, PERSONAL.key]
    assert [r.percent for r in usage.history(1, NOW)] == [10, 20, 70]  # no filter: all, the old line too
    assert [r.percent for r in usage.history(1, NOW, TEAM.key)] == [20]  # the old line is no one's
    assert [r.account for r in usage.history(1, NOW)] == ["", TEAM.key, PERSONAL.key]


def test_the_forecast_is_one_accounts():
    readings = [usage.Reading(NOW - timedelta(minutes=60 - m), "session", p, NOW + timedelta(hours=2), who)
                for m, p, who in [(0, 10, TEAM.key), (10, 90, PERSONAL.key), (30, 20, TEAM.key),
                                  (40, 95, PERSONAL.key), (60, 30, TEAM.key)]]
    team = usage.forecast(readings, "session", NOW, TEAM.key)
    assert team is not None and team.percent == 30 and round(team.per_hour) == 20
    assert usage.forecast(readings, "session", NOW, "someone-else") is None


def test_the_monitor_takes_no_other_accounts_reading():
    usage.record([Limit("session", 40, None, "")], now=NOW - timedelta(minutes=4), account=PERSONAL.key)
    assert usage.latest(now=NOW, account=TEAM.key) is None  # signed in with the Team since: read again
    assert [lim.percent for lim in usage.latest(now=NOW, account=PERSONAL.key)] == [40]
    assert [lim.percent for lim in usage.latest(now=NOW)] == [40]
    usage.record([Limit("session", 50, None, "")], now=NOW - timedelta(minutes=2))  # a line from before
    assert [lim.percent for lim in usage.latest(now=NOW, account=TEAM.key)] == [50]


def test_each_accounts_last_reading():
    usage.record([Limit("session", 99, None, "")], now=NOW - timedelta(hours=5))  # no account: not in it
    usage.record([Limit("session", 20, None, "")], now=NOW - timedelta(hours=4), account=TEAM.key)
    usage.record([Limit("session", 100, NOW + timedelta(hours=1), ""), Limit("codex 5h", 3, None, "")],
                 now=NOW - timedelta(hours=3), account=PERSONAL.key)
    usage.record([Limit("session", 35, None, "")], now=NOW - timedelta(hours=2), account=TEAM.key)
    last = usage.last_by_account()
    assert set(last) == {TEAM.key, PERSONAL.key}
    assert last[TEAM.key][0] == NOW - timedelta(hours=2) and [lim.percent for lim in last[TEAM.key][1]] == [35]
    assert [lim.label for lim in last[PERSONAL.key][1]] == ["session"]  # Codex's are not this account's


def test_the_last_reading_in_words():
    read = NOW - timedelta(hours=2)
    full = [Limit("session", 100, NOW.replace(hour=16), ""), Limit("week", 40, NOW + timedelta(days=3), ""),
            Limit("week Fable", 12, None, "")]
    assert usage.last_words(read, full, NOW) == "session 100% · opens 16:00 · week 40% · read 2h00m ago"
    passed = [Limit("session", 100, NOW - timedelta(minutes=5), ""), Limit("week", 40, NOW - timedelta(hours=1), "")]
    assert usage.last_words(read, passed, NOW) == "session probably open · week reset since · read 2h00m ago"
    tomorrow = [Limit("week Fable", 100, datetime(2026, 11, 24, 9, 0), "")]  # a model's week, in the way
    assert usage.last_words(NOW, tomorrow, NOW) == "week Fable 100% · opens Tue 09:00 · read just now"


def test_the_chart_is_the_signed_in_accounts_once_there_are_two():
    usage.record([Limit("session", 10, None, "")], now=NOW - timedelta(hours=2))
    assert usage.chart_account(TEAM.key, 1, NOW) is None  # old lines only: all of them
    usage.record([Limit("session", 20, None, "")], now=NOW - timedelta(hours=1), account=TEAM.key)
    assert usage.chart_account(TEAM.key, 1, NOW) is None  # one account: still all
    usage.record([Limit("session", 90, None, "")], now=NOW - timedelta(minutes=30), account=PERSONAL.key)
    assert usage.chart_account(TEAM.key, 1, NOW) == TEAM.key
    assert usage.chart_account(None, 1, NOW) is None


async def test_a_reading_taken_across_a_switch_is_dropped_and_read_again(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    claude_in(fake_home)  # the Team seat
    reads = []

    def read_all():
        reads.append(accounts.claude().key)
        if len(reads) == 1:  # signed in to the personal plan while claude -p /usage ran
            claude_in(fake_home, org=MINE, kind="claude_max", name=MINE_NAME)
        return [Limit("session", 100 if len(reads) == 1 else 15, None, "")]

    monkeypatch.setattr(usage, "read_all", read_all)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: reads)
        await app.workers.wait_for_complete()
        assert lines() == []  # whose limits those were is not known: none kept, none shown
        app.show_accounts()  # its next look (every few seconds) sees the switch: read again
        await wait_for(pilot, lambda: len(reads) >= 2 and lines())
        await app.workers.wait_for_complete()
        assert [lim.percent for lim in app.limits] == [15]
    assert reads[0] == TEAM.key
    assert [(line["account"], line["limits"][0]["percent"]) for line in lines()] == [(PERSONAL.key, 15)]


async def test_c_and_a_say_how_the_other_account_stood(fake_home, monkeypatch):
    from textual.widgets import Label, OptionList

    from tabmancer.accountsview import AccountsDialog
    from tabmancer.app import LimitDialog, SessionPicker

    claude_in(fake_home)
    accounts.remember(PERSONAL, "2026-11-23T09:00:00")
    now = datetime.now()
    usage.record([Limit("session", 12, now + timedelta(hours=3), ""), Limit("week", 40, now + timedelta(days=2), "")],
                 now=now - timedelta(minutes=45), account=PERSONAL.key)
    monkeypatch.setattr(usage, "read_all", lambda: [Limit("session", 100, None, ""), Limit("week", 60, None, "")])
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 44)) as pilot:
        await wait_for(pilot, lambda: "lim" in app.sessions and app.limits)
        await pilot.press("c")
        await wait_for(pilot, lambda: isinstance(app.screen, LimitDialog))
        plan = str(app.screen.query_one(".plan", Label).render())
        assert "Your plan now: session 100% · week 60%" in plan
        assert "s@example.com · personal · Max 5x: session 12% · week 40% · read 45m ago" in plan
        await pilot.press("escape")
        await pilot.pause(0.1)
        await pilot.press("A")
        await wait_for(pilot, lambda: isinstance(app.screen, AccountsDialog))
        options = app.screen.query_one(OptionList)
        said = {options.get_option_at_index(i).id: str(options.get_option_at_index(i).prompt)
                for i in range(options.option_count)}
        assert "session 12% · week 40% · read 45m ago" in said[PERSONAL.key]
        assert "read" not in said[TEAM.key]  # signed in now: its limits are in the top bar


def test_an_export_names_no_account_by_its_ids(fake_home):
    """An export gets shared: whose each reading is says "account 1", "account 2", never their ids."""
    import re
    import uuid

    from tabmancer import stats, tokens

    ids = [str(uuid.uuid4()) for _ in range(3)]
    claude_in(fake_home, account=ids[0], org=ids[2])  # signed in now: the second account below
    now = datetime.now()
    usage.record([Limit("session", 10, None, "")], now=now - timedelta(hours=3))  # from before: no one's
    usage.record([Limit("session", 20, None, "")], now=now - timedelta(hours=2), account=f"{ids[0]}:{ids[1]}")
    usage.record([Limit("session", 30, None, "")], now=now - timedelta(hours=1), account=f"{ids[0]}:{ids[2]}")
    data = stats.export(tokens.collect(7), {})
    assert [{k: v for k, v in r.items() if k in ("percent", "account", "current")} for r in data["limits"]] == [
        {"percent": 10}, {"percent": 20, "account": "account 1"},
        {"percent": 30, "account": "account 2", "current": True}]
    text = json.dumps(data)
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
    assert not any(one in text for one in ids)


# --- F3: a limit's note says how the other account stood -----------------------------------------
def clock(moment: datetime) -> str:
    return f"{moment:%H:%M}"


def test_a_limits_note_names_the_other_account_with_room():
    opens = NOW.replace(hour=16)
    room = ("b@example.com", NOW - timedelta(minutes=45), [Limit("session", 12, NOW + timedelta(hours=2), ""),
                                                           Limit("week", 40, NOW + timedelta(days=2), "")])
    full = ("c@example.com", NOW - timedelta(hours=1), [Limit("session", 100, NOW.replace(hour=14), "")])
    line, part = usage.other_accounts("a@example.com", opens, [full, room], clock, NOW)
    assert line == "a@example.com: limit reached (opens 16:00) · b@example.com has room (session 12%, 45m ago): A to switch"
    assert part == "b@example.com has room (session 12%, 45m ago): A to switch"
    lifted = ("c@example.com", NOW - timedelta(hours=6), [Limit("session", 100, NOW - timedelta(hours=1), "")])
    assert usage.other_accounts("a@example.com", opens, [lifted], clock, NOW)[1] == \
        "c@example.com has room (session probably open, 6h00m ago): A to switch"  # its reset has passed
    stale = ("c@example.com", NOW - timedelta(hours=6), [Limit("session", 30, NOW - timedelta(hours=1), "")])
    assert usage.other_accounts("a@example.com", opens, [stale], clock, NOW)[1] == \
        "c@example.com has room (session reset since, 6h00m ago): A to switch"  # as A says it (last_words)


def test_a_limits_note_says_when_all_are_full_and_nothing_with_no_reading():
    opens = NOW.replace(hour=16)
    full = ("c@example.com", NOW - timedelta(hours=1), [Limit("session", 100, NOW.replace(hour=14), "")])
    assert usage.other_accounts("a@example.com", opens, [full], clock, NOW) == ("both full · earliest 14:00",) * 2
    week = ("d@example.com", NOW, [Limit("week", 100, NOW + timedelta(days=1), "")])
    assert usage.other_accounts("a@example.com", opens, [full, week], clock, NOW)[0] == "all full · earliest 14:00"
    assert usage.other_accounts("a@example.com", opens, [], clock, NOW) == ("", "")


async def test_bit_and_the_desktop_note_say_the_other_account_has_room(fake_home, monkeypatch):
    from tabmancer import toast
    from tabmancer.app import SessionPicker
    from tabmancer.viewmodel import lifts

    claude_in(fake_home)  # the Team seat, at its limit
    accounts.remember(PERSONAL, "2026-11-23T09:00:00")
    now = datetime.now()
    usage.record([Limit("session", 12, now + timedelta(hours=3), "")], now=now - timedelta(minutes=45),
                 account=PERSONAL.key)
    monkeypatch.setattr(usage, "read_all", list)
    resets = (now + timedelta(hours=2)).strftime("%I:%M%p").lstrip("0").lower()
    write(fake_home, "lim", stopped_at(f"You've hit your session limit · resets {resets}"))
    sent = []
    monkeypatch.setattr(toast, "enabled", lambda: True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 44)) as pilot:
        await wait_for(pilot, lambda: "lim" in app.sessions and app.told_limit)
        said = " ".join(saying.text for saying in app.mascot.bubbles)
        assert "s@example.com · personal has room (session 12%, 45m ago): A to switch" in said
        app.send_toast = lambda _title, body: sent.append(body)  # the dashboard's own, set on it alone
        app.app_focus = False  # not in front (asked outside Windows only)
        app.tell_desktop([app.sessions["lim"]])
        opens = lifts(app.sessions["lim"].resets)
    (body,) = sent
    assert body.splitlines()[1] == (f"s@example.com · Example Labs: limit reached (opens {opens}) · "
                                    "s@example.com · personal has room (session 12%, 45m ago): A to switch")
