"""A session writing its own handover (handover.write_itself): an agy one on the chain's model, never
Argon or a Claude model from its transcript, and nothing run with every model out; a Codex one through
Codex's own headless resume, never claude's. Fakes only: no CLI is run."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import test_agy_picker
from test_agy_picker import ARGON, BOTH_OUT, HYDROGEN, NEON, neon_out

from tabmancer import agy, handover
from tabmancer.sessions import Session

WRITE_ITSELF = handover.write_itself  # the real one: conftest keeps tests off claude/agy -p
ANSWER = "<handover>\n## Goal\nShip it.\n</handover>"
usage = test_agy_picker.usage  # agy's /usage as a test sets it: no agy is run


@pytest.fixture
def ran(monkeypatch):
    """Each command write_itself runs, answered by what the test puts in ran['answer']."""
    said = {"argv": [], "kw": [], "answer": subprocess.CompletedProcess([], 0, ANSWER, "")}

    def run(argv, **kw):
        said["argv"].append(argv)
        said["kw"].append(kw)
        answer = said["answer"]
        return answer(argv) if callable(answer) else answer

    monkeypatch.setattr(handover.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(handover.subprocess, "run", run)
    return said


def agy_session(tmp_path, monkeypatch, ran_on: str) -> Session:
    """An agy session whose transcript says it ran on ran_on ("" when it does not say)."""
    monkeypatch.setattr(agy, "_read_transcript", lambda path: {"model": ran_on} if ran_on else {})
    return Session(session_id="agy-1", path=tmp_path / "agy-1.jsonl", cwd=str(tmp_path), cli="agy", model=ran_on)


@pytest.mark.parametrize("ran_on", [ARGON, "claude-opus-5-5", "claude-sonnet-5-5", ""])
def test_an_agy_session_off_the_chain_writes_on_the_chains_model(tmp_path, monkeypatch, usage, ran, ran_on):
    assert WRITE_ITSELF(agy_session(tmp_path, monkeypatch, ran_on)) == "## Goal\nShip it."
    (argv,) = ran["argv"]
    assert argv == ["/bin/agy", "-p", "--conversation", "agy-1", "--model", NEON]
    assert ran["kw"][0]["input"] == handover.REQUEST  # on stdin, never on the command line


def test_the_next_of_the_chain_when_the_first_is_out(tmp_path, monkeypatch, usage, ran):
    neon_out()
    WRITE_ITSELF(agy_session(tmp_path, monkeypatch, ARGON))
    assert ran["argv"][0][-2:] == ["--model", HYDROGEN]


def test_a_chain_model_it_ran_on_is_kept(tmp_path, monkeypatch, usage, ran):
    WRITE_ITSELF(agy_session(tmp_path, monkeypatch, HYDROGEN))
    (argv,) = ran["argv"]
    assert argv == ["/bin/agy", "-p", "--conversation", "agy-1"]  # it goes on with its own: no --model
    assert not any("claude" in word or "argon" in word for word in argv)


def test_every_model_out_runs_nothing_and_says_why(tmp_path, monkeypatch, usage, ran):
    usage["table"] = BOTH_OUT
    assert agy.refresh_quota()  # as saved: the handover reads no /usage of its own
    with pytest.raises(handover.HandoverError, match="no agy model now"):
        WRITE_ITSELF(agy_session(tmp_path, monkeypatch, ARGON))
    assert ran["argv"] == []


def test_a_run_out_of_quota_is_noted_for_the_model_it_ran_on(tmp_path, monkeypatch, usage, ran):
    noted = []
    monkeypatch.setattr(agy, "note_quota_error", lambda text, model, now=None: noted.append((text, model)))
    ran["answer"] = subprocess.CompletedProcess([], 1, "", "RESOURCE_EXHAUSTED: quota. Resets in 4h7m")
    with pytest.raises(handover.HandoverError, match="RESOURCE_EXHAUSTED"):
        WRITE_ITSELF(agy_session(tmp_path, monkeypatch, ARGON))
    assert noted and noted[0][1] == NEON and "RESOURCE_EXHAUSTED" in noted[0][0]
    noted.clear()
    # The model it was kept on is named too, from its transcript.
    with pytest.raises(handover.HandoverError):
        WRITE_ITSELF(agy_session(tmp_path, monkeypatch, HYDROGEN))
    assert noted[0][1] == HYDROGEN


def test_a_claude_session_fails_without_noting_agy_quota(tmp_path, monkeypatch, ran):
    noted = []
    monkeypatch.setattr(agy, "note_quota_error", lambda text, model, now=None: noted.append(model))
    ran["answer"] = subprocess.CompletedProcess([], 1, "", "RESOURCE_EXHAUSTED")
    with pytest.raises(handover.HandoverError):
        WRITE_ITSELF(Session(session_id="c1", path=None, cwd=str(tmp_path), model="claude-opus-5-5"))
    assert noted == [] and ran["argv"][0][:3] == ["/bin/claude", "-p", "--resume"]


def test_a_codex_session_is_resumed_by_codex_never_claude(tmp_path, ran):
    def codex(argv):  # codex exec writes its last message to the -o file, its progress elsewhere
        Path(argv[argv.index("-o") + 1]).write_text(ANSWER, encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "progress and the answer", "")

    ran["answer"] = codex
    session = Session(session_id="019a-thread", path=None, cwd=str(tmp_path), cli="codex", model="gpt-5.5")
    assert WRITE_ITSELF(session) == "## Goal\nShip it."
    (argv,) = ran["argv"]
    assert argv[:3] == ["/bin/codex", "exec", "resume"] and argv[-2:] == ["019a-thread", "-"]  # - : stdin
    assert argv[argv.index("-m") + 1] == "gpt-5.5" and "--skip-git-repo-check" in argv
    assert not any("claude" in word for word in argv)
    assert ran["kw"][0]["input"] == handover.REQUEST


def test_codex_on_stdout_alone_and_codex_missing(tmp_path, monkeypatch, ran):
    ran["answer"] = subprocess.CompletedProcess([], 0, ANSWER, "")  # no -o file written: stdout
    session = Session(session_id="t", path=None, cwd=str(tmp_path), cli="codex")
    assert WRITE_ITSELF(session) == "## Goal\nShip it."
    assert "-m" not in ran["argv"][0]  # no model known: Codex's own
    monkeypatch.setattr(handover.shutil, "which", lambda name: None if name == "codex" else f"/bin/{name}")
    with pytest.raises(handover.HandoverError, match="codex CLI is not on PATH"):
        WRITE_ITSELF(session)
    assert len(ran["argv"]) == 1  # nothing else was run, claude least of all


def test_another_cli_is_refused_rather_than_resumed_by_claude(tmp_path, ran):
    with pytest.raises(handover.HandoverError, match="cannot be resumed"):
        WRITE_ITSELF(Session(session_id="x", path=None, cwd=str(tmp_path), cli="gemini"))
    assert ran["argv"] == []
