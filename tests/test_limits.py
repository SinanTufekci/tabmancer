"""Sessions stopped at a usage limit: told apart from other errors, shown with the time the
limit lifts, and continued with c: now (after signing in with another account) or when the limit lifts;
a closed one in a new tab, an open one with the prompt typed into its own (closed and resumed if it
cannot be)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tabmancer import app as app_module
from tabmancer import handover, sessions, typein
from tabmancer.app import LimitDialog, SessionPicker
from tabmancer.config import read_json, write_json
from tabmancer.launch import CONTINUE_BECAUSE, Mode, continue_prompt, default_mode
from tabmancer.sessions import Status, _from_cache, _limit_resets, _to_cache, read_session
from tabmancer.usage import Limit

CWD = "C:/work/demo"


def utc(moment: datetime) -> str:
    """A local clock time as a transcript writes it."""
    return moment.astimezone().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stopped_at(text: str, error: str = "rate_limit", at: datetime | None = None) -> dict:
    return {"type": "assistant", "cwd": CWD, "timestamp": utc(at or datetime.now()), "error": error,
            "isApiErrorMessage": True, "message": {"content": [{"type": "text", "text": text}]}}


def write(home, sid: str, last: dict) -> None:
    folder = home / "projects" / "C--work-demo"
    folder.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "cwd": CWD, "entrypoint": "cli", "message": {"content": f"do {sid}"}},
               last, {"type": "ai-title", "aiTitle": f"Task {sid}"}]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                                         encoding="utf-8")


async def wait_for(pilot, condition, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


def test_the_reset_time_is_read_from_the_reply():
    afternoon = datetime(2026, 10, 20, 14, 30)
    assert _limit_resets(stopped_at("You've hit your session limit · resets 4pm", at=afternoon)) == \
        datetime(2026, 10, 20, 16, 0)
    assert _limit_resets(stopped_at("You've hit your session limit · resets 4:10pm", at=afternoon)) == \
        datetime(2026, 10, 20, 16, 10)
    late = datetime(2026, 10, 20, 23, 30)  # a time already past today is tomorrow's
    assert _limit_resets(stopped_at("You've hit your session limit · resets 2am", at=late)) == \
        datetime(2026, 10, 21, 2, 0)
    weekly = stopped_at("You've hit your weekly limit · resets Oct 6, 6am", at=datetime(2026, 10, 3, 9, 0))
    assert _limit_resets(weekly) == datetime(2026, 10, 6, 6, 0)
    unknown = stopped_at("Claude AI usage limit reached", at=afternoon)  # an older wording: five hours on
    assert _limit_resets(unknown) == afternoon + timedelta(hours=5)


def test_a_limit_and_a_dropped_connection_are_told_apart_from_other_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write(tmp_path, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    write(tmp_path, "net", stopped_at("API Error: Connection dropped (ECONNRESET)", "server_error"))
    write(tmp_path, "err", stopped_at("API Error: 400 bad request", "invalid_request"))
    found = {sid: read_session(tmp_path / "projects" / "C--work-demo" / f"{sid}.jsonl") for sid in ("lim", "net", "err")}
    assert found["lim"].status is Status.LIMIT and found["lim"].resets > datetime.now()
    assert found["net"].status is Status.DROPPED and found["net"].resets is None
    assert found["err"].status is Status.ERROR
    assert _from_cache(json.loads(json.dumps(_to_cache(found["lim"])))) == found["lim"]  # the cache keeps it


def test_each_reason_continues_with_its_own_words(fake_home):
    assert default_mode(Status.LIMIT) is Mode.AUTO and default_mode(Status.DROPPED) is Mode.AUTO
    assert continue_prompt(Status.LIMIT) == CONTINUE_BECAUSE[Status.LIMIT]
    assert "shut down" not in continue_prompt(Status.LIMIT)
    write_json("config.json", {"continue_prompt": "Go on."})  # a prompt of your own wins
    assert continue_prompt(Status.LIMIT) == "Go on."


async def test_a_limit_shows_when_it_lifts_and_is_not_counted_as_waiting_for_you(fake_home, monkeypatch):
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    write(fake_home, "open", stopped_at("You've hit your session limit · resets 11:59pm"))
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "open", "kind": "interactive", "status": "idle"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("open") and app.sessions["open"].live_state)
        await pilot.pause(0.3)
        closed, opened = app.sessions["lim"], app.sessions["open"]
        assert str(app.status_cell(closed)).startswith("◐ limit · ") and "23:59" in str(app.status_cell(closed))
        assert str(app.status_cell(opened)).startswith("◐ limit · ")  # open, but waiting for the limit
        assert not app.cards["card-waiting"].has_class("alert")  # NEED YOU: nobody
        assert "1 at the limit" in str(app.cards["card-open"].beside.content)
        assert app.check_action("continue_at_reset", ()) is True  # c is in the footer now
        closed.resets = datetime.now() - timedelta(minutes=1)
        assert str(app.status_cell(closed)) == "◐ limit lifted"


async def choose(pilot, app, answer: str) -> list[str]:
    """c, then one of the dialog's ways: now, lift or off. Returns the ways it offered, in order."""
    from textual.widgets import OptionList

    await pilot.press("c")
    await wait_for(pilot, lambda: isinstance(app.screen, LimitDialog))
    options = app.screen.query_one(OptionList)
    ways = [options.get_option_at_index(i).id for i in range(options.option_count)]
    options.highlighted = ways.index(answer)
    await pilot.press("enter")
    await pilot.pause(0.1)
    return ways


