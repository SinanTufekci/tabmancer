"""teams.json changed under its lock (teams.changing): two windows, or a window and `tabmancer team`, each
keep the other's change; a held lock is waited for a moment on a UI thread (UI_TRIES) and the full wait on
the command line, then said in plain words; a teams.json that is there but cannot be read is never
written over (every team in it would be lost); a new team is never saved over one of that name made
meanwhile (teams.make). lineups.json, the line-ups kept with Ctrl+L, the same way."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from conftest import wait_until
from test_engine_team_start import Front, lead
from test_team_keys import Recorder, keys_on, shop

from tabmancer import actions, cli, config, engine, newforms, picks, teams
from tabmancer.actions import Note
from tabmancer.teamforms import JoinTeam, Keep, TeamForm


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    """The lock's waits, kept here: none is slept."""
    kept: list[float] = []
    monkeypatch.setattr(config.time, "sleep", kept.append)
    return kept


def teams_file() -> Path:
    return config.config_dir() / teams.FILE


def test_a_change_is_saved_on_the_way_out_and_nothing_is_written_when_nothing_changed(tmp_path, monkeypatch):
    team = shop(tmp_path)
    saves: list = []
    real_save = teams.save
    monkeypatch.setattr(teams, "save", lambda saved: saves.append(sorted(saved)) or real_save(saved))
    with teams.changing() as saved:
        assert list(saved) == ["shop"] and saved["shop"].members == team.members
    assert saves == []
    with teams.changing() as saved:
        saved["shop"].goal = "Add a checkout and a cart"
    assert saves == [["shop"]] and teams.load()["shop"].goal == "Add a checkout and a cart"
    assert not (config.config_dir() / f"{teams.FILE}.lock").exists()  # let go of


def test_a_change_that_raises_saves_nothing(tmp_path):
    shop(tmp_path)
    with pytest.raises(ValueError), teams.changing() as saved:
        saved.pop("shop")
        raise ValueError("refused half way")
    assert list(teams.load()) == ["shop"]


def test_a_held_lock_is_waited_for_a_moment_on_a_ui_thread_then_nothing_is_changed(tmp_path, waits):
    team = shop(tmp_path)
    before = teams_file().read_bytes()
    with config.locked(teams.FILE):
        with pytest.raises(config.Busy):
            teams.add_members(team, "docs: z", config.UI_TRIES)
        with pytest.raises(config.Busy):
            teams.remove_member("shop", "api", config.UI_TRIES)
        with pytest.raises(config.Busy):
            teams.set_goal("shop", "Sell more", config.UI_TRIES)
        with pytest.raises(config.Busy):
            teams.add_to_goal("shop", "and a cart", config.UI_TRIES)
        with pytest.raises(config.Busy):
            teams.put(teams.new_team("docs", str(tmp_path), "Write", []), config.UI_TRIES)
        with pytest.raises(config.Busy):
            teams.drop("shop", config.UI_TRIES)
    assert waits == [config.BUSY_WAIT] * (6 * (config.UI_TRIES - 1))
    assert teams_file().read_bytes() == before


def test_the_command_line_keeps_the_full_wait(tmp_path, waits):
    team = shop(tmp_path)
    with config.locked(teams.FILE), pytest.raises(config.Busy):
        teams.add_members(team, "docs: z")
    assert waits == [config.BUSY_WAIT] * (config.BUSY_TRIES - 1)


def test_a_teams_file_that_cannot_be_read_is_never_written_over(tmp_path, waits):
    shop(tmp_path)
    teams_file().write_text('{"teams": [', encoding="utf-8")  # cut off mid-write by something else
    with pytest.raises(teams.Unreadable) as refused:
        teams.put(teams.new_team("docs", str(tmp_path), "Write", []), config.UI_TRIES)
    assert teams_file().read_text(encoding="utf-8") == '{"teams": ['
    assert isinstance(refused.value, config.Busy)  # every caller that says Busy says this too
    words = teams.not_saved_words("docs was not made", refused.value)
    assert words.startswith(f"docs was not made: {teams.FILE} could not be read, so it was left as it was")
    assert teams.not_saved_words("x", config.Busy("held")) == (
        "x: another tabmancer window is changing the teams right now. Try again in a moment.")


