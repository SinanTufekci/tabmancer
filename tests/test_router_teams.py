"""The team line-up Claude suggests knows what agy can run now: the model its chain gives from the
quota saved last, or that it has none, so no member is put on agy only to stand there. Read from
agy's saved quota only: no agy is run and nothing is read afresh."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tabmancer import agy, teams
from tabmancer.config import write_json

NEON, HYDROGEN = agy.DEFAULT_CHAIN


@pytest.fixture
def with_agy(monkeypatch):
    """A machine with agy, whose /usage is never run: the prompt reads the saved quota only."""
    monkeypatch.setattr(agy, "installed", lambda: True)
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: pytest.fail("the line-up prompt ran agy's /usage"))
    monkeypatch.setattr(agy, "refresh_quota", lambda *a, **k: pytest.fail("the line-up prompt read agy's quota"))


def soon(hours: float) -> datetime:
    return (datetime.now().astimezone() + timedelta(hours=hours)).replace(microsecond=0)


def bullet(folder, goal: str = "a shop") -> str:
    """The prompt's bullet on agy, for a project of its own (the repo's files name agy.py)."""
    prompt = teams.suggestion_prompt(goal, str(folder))
    return next(line for line in prompt.splitlines() if line.startswith("- ") and "agy" in line)


def test_the_line_up_names_the_model_agy_runs_now(with_agy, tmp_path):
    said = bullet(tmp_path)
    assert said.startswith(f"- {teams.AGY_ASKED}") and "its members run Neon now" in said
    agy.note_out_of_quota(NEON, resets=soon(3))
    said = bullet(tmp_path)
    assert "its members run Hydrogen now" in said and "Neon" not in said  # never a model that is out


def test_with_agy_out_of_quota_the_line_up_puts_no_one_on_agy(with_agy, tmp_path):
    back = soon(3)
    agy.note_out_of_quota(NEON, resets=back)
    agy.note_out_of_quota(HYDROGEN, resets=soon(4))
    said = bullet(tmp_path, "mix claude and agy for a shop")
    assert said == (f"- agy has no model now (Neon and Hydrogen out until {back:%b %d %H:%M}): put no @agy "
                    "member, even if the goal asks for agy, and say so in the size line's why; leave the CLI "
                    "out (Claude Code).")
    assert agy.PICK_OR_WAIT not in said  # the dialog's words, not Claude's


def test_without_agy_the_line_up_puts_no_one_on_agy(monkeypatch, tmp_path):
    monkeypatch.setattr(agy, "installed", lambda: False)
    assert bullet(tmp_path) == (
        "- agy (Antigravity) is not installed here: put no @agy member; leave the CLI out (Claude Code).")


def test_the_line_up_never_suggests_a_claude_or_gpt_model_on_agy(with_agy, tmp_path):
    write_json("config.json", {agy.CHAIN_SETTING: ["claude-opus-5-5", "gpt-oss-120b-medium", HYDROGEN]})
    said = bullet(tmp_path)
    assert "its members run Hydrogen now" in said and "never a Claude or GPT model with @agy" in said
    named = said.split("its members run ", 1)[1].split(" now", 1)[0]
    assert named == "Hydrogen" and not agy.barred(named)
