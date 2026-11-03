"""Model and effort: picked where sessions start, kept for the resumes Claude Code would lose them in."""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import box_has_focus

from tabmancer import app as app_module
from tabmancer import handover, newsessions, picks, teams
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.newsessions import NewSessions
from tabmancer.picks import Pick
from tabmancer.sessions import Session


@pytest.mark.parametrize(("line", "text", "pick"), [
    ("api: endpoints @sonnet @high", "api: endpoints", Pick("sonnet", "high")),
    ("api: endpoints @high @sonnet", "api: endpoints", Pick("sonnet", "high")),  # either order
    ("api: endpoints @XHigh", "api: endpoints", Pick("", "xhigh")),  # an effort word is never a model
    ("api: endpoints @claude-opus-5-5", "api: endpoints", Pick("claude-opus-5-5", "")),
    ("mail me@home ok", "mail me@home ok", Pick()),  # only words at the end, after a space
    ("lead", "lead", Pick()),
])
def test_the_at_words_at_a_lines_end_are_its_model_and_effort(line, text, pick):
    assert picks.split(line) == (text, pick)


def test_a_pick_says_itself_as_flags_and_words():
    assert Pick("sonnet", "high").flags() == ("--model", "sonnet", "--effort", "high")
    assert Pick("", "low").flags() == ("--effort", "low") and Pick().flags() == ()
    assert Pick("sonnet", "high").words() == "sonnet · high" and Pick("", "max").words() == "max effort"
    assert Pick("sonnet", "high").short() == "sonnet/high" and not Pick()
    assert picks.tags(Pick("opus", "xhigh")) == " @opus @xhigh"


@pytest.mark.parametrize(("model", "name"), [
    ("claude-opus-5-5", "Opus 5.5"), ("claude-haiku-5-5", "Haiku 5.5"), ("claude-fable-5-1", "Fable 5.1"),
    ("claude-sonnet-4-5-20250929", "Sonnet 4.5"), ("claude-opus-4-7[1m]", "Opus 4.7"),
    ("<synthetic>", ""), ("", ""), ("sonnet", "sonnet"),
])
def test_model_names_as_people_say_them(model, name):
    assert picks.model_name(model) == name


def test_a_fresh_session_gets_its_id_from_tabmancer_and_its_effort_back_on_resume():
    plain = Launch(title="api: new", cwd="C:/api", mode=Mode.NEW, session_id="", prompt="go")
    launch = picks.fresh(plain, Pick("sonnet", "low"))
    argv = launch.argv
    assert argv[:3] == ["claude", "--session-id", launch.new_id]
    assert argv[3:] == ["--model", "sonnet", "--effort", "low", "go"]
    assert picks.recall(launch.new_id) == Pick("sonnet", "low")
    # Claude Code resumes on the same model by itself, but at your default effort: the effort is given back.
    for mode in (Mode.RESUME, Mode.AUTO, Mode.FORK):
        again = picks.resumed(Launch(title="api", cwd="C:/api", mode=mode, session_id=launch.new_id, prompt="on"))
        assert again.effort == "low" and "--effort" in again.argv and "--model" not in again.argv
    assert picks.resumed(Launch("x", "C:/api", Mode.RESUME, "someone-else")).argv == ["claude", "--resume", "someone-else"]


def test_nothing_picked_or_another_cli_goes_as_it_was():
    plain = Launch(title="api: new", cwd="C:/api", mode=Mode.NEW, session_id="")
    assert picks.fresh(plain, Pick()) == plain and plain.argv == ["claude"]
    codex = Launch(title="api: new", cwd="C:/api", mode=Mode.NEW, session_id="", cli="codex")
    assert picks.fresh(codex, Pick("sonnet", "high")) == codex
    member = Launch("t: api", "C:/api", Mode.RESUME, "m", options=("--effort", "high"))
    picks.remember("m", Pick("", "low"))
    assert picks.resumed(member) == member  # its own flags say it already


