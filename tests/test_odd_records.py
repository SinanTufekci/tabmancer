"""Claude Code's transcripts are its own and change between releases: a record of a shape this
version does not expect costs at most that record, never the session, the list or the dashboard."""

import json

import pytest

from tabmancer import doctor, safework, sessions, tokens
from tabmancer.app import SessionPicker
from tabmancer.config import config_dir

PROMPT = {"type": "user", "cwd": "C:/work/odd", "entrypoint": "cli", "message": {"content": "do it"}}
REPLY = {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}
ODD = {
    "message-is-text": {"type": "assistant", "message": "a plain string"},
    "content-is-a-dict": {"type": "assistant", "message": {"content": {"type": "text", "text": "x"}}},
    "usage-is-a-list": {"type": "assistant", "message": {"id": "m1", "content": [], "usage": [1, 2]}},
    "timestamp-is-a-number": {"type": "assistant", "timestamp": 12345, "message": {"content": [{"type": "text", "text": "ok"}]}},
    "tool-input-is-text": {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": "f.py"}]}},
    "text-is-a-number": {"type": "assistant", "message": {"content": [{"type": "text", "text": 7}]}},
    "title-is-a-number": {"type": "ai-title", "aiTitle": 42},
    "cwd-is-a-number": {"type": "user", "cwd": 7, "message": {"content": "again"}},
    "origin-is-text": {"type": "user", "origin": "peer", "message": {"content": "hi"}},
    "record-is-a-list": [1, 2, 3],
}


def transcript(home, name, *records):
    project = home / "projects" / "C--work-odd"
    project.mkdir(parents=True, exist_ok=True)
    (project / f"{name}.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
                                          encoding="utf-8")


@pytest.mark.parametrize("name", ODD)
def test_an_odd_record_costs_only_itself(fake_home, name):
    transcript(fake_home, name, PROMPT, REPLY, ODD[name])
    found = {s.session_id: s for s in sessions.find_sessions(14, quick=True)}
    assert {"one", "two", name} <= set(found)  # the others, and this one too
    assert found[name].first_prompt == "do it"
    tokens.collect(14)  # the token count reads it too


def test_sanitize_drops_what_has_the_wrong_type_and_keeps_the_rest():
    record = sessions.sanitize({"type": "assistant", "cwd": 7, "timestamp": "2026-10-26T10:00:00Z", "message": {
        "model": 5, "usage": [1], "content": [{"type": "tool_use", "name": "Edit", "input": "x"}, "junk",
                                              {"type": "text", "text": "kept"}]}})
    assert "cwd" not in record and record["timestamp"] == "2026-10-26T10:00:00Z"
    message = record["message"]
    assert "model" not in message and "usage" not in message
    assert message["content"] == [{"type": "tool_use", "name": "Edit"}, {"type": "text", "text": "kept"}]


def test_a_transcript_that_cannot_be_read_at_all_is_left_out_and_counted(fake_home, monkeypatch):
    sessions.UNREADABLE.clear()
    transcript(fake_home, "broken", PROMPT, REPLY)
    tail = sessions._read_tail

    def fails_on_broken(f, size, session):
        if session.session_id == "broken":
            raise TypeError("a shape nobody expected")
        return tail(f, size, session)

    monkeypatch.setattr(sessions, "_read_tail", fails_on_broken)
    found = {s.session_id for s in sessions.find_sessions(14, quick=True)}
    assert found == {"one", "two"}
    assert sum(sessions.UNREADABLE.values()) == 1
    results = doctor._transcript_checks(set())
    assert any(level == "fail" and "could not be read (TypeError: a shape nobody expected)" in message
               for level, message in results)


async def test_the_dashboard_opens_over_odd_records_and_says_what_it_left_out(fake_home, monkeypatch):
    for name, odd in ODD.items():
        transcript(fake_home, name, PROMPT, REPLY, odd)
    sessions.UNREADABLE.clear()
    sessions.UNREADABLE["TypeError: made up"] += 2  # as if two could not be read
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        for _ in range(200):
            await pilot.pause(0.05)
            # notify() only posts a message: the note is in the list a moment after the sessions are
            if len(app.sessions) == 2 + len(ODD) and any("could not be read" in note.message
                                                         for note in app._notifications):
                break
        assert app.is_running and len(app.sessions) == 2 + len(ODD), sorted(app.sessions)
        notes = [note.message for note in app._notifications]
        assert any("2 transcripts could not be read" in note for note in notes), notes
    sessions.UNREADABLE.clear()


async def test_an_error_in_the_background_is_told_and_logged_and_the_dashboard_stays(fake_home, monkeypatch):
    def broken(days):
        raise ValueError("the token count fell over")

    monkeypatch.setattr(tokens, "collect", broken)
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        app.refresh_tokens()
        for _ in range(60):
            await pilot.pause(0.05)
            if any("went wrong" in note.message for note in app._notifications):
                break
        assert app.is_running
        told = [note.message for note in app._notifications if "went wrong" in note.message]
        assert told and "the token count fell over" in told[0] and safework.LOG in told[0]
    log = (config_dir() / safework.LOG).read_text(encoding="utf-8")
    assert "refresh_tokens" in log and "ValueError: the token count fell over" in log