async def test_c_continues_them_when_the_limit_lifts(fake_home, monkeypatch):
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    write(fake_home, "open", stopped_at("You've hit your session limit · resets 11:59pm"))
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "open", "kind": "interactive", "status": "idle"}])
    opened, closed_tabs, typed = [], [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(handover, "close", lambda session, open_ones, again="": closed_tabs.append(session.session_id))
    monkeypatch.setattr(typein, "type_into", lambda session_id, text: typed.append((session_id, text)) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("open") and app.sessions["open"].live_state)
        assert await choose(pilot, app, "lift") == ["lift", "now"]  # no reading of the plan: waiting comes first
        assert set(app.armed) == {"lim", "open"}
        cell = str(app.mode_cell(app.sessions["lim"]))
        assert cell.startswith("▶ at ") and cell.endswith("23:59")
        assert set(read_json("config.json")["continue_at_reset"]) == {"lim", "open"}  # kept over a restart
        assert await choose(pilot, app, "off") == ["lift", "now", "off"]
        assert not app.armed and read_json("config.json")["continue_at_reset"] == {}
        await choose(pilot, app, "lift")
        app.continue_due()  # not yet: nothing opens
        assert not opened and app.armed
        app.armed = dict.fromkeys(app.armed, datetime.now() - timedelta(minutes=2))  # the limit has lifted
        app.continue_due()
        await wait_for(pilot, lambda: opened and typed)
        assert typed == [("open", continue_prompt(Status.LIMIT))] and not closed_tabs  # in its own tab
        assert [launch.session_id for launch in opened] == ["lim"]
        assert opened[0].mode is Mode.AUTO and opened[0].argv[-1] == continue_prompt(Status.LIMIT)
        assert not app.armed


async def test_a_session_gone_on_by_hand_is_left_alone(fake_home, monkeypatch):
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: "lim" in app.sessions)
        await choose(pilot, app, "lift")
        app.sessions["lim"].status = Status.DONE  # continued by hand meanwhile
        app.sessions["lim"].resets = None
        app.armed["lim"] = datetime.now() - timedelta(minutes=2)
        app.continue_due()
        assert not opened and not app.armed
        assert app.check_action("continue_at_reset", ()) is False  # nothing to continue: not in the footer


async def test_c_continues_them_now_after_signing_in_with_another_account(fake_home, monkeypatch):
    # The limit stands until 23:59 on the account that hit it; signed in with another, the plan has room.
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    write(fake_home, "open", stopped_at("You've hit your session limit · resets 11:59pm"))
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "open", "kind": "interactive", "status": "idle"}])
    opened, closed_tabs, typed = [], [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(handover, "close", lambda session, open_ones, again="": closed_tabs.append(session.session_id))
    monkeypatch.setattr(typein, "type_into", lambda session_id, text: typed.append((session_id, text)) or True)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("open") and app.sessions["open"].live_state)
        app.limits = [Limit("session", 12, None, ""), Limit("week", 40, None, "")]
        assert await choose(pilot, app, "now") == ["now", "lift"]  # room on the plan: now comes first
        await wait_for(pilot, lambda: opened and typed)
        # The open one goes on in its own tab: a prompt is all it needs to carry on, on the new account.
        assert typed == [("open", continue_prompt(Status.LIMIT))] and not closed_tabs
        assert [launch.session_id for launch in opened] == ["lim"]
        assert not app.armed


async def test_an_open_one_that_cannot_be_typed_into_is_closed_and_resumed(fake_home, monkeypatch):
    write(fake_home, "open", stopped_at("You've hit your session limit · resets 11:59pm"))
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "open", "kind": "interactive", "status": "idle"}])
    opened, closed_tabs = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(handover, "close", lambda session, open_ones, again="": closed_tabs.append(session.session_id))
    monkeypatch.setattr(typein, "type_into", lambda session_id, text: False)  # e.g. not on Windows
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("open") and app.sessions["open"].live_state)
        await choose(pilot, app, "now")
        await wait_for(pilot, lambda: opened)
        assert closed_tabs == ["open"]  # closed first, then resumed
        assert opened[0].session_id == "open" and opened[0].argv[-1] == continue_prompt(Status.LIMIT)


async def test_the_limit_dialog_says_how_full_the_plan_is(fake_home, monkeypatch):
    from textual.widgets import Label

    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: "lim" in app.sessions)
        app.limits = [Limit("session", 100, None, ""), Limit("week", 60, None, "")]
        await pilot.press("c")
        await wait_for(pilot, lambda: isinstance(app.screen, LimitDialog))
        assert "Your plan now: session 100% · week 60%" in str(app.screen.query_one(".plan", Label).render())
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, LimitDialog) and not app.armed  # Esc: nothing changes