def test_with_no_teams_file_yet_a_team_is_made(tmp_path):
    assert not teams_file().exists()
    teams.put(teams.new_team("docs", str(tmp_path), "Write", []))
    assert list(teams.load()) == ["docs"]


def test_members_join_the_team_as_saved_now_not_as_a_window_last_read_it(tmp_path):
    """Another window added api2 since this one read the team: it is kept, and a second api2 is not added."""
    stale = shop(tmp_path)
    teams.add_members(stale, "api2: z")
    new, note = teams.add_members(stale, "api2: again; docs: w")
    assert [m.name for m in new] == ["docs"] and note == "already on the team: api2"
    assert [m.name for m in teams.load()["shop"].members] == ["lead", "api", "ui", "api2", "docs"]


def test_a_team_that_is_gone_is_not_saved_again(tmp_path, monkeypatch):
    shop(tmp_path)
    saves: list = []
    monkeypatch.setattr(teams, "save", saves.append)
    assert teams.remove_member("docs", "api") is None and teams.set_goal("docs", "x") is None
    assert teams.add_to_goal("docs", "x") is None and saves == []


def test_actions_say_why_nobody_joined(tmp_path, waits):
    team = shop(tmp_path)
    with config.locked(teams.FILE):
        added = actions.add_members(team, "docs: z", config.UI_TRIES)
    assert added.new == [] and added.team is team
    assert added.warning == teams.not_saved_words("Nobody joined", config.Busy("held"))


def test_the_dashboards_team_keys_wait_a_moment_and_say_what_was_not_done(tmp_path, waits):
    """a, d twice and g, on a dashboard's UI thread, with another window changing the teams."""
    team, surface = shop(tmp_path), Recorder()
    keys = keys_on(surface)
    api = next(m for m in team.members if m.name == "api")
    with config.locked(teams.FILE):
        keys.add(team, "docs: z", at_work=False)
        keys.remove(team, api, None)  # the first press only asks
        assert keys.remove(team, api, None) is None
        keys.change_goal(team, "Sell more", {})
    busy = "another tabmancer window is changing the teams right now. Try again in a moment."
    said = [call for call in surface.calls if call[0] == "say"]
    assert [words for _, words, _, _ in said] == [
        f"Nobody joined: {busy}",
        actions.removal_ask(team, api, None).words,
        f"api is still on the team: {busy}",
        f"The goal is not changed: {busy}",
    ]
    assert waits == [config.BUSY_WAIT] * (3 * (config.UI_TRIES - 1))
    assert teams.load()["shop"] == team  # nothing changed
    keys.change_goal(team, "Sell more", {"lead": None, "api": None, "ui": None})  # free again: changed
    assert teams.load()["shop"].goal == "Sell more"


def test_members_joining_a_team_at_work_wait_a_moment_too(tmp_path, waits):
    shop(tmp_path)
    surface = Recorder()
    with config.locked(teams.FILE):
        keys_on(surface).join_team(JoinTeam("shop", "docs: z", "lead", "Docs join.", "and docs"))
    assert surface.calls == [("say", ("Nobody joined shop: another tabmancer window is changing the teams right "
                                      "now. Try again in a moment."), "warning", 8)]
    assert [m.name for m in teams.load()["shop"].members] == ["lead", "api", "ui"]


def test_a_team_handover_that_cannot_be_saved_now_is_kept_for_h(tmp_path, waits):
    written = shop(tmp_path)
    front = Front()
    eng = engine.Engine(front, clock=lambda: datetime(2026, 11, 26, 12, 0), state=engine.EngineState())
    eng.state.kept_handovers["shop"] = (lead(), "## written", "api: carry on", True)
    with config.locked(teams.FILE):
        eng.team_start(written, lead(), "## written", ("api: carry on", ""))
    assert front.calls == [("say_note", Note(
        "shop is not handed over: another tabmancer window is changing the teams right now. Try again in a moment. "
        "h shows the handover again.", "warning", 10))]
    assert "shop" in eng.state.kept_handovers and teams.load()["shop"] == written
    eng.team_start(written, lead(), "## written", ("api: carry on", ""))  # free again: handed over
    assert [m.name for m in teams.load()["shop"].members] == ["lead", "api"]
    assert "shop" not in eng.state.kept_handovers


