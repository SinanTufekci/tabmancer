"""Hiding sessions (or a whole project) without deleting them, and pinning them above the rest."""

from __future__ import annotations

import json

from tabmancer import marks, sessions
from tabmancer.app import HEADING, SessionPicker
from tabmancer.config import read_json


def write(home, folder: str, sid: str, title: str) -> None:
    cwd = f"C:/work/{folder}"
    project = home / "projects" / f"C--work-{folder}"
    project.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "cwd": cwd, "entrypoint": "cli", "message": {"content": f"do {sid}"}},
               {"type": "assistant", "cwd": cwd, "message": {"content": [{"type": "text", "text": "ok"}],
                                                             "stop_reason": "end_turn"}},
               {"type": "ai-title", "aiTitle": title}]
    (project / f"{sid}.jsonl").write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                                          encoding="utf-8")


async def wait_for(pilot, condition, tries=60):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


def keys(app) -> list[str]:
    return [row.key.value for row in app.table.ordered_rows]


async def cursor_on(pilot, app, key: str) -> None:
    """The cursor on a session's row, and still there when the key goes: a poll landing on a busy
    machine may make the rows again in between."""
    for _ in range(40):
        app.table.move_cursor(row=keys(app).index(key))
        await pilot.pause(0.05)
        if app.cursor_key() == key:
            return
    raise AssertionError(f"the cursor never stayed on {key}")


async def test_x_hides_a_session_and_x_on_it_brings_it_back(fake_home):
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions)
        await cursor_on(pilot, app, "one")
        await pilot.press("x")
        assert keys(app) == ["two"] and "1 hidden (X shows them)" in app.sub_title
        assert read_json(marks.FILE)["hidden"] == ["one"]
        assert marks.Marks.load().hidden == {"one"}  # kept over a restart
        await pilot.press("X")  # shown again, marked as hidden
        assert "one" in keys(app) and str(app.table.get_cell("one", "title")).startswith("hidden · ")
        assert "showing 1 hidden" in app.sub_title
        await cursor_on(pilot, app, "one")
        await pilot.press("x")  # back in sight
        assert not app.marks.hidden
        await pilot.press("X")
        assert sorted(keys(app)) == ["one", "two"] and "hidden" not in app.sub_title


async def test_x_hides_every_ticked_session_but_never_an_open_one(fake_home, monkeypatch):
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [
        {"sessionId": "two", "kind": "interactive", "status": "idle"}])
    write(fake_home, "demo", "three", "Third task")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions.get("two") and app.sessions["two"].live_state)
        app.selected = {"one", "three"}
        await pilot.press("x")
        assert app.marks.hidden == {"one", "three"} and not app.selected
        assert [k for k in keys(app) if not k.startswith(HEADING)] == ["two"]
        await cursor_on(pilot, app, "two")
        await pilot.press("x")  # open: it stays
        assert "two" not in app.marks.hidden and "two" in keys(app)
        app.marks.hidden.add("two")  # hidden while it was closed, open again now: it shows
        app.refresh_rows()
        assert "two" in keys(app)


async def test_a_hidden_project_drops_out_of_the_list_and_the_counts(fake_home):
    write(fake_home, "cctest", "t1", "Test one")
    write(fake_home, "cctest", "t2", "Test two")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: len(app.sessions) == 4)
        projects = app.projects
        index = next(i for i in range(projects.option_count) if "cctest" in projects.get_option_at_index(i).id)
        key = projects.get_option_at_index(index).id
        projects.focus()
        projects.highlighted = index
        await pilot.pause(0.1)
        await pilot.press("x")
        assert app.marks.projects == {key}
        await pilot.pause(0.1)
        assert sorted(s.session_id for s in app.visible()) == ["one", "two"]
        assert "2 hidden" in app.sub_title and "2 sessions" in app.sub_title
        ids = [projects.get_option_at_index(i).id for i in range(projects.option_count)]
        assert not any("cctest" in i for i in ids)  # the sidebar leaves it out too


async def test_f_pins_a_session_above_the_rest(fake_home):
    write(fake_home, "demo", "three", "Third task")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: len(app.sessions) == 3)
        await cursor_on(pilot, app, "one")
        await pilot.press("f")
        assert keys(app)[:3] == [f"{HEADING}pinned", "one", f"{HEADING}rest"]
        assert str(app.table.get_cell(f"{HEADING}pinned", "project")) == "\n★ PINNED · 1"
        assert str(app.table.get_cell("one", "title")).startswith("★ ")
        assert marks.Marks.load().pinned == {"one"}
        await cursor_on(pilot, app, "one")
        await pilot.press("f")  # again: unpinned, no headings for one group
        assert not any(k.startswith(HEADING) for k in keys(app))


async def test_hidden_sessions_are_not_ticked_for_reopening(fake_home, monkeypatch):
    from tabmancer import app as app_module

    m = marks.Marks(hidden={"one"})
    m.save()
    monkeypatch.setattr(app_module, "open_at_shutdown", lambda sessions, shutdowns: {"one", "two"})
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: app.sessions)
        assert app.selected == {"two"} and app.preselected == {"two"}
