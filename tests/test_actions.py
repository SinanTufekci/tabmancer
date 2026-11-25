"""The dashboard's actions without a screen (actions.py): what they say and do, from fakes, and that
no UI comes with them. The dashboard's own tests (test_handover) show the same through its keys."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tabmancer import actions, actor, clis, handover, summarize, teams, typein, usage, viewmodel, wrapped
from tabmancer.launch import Mode, continue_prompt
from tabmancer.sessions import Exit, Session, Status


def test_actions_load_no_ui():
    code = ("import sys, tabmancer.actions; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def one(sid="a", title="Checkout", state="idle", path=None, **kw) -> Session:
    return Session(session_id=sid, path=path, cwd="C:/work/shop", title=title, exit=Exit.RUNNING, live_state=state, **kw)


def test_what_stops_a_handover_in_the_words_the_dashboard_says(tmp_path, monkeypatch):
    background = one(status=Status.BACKGROUND)
    assert actions.handover_refusal(background, []) == actions.Refusal(
        "Background sessions cannot be handed over from here: attach to it first.")

    monkeypatch.setattr(clis, "installed", lambda name: None)
    unread = one(cli="agy")
    assert actions.handover_refusal(unread, [unread]).words == (
        "Antigravity has no readable transcript for this session and agy is not on PATH.")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    assert actions.handover_refusal(one(cli="agy", path=transcript), []) is None  # its transcript will do

    waiting, twin = one(), one("b")
    refusal = actions.handover_refusal(waiting, [waiting, twin])
    assert refusal.seconds == 10 and refusal.words.startswith("shop cannot be handed over now: another open session")
    assert actions.handover_refusal(waiting, [waiting, twin], queued=True) is None  # h again: cancelled, not checked
    assert actions.handover_refusal(one(state="busy"), [one(state="busy")]) is None  # handed over once it finishes
    closed = Session(session_id="c", path=None, cwd="C:/work/shop", exit=Exit.CLEAN)
    assert actions.handover_refusal(closed, [waiting, twin]) is None  # nothing to close


@pytest.fixture
def calls(monkeypatch):
    """What write_handover asked of handover: no CLI and no tab is touched."""
    said: list = []
    monkeypatch.setattr(handover, "from_transcript", lambda s: said.append("transcript") or "from the transcript")
    monkeypatch.setattr(handover, "write_itself", lambda s: said.append("self") or "by itself")
    monkeypatch.setattr(handover, "close", lambda s, open_ones: said.append(("close", s.live_state, len(open_ones))))
    monkeypatch.setattr(teams, "member_of", lambda s: None)
    return said


def test_a_handover_written_both_ways(calls):
    closed = Session(session_id="c", path=None, cwd="C:/work/shop", exit=Exit.CLEAN)
    assert actions.write_handover(closed, "self", lambda: pytest.fail("not open: nothing to read")) == \
        actions.Handed("by itself")
    session = one()
    now = lambda: (one(state="idle"), [session, one("b")])
    assert actions.write_handover(session, "transcript", now) == actions.Handed("from the transcript")
    assert calls == ["self", "transcript", ("close", "idle", 2)]  # written first, then closed as it is now
    calls.clear()
    assert actions.write_handover(session, "self", now).written == "by itself"
    assert calls == [("close", "idle", 2), "self"]  # closed first: it resumes to write


def test_a_close_that_fails(calls, monkeypatch):
    def fails(s, open_ones):
        raise handover.HandoverError("its tab was not found in Windows Terminal")

    monkeypatch.setattr(handover, "close", fails)
    session = one()
    now = lambda: (session, [session])
    # From the transcript it is written already: the fresh one opens, the old one is said to be open.
    assert actions.write_handover(session, "transcript", now) == actions.Handed(
        "from the transcript", "its tab was not found in Windows Terminal")
    # A team member's is not: its fresh session would share its name with the open one.
    monkeypatch.setattr(teams, "member_of", lambda s: ("team", "member"))
    with pytest.raises(actions.ActionError) as raised:
        actions.write_handover(session, "transcript", now)
    assert raised.value.words == "shop was not handed over: its tab was not found in Windows Terminal."
    assert not raised.value.closed
    assert "self" not in calls  # the session writing it itself never ran: it could not be closed


def test_a_failure_after_the_close_says_it_is_closed(calls, monkeypatch):
    def nothing(s):
        raise summarize.SummaryError("claude returned nothing.")

    monkeypatch.setattr(handover, "write_itself", nothing)
    session = one()
    with pytest.raises(actions.ActionError) as raised:
        actions.write_handover(session, "self", lambda: (session, [session]))
    assert raised.value.closed
    assert raised.value.words == ("shop was not handed over: claude returned nothing. "
                                  "It is closed now; Enter on it resumes it.")
    monkeypatch.setattr(handover, "from_transcript", lambda s: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(actions.ActionError, match="^shop was not handed over: disk full.$"):
        actions.write_handover(session, "transcript", lambda: (session, [session]))


# --- a whole team's handover ------------------------------------------------------------------------

def shop_team():
    return teams.new_team("shop", "C:/shop", "A checkout", teams.parse_members(
        "lead: leads @opus; api: endpoints; ui: screens", lead_ok=True))


def member(name, running=True, state="idle"):
    return Session(session_id=name, path=None, cwd="C:/shop", title=f"shop-{name}",
                   exit=Exit.RUNNING if running else Exit.CLEAN, live_state=state)


@pytest.fixture
def team_calls(monkeypatch):
    said: list = []
    monkeypatch.setattr(handover, "work_left", lambda team, members: said.append("left") or "a file not committed")
    monkeypatch.setattr(handover, "close", lambda s, open_ones, again="": said.append(("close", s.title, again)))
    monkeypatch.setattr(handover, "write_team", lambda lead, team, left: said.append(("write", lead.title, left))
                        or ("## Goal\nA checkout.", "api: endpoints @sonnet"))
    return said


def test_a_team_handed_over_closes_the_rest_then_its_lead_which_writes(team_calls):
    team = shop_team()
    members = {"lead": member("lead"), "api": member("api"), "ui": member("ui", running=False)}
    closed: list = []
    handed = actions.write_team_handover(team, members, lambda lead: (lead, list(members.values())),
                                         lambda names: closed.append(names) or ("closed", "information"))
    assert handed == actions.TeamHanded(members["lead"], "## Goal\nA checkout.", "api: endpoints @sonnet", True)
    assert closed == [["shop-api"]]  # only the open ones, never the lead's here
    assert team_calls[0] == "left"  # what git shows, read before anyone closes
    assert team_calls[1][:2] == ("close", "shop-lead") and "h twice again" in team_calls[1][2]
    assert team_calls[2] == ("write", "shop-lead", "a file not committed")


def test_no_line_up_suggested_keeps_the_teams_own(team_calls, monkeypatch):
    monkeypatch.setattr(handover, "write_team", lambda lead, team, left: ("## Goal", ""))
    team = shop_team()
    handed = actions.write_team_handover(team, {"lead": member("lead", running=False)}, lambda lead: pytest.fail("closed"))
    assert handed.lineup == handover.current_lineup(team) and not handed.suggested
    assert actions.write_team_handover(team, {}, lambda lead: pytest.fail("no lead")) is None  # no session yet
    peers = teams.new_team("pals", "C:/pals", "goal", teams.parse_members("a: x; b: y"), peers=True)
    assert peers.lead is None and actions.write_team_handover(peers, {}, lambda lead: pytest.fail("no lead")) is None


def test_a_tab_that_would_not_close_stops_the_team_handover(team_calls):
    team = shop_team()
    members = {"lead": member("lead"), "api": member("api")}
    with pytest.raises(actions.ActionError) as raised:
        actions.write_team_handover(team, members, lambda lead: (lead, []),
                                    lambda names: ("Could not find the tab of shop-api: close it yourself (/exit).", "warning"))
    assert raised.value.words == ("shop was not handed over: Could not find the tab of shop-api: close it yourself "
                                  "(/exit). Members closed on the way stay closed: s in the team's view starts them again.")
    assert not any(call[0] in ("close", "write") for call in team_calls if isinstance(call, tuple))


# --- opening sessions ---------------------------------------------------------------------------------

def test_how_a_closed_session_opens():
    from tabmancer.launch import Mode
    closed = replace(one(title="A rather long title that goes on well past the tab"), exit=Exit.CLEAN)
    launch = actions.launch_for(closed, Mode.RESUME, saved={})
    assert (launch.mode, launch.session_id, launch.cwd) == (Mode.RESUME, "a", "C:/work/shop")
    assert len(launch.title) == 48 and launch.title.endswith("…")
    background = replace(one(status=Status.BACKGROUND), exit=Exit.CLEAN)
    attached = actions.launch_for(background, Mode.RESUME, {"id": "job-7"}, {})
    assert (attached.mode, attached.session_id) == (Mode.ATTACH, "job-7")
    auto = actions.launch_for(closed, Mode.AUTO, saved={})
    assert auto.prompt  # told to carry on


def test_still_opening_counts_by_the_clock_it_is_given():
    assert actions.still_opening({"a": 100.0, "b": 50.0}, 120.0) == {"a"}


def opening(monkeypatch):
    from tabmancer import wrapped
    from tabmancer.launch import Launch, Mode
    noted = []
    monkeypatch.setattr(wrapped, "note_opened", noted.extend)
    resume = Launch(title="shop: Checkout", cwd="C:/s", mode=Mode.RESUME, session_id="a", cli="claude")
    fresh = Launch(title="web: New", cwd="C:/w", mode=Mode.NEW, session_id="", cli="claude")
    return noted, resume, fresh


def test_one_open_elsewhere_is_left_out_and_the_rest_open(monkeypatch):
    noted, resume, fresh = opening(monkeypatch)
    asked, tabs = [], []
    opened = actions.open_launches([resume, fresh], lambda: asked.append(1) or {"a"},
                                   lambda rest: tabs.append(rest) or [])
    assert asked == [1] and tabs == [[fresh]]
    assert opened.skipped == [resume] and opened.skipped_ids == {"a"}
    assert opened.skipped_words == "Open already, so not opened again: shop: Checkout."
    assert opened.words == "Opened web: New in a new tab." and opened.resumed_ids == set() and noted == []

    asked.clear()
    opened = actions.open_launches([fresh], lambda: asked.append(1) or set(), lambda rest: [])
    assert asked == []  # nothing resumes: the live sessions are not asked for


def test_a_resumed_one_is_counted_and_said(monkeypatch):
    noted, resume, fresh = opening(monkeypatch)
    opened = actions.open_launches([resume, fresh], set, lambda rest: [])
    assert opened.opened_ids == {"a", ""} and opened.resumed_ids == {"a"}
    assert opened.words == "Opened 2 sessions in new tabs." and noted == [("a", "resume")]


def test_a_terminal_that_will_not_start_or_open_tabs(monkeypatch):
    noted, resume, fresh = opening(monkeypatch)

    def broken(rest):
        raise OSError("wt.exe not found")
    assert actions.open_launches([fresh], set, broken).error == "Could not open the tab: wt.exe not found"
    left = actions.open_launches([resume, fresh], set, lambda rest: ["claude --resume a", "claude"])
    assert left.leftover == ["claude --resume a", "claude"] and left.opened == [] and noted == []


def test_which_did_not_come_up():
    shop, web = replace(one(), exit=Exit.CLEAN), replace(one("b"), exit=Exit.CLEAN, cwd="C:/work/web")
    sessions = {"a": shop, "b": web, "c": one("c")}
    assert actions.came_up_missing({"a"}, set(), sessions) == f"{shop.project} did not come up: its tab says why."
    assert actions.came_up_missing({"a", "b", "c"}, {"a"}, sessions) == "web did not come up: its tab says why."
    assert actions.came_up_missing({"c", "gone"}, set(), sessions) is None
    both = actions.came_up_missing({"a", "b"}, set(), sessions)
    assert both.endswith("did not come up: their tabs say why.")


def test_going_to_a_tab(monkeypatch):
    from tabmancer import focus
    monkeypatch.setattr(focus, "focus_tab", lambda title: True)
    session = one()
    assert actions.go_to_tab(session) == actions.Note(f"Switched to {session.project}. Type your prompt there.",
                                                       seconds=4)
    monkeypatch.setattr(focus, "focus_tab", lambda title: False)
    note = actions.go_to_tab(session)
    assert (note.severity, note.seconds) == ("warning", 8) and note.words.startswith("Could not find the tab of")


# --- a team's keys ------------------------------------------------------------------------------------

def test_a_team_start_and_when_there_is_nothing_to_start():
    team = shop_team()
    start = actions.start_team(team, {})
    assert [launch.title.partition(": ")[2] for launch in start.launches][-1] == "lead"  # the lead last
    everyone = {m.name: member(m.name) for m in team.members}
    assert actions.start_team(team, everyone) == actions.TeamStart([], "Every member is open already.")
    assert actions.start_team(team, everyone, set()).words == "Every member of shop is open already, or opening."


def test_a_team_opening_leaves_out_who_is_opening_already(monkeypatch):
    team = shop_team()
    launches = actions.start_team(team, {}).launches
    noted = []
    monkeypatch.setattr(teams, "opening", lambda now=None: {teams.session_name_of(team, launches[0])})
    monkeypatch.setattr(teams, "note_opening", noted.extend)
    opening = actions.team_opening(team, launches)
    assert opening.quiet == [] and opening.tabs == launches[1:] and len(noted) == len(launches) - 1
    monkeypatch.setattr(teams, "opening", lambda now=None: {teams.session_name_of(team, x) for x in launches})
    assert actions.team_opening(team, launches).words == "The members of shop are opening already."


def test_members_started_in_the_background(monkeypatch):
    team = shop_team()
    launches = actions.start_team(team, {}).launches[:2]
    monkeypatch.setattr(teams, "background_start", lambda team, launch: [launch.title])

    def run(argv):
        if argv[0].endswith("ui"):
            raise OSError("no claude")
    notes = actions.start_in_background(team, launches, run)
    names = [launch.title.partition(": ")[2] for launch in launches]
    assert notes[0] == actions.Note(f"Started {names[0]} of shop in the background. B lists them; T shows the team.",
                                    seconds=6)
    assert notes[1] == actions.Note(f"Could not start in the background: {names[1]}: no claude", "warning", 12)


def test_members_added_removed_and_the_goal_changed():
    teams.save({"shop": shop_team()})
    added = actions.add_members(shop_team(), "docs: the README; api: again")
    assert [m.name for m in added.new] == ["docs"] and "docs" in {m.name for m in added.team.members}
    assert added.warning == "Already on the team: api."
    assert added.joined_words == "docs joined shop: they start with the team (Enter on its row)."
    assert added.launches  # it introduces itself
    assert actions.add_members(added.team, "").new == []

    lead = next(m for m in added.team.members if m.lead)
    assert actions.removal_refusal(None).words == "Pick a member with ← → first."
    assert actions.removal_refusal(lead).severity == "warning"
    docs = next(m for m in added.team.members if m.name == "docs")
    assert actions.removal_refusal(docs) is None
    assert actions.removal_ask(docs, member("docs")).words == (
        "Press d again to take docs off the team. Its sessions stay. Its tab stays open, as an ordinary session.")
    removed = actions.remove_member(added.team, "docs")
    assert removed.done == "docs is off the team." and "docs" not in {m.name for m in removed.team.members}
    assert removed.note

    assert actions.change_goal(removed.team, "  ") is None
    assert actions.change_goal(removed.team, removed.team.goal) is None
    changed = actions.change_goal(removed.team, "A checkout and a cart")
    assert changed.team.goal == "A checkout and a cart" and changed.done == "The goal is changed." and changed.note


def test_who_hears_of_a_change(monkeypatch):
    sent = []
    monkeypatch.setattr(actions.teambus, "send", lambda *a, **kw: sent.append(a[1:3]))
    team = shop_team()
    told = actions.tell_lead(team, {}, "note", "Done.")
    assert told == actions.Told("Done. The lead is not open: it starts with the change in its role.", seconds=6)
    lead = member("lead")
    told = actions.tell_lead(team, {"lead": lead}, "note", "Done.")
    assert (told.clipboard, told.go_to, told.title, told.seconds) == ("note", lead, "Tell the lead", 12)
    bus = shop_team()
    bus.members[1].cli = "agy"
    told = actions.tell_lead(bus, {"lead": lead}, "note", "Done.")
    assert told.words == "Done. Sent the update to lead over the team bus." and not told.clipboard
    assert sent == [(teams.YOU, "lead")]


def test_peers_hear_of_a_change_each(monkeypatch):
    peers = teams.new_team("pair", "C:/shop", "A checkout", teams.parse_members("api: endpoints; ui: screens"),
                            peers=True)
    assert peers.lead is None
    told = actions.tell_lead(peers, {"api": member("api"), "ui": member("ui", running=False)}, "note", "Done.")
    assert told.go_to.session_id == "api" and told.clipboard == "note" and "(api; Ctrl+V, Enter)" in told.words
    assert actions.tell_lead(peers, {}, "note", "Done.").words == (
        "Done. Nobody is open: each starts with the change in its role.")


def test_a_team_closed():
    assert actions.closing_note(shop_team(), {"api": member("api", state="busy"), "ui": member("ui")}) == (
        "Press x again to close the 2 open members' tabs. 1 of them is working and would stop. Their sessions "
        "stay, and s starts them again.")
    assert actions.closing_note(shop_team(), {"api": None}) is None
    assert actions.closed_note(["shop-api"], lambda names: ("Could not close shop-api.", "warning")) == actions.Note(
        "Could not close shop-api.", "warning", 8)
    assert actions.closed_note(["shop-api"], lambda names: ("Closed 1.", "information")).seconds == 4


# --- slice 6: usage limits (c) and the queued handover (h) -------------------------------------------

def stopped(sid="s", resets=None, **kw) -> Session:
    """Closed at a usage limit."""
    return Session(session_id=sid, path=None, cwd=f"C:/work/{sid}", exit=Exit.CLEAN, status=Status.LIMIT,
                   resets=resets or datetime(2026, 11, 23, 16, 0), **kw)


def test_what_c_continues_and_when():
    early, late = datetime.now() - timedelta(minutes=5), datetime.now() + timedelta(hours=2)
    closed, waiting = stopped("x", early), one("y", resets=late)
    behind = one("z", resets=early, status=Status.BACKGROUND)  # at the limit, but in the background
    assert viewmodel.at_limit(waiting) and viewmodel.at_limit(behind)  # open and idle since: it waits for the limit
    assert actions.stopped_at_limit({"x": closed, "y": waiting, "z": behind, "a": one()}) == [closed, waiting]
    assert actions.continue_when([closed, waiting]) == "now"
    assert actions.continue_when([waiting]) == f"at {late:%H:%M}"

    limits = [usage.Limit("session", 40, None, ""), usage.Limit("week", 70, None, ""), usage.Limit("week Fable", 9,
                                                                                                    None, "")]
    assert actions.plan_words(limits, ["b@x has room"]) == ("Your plan now: session 40% · week 70%\nb@x has room",
                                                            True)
    assert actions.plan_words([usage.Limit("session", 100, None, "")], []) == ("Your plan now: session 100%", False)
    assert actions.plan_words([], []) == ("", False)


def test_the_c_dialogs_answers():
    a, b = stopped("a"), stopped("b", datetime(2026, 11, 23, 18, 0))
    armed = {"a": datetime(2026, 11, 23, 16, 0), "other": datetime(2026, 11, 24, 9, 0)}

    now = actions.continue_choice("now", ["a"], [a, b], armed, "now")
    assert (now.armed, now.go_on, now.hand_over, now.note, now.rows) == ({"other": armed["other"]}, [a], [], None,
                                                                          False)
    handed = actions.continue_choice("handover", ["a", "b"], [a, b], armed, "now")
    assert (handed.go_on, handed.hand_over, handed.armed) == ([], [a, b], {"other": armed["other"]})

    lift = actions.continue_choice("lift", ["a", "b"], [a, b], armed, "at 16:00")
    assert lift.armed == {"a": a.resets, "b": b.resets} and lift.rows  # the lot, as chosen: the rest no longer
    assert lift.note == actions.Note("2 sessions continue at 16:00, when the limit lifts. Leave the dashboard open.",
                                     seconds=8)
    assert actions.continue_choice("lift", ["b"], [a, b], {}, "now").note.words.startswith("1 session continues now,")

    off = actions.continue_choice("off", [], [a, b], armed, "now")
    assert off.armed == {} and off.rows and off.note.words == "Sessions at a limit will not continue by themselves."
    assert actions.continue_choice("later", ["a"], [a, b], armed, "now") is None


def test_which_armed_sessions_are_due():
    reset = datetime(2026, 11, 23, 16, 0)
    waiting, gone_on, again = stopped("w", reset), one("g", state="busy", resets=reset), stopped("n", reset)
    sessions = {"w": waiting, "g": gone_on, "n": again}
    armed = {"w": reset, "g": reset, "n": reset + timedelta(hours=3), "lost": reset}

    assert actions.due(armed, sessions, reset).gone == []  # not before the margin is past
    due = actions.due(armed, sessions, reset + actions.LIMIT_MARGIN)
    assert due.gone == ["w", "g", "lost"] and due.lifted == [waiting]  # one gone on by hand is left alone
    again.signed_in_again = True  # /login with another account: now, not at its reset
    assert actions.due(armed, sessions, reset + actions.LIMIT_MARGIN).lifted == [waiting, again]


def test_who_sets_a_session_to_continue_at_its_reset():
    reset = datetime.now() + timedelta(hours=2)
    waiting = stopped("w", reset)
    assert actions.arm_refusal(waiting, acts=True) is None
    assert actions.arm_refusal(waiting, acts=False) == actions.ARM_ELSEWHERE  # only the dashboard that acts
    assert actions.arm_refusal(one("b", state="busy", resets=reset), acts=True) == "not at a limit"
    assert actions.arm_refusal(replace(waiting, status=Status.BACKGROUND), acts=True) == "not at a limit"
    assert actions.armed_words(reset) == f"continues at {viewmodel.lifts(reset)}"
    for answer, refused in (("now", None), ("handover", None), ("lift", actions.ACTS_ELSEWHERE),
                            ("off", actions.ACTS_ELSEWHERE)):
        assert actions.answer_refusal(answer, acts=False) is refused and actions.answer_refusal(answer, True) is None
    assert actions.ACTS_ELSEWHERE.severity == "warning" and actions.ACTS_ELSEWHERE.seconds == 10
    assert actions.still_armed({"w": reset, "gone": reset}, {"w", "x"}) == {"w": reset}


def test_saving_the_sessions_to_continue_keeps_the_rest_of_config(monkeypatch):
    from tabmancer import config, store

    config.write_json("config.json", {"theme": "dusk"})
    assert actions.save_armed({"a": datetime(2026, 11, 23, 16, 0)})
    assert config.read_json("config.json") == {"theme": "dusk", "continue_at_reset": {"a": "2026-11-23T16:00:00"}}
    assert store.read_armed() == {"a": datetime(2026, 11, 23, 16, 0)}
    (config.config_dir() / "config.json").write_text("{not json", encoding="utf-8")
    assert not actions.save_armed({})  # left as it is
    assert (config.config_dir() / "config.json").read_text(encoding="utf-8") == "{not json"


def test_how_stopped_sessions_go_on(monkeypatch):
    closed, ready, busy = stopped("c"), one("r", resets=datetime.now()), one("b", state="busy", resets=datetime.now())
    launches, still_open = actions.continue_launches([closed, ready, busy], lambda s: actions.launch_for(s, Mode.RESUME))
    assert [launch.session_id for launch in launches] == ["c"] and still_open == [ready, busy]
    assert launches[0].mode is Mode.AUTO and launches[0].prompt == continue_prompt(Status.LIMIT)

    typed, noted = [], []
    monkeypatch.setattr(typein, "type_into", lambda sid, text: typed.append((sid, text)) or True)
    monkeypatch.setattr(wrapped, "note_opened", noted.extend)
    done = actions.type_continue(still_open, len(launches))
    assert typed == [("r", continue_prompt(Status.LIMIT))]  # a working one is not typed into
    assert noted == [("r", "limit")] and done.back == 2
    assert done.missed == actions.Note("Could not write in the tab of shop: type “continue” there yourself.",
                                       "warning", 15)
    assert actions.back_words(done.back) == "2 back at work"


def test_what_a_limit_check_finds_and_bit_says(monkeypatch):
    soon = datetime.now() + timedelta(hours=1)
    mine, theirs, free = stopped("mine", soon), stopped("theirs", soon), one("free")
    sessions = {"mine": mine, "theirs": theirs, "free": free}
    kept, at = actions.limited_now(sessions, {"theirs": "acct-b"}, "acct-a", "acct-a", lambda a, b: a == b)
    assert kept == {"mine": "acct-a", "theirs": "acct-b"} and at == {"mine", "theirs"}
    assert theirs.signed_in_again and not mine.signed_in_again  # stopped on another account than this one

    told = {"theirs"}
    assert actions.newly_stopped(sessions, at, told, {}) == [mine]
    assert actions.newly_stopped(sessions, at, told, {"mine": soon}) == []  # to continue by itself: not told

    said = actions.limit_say([mine], lambda when: "")
    assert said == actions.Say(f"mine: limit until {soon:%H:%M}. c continues then", "", 10)
    assert actions.limit_say([mine], lambda when: "b@x has room").seconds == 12
    assert actions.limit_say([theirs], lambda when: "") == actions.Say("theirs: signed in again. c continues now",
                                                                       "lifted")
    lifted = stopped("old", datetime.now() - timedelta(minutes=1))
    assert actions.limit_say([lifted], lambda when: "").words == "old: the limit has lifted. c continues now"


@pytest.fixture
def processes(monkeypatch):
    """Fake processes: pid -> (start time, command line); this one is pid 10."""
    table = {10: ("100", "tabmancer"), 20: ("200", "tabmancer monitor")}
    monkeypatch.setattr(actor, "process", lambda pid: table.get(pid))
    monkeypatch.setattr(actor, "_mine", {"pid": 10, "started": "100", "command": "tabmancer"})
    return table


def test_a_queued_handover_is_seen_only_while_its_window_runs(processes):
    from tabmancer import config

    assert actions.save_queued({})  # nothing anywhere: no file written
    assert not (config.config_dir() / actions.QUEUE).exists() and actions.read_queued() == {}
    assert actions.save_queued({"a": "self"})
    assert actions.read_queued() == {"a": "self"}

    other = {"pid": 20, "started": "200", "command": "tabmancer monitor"}
    saved = config.read_json(actions.QUEUE)
    config.write_json(actions.QUEUE, {**saved, "b": {"how": "transcript", "by": other}})
    assert actions.read_queued() == {"a": "self", "b": "transcript"}
    assert actions.save_queued({"c": "self"})  # this window's own replaced, the other window's kept
    assert actions.read_queued() == {"b": "transcript", "c": "self"}

    processes[20] = ("999", "tabmancer monitor")  # the same pid, started again since: another process
    assert actions.read_queued() == {"c": "self"}
    processes[20] = ("200", "notepad")  # the same pid and start time, another command line
    assert actions.read_queued() == {"c": "self"}
    del processes[20]
    assert actions.save_queued({"c": "self"}) and set(config.read_json(actions.QUEUE)) == {"c"}  # its entry gone

    processes[10] = ("101", "tabmancer")  # this window's pid, now someone else's: nothing of it stands
    assert actions.read_queued() == {}


def test_a_queue_file_that_is_not_a_queue(processes):
    from tabmancer import config

    for junk in ([1, 2], {"a": "self"}, {"a": {"how": "self"}}, {"a": {"how": 3, "by": actor.me()}}):
        config.write_json(actions.QUEUE, junk)
        assert actions.read_queued() == {}
    (config.config_dir() / actions.QUEUE).write_text("{half", encoding="utf-8")
    assert actions.read_queued() == {} and actions.save_queued({"a": "self"})
    assert actions.read_queued() == {"a": "self"}


def test_what_h_says_on_a_working_session_and_when_it_finishes():
    working = one(state="busy")
    assert actions.busy(working) and not actions.finished(working)
    assert actions.finished(one()) and actions.finished(replace(working, exit=Exit.CLEAN))
    assert not actions.busy(one())
    note = actions.queued_note(working, "self")
    assert (note.words, note.seconds, note.title) == (
        "shop is working: it is handed over as soon as it finishes (it writes its handover). h again cancels.", 10,
        "Handing over shop")
    assert f"(Claude ({handover.MODEL}) writes the handover)" in actions.queued_note(working, "transcript").words
    assert actions.unqueued_note(working) == actions.Note("shop will not be handed over when it finishes.", seconds=5)

    claims = []
    first = actions.queued_finished("a", one(), False, lambda what: claims.append(what) or True)
    assert first.hand_over and claims == ["handover-a"]
    assert (first.note.words, first.note.seconds, first.note.title) == (
        "shop finished: handing it over now.", 10, "Handing over shop")
    second = actions.queued_finished("a", one(), False, lambda what: False)
    assert not second.hand_over and second.note.words == "shop finished: another tabmancer window hands it over."
    closed = actions.queued_finished("a", replace(working, exit=Exit.CLEAN), True, lambda what: True)
    assert not closed.hand_over and closed.note.severity == "warning" and closed.note.words == (
        "shop closed before it finished: not handed over from here.")
    assert actions.queued_finished("a", replace(working, exit=Exit.CLEAN), False, lambda what: True).hand_over
