import json

import pytest

from tabmancer import summarize as Z
from tabmancer.sessions import Session, Status

REAL_ASK = Z.ask  # conftest stubs it for every test; these tests drive it through a fake subprocess


@pytest.fixture(autouse=True)
def real_ask(monkeypatch):
    monkeypatch.setattr(Z, "ask", REAL_ASK)


def write_transcript(tmp_path, records):
    path = tmp_path / "s.jsonl"
    lines = [json.dumps({"parentUuid": None, **r}, separators=(",", ":")) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def user(text, **extra):
    return {"type": "user", "message": {"content": text}, **extra}


def reply(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


@pytest.fixture
def session(tmp_path):
    path = write_transcript(tmp_path, [
        user("build the parser"), reply("one"), user("subagent work", isSidechain=True),
        user([{"type": "tool_result", "content": "x"}]), reply("two"), user("now add tests"),
        reply("three"), reply("four"),
    ])
    s = Session(session_id="s", path=path, cwd="C:/work/demo", title="Parser", size=path.stat().st_size)
    s.status, s.files = Status.DONE, ["parser.py"]
    return s


def test_digest_keeps_prompts_last_replies_and_files(session):
    text = Z.digest(session)
    assert "- build the parser" in text and "- now add tests" in text
    assert "subagent work" not in text and "tool_result" not in text
    assert "- two" in text and "- four" in text and "- one" not in text  # last three replies
    assert "parser.py" in text


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_summarize_runs_claude_without_tools_and_caches(session, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result(stdout="  Built a parser.\n Tests still missing. ")

    monkeypatch.setattr(Z.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(Z.subprocess, "run", fake_run)
    entry = Z.summarize(session)
    assert entry["summary"] == "Built a parser. Tests still missing."
    assert entry["size"] == session.size
    (argv, kwargs), = calls
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--no-session-persistence" in argv
    assert "build the parser" in kwargs["input"]
    assert Z.load_cache()["s"]["summary"] == entry["summary"]


def test_summarize_reports_failures(session, monkeypatch):
    monkeypatch.setattr(Z.shutil, "which", lambda name: None)
    with pytest.raises(Z.SummaryError, match="not on PATH"):
        Z.summarize(session)
    monkeypatch.setattr(Z.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(Z.subprocess, "run", lambda *a, **k: Result(1, "", "rate limited"))
    with pytest.raises(Z.SummaryError, match="rate limited"):
        Z.summarize(session)
    assert Z.load_cache() == {}
