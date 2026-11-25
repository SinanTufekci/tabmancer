"""The command palette's commands (commands.py): every key of keys.py, named as the menus name it, greyed
where the window does not do it, and found by a few of its words; the jumps to a team, a session, a member."""

from __future__ import annotations

from datetime import datetime, timedelta

from tabmancer import commands
from tabmancer import viewmodel as VM
from tabmancer.keys import KEYS, MONITOR_KEYS
from tabmancer.sessions import Exit, Session, Status

ELSEWHERE = "In the terminal dashboard for now: tabmancer"


def listed(keys) -> list[tuple[str, str]]:
    return [(key, words) for _group, entries in keys for key, _action, words in entries]


def test_every_key_is_a_command_named_as_in_the_menus():
    for keys in (KEYS, MONITOR_KEYS):
        made = commands.commands(keys, set(), ELSEWHERE)
        assert [(c.key, c.said) for c in made] == listed(keys)  # each, once, in keys.py's order
        assert all(c.kind == "key" and c.words == commands.short(c.said) for c in made)
    made = {c.key: c for c in commands.commands(KEYS, set(), ELSEWHERE)}
    assert made["h"].group == "Work with a session" and made["h"].words.startswith("Hand it over")


def test_what_the_window_does_not_do_is_greyed_and_says_where_it_is_done():
    made = {c.key: c for c in commands.commands(KEYS, {"?", "q", "T"}, ELSEWHERE)}
    assert made["T"].enabled and not made["T"].why
    assert not made["h"].enabled and made["h"].why == ELSEWHERE
    first = "Pick a member first"
    alone = {c.key: c for c in commands.commands(MONITOR_KEYS, {"Enter", "j"}, "elsewhere", needs_pick={"Enter"},
                                                 picked=False, pick_first=first)}
    assert not alone["Enter"].enabled and alone["Enter"].why == first  # it acts on the picked member
    assert alone["j"].enabled
    picked = {c.key: c for c in commands.commands(MONITOR_KEYS, {"Enter"}, "elsewhere", needs_pick={"Enter"},
                                                  picked=True, pick_first=first)}
    assert picked["Enter"].enabled


def test_a_few_words_find_the_command():
    made = commands.commands(KEYS, {"?", "q", "T"}, ELSEWHERE)
    assert commands.find(made, "hand")[0].key == "h"  # Hand over first, greyed or not
    assert commands.find(made, "T")[0].key == "T" and commands.find(made, "t")[0].key == "t"  # the key as typed
    assert commands.find(made, "WRAP")[0].key == "W"  # case does not matter in the words
    assert commands.find(made, "hid ses")[0].key == "x"  # each word starts a word of its name
    assert commands.find(made, "zzqq") == []
    assert [c.key for c in commands.find(made, "")] == [key for key, _ in listed(KEYS)]  # nothing typed: every key
    found = [c.key for c in commands.find(made, "members")]
    assert found.index("E") < found.index("Enter")  # in its name before in what it does in full
    assert commands.in_order("hndvr", "hand it over") and not commands.in_order("rh", "hand it over")


def made_session(sid: str, title: str, cwd: str, prompt: str = "") -> Session:
    return Session(session_id=sid, path=None, cwd=cwd, status=Status.RUNNING, exit=Exit.RUNNING, live_state="idle",
                   last_active=datetime.now() - timedelta(minutes=3), title=title, first_prompt=prompt)


def test_teams_sessions_and_members_are_jumps_found_by_name_or_where_they_are():
    sessions = {"s1": made_session("s1", "Rate limiting for the public API", "C:/work/api-server"),
                "s2": made_session("s2", "Fix the login redirect loop", "C:/work/web", prompt="the handshake fails")}
    view = VM.build(sessions, hidden=set(), pinned=set(), git={}, team_states={}, tokens={}, limits=[], armed={},
                    queued={})
    jumps = commands.session_jumps(view.sessions)
    assert {(c.kind, c.target, c.words) for c in jumps} == {("session", "s1", "Rate limiting for the public API"),
                                                             ("session", "s2", "Fix the login redirect loop")}
    team = VM.TeamView("shop", "shop", "Add Stripe checkout", 2, 0, [], [], [])
    entries = commands.commands(KEYS, set(), ELSEWHERE) + commands.team_jumps([team]) + jumps
    assert commands.find(entries, "rate lim")[0].target == "s1"
    assert commands.find(entries, "shop")[0] == commands.Command("team", "shop", "Add Stripe checkout",
                                                                 group="shop", target="shop")
    assert commands.find(entries, "team sh")[0].target == "shop"  # its kind is a word of it too
    found = commands.find(entries, "sho")  # the team and the keys that "Show ..." all start with it
    assert found[0].target == "shop" and found[1].kind == "key" and found[1].words.startswith("Show")
    assert commands.find(entries + [commands.Command("team", "T")], "T")[0].key == "T"  # the key as typed first
    assert [c.target for c in commands.find(entries, "api-serv")] == ["s1"]  # by its project
    by_prompt = commands.find(entries, "handshake", sessions)  # by what was asked in it, as / finds it
    assert [c.target for c in by_prompt if c.kind == "session"] == ["s2"]
    assert "s2" not in [c.target for c in commands.find(entries, "handshake")]  # without the sessions: names only
    rows = VM.project_rows(list(sessions.values()))
    projects = commands.project_jumps(rows[1:])  # every project but All projects
    assert projects[0] == commands.Command("project", "api-server", "1 session in sight", target=rows[1].key)
    assert commands.find(entries + projects, "project web")[0] == projects[1]  # its kind is a word of it too
    member = VM.MemberView("api", False, "endpoints", "s1", "working", "working", "working")
    found = commands.member_jumps([member])
    assert found == [commands.Command("member", "api", "working · endpoints", group="member", target="api")]
    assert commands.find(found, "endp")[0].target == "api"  # by its part in the team


def test_a_menu_names_a_key_by_its_first_words():
    assert commands.short("take the picked member off the team (twice); its sessions stay") == \
        "Take the picked member off the team"
    assert commands.short("add members") == "Add members"
