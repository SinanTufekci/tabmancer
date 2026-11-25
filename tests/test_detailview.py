"""A session's whole preview as one value (sessionwords.detail -> DetailView) from plain inputs, and a
team's first lines (teamwords.team_detail): what the terminal dashboard draws and the desktop drawer
shows, with no UI behind them."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import sessionwords, teams, teamwords
from tabmancer.launch import Launch, Mode
from tabmancer.palette import AMBER, MUTED, TEXT
from tabmancer.sessions import Exit, Session, Status

ACCENT = "#d97757"


def s(sid="s1", exit=Exit.CLEAN, status=Status.DONE, **kw) -> Session:
    kw.setdefault("last_active", datetime.now() - timedelta(minutes=5))
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd="C:/work/shop", title="Fix the cart",
                   exit=exit, status=status, first_prompt="fix the cart", last_reply="Fixed.", **kw)


def never() -> Launch:
    raise AssertionError("asked how it opens")


def resume() -> Launch:
    return Launch(title="t", cwd="C:/work/shop", mode=Mode.RESUME, session_id="s1")


def test_the_preview_is_the_parts_each_front_draws():
    session = s()
    asked = []

    def launch() -> Launch:
        asked.append(1)
        return resume()
    inputs = sessionwords.DetailInputs(launch=launch, mode=Mode.RESUME, summary={"summary": "Cart fixed.", "size": 0},
                                       room=40, more=3, accent=ACCENT)
    view = sessionwords.detail(session, inputs)
    assert view.head == "Fix the cart"
    assert (view.status, view.means) == sessionwords.status_row(session)
    assert view.enter == [("⏎ ", f"bold {ACCENT}"), ("resumes it in a new tab · m changes how", ACCENT)]
    assert view.said == sessionwords.said_rows(session, summary={"summary": "Cart fixed.", "size": 0}, room=40,
                                               more=3, accent=ACCENT)
    assert view.facts == sessionwords.facts(session, inputs)
    assert [label for label, _ in view.facts][:1] == ["Folder"] and asked  # Enter and Opens as ask it


def test_an_open_one_is_never_asked_how_it_opens_and_ticks_go_first():
    running = s(exit=Exit.RUNNING, status=Status.RUNNING)
    view = sessionwords.detail(running, sessionwords.DetailInputs(launch=never, mode=Mode.RESUME))
    assert view.enter[1][0] == "goes to its tab, to type there"
    ticked = sessionwords.detail(running, sessionwords.DetailInputs(launch=never, mode=Mode.RESUME,
                                                                     selected={"s1", "s2"}))
    assert ticked.enter[1][0] == "opens the 2 ticked sessions, each in a tab"


def test_a_members_messages_and_the_filters_words_come_in():
    session = s()
    messages = [("1 in · 0 out", TEXT)]
    view = sessionwords.detail(session, sessionwords.DetailInputs(
        launch=resume, mode=Mode.RESUME, messages=messages, found=("it said cart here", 8, 12), accent=ACCENT,
        selected={"x"}))
    assert view.said[0] == ("Messages", messages)  # one line: each front cuts it to its room
    assert view.said[1] == ("Found", [("it said ", ""), ("cart", f"bold reverse {ACCENT}"), (" here", "")])


def test_a_teams_first_lines():
    team = teams.new_team("shop", "C:/work/shop", "Add  a\ncheckout", teams.parse_members("api: endpoints"))
    state = teams.TeamState(team, {}, [])
    at_work = teamwords.team_detail(state, 0, ACCENT, True)
    assert at_work.head == teamwords.head(state, 0, ACCENT)
    assert at_work.goal == [("Goal  ", MUTED), ("Add a checkout", TEXT)] and at_work.closed == []
    closed = teamwords.team_detail(state, 0, ACCENT, False)
    assert closed.closed == [(f"Closed. Enter starts it: each member resumed as itself, {team.lead.name} last.",
                              AMBER)]


def test_the_inputs_read_off_what_a_front_holds():
    team = teams.new_team("shop", "C:/work/shop", "Goal", teams.parse_members("api: endpoints"))
    member = s("m1", context=50_000, model="claude-opus-5-5")
    state = teams.TeamState(team, {"api": member}, [teams.Message(sender="lead", receiver="api", text="go",
                                                                  at=datetime.now() - timedelta(minutes=2))])
    held = {"members": {"m1": ("shop", "api")}, "team_states": {"shop": state}, "git": {}, "session_tokens": {},
            "background": {}, "armed": {}, "queued": {"m1": "new"}, "modes": {"m1": Mode.AUTO},
            "default_cap": 300_000, "accent": ACCENT}
    inputs = sessionwords.read_inputs(member, **held, summaries={"m1": {"summary": "x"}}, room=50, more=3)
    assert inputs.member == ("shop", "api") and inputs.mode is Mode.AUTO and inputs.queued
    assert inputs.messages == sessionwords.messages(state.said, "api", teamwords.colours(team, ACCENT))
    assert inputs.summary == {"summary": "x"} and (inputs.room, inputs.more, inputs.default_cap) == (50, 3, 300_000)
    assert inputs.launch().mode is Mode.AUTO  # as m set it
    facts_only = sessionwords.read_inputs(member, **held, summaries={"m1": {"summary": "x"}}, said=False)
    assert facts_only.messages is None and facts_only.summary is None
    alone = sessionwords.read_inputs(s("o1"), **held)
    assert alone.member is None and alone.messages is None and alone.mode is Mode.RESUME and not alone.queued


def test_with_agys_chain_all_out_the_preview_says_nothing_opens_it(monkeypatch):
    from tabmancer import agy

    why = "Neon and Hydrogen out until Nov 25 14:00: pick one or wait"

    def out_of_quota(*a, **k):
        raise agy.no_model(why)
    monkeypatch.setattr(agy, "model_flags", out_of_quota)
    said = "Neon and Hydrogen out until Nov 25 14:00: nothing opens it until the quota is back"
    assert agy.wait_words(agy.no_model(why)) == said  # never another model: there, nothing is chosen
    assert agy.wait_words(agy.no_model("")) == "no agy model now: nothing opens it until the quota is back"
    gemini = s("g1", cli="agy")
    inputs = sessionwords.DetailInputs(launch=lambda: Launch(title="t", cwd="C:/w", mode=Mode.RESUME,
                                                             session_id="g1", cli="agy"), mode=Mode.RESUME)
    view = sessionwords.detail(gemini, inputs)  # never raises: it says why
    assert view.enter == [(said, AMBER)] and ("", [(said, AMBER)]) in view.facts
    ticked = sessionwords.detail(gemini, sessionwords.DetailInputs(launch=inputs.launch, mode=Mode.RESUME,
                                                                    selected={"g1"}))
    assert ticked.enter[1][0] == "opens the 1 ticked session, each in a tab"  # what Enter does first
