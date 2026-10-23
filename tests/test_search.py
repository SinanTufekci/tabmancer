"""The filter finds a session by anything said in it, not only by its title and prompts."""

from __future__ import annotations

import io
import json

from rich.console import Console

from tabmancer import search
from tabmancer.app import SessionPicker
from tabmancer.sessions import read_session

CWD = "C:/work/demo"


def line(record: dict) -> str:
    return json.dumps(record, separators=(",", ":")) + "\n"


def prompt(text: str) -> dict:
    return {"type": "user", "cwd": CWD, "entrypoint": "cli", "message": {"content": text}}


def reply(text: str) -> dict:
    return {"type": "assistant", "cwd": CWD, "message": {"content": [{"type": "text", "text": text}],
                                                         "stop_reason": "end_turn"}}


def tool_output(text: str) -> dict:
    return {"type": "user", "cwd": CWD, "message": {"content": [{"type": "tool_result", "content": text}]}}


async def wait_for(pilot, condition, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


def test_only_what_was_said_is_kept_and_only_what_is_new_is_read(tmp_path):
    path = tmp_path / "s1.jsonl"
    path.write_text(line(prompt("set up the rate limiter")) + line(tool_output("SECRET_FILE_CONTENT"))
                    + line(prompt("<local-command-stdout>LOCAL_OUTPUT</local-command-stdout>"))
                    + line(reply("Done with a token bucket per key.")), encoding="utf-8")
    index = search.Index()
    index.update([read_session(path)])
    assert index.find("token bucket", ["s1"]) == {"s1"}
    assert index.find("secret_file", ["s1"]) == set()  # tool output is not what was said
    assert index.find("local_output", ["s1"]) == set()  # nor what a local command printed
    assert index.find("ra", ["s1"]) == set()  # too short to mean anything
    read = index.entries["s1"]["read"]
    with path.open("a", encoding="utf-8") as f:
        f.write(line(prompt("now add retries")) + '{"type":"assistant","message":{"content":[{"type":"te')
    index.update([read_session(path)])
    assert index.find("retries", ["s1"]) == {"s1"}
    assert index.entries["s1"]["read"] > read  # it went on from where it was...
    assert "token bucket" in index.text("s1") and index.text("s1").count("rate limiter") == 1  # ...not again
    assert index.entries["s1"]["read"] < path.stat().st_size  # a half-written line waits for its end


def test_the_index_is_kept_and_forgets_deleted_sessions(fake_home, tmp_path):
    path = tmp_path / "s2.jsonl"
    path.write_text(line(prompt("hello there")), encoding="utf-8")
    search.Index().update([read_session(path)])
    assert search.Index.load().find("hello", ["s2"]) == {"s2"}  # from search.json
    path.unlink()
    index = search.Index.load()
    index.update([])
    assert "s2" not in index.entries and "s2" not in search.Index.load().entries


def test_a_turkish_capital_i_is_found_and_placed_right():
    assert search.fold("İstanbul") == "istanbul"
    index = search.Index({"s": {"path": "", "read": 0, "text": "İİİ toplantı İstanbul'da yapıldı"}})
    assert index.find("istanbul", ["s"]) == {"s"} and index.find("İSTANBUL", ["s"]) == {"s"}
    piece, start, end = index.snippet("s", "istanbul", width=40)
    assert piece[start:end] == "İstanbul"


async def test_the_filter_finds_words_said_anywhere_and_shows_where(fake_home):
    project = fake_home / "projects" / "C--work-demo"
    (project / "deep.jsonl").write_text(line(prompt("look at the auth flow")) + line(reply("Hmm."))
                                        + line(prompt("and then?"))
                                        + line(reply("The cookie path was wrong, so the session looped."))
                                        + line(prompt("ok thanks")) + line(reply("Anytime.")), encoding="utf-8")
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_for(pilot, lambda: "deep" in app.search.entries)
        await pilot.press("slash", *"cookie path")
        await wait_for(pilot, lambda: [s.session_id for s in app.visible()] == ["deep"])
        app.table.move_cursor(row=0)
        await pilot.pause(0.1)
        console = Console(width=120, file=io.StringIO(), record=True)
        console.print(app.detail.content)
        found = next(row for row in console.export_text().split("\n") if row.startswith("Found"))
        assert "cookie path was wrong" in found
