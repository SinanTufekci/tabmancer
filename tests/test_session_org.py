"""Which organisation a session's conversation went to, and what its tool calls read: going on with an
account in another organisation sends it there, so c, a handover and a summary say so (never stop it)."""

from __future__ import annotations

import json
from datetime import datetime

from test_accounts import MINE, MINE_NAME, TEAM_A, claude_in
from test_limits import CWD, stopped_at, utc, wait_for

from tabmancer import sessions
from tabmancer.sessions import _from_cache, _to_cache, read_session

TEAM_B = "org-team-b"


def owner(org: str) -> dict:
    return {"type": "session-owner", "accountUuid": "acc-1", "organizationUuid": org, "sessionId": "s"}


def credential(org: str) -> dict:
    return {"type": "attachment", "isSidechain": False, "cwd": CWD, "timestamp": utc(datetime.now()),
            "attachment": {"type": "credential_org", "organizationUuid": org}}


def prompt(text: str = "do it") -> dict:
    return {"type": "user", "cwd": CWD, "entrypoint": "cli", "timestamp": utc(datetime.now()),
            "message": {"content": text}}


def called(*tools: str) -> dict:
    return {"type": "assistant", "cwd": CWD, "timestamp": utc(datetime.now()), "message": {
        "model": "claude-opus-5-5", "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": {}}
                                                 for i, tool in enumerate(tools)]}}


def transcript(home, sid: str, records: list[dict]):
    folder = home / "projects" / "C--work-demo"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")
    return path


def test_the_latest_requests_organisation_is_the_sessions(fake_home):
    path = transcript(fake_home, "a", [owner(TEAM_A), credential(TEAM_A), prompt(), credential(TEAM_B),
                                       prompt("again"), credential(TEAM_A), prompt("and back")])
    assert read_session(path).org == TEAM_A  # A, then B, then A: where it went last
    path = transcript(fake_home, "b", [owner(TEAM_A), credential(TEAM_A), prompt(), credential(MINE), prompt("on")])
    assert read_session(path).org == MINE
    path = transcript(fake_home, "c", [owner(TEAM_B), prompt()])  # no request noted: where it started
    assert read_session(path).org == TEAM_B
    path = transcript(fake_home, "d", [prompt()])  # from before either: not known
    assert read_session(path).org == ""


def test_a_session_read_on_takes_the_organisation_of_its_new_requests(fake_home):
    path = transcript(fake_home, "a", [owner(TEAM_A), credential(TEAM_A), prompt()])
    assert read_session(path, follow=True).org == TEAM_A
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(credential(MINE)) + "\n" + json.dumps(prompt("on the other")) + "\n")
    assert read_session(path, follow=True).org == MINE
    session = read_session(path, follow=True)
    assert _from_cache(json.loads(json.dumps(_to_cache(session)))).org == MINE  # kept between runs


def test_only_tools_it_called_mark_what_it_read(fake_home):
    offered = {"type": "attachment", "attachment": {"type": "deferred_tools_delta", "addedNames": [
        "mcp__claude_ai_Gmail__search_threads", "mcp__claude-in-chrome__navigate"]}}
    listed = prompt("tools: mcp__claude_ai_Gmail__get_message, mcp__claude_ai_Google_Drive__read_file_content")
    path = transcript(fake_home, "a", [owner(TEAM_A), prompt(), offered, listed,
                                       called("Read", "mcp__claude_ai_Claude_Docs__read")])
    assert read_session(path).sensitive == []  # offered, listed, a doc of its own: nothing read
    path = transcript(fake_home, "b", [prompt(), called("mcp__claude-in-chrome__navigate"), prompt("more"),
                                       called("mcp__claude_ai_Gmail__get_message", "mcp__claude-in-chrome__find"),
                                       called("mcp__claude_ai_Google_Calendar__list_events"),
                                       called("mcp__claude_ai_Trello__get_card")])
    session = read_session(path)
    assert session.sensitive == ["browser", "mail", "calendar", "connector"]
    assert _from_cache(json.loads(json.dumps(_to_cache(session)))).sensitive == session.sensitive
    assert sessions.sensitive_kind("mcp__claude_ai_Google_Drive__read_file_content") == "files"
    assert sessions.sensitive_kind("mcp__kicad__open_board") == ""  # a local server: nothing goes out of it


async def test_c_says_where_a_conversation_from_another_organisation_goes(fake_home):
    from textual.widgets import Label, OptionList

    from tabmancer.app import LimitDialog, SessionPicker

    claude_in(fake_home, org=MINE, kind="claude_max", name=MINE_NAME)  # signed in to the personal plan now
    transcript(fake_home, "lim", [owner(TEAM_A), credential(TEAM_A), prompt(), called("mcp__claude-in-chrome__find"),
                                  stopped_at("You've hit your session limit · resets 11:59pm")])
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        await wait_for(pilot, lambda: "lim" in app.sessions and app.account is not None)
        await pilot.press("c")
        await wait_for(pilot, lambda: isinstance(app.screen, LimitDialog))
        said = str(app.screen.query_one(".away", Label).render())
        assert said == ("One of the ticked ran on another organisation: its conversation goes to your personal "
                        "account if it goes on (browser content in it).")
        ways = app.screen.query_one("#ways", OptionList)
        assert ways.get_option_at_index(ways.highlighted).id == "handover"  # the handover first
        assert "only a digest goes" in str(ways.get_option_at_index(ways.highlighted).prompt)
        await pilot.press("escape")