def test_a_linked_request_names_its_team_under_the_lock(tmp_path, waits):
    folder = tmp_path / "shop"
    folder.mkdir()
    asked = newforms.Request([str(folder)], "claude", "api: x\nui: y", picks.Pick(), linked=True)
    with config.locked(teams.FILE), pytest.raises(config.Busy):
        newforms.build(asked, config.UI_TRIES)
    assert teams.load() == {}
    first, second = newforms.build(asked), newforms.build(asked)
    assert (first.team.name, second.team.name) == ("shop", "shop-2")
    assert sorted(teams.load()) == ["shop", "shop-2"]


def test_tabmancer_team_add_says_why_nobody_joined(tmp_path, waits, capsys):
    shop(tmp_path)
    with config.locked(teams.FILE):
        assert cli.add_to_team("shop", "docs: z") == 1
    assert capsys.readouterr().out.strip() == (
        "Nobody joined: another tabmancer window is changing the teams right now. Try again in a moment.")
    assert waits == [config.BUSY_WAIT] * (config.BUSY_TRIES - 1)


def lineups_file() -> Path:
    return config.config_dir() / teams.LINEUPS_FILE


def test_two_windows_each_keep_the_others_line_ups():
    """Each form read the kept line-ups when it opened; each keep and forget reads them again under the lock."""
    first, second = TeamForm(set()), TeamForm(set())
    assert first.keep("api pair", ["api: x", "ui: y"], False).changed
    assert second.keep("docs", ["docs: z"], True).changed
    assert teams.saved_lineups() == {"api pair": "api: x\nui: y", "docs": f"{teams.PEERS_MARK}docs: z"}
    shown = second.template_text("docs", "")
    second.keep_step(shown)  # the first press only asks
    assert second.keep_step(shown).changed
    assert teams.saved_lineups() == {"api pair": "api: x\nui: y"}  # first's kept, though second never read it


def test_a_held_lineups_lock_is_waited_for_a_moment_then_said(waits):
    form = TeamForm(set())
    assert form.keep("docs", ["docs: z"], False).changed
    before = lineups_file().read_bytes()
    shown = form.template_text("docs", "")
    with config.locked(teams.LINEUPS_FILE):
        refused = form.keep("api", ["api: x"], False)
        form.keep_step(shown)  # the first press only asks
        still = form.keep_step(shown)
    busy = "another tabmancer window is changing the kept line-ups right now. Try again in a moment."
    assert refused == Keep(Note(f"The line-up api is not kept: {busy}", "warning", 8))
    assert still == Keep(Note(f"The line-up docs is still kept: {busy}", "warning", 8))
    assert waits == [config.BUSY_WAIT] * (2 * (config.UI_TRIES - 1))
    assert lineups_file().read_bytes() == before
    assert "api" not in form.lineups and form.picked() == "docs" and "docs" in form.saved
    assert form.keep_step(shown).changed and teams.saved_lineups() == {}  # free again: Ctrl+L once more forgets


@pytest.mark.parametrize("held", ['{"lineups": {"api": "api: x', "[]"])
def test_a_lineups_file_that_cannot_be_read_is_never_written_over(held, waits):
    """Cut short, or holding no dict: read as none, so written over, every line-up in it would be lost."""
    lineups_file().parent.mkdir(parents=True, exist_ok=True)
    lineups_file().write_text(held, encoding="utf-8")
    kept = TeamForm(set()).keep("docs", ["docs: z"], False)
    assert kept.note is not None and not kept.changed
    assert kept.note.words.startswith(
        f"The line-up docs is not kept: {teams.LINEUPS_FILE} could not be read, so it was left as it was")
    with pytest.raises(teams.Unreadable):
        teams.keep_lineup("api", None)
    assert lineups_file().read_text(encoding="utf-8") == held
    lineups_file().unlink()  # none yet: made at the first keep
    teams.keep_lineup("docs", "docs: z")
    assert teams.saved_lineups() == {"docs": "docs: z"}


