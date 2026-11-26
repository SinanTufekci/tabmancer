import json

import pytest
import test_agy_picker
from test_agy_picker import BOTH_OUT, TABLE, neon_out

from tabmancer import summarize as Z
from tabmancer.sessions import Session, Status

REAL_ASK = Z.ask  # conftest stubs it for every test; these tests drive it through a fake subprocess
usage = test_agy_picker.usage  # agy's /usage as a test sets it: no agy is run


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

    monkeypatch.setattr(Z.shutil, "which", lambda name: "claude" if name == "claude" else None)  # no agy
    monkeypatch.setattr(Z.subprocess, "run", fake_run)
    entry, saved = Z.summarize(session)
    assert saved and entry["summary"] == "Built a parser. Tests still missing."
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
    monkeypatch.setattr(Z.shutil, "which", lambda name: "claude" if name == "claude" else None)  # no agy
    monkeypatch.setattr(Z.subprocess, "run", lambda *a, **k: Result(1, "", "rate limited"))
    with pytest.raises(Z.SummaryError, match="rate limited"):
        Z.summarize(session)
    assert Z.load_cache() == {}


# --- summaries.json: one process at a time, the others' summaries kept ------------------------------

WRITER = """
import sys
from tabmancer import summarize
for at in range(int(sys.argv[2])):
    assert summarize._store(f"{sys.argv[1]}-{at}", {"summary": sys.argv[1]})
"""


def test_two_processes_saving_at_once_each_keep_the_others_summaries(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    import tabmancer

    env = {**os.environ, "PYTHONPATH": str(Path(tabmancer.__file__).parents[1])}
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, name, "15"], env=env) for name in ("terminal", "window")]
    assert [writer.wait(timeout=60) for writer in writers] == [0, 0]
    saved = Z.load_cache()
    assert len(saved) == 30 and {entry["summary"] for entry in saved.values()} == {"terminal", "window"}
    assert not (Z.config_dir() / f"{Z.FILE}.lock").exists()  # let go of


def test_a_held_lock_keeps_the_summary_in_memory_and_says_it_was_not_saved(session, monkeypatch):
    from tabmancer import config, sessionwords

    monkeypatch.setattr(config, "BUSY_TRIES", 2)  # not a second's wait
    monkeypatch.setattr(Z, "ask", lambda prompt, model, **k: "Built a parser.")
    Z._store("other", {"summary": "kept"})
    with config.locked(Z.FILE):
        summary = Z.summarize(session)
    assert summary.entry["summary"] == "Built a parser." and not summary.saved
    assert Z.load_cache() == {"other": {"summary": "kept"}}  # left as it was
    note = sessionwords.summary_note(summary, None)
    assert note is not None and note.words == Z.UNSAVED and note.severity == "warning"
    assert sessionwords.summary_note(Z.Summary({}, True), None) is None
    failed = sessionwords.summary_note(None, "rate limited")
    assert failed is not None and (failed.words, failed.severity, failed.seconds) == ("Summary failed: rate limited", "error", 8)


def test_an_unreadable_summaries_file_is_never_written_over(session, monkeypatch):
    monkeypatch.setattr(Z, "ask", lambda prompt, model, **k: "Built a parser.")
    path = Z.config_dir() / Z.FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    for kept in ("{cut short", "[1, 2]"):
        path.write_text(kept, encoding="utf-8")
        assert not Z.summarize(session).saved
        assert not Z.forget(["s"])
        assert path.read_text(encoding="utf-8") == kept
    path.unlink()
    assert Z.forget(["nobody"]) and not path.exists()  # nothing to drop: nothing written
    assert Z.summarize(session).saved and Z.forget(["s"]) and Z.load_cache() == {}


# --- on agy: the chain's model, never Argon or a Claude one ------------------------------------------

@pytest.mark.parametrize(("out", "model"), [("", "gemini-4-neon-high"), ("neon", "gemini-4-hydrogen-high")])
def test_on_agy_a_summary_runs_on_the_chain_never_argon_or_claude(session, monkeypatch, usage, out, model):
    from tabmancer import agy, clis

    usage["table"] = TABLE  # Argon at 0%: says nothing of the chain
    assert agy.refresh_quota()  # as saved: a summary reads no /usage of its own
    if out:
        neon_out()
    runs = []
    monkeypatch.setattr(Z.shutil, "which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr(clis, "main_cli", lambda: "agy")
    monkeypatch.setattr(Z, "_run", lambda argv, timeout, cwd: runs.append(argv) or (0, "Built a parser.", ""))
    assert Z.summarize(session).entry["summary"] == "Built a parser."
    (argv,) = runs
    picked = argv[argv.index("--model") + 1]
    assert picked == model and not agy.barred(picked) and "argon" not in picked


def test_on_agy_with_the_chain_out_no_summary_runs(session, monkeypatch, usage):
    from tabmancer import agy, clis

    usage["table"] = BOTH_OUT
    assert agy.refresh_quota()
    runs = []
    monkeypatch.setattr(Z.shutil, "which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr(clis, "main_cli", lambda: "agy")
    monkeypatch.setattr(Z, "_run", lambda argv, timeout, cwd: runs.append(argv) or (0, "x", ""))
    with pytest.raises(Z.SummaryError, match="no agy model now"):
        Z.summarize(session)
    assert runs == [] and Z.load_cache() == {}
