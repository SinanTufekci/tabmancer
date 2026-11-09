"""The router (a model, an effort and a context cap suggested before a session opens) and the context
cap itself: given to new sessions and teams, given back on resume, and how the dashboard weighs it."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from conftest import box_has_focus

from tabmancer import app as app_module
from tabmancer import cli, context, newsessions, picks, router, summarize, teams
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.newsessions import NewSessions
from tabmancer.picks import Pick
from tabmancer.sessions import Session


@pytest.fixture
def capped(monkeypatch):
    """New sessions get a 300k cap, as they do outside the tests (conftest turns it off)."""
    monkeypatch.delenv("TABMANCER_CONTEXT_CAP", raising=False)


async def until(pilot, check, tries=120):
    for _ in range(tries):
        await pilot.pause(0.05)
        if check():
            return
    raise AssertionError("condition never became true")


# --- the cap's words ---------------------------------------------------------------------------

@pytest.mark.parametrize(("word", "tokens"), [
    ("300k", 300_000), ("300K", 300_000), ("300000", 300_000), ("300", 300_000), ("1m", 1_000_000),
    ("0.5m", 500_000), ("50k", 0), ("2m", 0), ("auto", 0), ("", 0), ("high", 0),
])
def test_a_cap_is_read_as_claude_code_takes_it(word, tokens):
    assert picks.cap_tokens(word) == tokens


def test_the_default_cap_is_300k_unless_changed(capped, monkeypatch):
    assert picks.default_cap() == "300k"
    from tabmancer.config import write_json

    write_json("config.json", {"context_cap": "400k"})
    assert picks.default_cap() == "400k"
    write_json("config.json", {"context_cap": "off"})
    assert picks.default_cap() == picks.NO_CAP
    write_json("config.json", {"context_cap": "lots"})  # not a cap: the default, not a broken flag
    assert picks.default_cap() == "300k"
    monkeypatch.setenv("TABMANCER_CONTEXT_CAP", "200k")
    assert picks.default_cap() == "200k"


def test_a_cap_is_a_flag_and_no_cap_none():
    assert Pick("sonnet", "high", "300k").flags() == ("--model", "sonnet", "--effort", "high", "--autocompact", "300k")
    assert Pick(cap=picks.NO_CAP).flags() == () and Pick(cap="300k")
    assert Pick("sonnet", "high", "300k").words() == "sonnet · high"  # the cap is said where it matters
    launch = Launch("a", "C:/a", Mode.NEW, "", cap=picks.NO_CAP)
    assert launch.argv == ["claude"]
    member = Launch("t: a", "C:/a", Mode.RESUME, "m", cap="300k", options=("--autocompact", "400k"))
    assert member.argv.count("--autocompact") == 1 and "400k" in member.argv  # its own flags win


# --- new sessions and resumes ---------------------------------------------------------------------

def test_a_new_session_starts_capped_and_keeps_its_cap_on_resume(capped):
    plain = Launch(title="api: new", cwd="C:/api", mode=Mode.NEW, session_id="", prompt="go")
    launch = picks.fresh(plain, Pick())
    assert launch.argv == ["claude", "--session-id", launch.new_id, "--autocompact", "300k", "go"]
    assert picks.recall(launch.new_id) == Pick(cap="300k")
    # Claude Code forgets the cap on resume (a 1M window again): tabmancer gives it back, however
    # big the context is by then.
    for mode in (Mode.RESUME, Mode.AUTO, Mode.FORK):
        again = picks.resumed(Launch("api", "C:/api", mode, launch.new_id, prompt="on"), context=290_000)
        assert again.cap == "300k" and again.argv.count("--autocompact") == 1


def test_claude_codes_own_window_picked_stays_so(capped):
    launch = picks.fresh(Launch("api: new", "C:/api", Mode.NEW, ""), Pick(cap=picks.NO_CAP))
    assert "--autocompact" not in launch.argv
    again = picks.resumed(Launch("api", "C:/api", Mode.RESUME, launch.new_id), context=10_000)
    assert "--autocompact" not in again.argv  # not the default cap: you said no cap


def test_a_session_from_before_caps_is_capped_only_while_well_under_the_cap(capped):
    small = picks.resumed(Launch("old", "C:/old", Mode.RESUME, "old-small"), context=120_000)
    assert small.cap == "300k"
    near = picks.resumed(Launch("old", "C:/old", Mode.RESUME, "old-near"), context=260_000)
    big = picks.resumed(Launch("old", "C:/old", Mode.RESUME, "old-big"), context=640_000)
    assert not near.cap and not big.cap  # it would be compacted the moment it opened: left as it was
    # Opened with the cap, it keeps it from then on, though it grows near it.
    picks.note_opened([small])
    assert picks.recall("old-small").cap == "300k"
    later = picks.resumed(Launch("old", "C:/old", Mode.RESUME, "old-small"), context=262_000)
    assert later.cap == "300k"


def test_opening_tabs_keeps_a_resumed_sessions_cap(capped, monkeypatch):
    from tabmancer import launch as launch_module

    monkeypatch.setattr(launch_module, "terminal_kind", lambda: "none")  # nothing opens: lines come back
    launch = picks.resumed(Launch("old", "C:/old", Mode.RESUME, "old-one"), context=50_000)
    assert launch_module.open_tabs([launch]) and picks.recall("old-one").cap == "300k"


def test_a_handover_starts_capped(capped):
    from tabmancer import handover

    old = Session(session_id="was", path=None, cwd="C:/api", last_active=datetime.now())
    assert handover.fresh(old, "## Goal\nOn.").cap == "300k"  # an old session without one: a fresh one is capped
    picks.remember("wide", Pick(cap="600k"))
    wide = Session(session_id="wide", path=None, cwd="C:/api", last_active=datetime.now())
    assert handover.fresh(wide, "## Goal\nOn.").cap == "600k"  # one opened with its own: that one


def test_the_new_command_takes_a_cap(fake_home, tmp_path, monkeypatch, capped):
    opened = []
    monkeypatch.setattr(cli, "open_tabs", lambda launches: opened.extend(launches) or [])
    (tmp_path / "proj").mkdir()
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "hi"])
    assert opened[-1].argv[-3:] == ["--autocompact", "300k", "hi"]
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "hi", "--autocompact", "600k"])
    assert opened[-1].argv[-3:] == ["--autocompact", "600k", "hi"]
    cli.main(["new", str(tmp_path / "proj"), "--prompt", "hi", "--autocompact", "auto"])
    assert "--autocompact" not in opened[-1].argv
    with pytest.raises(SystemExit):
        cli.main(["new", str(tmp_path / "proj"), "--autocompact", "50k"])


# --- teams ---------------------------------------------------------------------------------------

def test_a_new_team_is_capped_and_an_old_one_is_not(capped):
    team = teams.new_team("shop", "C:/shop", "a shop", teams.parse_members("api: endpoints @sonnet"))
    assert team.cap == "300k"
    member = team.members[1]
    flags = teams.options(team, member)
    assert flags[flags.index("--autocompact") + 1] == "300k"
    saved = {team.name: team, "old": teams.Team("old", "C:/old", "older", [teams.Member("api", "x")])}
    teams.save(saved)
    loaded = teams.load()
    assert loaded["shop"].cap == "300k" and loaded["old"].cap == ""
    assert "--autocompact" not in teams.options(loaded["old"], loaded["old"].members[0])
    assert teams.cap_of("anyone", loaded["shop"]) == 300_000 and teams.cap_of("anyone", loaded["old"]) == 0


def test_the_team_suggestion_and_the_router_pick_alike():
    assert router.GUIDE in teams.suggestion_prompt("a shop", ".")


# --- how full, and how cold ---------------------------------------------------------------------

def session(context: int, idle_minutes: float = 0, model: str = "claude-opus-5-5") -> Session:
    return Session(session_id="s", path=None, cwd="C:/a", context=context, model=model,
                   last_active=datetime.now() - timedelta(minutes=idle_minutes))


def test_a_capped_session_is_full_against_its_cap():
    assert context.share(session(150_000), cap=300_000) == 0.5
    assert context.share(session(150_000)) == 0.15  # of Opus's 1M window
    assert not context.crowded(session(200_000), cap=300_000)
    assert context.crowded(session(230_000), cap=300_000)  # some way before Claude compacts it (267k)


def test_an_uncapped_session_is_crowded_past_the_default_cap():
    assert context.crowded(session(320_000), default_cap=300_000)  # past what new sessions are kept to
    assert not context.crowded(session(320_000))  # no default cap: 60% of the window, as before
    assert context.crowded(session(620_000))


def test_a_big_context_left_an_hour_has_lost_its_cache():
    assert context.cold(session(400_000, idle_minutes=61))
    assert not context.cold(session(400_000, idle_minutes=30))
    assert not context.cold(session(40_000, idle_minutes=600))  # too small to be worth a word


# --- the router ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("answer", "pick", "why"), [
    ("Pick: sonnet high 300k\nWhy: routine tests", Pick("sonnet", "high", "300k"), "routine tests"),
    ("**Pick:** opus xhigh 600k\n**Why:** a wide refactor.", Pick("opus", "xhigh", "600k"), "a wide refactor"),
    ("Pick: haiku low\nWhy: a look-up", Pick("haiku", "low"), "a look-up"),  # no cap named: yours stays
    ("I'd say sonnet at medium, 300k.", Pick("sonnet", "medium", "300k"), ""),  # no lines: its words
    ("Pick: sonnet high 300\nWhy: x", Pick("sonnet", "high", "300k"), "x"),  # Claude Code's shorthand
])
def test_the_routers_answer_is_read_as_a_pick(answer, pick, why):
    assert router.read(answer) == router.Route(pick, why)


def test_an_answer_without_a_pick_is_none():
    assert router.read("I cannot tell from this.") is None


def test_the_router_is_asked_about_the_message_and_how_to_pick():
    asked = router.prompt("add tests for picks", ["tabmancer"])
    assert asked.startswith(router.PROMPT_START) and "add tests for picks" in asked and "tabmancer" in asked
    assert router.GUIDE in asked and router.CAP_GUIDE in asked and "Pick:" in asked
    assert "pick for the hardest" in router.prompt("one thing\nanother thing", [])
    assert len(router.prompt("x " * 5000, [])) < 8000  # a pasted page: its start is enough
    assert not router.worth_routing("fix it") and router.worth_routing("fix the login page")


def test_the_router_asks_a_small_model_without_tools(monkeypatch):
    asked = []
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="":
                        asked.append((model, effort)) or "Pick: sonnet medium 300k\nWhy: routine")
    assert router.suggest("write the tests", ["a"]).pick == Pick("sonnet", "medium", "300k")
    assert asked == [("haiku", "low")]
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="": "no idea")
    with pytest.raises(summarize.SummaryError):
        router.suggest("write the tests", ["a"])


def test_the_router_can_be_turned_off(monkeypatch):
    from tabmancer.config import write_json

    monkeypatch.delenv("TABMANCER_ROUTER", raising=False)
    assert router.enabled()
    write_json("config.json", {"router": False})
    assert not router.enabled()
    write_json("config.json", {})
    monkeypatch.setenv("TABMANCER_ROUTER", "0")
    assert not router.enabled()


# --- the n dialog --------------------------------------------------------------------------------

async def open_dialog(pilot, app):
    await until(pilot, lambda: bool(app.sessions))
    await pilot.press("n")
    await until(pilot, lambda: isinstance(app.screen, NewSessions))
    await box_has_focus(pilot, app)
    return app.screen


async def test_the_n_dialog_takes_the_routers_pick_with_ctrl_r(fake_home, tmp_path, monkeypatch, capped):
    opened, asked = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="":
                        asked.append(prompt) or "Pick: sonnet medium 400k\nWhy: well-defined tests")
    folder = tmp_path / "api"
    folder.mkdir()
    monkeypatch.setattr(newsessions, "projects", lambda sessions: [newsessions.Project(str(folder))])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        dialog = await open_dialog(pilot, app)
        route = dialog.query_one("#new-route")
        assert dialog.query_one("#new-cap").value == "300k"  # the default cap, there to see and change
        assert "write the first message" in str(route.render())
        await pilot.press("space", "tab")  # tick the folder, to the first message
        await pilot.press("ctrl+r")  # too short to say anything yet
        assert not asked
        dialog.message.insert("write unit tests for the picks module")
        await pilot.press("ctrl+r")
        await until(pilot, lambda: dialog.pick() == Pick("sonnet", "medium", "400k"))
        assert len(asked) == 1 and "write unit tests for the picks module" in asked[0] and "api" in asked[0]
        assert "the router's pick" in str(route.render()) and "well-defined tests" in str(route.render())
        assert dialog.query_one(".hint").region.bottom <= 30  # the keys line still on a 120x30 screen
        await pilot.press("ctrl+s")
        await until(pilot, lambda: len(opened) == 1)
    assert opened[0].argv[-5:] == ["--effort", "medium", "--autocompact", "400k",
                                   "write unit tests for the picks module"]


async def test_the_n_dialog_suggests_by_itself_and_leaves_the_pick_yours(fake_home, tmp_path, monkeypatch, capped):
    asked = []
    monkeypatch.delenv("TABMANCER_ROUTER", raising=False)
    monkeypatch.setattr(NewSessions, "ROUTE_AFTER", 0.05)
    monkeypatch.setattr(summarize, "ask", lambda prompt, model, timeout=180, effort="":
                        asked.append(prompt) or "Pick: haiku low 200k\nWhy: a quick look")
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        dialog = await open_dialog(pilot, app)
        await pilot.press("tab")
        dialog.message.insert("what does the config file hold")
        route = dialog.query_one("#new-route")
        await until(pilot, lambda: "Router suggests" in str(route.render()))
        assert "haiku · low · 200k" in str(route.render()) and "Ctrl+R" in str(route.render())
        assert dialog.pick() == Pick(cap="300k")  # suggested only: nothing taken without Ctrl+R
        await pilot.press("ctrl+r")  # the answer is there already: taken at once, not asked again
        assert dialog.pick() == Pick("haiku", "low", "200k") and len(asked) == 1
        dialog.query_one("#new-model").pick("sonnet")  # changed after: the suggestion shows again
        await pilot.pause(0.05)
        assert "Router suggests" in str(route.render())


async def test_a_failed_router_says_why_and_asks_again(fake_home, monkeypatch, capped):
    def fail(prompt, model, timeout=180, effort=""):
        raise summarize.SummaryError("claude returned nothing.")

    monkeypatch.setattr(summarize, "ask", fail)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        dialog = await open_dialog(pilot, app)
        await pilot.press("tab")
        dialog.message.insert("refactor the session list")
        await pilot.press("ctrl+r")
        route = dialog.query_one("#new-route")
        await until(pilot, lambda: "no suggestion" in str(route.render()))
        assert "claude returned nothing" in str(route.render()) and dialog.pick() == Pick(cap="300k")