TAKEN = "shop was not made: a team named shop was made meanwhile. Give yours another name."


def test_a_new_team_is_never_saved_over_one_of_that_name_made_meanwhile(tmp_path, waits):
    team = shop(tmp_path)  # made by another window since this one's T dialog read the names in use
    before = teams_file().read_bytes()
    with pytest.raises(teams.Taken) as refused:
        teams.make(teams.new_team("shop", str(tmp_path), "Another goal", []), config.UI_TRIES)
    assert teams_file().read_bytes() == before and teams.load()["shop"] == team
    assert isinstance(refused.value, config.Busy)  # every caller that says Busy says this too
    assert teams.not_saved_words("shop was not made", refused.value) == TAKEN
    with config.locked(teams.FILE), pytest.raises(config.Busy):
        teams.make(teams.new_team("docs", str(tmp_path), "Write", []), config.UI_TRIES)
    assert waits == [config.BUSY_WAIT] * (config.UI_TRIES - 1) and teams_file().read_bytes() == before
    teams.make(teams.new_team("docs", str(tmp_path), "Write", []), config.UI_TRIES)
    assert sorted(teams.load()) == ["docs", "shop"]


def test_load_waits_for_a_held_teams_file_as_long_as_asked(tmp_path, waits, monkeypatch):
    shop(tmp_path)
    read_text = Path.read_text

    def held_open(self, *args, **kwargs):  # the file itself held by another process (replaced just now)
        if self.name == teams.FILE:
            raise PermissionError("in use")
        return read_text(self, *args, **kwargs)

    with monkeypatch.context() as held:
        held.setattr(Path, "read_text", held_open)
        assert teams.load(config.UI_TRIES) == {}  # read as none: a UI thread is not held up ~1 s
        assert waits == [config.BUSY_WAIT] * (config.UI_TRIES - 1)
    assert list(teams.load()) == ["shop"]


async def test_the_dashboards_T_says_why_a_team_was_not_made(fake_home, tmp_path, monkeypatch):
    """Ctrl+S with another tabmancer changing the teams, then with a team of that name made meanwhile: not
    made, said, nothing opened, the other team kept; the names in use are read with a short wait."""
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker
    from tabmancer.teamview import NewTeamDialog

    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    loads: list[int | None] = []
    real_load = teams.load
    monkeypatch.setattr(teams, "load", lambda tries=None: loads.append(tries) or real_load(tries))
    folder = tmp_path / "shop"
    folder.mkdir()
    other = teams.new_team("shop", str(folder), "Another window's goal", teams.parse_members("docs: z"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await wait_until(pilot, lambda: app.sessions)

        async def make_shop() -> None:
            await pilot.press("T")  # no teams yet: straight to a new one
            await wait_until(pilot, lambda: isinstance(app.screen, NewTeamDialog))
            dialog = app.screen
            dialog.query_one("#team-name").value = "shop"
            dialog.query_one("#team-folder").value = str(folder)
            dialog.query_one("#team-goal").text = "Add a checkout"

        def said(words: str) -> bool:
            return any(str(n.message) == words for n in app._notifications)

        await make_shop()
        assert config.UI_TRIES in loads  # the names in use, read on the UI thread
        with config.locked(teams.FILE):
            await pilot.press("ctrl+s")
            await wait_until(pilot, lambda: not isinstance(app.screen, NewTeamDialog))
        assert said(teams.not_saved_words("shop was not made", config.Busy("held")))
        assert teams.load() == {} and opened == []
        await make_shop()
        teams.put(other)  # made meanwhile, elsewhere
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: said(TAKEN))
        assert teams.load() == {"shop": other} and opened == []
