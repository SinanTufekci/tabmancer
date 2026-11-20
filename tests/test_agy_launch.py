"""agy's command lines: never a Claude or GPT model, whatever path the launch takes."""

from __future__ import annotations

import json

import pytest

from tabmancer import agy
from tabmancer.launch import Launch, Mode

ARGON, HYDROGEN, FLASH = agy.CHAIN


@pytest.fixture
def agy_home(tmp_path, monkeypatch):
    """An agy home whose transcripts say which model each conversation ran on."""
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy")

    def ran_on(session_id: str, model: str) -> None:
        path = agy.transcript_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"type": "PLANNER_RESPONSE", "status": "DONE", "model": model, "created_at": "2026-11-20T10:00:00Z"}
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    return ran_on


def model_in(argv: list[str]) -> str | None:
    for index, word in enumerate(argv):
        if word == "--model":
            return argv[index + 1]
        if word.startswith("--model="):
            return word.split("=", 1)[1]
    return None


@pytest.mark.parametrize("mode", [Mode.NEW, Mode.CONTINUE])
@pytest.mark.parametrize("model", ["", "auto", "AUTO", "opus", "claude-opus-5-5-high"])
def test_a_new_agy_session_runs_on_the_chain(mode, model):
    argv = Launch("x", "C:/work", mode, "", cli="agy", model=model).argv
    assert model_in(argv) == ARGON
    assert "auto" not in [w.lower() for w in argv]


def test_a_resume_keeps_the_model_it_ran_on(agy_home):
    agy_home("c1", "gemini-4-neon-high")
    for mode in (Mode.RESUME, Mode.AUTO, Mode.FORK):
        assert model_in(Launch("x", "C:/work", mode, "c1", cli="agy").argv) is None


@pytest.mark.parametrize("ran_on", ["claude-opus-5-5-high", "claude-sonnet-5-5-low", "gpt-oss-120b-medium"])
def test_a_resume_of_a_conversation_that_ran_on_claude_or_gpt_goes_on_on_the_chain(agy_home, ran_on):
    agy_home("c1", ran_on)
    for mode in (Mode.RESUME, Mode.AUTO, Mode.FORK):
        assert model_in(Launch("x", "C:/work", mode, "c1", cli="agy").argv) == ARGON
    flags, _, note = agy.model_flags("", (), "c1")
    assert flags == ["--model", ARGON] and ran_on in note


def test_a_resume_with_no_transcript_goes_on_on_the_chain(agy_home):
    assert model_in(Launch("x", "C:/work", Mode.RESUME, "unknown", cli="agy").argv) == ARGON


@pytest.mark.parametrize("model", ["opus", "auto", "claude-sonnet-5-5-high"])
def test_a_resume_asked_for_claude_or_auto_gets_the_chain(agy_home, model):
    agy_home("c1", "gemini-4-neon-high")
    assert model_in(Launch("x", "C:/work", Mode.RESUME, "c1", cli="agy", model=model).argv) == ARGON


@pytest.mark.parametrize("options", [
    ("--model", "claude-opus-5-5-high"), ("--model=claude-opus-5-5-high",), ("--model", "auto"),
    ("--mode", "auto", "--model", "sonnet"), ("--model=chatgpt-4o",),
])
def test_a_model_in_the_options_is_guarded_too(options):
    argv = Launch("x", "C:/work", Mode.NEW, "", cli="agy", options=options).argv
    assert model_in(argv) == ARGON
    assert sum(w == "--model" or w.startswith("--model=") for w in argv) == 1
    assert not any(agy.barred(w) for w in argv)
    if "--mode" in options:
        assert argv[argv.index("--mode") + 1] == "auto"  # --mode auto is agy's mode, not a model


def test_a_non_claude_model_in_the_options_is_kept():
    argv = Launch("x", "C:/work", Mode.NEW, "", cli="agy", options=("--model", "gemini-4-neon-high")).argv
    assert model_in(argv) == "gemini-4-neon-high"


def test_no_effort_reaches_agy():
    argv = Launch("x", "C:/work", Mode.NEW, "", cli="agy", model="gemini-4-hydrogen-high", effort="max").argv
    assert "--effort" not in argv and model_in(argv) == HYDROGEN


@pytest.mark.parametrize("model", ["chatgpt-4o", "ChatGPT 4o", "gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"])
def test_gpt_by_any_name_is_barred(model):
    assert agy.barred(model)


@pytest.mark.parametrize("model", ["o3", "o4-mini", "gemini-4-neon-high", *agy.CHAIN])
def test_others_are_not_barred(model):
    assert not agy.barred(model)
