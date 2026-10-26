"""What someone sees before they have any sessions, and output that goes into a pipe or a file."""

import io
import json
import os
import sys
import time

from conftest import _records

from tabmancer import cli
from tabmancer.app import SessionPicker


def test_list_says_there_are_no_sessions_yet_and_how_to_see_some(capsys):
    cli.main(["list"])  # conftest points ~/.claude at a folder that does not exist
    said = capsys.readouterr()
    assert said.out == ""  # a script reading the list gets nothing, not the message
    assert "No Claude Code sessions yet" in said.err and "tabmancer --demo" in said.err


def test_list_says_where_else_to_look_when_the_window_is_empty(fake_home, capsys):
    month_ago = time.time() - 30 * 86400
    for transcript in fake_home.glob("projects/*/*.jsonl"):
        os.utime(transcript, (month_ago, month_ago))
    cli.main(["list"])
    said = capsys.readouterr().err
    assert "No sessions in the last" in said and "--days 60" in said and "--all" in said


async def test_the_dashboard_says_what_to_do_with_no_sessions():
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        for _ in range(50):
            await pilot.pause(0.05)
            if "No Claude Code sessions yet" in str(app.detail.render()):
                break
        assert "press n" in str(app.detail.render())
        assert any("No sessions here yet" in saying.text for saying in app.mascot.bubbles)


def test_piped_output_is_utf8_whatever_the_code_page(fake_home, monkeypatch):
    project = fake_home / "projects" / "C--work-demo"
    title = "Türkçe başlık ğışİ ✓ 🚀"
    lines = [json.dumps(r, separators=(",", ":")) for r in _records("merhaba", "tamam", title)]
    (project / "tr.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    piped = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")  # what Windows gives a pipe
    monkeypatch.setattr(sys, "stdout", piped)
    cli.main(["list", "--json"])
    piped.flush()
    rows = json.loads(piped.buffer.getvalue().decode("utf-8"))
    assert title in [row["title"] for row in rows]
