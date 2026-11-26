"""The main CLI (config.json "main_cli") and the CLI that answers tabmancer's own background calls.

Someone with a Google AI plan and no Claude account runs tabmancer on Antigravity alone: n starts agy,
a team member with no @cli runs on agy, and the router, summaries, improve, the team line-up, handovers
and the report are answered by `agy -p` on the chain's model, never a Claude one. Fakes only: no agy
or claude is run.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tabmancer import agy, cli, clis, newsessions, router, summarize, teams
from tabmancer.config import read_json, write_json

REAL_ASK = summarize.ask  # conftest puts a refusal in its place for every other test
AGY = "C:/tools/agy.exe"
CLAUDE = "C:/tools/claude.exe"


def on_path(monkeypatch, *names: str) -> None:
    where = {"agy": AGY, "claude": CLAUDE}
    monkeypatch.setattr(summarize.shutil, "which", lambda name: where[name] if name in names else None)


def signed_in(monkeypatch, yes: bool) -> None:
    from tabmancer import accounts

    for key in summarize.CLAUDE_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(accounts, "claude", lambda: object() if yes else None)


@pytest.fixture
def runs(monkeypatch):
    """Every agy run asked for, answered from the list given (exit code, stdout, stderr)."""
    seen: list[dict] = []
    answers: list[tuple[int | None, str, str]] = []

    def run(argv, timeout, cwd):
        seen.append({"argv": argv, "timeout": timeout, "cwd": cwd})
        return answers.pop(0) if answers else (0, "an answer", "")

    monkeypatch.setattr(summarize, "_run", run)
    monkeypatch.setattr(summarize, "ask", REAL_ASK)
    seen_answers = answers
    return seen, seen_answers


# --- the main CLI -----------------------------------------------------------------------------------

@pytest.mark.parametrize(("saved", "main"), [
    (None, "claude"), ({}, "claude"), ({"main_cli": "agy"}, "agy"), ({"main_cli": "claude"}, "claude"),
    ({"main_cli": "codex"}, "claude"),  # Codex has no background run tabmancer can use
    ({"main_cli": "AGY"}, "claude"), ({"main_cli": ["agy"]}, "claude"), ([], "claude"),
])
def test_the_main_cli_is_claude_unless_agy_is_saved(saved, main):
    if saved is not None:
        write_json("config.json", saved)
    assert clis.main_cli() == main


def test_agy_as_main_is_turned_on_and_the_rest_of_the_config_kept():
    write_json("config.json", {"theme": "dark", "clis": ["claude"]})
    assert clis.set_main("agy") == "agy"
    saved = read_json("config.json")
    assert saved == {"theme": "dark", "clis": ["claude", "agy"], "main_cli": "agy"}
    assert clis.set_main("codex") == "claude"  # not offered: Claude Code
    assert read_json("config.json")["clis"] == ["claude", "agy"]  # going back turns nothing off


def test_an_unreadable_config_is_not_written_over(tmp_path):
    folder = Path(os.environ["TABMANCER_CONFIG_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{ half a file", encoding="utf-8")
    assert clis.set_main("agy") == "claude"
    assert (folder / "config.json").read_text(encoding="utf-8") == "{ half a file"


@pytest.mark.parametrize(("listed", "main", "starts"), [
    (["claude", "agy"], "agy", "agy"),
    (["claude", "agy"], "claude", "claude"),
    (["claude"], "agy", "claude"),  # agy turned off again by hand: what is on
    (["codex", "agy"], "claude", "codex"),  # Claude Code off: the first on, as before
    (["agy"], "agy", "agy"),
])
def test_n_starts_the_main_cli_when_it_is_on(listed, main, starts):
    write_json("config.json", {"clis": listed, "main_cli": main})
    assert newsessions.default_cli() == starts


# --- team members with no @cli ----------------------------------------------------------------------

@pytest.mark.parametrize(("main", "line", "runs_on"), [
    ("claude", "api: endpoints", "claude"),
    ("agy", "api: endpoints", "agy"),
    ("agy", "api: endpoints @sonnet", "claude"),  # a Claude model is Claude Code's, whatever is main
    ("agy", "api: endpoints @claude-opus-4-5 @high", "claude"),
    ("claude", "api: endpoints @gemini-4-hydrogen-high", "agy"),
    ("agy", "api: endpoints @high", "agy"),  # an effort alone names no CLI
    ("agy", "api: endpoints @codex", "codex"),
    ("claude", "api: endpoints @agy", "agy"),
])
def test_a_member_with_no_cli_tag_runs_on_the_main_cli(main, line, runs_on):
    write_json("config.json", {"main_cli": main})
    member, = teams.parse_members(line)
    assert member.cli == runs_on


def test_a_lead_with_no_cli_tag_runs_on_the_main_cli_too():
    write_json("config.json", {"main_cli": "agy"})
    lead, = teams.parse_members("lead: keeps track", lead_ok=True)
    assert lead.lead and lead.cli == "agy"


@pytest.mark.parametrize("written_under", ["claude", "agy"])
@pytest.mark.parametrize("read_under", ["claude", "agy"])
def test_a_members_line_reads_back_to_its_cli_whatever_is_main_by_then(written_under, read_under):
    members = [teams.Member("api", "endpoints", cli="claude"), teams.Member("ui", "screens", cli="agy"),
               teams.Member("ops", "deploys", cli="codex")]
    write_json("config.json", {"main_cli": written_under})
    text = "\n".join(m.line() for m in members)
    write_json("config.json", {"main_cli": read_under})
    # The one case that cannot hold: under Claude Code as main a Claude member's line has no tag (as
    # it had before main_cli), so read under agy it is agy's. Team files keep "cli" as a field of
    # its own; only a line of text written and read across a switch loses it.
    first = "agy" if (written_under, read_under) == ("claude", "agy") else "claude"
    assert [m.cli for m in teams.parse_members(text)] == [first, "agy", "codex"]


def test_with_claude_main_a_claude_member_still_has_no_tag():
    assert teams.Member("api", "endpoints").line() == "api: endpoints"  # as it was before main_cli


# --- which CLI answers the background calls ---------------------------------------------------------

@pytest.mark.parametrize(("main", "found", "signed", "answers"), [
    ("claude", ("claude", "agy"), True, "claude"),
    ("agy", ("claude", "agy"), True, "agy"),
    ("claude", ("agy",), True, "agy"),  # no claude on PATH
    ("claude", ("claude", "agy"), False, "agy"),  # claude there, but signed in to nothing
    ("agy", ("claude",), True, "claude"),  # agy picked but not installed: Claude Code still answers
    ("claude", ("claude",), False, "claude"),  # nothing else to ask: claude says what is wrong
    ("claude", (), True, "claude"),
])
def test_the_backend(monkeypatch, main, found, signed, answers):
    write_json("config.json", {"main_cli": main})
    on_path(monkeypatch, *found)
    signed_in(monkeypatch, signed)
    assert summarize.backend() == answers


@pytest.mark.parametrize("key", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_a_key_in_the_environment_is_signed_in(monkeypatch, key):
    on_path(monkeypatch, "claude", "agy")
    signed_in(monkeypatch, False)
    monkeypatch.setenv(key, "1")
    assert summarize.backend() == "claude"


def test_a_key_claude_code_keeps_is_signed_in(monkeypatch, tmp_path):
    on_path(monkeypatch, "claude", "agy")
    signed_in(monkeypatch, False)
    from tabmancer import accounts

    folder = Path(os.environ["CLAUDE_CONFIG_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".claude.json").write_text('{"primaryApiKey": "sk-test"}', encoding="utf-8")
    accounts._read_before.clear()
    assert summarize.backend() == "claude"


# --- the agy call -----------------------------------------------------------------------------------

def agy_main(monkeypatch) -> None:
    write_json("config.json", {"main_cli": "agy"})
    on_path(monkeypatch, "claude", "agy")
    signed_in(monkeypatch, True)


def test_agy_answers_on_the_chains_model_from_its_own_folder(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, _ = runs
    assert summarize.ask("Summarise this.", "haiku", timeout=90, effort="low") == "an answer"
    call, = seen
    assert call["argv"] == [AGY, "--model", "gemini-4-neon-high", "--disable-slash-commands",
                            "--print-timeout", "90s", "-p", "Summarise this."]
    assert Path(call["cwd"]) == agy.background_dir() and agy.background_dir().is_dir()
    assert call["timeout"] > 90  # agy stops the turn itself first
    assert not any(word in " ".join(call["argv"]) for word in ("haiku", "low", "claude"))


def test_a_short_timeout_still_gives_agy_time_for_a_call(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, _ = runs
    summarize.ask("x", "", timeout=5)
    assert seen[0]["argv"][seen[0]["argv"].index("--print-timeout") + 1] == f"{summarize.AGY_MIN_WAIT}s"


def test_the_chain_in_the_config_is_followed(monkeypatch, runs):
    agy_main(monkeypatch)
    write_json("config.json", {"main_cli": "agy", "agy_models": ["claude-opus-4-5", "gemini-4-hydrogen-high"]})
    seen, _ = runs
    summarize.ask("x", "opus")
    assert seen[0]["argv"][2] == "gemini-4-hydrogen-high"  # never Claude through agy, even listed


def test_with_the_chain_out_of_quota_nothing_runs_and_it_says_until_when(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, _ = runs
    back = datetime.now(timezone.utc) + timedelta(hours=3)
    for model in agy.chain():
        agy.note_out_of_quota(model, back)
    with pytest.raises(summarize.SummaryError) as said:
        summarize.ask("x", "haiku")
    assert not seen
    text = str(said.value)
    assert "no agy model now" in text and "Neon" in text and "Hydrogen" in text
    assert back.astimezone().strftime("%H:%M") in text


def test_a_run_out_of_quota_is_noted_and_the_next_goes_to_the_next_model(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, answers = runs
    answers.append((1, "", "RESOURCE_EXHAUSTED (429): Individual quota reached. Resets in 2h10m"))
    with pytest.raises(summarize.SummaryError) as said:
        summarize.ask("x", "")
    assert "Neon (High) is out of quota until" in str(said.value)
    summarize.ask("x", "")
    assert [call["argv"][2] for call in seen] == ["gemini-4-neon-high", "gemini-4-hydrogen-high"]


def test_other_failures_are_said_and_not_noted(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, answers = runs
    answers += [(1, "", "503 overloaded, try again"), (summarize.PRINT_TIMEOUT_EXIT, "half an ans", ""),
                (None, "", ""), (0, "  ", "")]
    for words in ("503 overloaded", "ran out of time", "gave no answer", "returned nothing"):
        with pytest.raises(summarize.SummaryError, match=words):
            summarize.ask("x", "")
    assert {call["argv"][2] for call in seen} == {"gemini-4-neon-high"}


def test_no_agy_on_path_is_said(monkeypatch, runs):
    seen, _ = runs
    monkeypatch.setattr(summarize.shutil, "which", lambda name: None)
    with pytest.raises(summarize.SummaryError, match="agy is not on PATH"):
        summarize._ask_agy("x", 30)
    assert not seen


def test_a_prompt_too_long_for_the_command_line_loses_its_middle():
    prompt = "INSTRUCTIONS " + "x" * 50_000 + " NEWEST"
    fitted = summarize.fit_argument(prompt)
    assert len(fitted) == summarize.AGY_MAX_ARG
    assert fitted.startswith("INSTRUCTIONS ") and fitted.endswith(" NEWEST") and summarize.CUT in fitted
    assert summarize.fit_argument("short") == "short"


def test_a_real_process_is_started_without_a_window_and_with_no_stdin(monkeypatch):
    started = {}

    class Done:
        returncode, pid = 0, 4242

        def communicate(self, timeout=None):
            return "answer", ""

    def popen(argv, **kw):
        started.update(kw, argv=argv)
        return Done()

    monkeypatch.setattr(summarize.subprocess, "Popen", popen)
    assert summarize._run(["agy", "-p", "x"], 30, "here") == (0, "answer", "")
    assert started["stdin"] is subprocess.DEVNULL and started["cwd"] == "here"


def test_a_run_past_its_time_is_killed_with_its_children(monkeypatch):
    killed = []

    class Slow:
        returncode, pid = None, 4242
        calls = 0

        def communicate(self, timeout=None):
            Slow.calls += 1
            raise subprocess.TimeoutExpired("agy", timeout)

        def kill(self):
            killed.append("kill")

    monkeypatch.setattr(summarize.subprocess, "Popen", lambda argv, **kw: Slow())
    monkeypatch.setattr(summarize.subprocess, "run", lambda argv, **kw: killed.append(argv))
    assert summarize._run(["agy"], 1, "here")[0] is None
    assert killed == ([["taskkill", "/T", "/F", "/PID", "4242"]] if os.name == "nt" else ["kill"])


def test_claude_still_answers_with_its_model_when_it_is_main(monkeypatch):
    write_json("config.json", {"main_cli": "claude"})
    on_path(monkeypatch, "claude", "agy")
    signed_in(monkeypatch, True)
    monkeypatch.setattr(summarize, "ask", REAL_ASK)
    seen = {}

    def claude_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "fine", "")

    monkeypatch.setattr(summarize.subprocess, "run", claude_run)
    assert summarize.ask("x", "haiku", effort="low") == "fine"
    assert seen["argv"][:5] == [CLAUDE, "-p", "--model", "haiku", "--effort"]


# --- the router on agy ------------------------------------------------------------------------------

def test_for_a_claude_session_the_router_still_picks_a_claude_model_when_agy_answers(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, answers = runs
    answers.append((0, "Pick: sonnet high 300k\nWhy: a feature across a few files", ""))
    route = router.suggest("add a settings page with a form and its tests", ["shop"], cli="claude")
    assert not route.guessed and route.cli == "claude" and route.pick.model == "sonnet"
    asked = seen[0]["argv"][-1]
    assert "opus" in asked and "sonnet" in asked  # the same prompt as Claude gets: Claude's models
    assert seen[0]["argv"][2] == "gemini-4-neon-high"


def test_the_router_says_when_the_chain_is_out_and_guesses(monkeypatch, runs):
    agy_main(monkeypatch)
    seen, _ = runs
    for model in agy.chain():
        agy.note_out_of_quota(model, datetime.now(timezone.utc) + timedelta(hours=1))
    route = router.suggest("add a settings page with a form and its tests", ["shop"], cli="claude")
    assert route.guessed and "no agy model now" in route.why and not seen


# --- tabmancer's own agy calls are not your conversations ---------------------------------------------

def agy_index(home: Path, rows: list[tuple]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / agy.INDEX)
    con.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, "
                "step_count integer, last_modified_time datetime, workspace_uris text, status text, "
                "nesting_depth integer, not_fully_idle numeric, killed numeric)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_the_background_folder_is_left_out_of_the_list(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path / "agy")
    monkeypatch.setattr(agy.tempfile, "gettempdir", lambda: str(tmp_path / "Temp"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.1234567+00:00")
    background = (tmp_path / "Temp" / agy.BACKGROUND_DIR).as_uri()
    agy_index(tmp_path / "agy", [
        ("mine", "Bolt analysis", "", 4, now, '["file:///C:/work/parts"]', "", 0, 0, 0),
        ("summary", "Summarise", "", 2, now, f'["{background}"]', "", 0, 0, 0),
        ("upper", "Summarise", "", 2, now, f'["{background.upper().replace("FILE:", "file:")}"]', "", 0, 0, 0),
        ("inside", "Below it", "", 2, now, f'["{background}/sub"]', "", 0, 0, 0),
    ])
    found = {s.session_id for s in agy.find(14)}
    assert "mine" in found and "summary" not in found and "inside" in found
    assert ("upper" in found) == (os.name != "nt")  # Windows folders match in any case


# --- step_state: what an agy conversation is doing ------------------------------------------------------

SID = "6a2b4b6c-cf4c-4a60-a0ac-a1fa413c7d35"


def conversation(home: Path, statuses: list[int]) -> None:
    (home / "conversations").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "conversations" / f"{SID}.db")
    con.execute("CREATE TABLE steps (idx integer, step_type integer NOT NULL DEFAULT 0, "
                "status integer NOT NULL DEFAULT 0, step_payload blob, PRIMARY KEY (idx))")
    con.executemany("INSERT INTO steps (idx, step_type, status) VALUES (?, 132, ?)", list(enumerate(statuses)))
    con.commit()
    con.close()


@pytest.mark.parametrize(("statuses", "state"), [
    ([3, 9, 1], "permission"),
    ([3, 2], "busy"),  # a tool or command running
    ([3, 8], "busy"),  # the model writing
    ([3, 2, 10, 1], "busy"),  # queued and pending behind it are not reached
    ([3, 3], ""), ([3, 7], ""), ([3, 6], ""), ([3, 5], ""), ([], ""),
])
def test_step_state(tmp_path, monkeypatch, statuses, state):
    monkeypatch.setattr(agy, "home", lambda: tmp_path)
    conversation(tmp_path, statuses)
    assert agy.step_state(SID) == state
    assert agy.waiting_for_you(SID) is (state == "permission")


def test_step_state_of_nothing_readable_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "home", lambda: tmp_path)
    assert agy.step_state(SID) == "" and agy.step_state("agy:worker") == "" and agy.step_state("") == ""
    (tmp_path / "conversations").mkdir()
    (tmp_path / "conversations" / f"{SID}.db").write_text("not a database", encoding="utf-8")
    assert agy.step_state(SID) == ""


# --- tabmancer clis main ------------------------------------------------------------------------------

def test_clis_main_sets_and_says_the_cost(capsys):
    cli.manage_clis("main", ["agy"])
    out = capsys.readouterr().out
    assert clis.main_cli() == "agy" and "Main CLI: Antigravity" in out and "Gemini quota" in out
    assert "agy" in read_json("config.json")["clis"]


@pytest.mark.parametrize("names", [["codex"], [], ["claude", "agy"], ["gemini"]])
def test_clis_main_takes_one_of_claude_or_agy(names):
    with pytest.raises(SystemExit):
        cli.manage_clis("main", names)
    assert clis.main_cli() == "claude"


# --- the , dialog -------------------------------------------------------------------------------------

async def test_m_in_the_cli_dialog_picks_the_main_cli_and_enter_saves_it():
    from textual.app import App

    from tabmancer.clisettings import CliSettings

    got = []

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(CliSettings(), got.append)

    app = Host()
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, CliSettings) and "Main CLI: Claude Code" in str(dialog.main_words())
        await pilot.press("m")
        words = str(dialog.main_words())
        assert "Main CLI: Antigravity" in words and "Gemini quota" in words and "never Claude" in words
        assert clis.main_cli() == "claude"  # nothing saved before Enter
        await pilot.press("enter")
        await pilot.pause()
    assert [saved.after for saved in got] == [["claude", "agy"]]  # agy as main is turned on too (clis.Saved)
    assert clis.main_cli() == "agy"


async def test_esc_in_the_cli_dialog_keeps_the_main_cli():
    from textual.app import App

    from tabmancer.clisettings import CliSettings

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(CliSettings())

    app = Host()
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        await pilot.press("m", "escape")
        await pilot.pause()
    assert clis.main_cli() == "claude" and clis.enabled() == ["claude"]