def test_the_oldest_picks_go_first(monkeypatch):
    monkeypatch.setattr(picks, "KEEP", 3)
    for index in range(5):  # within a second the times tie, and the order they were written in decides
        picks.remember(f"s{index}", Pick("", "low"))
    assert set(picks.read_json(picks.FILE)["picks"]) == {"s2", "s3", "s4"}


def test_team_lines_take_an_effort_and_members_start_with_it():
    members = teams.parse_members("lead @opus @xhigh\napi: endpoints @sonnet @medium\nui: screens @high", lead_ok=True)
    assert [(m.name, m.model, m.effort) for m in members] == [
        ("lead", "opus", "xhigh"), ("api", "sonnet", "medium"), ("ui", "", "high")]
    team = teams.new_team("shop", "C:/shop", "Ship it", members)
    options = teams.options(team, members[1])
    assert options[options.index("--model") + 1] == "sonnet" and options[options.index("--effort") + 1] == "medium"
    teams.save({team.name: team})
    assert [(m.model, m.effort) for m in teams.load()["shop"].members] == [
        ("opus", "xhigh"), ("sonnet", "medium"), ("", "high")]


def test_a_handover_starts_on_the_model_and_effort_the_old_session_was_opened_with():
    picks.remember("old", Pick("sonnet", "high"))
    old = Session(session_id="old", path=None, cwd="C:/api", last_active=datetime.now())
    launch = handover.fresh(old, "## Goal\nShip it.")
    assert launch.model == "sonnet" and launch.effort == "high" and launch.new_id
    assert picks.recall(launch.new_id) == Pick("sonnet", "high")


def test_new_command_takes_a_model_and_an_effort(tmp_path, monkeypatch):
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    (tmp_path / "proj").mkdir()
    cli.main(["new", str(tmp_path / "proj"), "--model", "haiku", "--effort", "low"])
    assert opened[0].argv[3:] == ["--model", "haiku", "--effort", "low"]
    with pytest.raises(SystemExit):
        cli.main(["new", str(tmp_path / "proj"), "--cli", "codex", "--effort", "low"])


async def until(pilot, check, tries=80):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def test_the_new_team_dialog_keeps_its_keys_on_a_120x30_screen(fake_home):
    # A second help line under the members box once pushed the keys off the bottom.
    from tabmancer.teamview import NewTeamDialog

    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        app.push_screen(NewTeamDialog(str(fake_home), set()))
        await until(pilot, lambda: isinstance(app.screen, NewTeamDialog))
        await pilot.pause(0.2)
        hint = app.screen.query_one(".hint")
        assert "Ctrl+S" in str(hint.render()) and 0 < hint.region.bottom <= 30


async def test_the_n_dialog_picks_a_model_and_an_effort(fake_home, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    folder = tmp_path / "api"
    folder.mkdir()
    monkeypatch.setattr(newsessions, "projects", lambda sessions: [newsessions.Project(str(folder))])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await until(pilot, lambda: bool(app.sessions))
        await pilot.press("n")
        await until(pilot, lambda: isinstance(app.screen, NewSessions))
        await box_has_focus(pilot, app)
        dialog = app.screen
        words = dialog.query_one("#new-pick-words")
        assert "settings pick" in str(words.render())  # nothing picked: nothing is passed
        await pilot.press("space", "tab", "tab")  # tick the folder; past the first message to Model
        assert dialog.focused.id == "new-model"
        await pilot.press("right", "right")  # your default -> opus -> sonnet
        assert "sonnet: about half" in str(words.render())
        await pilot.press("tab", "left")  # Effort: your default -> max (it goes round)
        assert "max: as deep" in str(words.render())
        await pilot.press("left", "left")  # max -> xhigh -> high
        # Everything fits a 120x30 window: the keys line is still on screen.
        assert dialog.query_one(".hint").region.bottom <= 30
        await pilot.press("enter")
        await until(pilot, lambda: len(opened) == 1)
    launch = opened[0]
    assert (launch.model, launch.effort) == ("sonnet", "high") and launch.argv[1] == "--session-id"
    assert picks.recall(launch.new_id) == Pick("sonnet", "high")
