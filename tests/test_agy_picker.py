"""Where an agy model is picked: the n dialog's Model row and a team member's command line."""

from __future__ import annotations

import pytest
from conftest import box_has_focus

from tabmancer import agy, newsessions, router, teams
from tabmancer.app import SessionPicker
from tabmancer.newsessions import NewSessions
from tabmancer.picks import Pick

ARGON, HYDROGEN, FLASH = agy.CHAIN
ARGON_OUT = ("Gemini Models\tWeekly Limit Remaining\t54%\t2099-11-25T08:53:36Z\n"
             "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t0%\t2099-11-25T07:02:59Z\n")


@pytest.fixture
def usage(monkeypatch):
    """agy's /usage as a test sets it; no agy is run."""
    said = {"table": None, "asked": 0}
    monkeypatch.setattr(agy, "_last_read", None)

    def run(timeout):
        said["asked"] += 1
        return said["table"]

    monkeypatch.setattr(agy, "_run_usage", run)
    return said


async def until(pilot, check, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


async def open_dialog(pilot, app):
    await until(pilot, lambda: bool(app.sessions))
    await pilot.press("n")
    await until(pilot, lambda: isinstance(app.screen, NewSessions))
    await box_has_focus(pilot, app)
    return app.screen


async def test_the_agy_model_row_holds_the_chain_and_no_claude(fake_home, usage):
    usage["table"] = ARGON_OUT
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        dialog = await open_dialog(pilot, app)
        claude_row, agy_row = dialog.query_one("#new-model"), dialog.query_one("#new-agy-model")
        assert claude_row.display and not agy_row.display
        dialog.cli = "agy"
        dialog.show_cli()
        assert agy_row.display and not claude_row.display
        assert agy_row.words == [newsessions.AGY_CHAIN, *agy.CHAIN]
        assert not any(agy.is_claude(word) for word in agy_row.words)
        await until(pilot, lambda: dialog.pick() == Pick(HYDROGEN))  # the quota read: Argon is out
        assert usage["asked"] == 1
        words = str(dialog.query_one("#new-pick-words").render())
        assert "Now: Hydrogen" in words and "Argon out of quota" in words

        agy_row.pick(FLASH)  # picked by hand: kept
        assert dialog.pick() == Pick(FLASH)
        launch, = newsessions.launches(["C:/work/demo"], "agy", None, dialog.pick())
        assert launch.argv[:3] == ["agy", "--model", FLASH]

        dialog.take(router.Route(Pick(HYDROGEN), "Argon out: Hydrogen next", cli="agy"))
        assert agy_row.value == newsessions.AGY_CHAIN  # what the chain picks now: it keeps following the quota
        assert dialog.pick() == Pick(HYDROGEN)
        assert dialog.taken(router.Route(Pick(HYDROGEN), "", cli="agy"))

        dialog.take(router.Route(Pick(ARGON), "Argon while its quota lasts", cli="agy"))  # an older answer
        assert agy_row.value == ARGON
        assert dialog.pick() == Pick(HYDROGEN)  # Argon asked for, but out: the chain goes on

        dialog.cli = "claude"
        dialog.show_cli()
        assert claude_row.display and not agy_row.display
        assert dialog.pick().model == ""  # Claude Code's row never touched


async def test_quota_is_read_once_a_dialog(fake_home, usage):
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        dialog = await open_dialog(pilot, app)
        for cli in ("agy", "claude", "agy"):
            dialog.cli = cli
            dialog.show_cli()
        await pilot.pause(0.2)
        assert usage["asked"] == 1
        assert dialog.pick() == Pick(ARGON)  # nothing read: the chain's first


def _agy_member(tmp_path, tags: str) -> tuple[teams.Team, teams.Member]:
    members = teams.parse_members(f"lead: plans @opus\nui: screens {tags}", lead_ok=True)
    team = teams.new_team("shop", str(tmp_path), "Build it", members)
    return team, team.members[1]


@pytest.mark.parametrize(("tags", "model"), [
    ("@agy", ARGON), ("@agy @opus", ARGON), ("@agy @claude-sonnet-5-5-high", ARGON),
    ("@agy @gemini-4-neon", "gemini-4-neon"), ("@agy @gpt-oss-120b-medium", ARGON), ("@gemini-4-hydrogen-high", HYDROGEN), ("@agy @gemini-3.8-flash-high", FLASH),
])
def test_an_agy_member_runs_on_the_chain_never_claude(tmp_path, usage, tags, model):
    team, member = _agy_member(tmp_path, tags)
    assert member.cli == "agy"
    flags = teams.options(team, member)
    assert flags[flags.index("--model") + 1] == model
    assert not any(agy.barred(flag) for flag in flags)


def test_an_agy_member_follows_the_quota(tmp_path, usage):
    usage["table"] = ARGON_OUT
    assert agy.refresh_quota()
    team, member = _agy_member(tmp_path, "@agy")
    flags = teams.options(team, member)
    assert flags[flags.index("--model") + 1] == HYDROGEN


def test_agy_notes_name_the_members_whose_model_was_replaced(tmp_path, usage):
    members = teams.parse_members("lead: plans @opus\nui: screens @agy @opus\nqa: tests @agy\n"
                                  "neon: tries @agy @gemini-4-neon-high", lead_ok=True)
    team = teams.new_team("shop", str(tmp_path), "Build it", members)
    notes = teams.agy_notes(team)
    assert len(notes) == 1 and notes[0].startswith("ui: opus never runs through agy: Argon instead")
    assert teams.agy_notes(team, {"qa"}) == []


def test_team_start_says_when_an_agy_member_gets_another_model(fake_home, tmp_path, monkeypatch, capsys, usage):
    from tabmancer import cli

    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    members = teams.parse_members("lead: plans @opus\nui: screens @agy @claude-sonnet-5-5-high", lead_ok=True)
    teams.save({"shop": teams.new_team("shop", str(tmp_path), "Build it", members)})
    assert cli.start_team("shop", "ui") == 0
    said = capsys.readouterr().out
    assert "ui: claude-sonnet-5-5-high never runs through agy: Argon instead" in said
    argv = opened[0].argv
    assert argv[argv.index("--model") + 1] == ARGON
