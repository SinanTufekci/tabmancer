"""The written report: what Claude is shown, the numbers, the cache, the screen and the command."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tabmancer import report, summarize
from tabmancer import tokens as T
from tabmancer.sessions import Session, Status


def session(sid, cwd, days_ago=0, **extra):
    fields = {"title": f"Work on {sid}", "first_prompt": f"start {sid}", "last_prompt": f"finish {sid}",
              "last_reply": f"Finished {sid}.", "files": ["src/app.py"], "status": Status.DONE,
              "lines_added": 10, "lines_removed": 2}
    fields.update(extra)
    return Session(session_id=sid, path=None, cwd=cwd, last_active=datetime.now() - timedelta(days=days_ago),
                   **fields)


def test_the_digest_groups_the_periods_sessions_by_project():
    first = date.today() - timedelta(days=6)
    sessions = [session("a1", "C:/work/api"), session("a2", "C:/work/api", days_ago=1),
                session("w1", "C:/work/web", days_ago=2), session("old", "C:/work/web", days_ago=30)]
    text = report.digest(sessions, first, date.today(), {"a2": {"summary": "Added rate limits.", "size": 0},
                                                         "w1": {"summary": "Began the page.", "size": 5}})
    assert text.index("# Project api") < text.index("# Project web")  # most sessions first
    assert "start a1" in text and "finish a1" in text and "Finished a1." in text
    assert "summary: Added rate limits." in text and "Finished a2." not in text  # a summary of it as it is wins
    # One written before the latest work (the transcript has grown since): the last reply, and the summary as older.
    assert "Finished w1." in text and "summary, written before the latest work: Began the page." in text
    assert "files: src/app.py" in text and "lines: +10 -2" in text
    assert "old" not in text  # before the period


def reply(message_id, moment, cwd):
    return {"type": "assistant", "cwd": cwd, "timestamp": moment.astimezone(timezone.utc).isoformat(),
            "message": {"id": message_id, "model": "claude-opus-5-5", "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 1000, "output_tokens": 50}}}


@pytest.fixture
def week(tmp_path, monkeypatch):
    """Two sessions with transcripts (for the numbers), and the session list that goes with them."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    folder = tmp_path / "claude" / "projects" / "demo"
    folder.mkdir(parents=True)
    now = datetime.now().replace(microsecond=0)
    for sid, cwd in (("a1", "C:/work/api"), ("w1", "C:/work/web")):
        lines = [reply(f"{sid}-1", now - timedelta(minutes=10), cwd), reply(f"{sid}-2", now - timedelta(minutes=8), cwd)]
        (folder / f"{sid}.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in lines),
                                             encoding="utf-8")
    sessions = {"a1": session("a1", "C:/work/api"), "w1": session("w1", "C:/work/web")}
    return sessions, T.collect(7)


def test_the_numbers_come_from_the_stats(week):
    sessions, stats_report = week
    table = report.numbers(stats_report, sessions)
    assert "| api | 1 | 3m |" in table and "| web | 1 | 3m |" in table
    lines = table.splitlines()
    assert lines[-3].startswith("| **All** | **2** |") and lines[-1].startswith("_Projects worked on side by side")


def test_writing_is_one_call_cached_until_the_sessions_change(week, monkeypatch):
    sessions, stats_report = week
    calls = []

    def ask(prompt, model, timeout=180):
        calls.append(prompt)
        return "A good week.\n\n## api\n- Rate limits done."

    monkeypatch.setattr(summarize, "ask", ask)
    entry = report.write(sessions, stats_report, summaries={})
    assert entry["text"].startswith("# ")  # the model wrote no heading, so the dates are one
    assert "## api\n- Rate limits done." in entry["text"]
    assert "## Numbers" in entry["text"]  # the model left the table out: ours goes in
    assert report.INSTRUCTIONS in calls[0] and "start a1" in calls[0] and "| api | 1 |" in calls[0]
    assert report.write(sessions, stats_report, summaries={}) == entry and len(calls) == 1
    assert report.latest(7)["key"] == entry["key"]
    assert report.key_for(list(sessions.values()), stats_report, {}) == entry["key"]
    sessions["a1"].last_prompt = "one more thing"
    report.write(sessions, stats_report, summaries={})
    assert len(calls) == 2


def test_a_table_the_model_copied_faithfully_is_kept():
    table = ("| Project | Sessions | Time at work | Tokens | Output |\n|---|---:|---:|---:|---:|\n"
             "| api | 1 | 3m | 2.1k | 100 |\n| **All** | **1** | **3m** | **2.1k** | **100** |")
    translated = ("# Hafta\n\n## Sayılar\n\n| Proje | Oturum | Çalışma | Token | Çıktı |\n|---|---|---|---|---|\n"
                  "| api | 1 | 3m | 2.1k | 100 |\n| All | 1 | 3m | 2.1k | 100 |")
    assert report.has_numbers(translated, table)
    assert not report.has_numbers(translated.replace("2.1k", "2.2k"), table)  # a changed number
    assert not report.has_numbers("# Hafta\n\nno table", table)


def test_nothing_to_report_is_an_error(week, monkeypatch):
    _, stats_report = week
    with pytest.raises(summarize.SummaryError):
        report.write({"x": session("x", "C:/work/api", days_ago=30)}, stats_report, summaries={})


def test_the_report_is_saved_as_markdown(tmp_path):
    entry = {"text": "# Work report\n", "first": "2026-10-10", "last": "2026-10-16"}
    path = report.save(entry, folder=tmp_path)
    assert path.name == "tabmancer-report-2026-10-10-to-2026-10-16.md"
    assert path.read_text(encoding="utf-8") == "# Work report\n"


async def test_the_report_tab_writes_saves_and_copies(fake_home, monkeypatch, tmp_path):
    from tabmancer import statsview
    from tabmancer.app import SessionPicker

    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180: "## demo\n- Did the tasks.")
    saved, copied = [], []
    monkeypatch.setattr(report, "save", lambda entry: saved.append(entry) or tmp_path / "r.md")
    monkeypatch.setattr(statsview, "copy_text", lambda app, text: copied.append(text))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(80):
            await pilot.pause(0.05)
            if app.token_report is not None:
                break
        await pilot.press("u")
        screen = app.screen
        await pilot.press("c")  # nothing yet
        assert not copied
        await pilot.press("r")
        assert screen.query_one("TabbedContent").active == "report"
        for _ in range(80):
            await pilot.pause(0.05)
            if saved and not screen.writing:
                break
        assert saved and "- Did the tasks." in saved[0]["text"]
        await pilot.press("c")
        assert copied == [saved[0]["text"]]


def test_report_command(week, monkeypatch, capsys, tmp_path):
    from tabmancer import cli

    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180: "## demo\n- Shipped it.")
    monkeypatch.setattr(cli, "find_sessions", lambda days, quick=False: list(week[0].values()))
    monkeypatch.setattr(report, "save", lambda entry: tmp_path / "r.md")
    cli.main(["report", "--save"])
    captured = capsys.readouterr()
    assert "- Shipped it." in captured.out and "r.md" in captured.err
    assert "last 7 days" in captured.err  # a week unless --days says otherwise