async def test_c_says_nothing_within_one_organisation_or_when_it_is_not_known(fake_home):
    from textual.widgets import Label, OptionList

    from tabmancer.app import LimitDialog, SessionPicker

    claude_in(fake_home, account="acc-2")  # another seat in the same Team: the same organisation
    transcript(fake_home, "lim", [owner(TEAM_A), credential(TEAM_A), prompt(), called("mcp__claude-in-chrome__find"),
                                  stopped_at("You've hit your session limit · resets 11:59pm")])
    transcript(fake_home, "old", [prompt(), stopped_at("You've hit your session limit · resets 11:59pm")])
    app = SessionPicker(days=14)
    async with app.run_test(size=(160, 50)) as pilot:
        await wait_for(pilot, lambda: {"lim", "old"} <= app.sessions.keys() and app.account is not None)
        await pilot.press("c")
        await wait_for(pilot, lambda: isinstance(app.screen, LimitDialog))
        assert str(app.screen.query_one(".away", Label).render()) == ""
        ways = app.screen.query_one("#ways", OptionList)
        assert ways.get_option_at_index(ways.highlighted).id != "handover"
        assert "only a digest goes" not in str(ways.get_option_at_index(2).prompt)
        await pilot.press("escape")


async def test_a_handover_and_a_summary_say_where_it_goes_and_go_on(fake_home, monkeypatch):
    from tabmancer.app import SessionPicker

    claude_in(fake_home, org=TEAM_B, name="Other Co")
    transcript(fake_home, "a", [owner(TEAM_A), credential(TEAM_A), prompt(), called("mcp__claude_ai_Gmail__get_message")])
    app = SessionPicker(days=14)
    said, handed, summed = [], [], []
    async with app.run_test(size=(160, 50)) as pilot:
        await wait_for(pilot, lambda: "a" in app.sessions and app.account is not None)
        monkeypatch.setattr(app, "notify", lambda text, **_: said.append(text))
        monkeypatch.setattr(app, "hand_over", lambda session, how: handed.append(how))
        monkeypatch.setattr(app, "run_summary", lambda session: summed.append(session.session_id))
        app.start_handover(app.sessions["a"], "transcript")
        assert handed == ["transcript"]  # said, not stopped
        assert said[0] == "demo ran on another organisation: a digest of it goes to the Other Co organisation (mail content in it)."
        app.start_handover(app.sessions["a"], "self")
        assert "its whole conversation goes" in said[2]
        monkeypatch.setattr(app, "current", lambda: app.sessions["a"])
        app.action_summarize()  # s on its row
        assert summed == ["a"] and said[-1].startswith("demo ran on another organisation: a digest of it goes")
        claude_in(fake_home, org=TEAM_A)  # back on the organisation it ran on: nothing to say
        app.account = None
        app.show_accounts()
        said.clear()
        app.tell_elsewhere(app.sessions["a"], "a digest of it")
        assert said == []


def test_a_call_further_back_than_the_tail_is_found_when_asked(fake_home, monkeypatch):
    """A session's own read sees its tail; before its conversation goes elsewhere, the whole
    transcript is looked through for calls, and only calls (an offered tool is listed the same way)."""
    monkeypatch.setattr(sessions, "TAIL_BYTES", 4096)
    monkeypatch.setattr(sessions, "MAX_TAIL_BYTES", 4096)
    filler = [prompt("x" * 500) for _ in range(40)]
    path = transcript(fake_home, "big", [owner(TEAM_A), prompt(), called("mcp__claude_ai_Gmail__get_message"),
                                         *filler, called("Read")])
    assert path.stat().st_size > 4 * 4096
    session = read_session(path)
    assert session.sensitive == []  # its tail had no such call
    assert sessions.sensitive_calls(path) == ["mail"]
    monkeypatch.setattr(sessions, "SCAN_BLOCK", 37)  # a call cut by every block's edge is still found
    sessions._SCANNED.clear()
    assert sessions.sensitive_calls(path) == ["mail"]


def test_a_listed_tool_is_no_call(fake_home):
    listing = {"type": "api-request", "tools": [
        {"name": "mcp__claude-in-chrome__read_page", "defer_loading": True},
        {"name": "mcp__claude_ai_Gmail__search_threads", "defer_loading": True}]}
    path = transcript(fake_home, "a", [owner(TEAM_A), prompt(), listing, called("Read")])
    assert sessions.sensitive_calls(path) == [] and read_session(path).sensitive == []
    assert sessions.sensitive_calls(None) == [] and sessions.sensitive_calls(path.with_name("gone.jsonl")) == []


def test_a_content_item_that_is_no_block_is_passed_over(fake_home):
    odd = called("mcp__claude-in-chrome__find")
    odd["message"]["content"].insert(0, "a bare string")
    path = transcript(fake_home, "a", [prompt(), odd])
    assert read_session(path).sensitive == ["browser"]
