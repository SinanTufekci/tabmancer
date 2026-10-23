"""The dashboard at the widths terminals come in: the session table never scrolls sideways."""

from __future__ import annotations

import json

import pytest

from tabmancer import sessions
from tabmancer.app import SessionPicker


def transcript(home, sid, cwd, title):
    folder = home / "projects" / cwd.replace(":", "-").replace("/", "-")
    folder.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "cwd": cwd, "entrypoint": "cli", "message": {"content": "go"}},
               {"type": "assistant", "cwd": cwd, "message": {"content": [{"type": "text", "text": "ok"}],
                                                             "stop_reason": "end_turn"}},
               {"type": "ai-title", "aiTitle": title}]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                                         encoding="utf-8")


@pytest.mark.parametrize("cols", [100, 112, 116, 120, 135, 150, 200])
async def test_the_session_table_never_scrolls_sideways(fake_home, monkeypatch, cols):
    # The widest of everything: a long project name, a long title, a background session that needs you.
    transcript(fake_home, "wide", "C:/work/a-project-with-a-long-name", "A title long enough to need cutting " * 3)
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "wide", "kind": "background", "state": "blocked", "pid": 1},
        {"sessionId": "one", "kind": "interactive", "status": "idle"}])
    app = SessionPicker(days=14)
    async with app.run_test(size=(cols, 40)) as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if app.sessions.get("wide") and app.sessions["wide"].live_state:
                break
        for view in ("list", "live"):
            if view == "live":
                await pilot.press("l")
            await pilot.pause(0.4)
            table = app.table
            columns = sum(column.get_render_width(table) for column in table.columns.values())
            assert columns <= table.size.width, f"{view} at {cols} columns: {columns} > {table.size.width}"
