"""Another model taking over in a session by itself, and the Model column naming the model's version.

Claude Code lets another model answer when the one a session runs on cannot: "Opus 5.5's safeguards
flagged this session. ... Opus 4.8 is answering instead" (seen in a team member, which went on with
Opus 4.8 for an hour unnoticed). The dashboard says so, once, and marks the model until it is back.
"""

import json
from datetime import datetime, timedelta, timezone

from tabmancer import app as app_module
from tabmancer import context, sessions, teamview
from tabmancer.app import SessionPicker
from tabmancer.sessions import Exit, read_session

NOTICE = ("Opus 5.5's safeguards flagged this session. You may be seeing this for the first time: Opus 5.5 is "
          "more capable and has stronger safeguards as a result. Opus 4.8 is answering instead, or you can edit "
          "and retry with Opus 5.5. Send feedback with /feedback.")


def stamp(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def prompt(text: str) -> dict:
    return {"type": "user", "timestamp": stamp(), "cwd": "C:/work/demo", "message": {"role": "user", "content": text}}


def reply(model: str, text: str = "Done.") -> dict:
    return {"type": "assistant", "timestamp": stamp(), "message": {
        "role": "assistant", "model": model, "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 5}}}


def working(model: str) -> dict:
    return {"type": "assistant", "timestamp": stamp(), "message": {
        "role": "assistant", "model": model, "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}}]}}


def notice(text: str = NOTICE, level: str = "notice") -> dict:
    return {"type": "system", "subtype": "informational", "level": level, "timestamp": stamp(), "content": text}


def model_command() -> dict:
    return prompt("<command-name>/model</command-name>\n<command-message>model</command-message>")


def fell(tmp_path, *records) -> str:
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return read_session(path).fell_back


def test_claude_codes_notice_says_which_model_answers_instead(tmp_path):
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), prompt("next"), notice(),
                reply("claude-opus-4-8")) == "Opus 5.5 → Opus 4.8 (safeguards)"


def test_a_weaker_model_mid_turn_is_a_fall_too(tmp_path):
    assert fell(tmp_path, prompt("go"), working("claude-opus-5-5"), reply("claude-sonnet-5-5")) == \
        "Opus 5.5 → Sonnet 5.5"


def test_the_first_model_answering_again_ends_it(tmp_path):
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), notice(), reply("claude-opus-4-8"),
                prompt("again"), reply("claude-opus-5-5")) == ""


def test_your_model_command_ends_it(tmp_path):
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), notice(), reply("claude-opus-4-8"),
                model_command(), prompt("go on"), reply("claude-opus-4-8")) == ""


def test_what_is_no_fall(tmp_path):
    # A new turn on another model: a session resumed with --model, or one where you typed /model.
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), prompt("next"), reply("claude-sonnet-5-5")) == ""
    # A stronger one mid-turn: back to the first, read from a tail that began after the fall.
    assert fell(tmp_path, prompt("go"), working("claude-opus-4-8"), reply("claude-opus-5-5")) == ""
    # A notice of another kind, or one that is not Claude Code's notice level.
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), notice("Conversation compacted")) == ""
    assert fell(tmp_path, prompt("go"), reply("claude-opus-5-5"), notice(NOTICE, level="info")) == ""


def test_the_model_column_names_the_version():
    assert context.short_model("claude-sonnet-5-5") == "sonnet 5.5"
    assert context.short_model("claude-opus-4-8[1m]") == "opus 4.8"
    assert context.short_model("claude-haiku-5") == "haiku 5"
    assert context.short_model("claude-opus-4-20250514") == "opus 4"  # a date is no version
    assert context.short_model("gpt-something-very-long-indeed", 10) == "gpt-somet…"


def open_session(tmp_path, **changes) -> sessions.Session:
    return sessions.Session(session_id="s", path=tmp_path / "s.jsonl", cwd="C:/work/demo", title="t",
                            exit=Exit.RUNNING, live_state="idle", **changes)


async def test_the_dashboard_says_once_that_the_model_fell_back(fake_home, monkeypatch):
    path = fake_home / "projects" / "C--work-demo" / "one.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(reply("claude-opus-5-5")) + "\n")
    monkeypatch.setattr(sessions, "_agents_json",
                        lambda include_finished=False: [{"sessionId": "one", "kind": "interactive", "status": "idle"}])
    monkeypatch.setattr(app_module, "LIVE_REFRESH_SECONDS", 0.1)
    monkeypatch.setattr(app_module, "FULL_POLL_EVERY", 1)
    app = SessionPicker(days=14, live=True)
    said = []
    async with app.run_test(size=(160, 40)) as pilot:
        real = app.notify
        monkeypatch.setattr(app, "notify", lambda text, **kw: said.append((text, kw)) or real(text, **kw))
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions.get("one") and app.sessions["one"].live_state:
                break
        assert str(app.model_cell(app.sessions["one"])) == "opus 5.5"
        with path.open("a", encoding="utf-8") as f:
            for record in (prompt("next"), notice(), reply("claude-opus-4-8")):
                f.write(json.dumps(record) + "\n")
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions["one"].fell_back:
                break
        await pilot.pause(0.5)  # more polls: it is said once
        told = [kw for text, kw in said if "Opus 5.5 → Opus 4.8 (safeguards)" in text]
        assert len(told) == 1 and told[0]["severity"] == "warning"
        cell = app.model_cell(app.sessions["one"])
        assert str(cell).startswith("opus 4.8↓")


def test_a_members_card_shows_the_model_that_answers(tmp_path):
    from tabmancer.picks import Pick

    assert teamview.card_model(None, Pick("sonnet", "high")) == ("sonnet/high", False)  # not open yet
    session = open_session(tmp_path, model="claude-sonnet-5-5")
    assert teamview.card_model(session, Pick("sonnet", "high")) == ("sonnet 5.5/high", False)
    session.model, session.fell_back = "claude-opus-4-8", "Opus 5.5 → Opus 4.8 (safeguards)"
    assert teamview.card_model(session, Pick("opus", "xhigh")) == ("opus 4.8↓/xhigh", True)
    session.exit = Exit.CLEAN  # closed: what it ran on, no alarm
    assert teamview.card_model(session, Pick("opus")) == ("opus 4.8", False)
