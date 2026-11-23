"""The team monitor (`tabmancer team watch`, M): a team on a screen of its own that only shows it and
does what you ask there, never the list, never anything by itself."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from rich.text import Text

from tabmancer import accounts, agy, claudeupdate, cli, config, teams, teamwatch, toast, usage, wrapped
from tabmancer import app as app_module
from tabmancer import viewmodel as VM
from tabmancer.app import SessionPicker
from tabmancer.launch import Launch, Mode
from tabmancer.monitorview import TeamMonitor, keys_line, picked_lines, quit_note, quota_line
from tabmancer.sessions import Exit


@pytest.fixture
def shop(tmp_path):
    team = teams.new_team("shop", str(tmp_path), "Add a checkout", teams.parse_members("api: the API; ui: the pages"))
    other = teams.new_team("docs", str(tmp_path), "Write the docs", teams.parse_members("writer: the guide"))
    teams.save({"shop": team, "docs": other})
    return team


async def settle(pilot, app, tries: int = 60) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if isinstance(app.screen, TeamMonitor) and app.screen.view is not None:
            return


@pytest.mark.parametrize("size", [(120, 30), (200, 50)])
async def test_the_monitor_is_all_there_is(fake_home, shop, size):
    app = SessionPicker(days=14, watch="shop")
    seen = []
    async with app.run_test(size=size) as pilot:
        await settle(pilot, app)
        seen.append(app.screen)
        assert isinstance(app.screen, TeamMonitor) and app.screen.team.name == "shop"
        assert app.screen.has_class("wide") is (size[0] >= 180)
        await pilot.press("escape")  # unpicks: the team view's Esc went back to the list
        await pilot.pause(0.1)
        seen.append(app.screen)
        app.show_monitor("docs")  # T, or a handover of another team: that team's monitor, in its place
        await settle(pilot, app)
        seen.append(app.screen)
        assert app.screen.team.name == "docs" and len(app.screen_stack) == 2
        assert app.watch == "docs"
    assert all(isinstance(screen, TeamMonitor) for screen in seen)


async def test_the_monitor_does_nothing_by_itself(fake_home, shop, monkeypatch):
    done = []
    monkeypatch.setattr(usage, "record", lambda limits: done.append("usage.record"))
    monkeypatch.setattr(accounts, "remember", lambda *a, **k: done.append("accounts.remember"))
    monkeypatch.setattr(accounts, "keep_limited", lambda kept: done.append("accounts.keep_limited"))
    monkeypatch.setattr(claudeupdate, "due", lambda *a, **k: True)
    monkeypatch.setattr(SessionPicker, "update_claude", lambda self: done.append("update_claude"))
    monkeypatch.setattr(SessionPicker, "check_updates", lambda self: done.append("check_updates"))
    monkeypatch.setattr(SessionPicker, "index_search", lambda self: done.append("index_search"))
    monkeypatch.setattr(wrapped, "due", lambda now=None: True)
    monkeypatch.setattr(toast, "show", lambda *a, **k: done.append("toast"))
    monkeypatch.setattr(agy, "_run_usage", lambda timeout: done.append("agy /usage"))
    config.write_json("config.json", {"continue_at_reset": {"one": "2026-11-20T10:00:00"}})
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        app.tell_wrapped = lambda: done.append("wrapped")  # not even on a timer
        app.check_limits()
        app.save_armed()
        await pilot.pause(0.2)
        assert not app.acts and app.armed == {}
    assert done == []
    assert app_module.read_json("config.json") == {"continue_at_reset": {"one": "2026-11-20T10:00:00"}}



@pytest.mark.parametrize("monitor_up", [True, False])
async def test_the_lists_keys_do_nothing_in_a_monitor(fake_home, shop, monkeypatch, monitor_up):
    """x, f, space, m are the list's: a monitor leaves hidden, pinned and ticked sessions as they are,
    with its screen up or before it is (the list underneath)."""
    if not monitor_up:
        monkeypatch.setattr(SessionPicker, "show_monitor", lambda self, name: None)
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app) if monitor_up else await pilot.pause(0.5)
        assert isinstance(app.screen, TeamMonitor) is monitor_up
        for action in ("hide", "pin", "toggle", "cycle_mode", "toggle_all", "summarize", "handover"):
            assert app.check_action(action, ()) is False, action
        for key in ("x", "f", "space", "m", "a"):
            await pilot.press(key)
        await pilot.pause(0.1)
        assert not app.marks.pinned and not app.marks.hidden and not app.selected


async def test_q_quits_but_a_handover_on_its_way_takes_a_second_one(fake_home, shop, monkeypatch):
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        monkeypatch.setattr(TeamMonitor, "handing_over",
                            lambda self: (["api's handover, queued for when it finishes"], []))
        exits = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(a))
        await pilot.press("q")
        await pilot.pause(0.1)
        assert exits == [] and isinstance(app.screen, TeamMonitor)
        assert any("q again quits" in str(n.message) for n in app._notifications)
        await pilot.press("q")
        await pilot.pause(0.1)
        assert exits == [(None,)]
        monkeypatch.setattr(TeamMonitor, "handing_over", lambda self: ([], []))
        app.screen.quitting = False
        await pilot.press("q")  # nothing on its way: one q
        await pilot.pause(0.1)
        assert exits == [(None,), (None,)]



async def test_q_during_a_handover_being_written_says_what_it_cuts(fake_home, shop, monkeypatch):
    """A thread handover outlives a q (the process waits for it), but the fresh session it opens at
    the end needs the app: the first q says so."""
    import threading

    from tabmancer import handover

    gate, ready = threading.Event(), []
    monkeypatch.setattr(handover, "from_transcript", lambda session: gate.wait(5) and "written")
    monkeypatch.setattr(SessionPicker, "handover_ready", lambda self, session, written, left_open="": ready.append(written))
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        exits = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(a))
        app.hand_over(replace(app.sessions["one"], exit=Exit.CLEAN), "transcript")
        await pilot.pause(0.1)
        assert app.screen.handing_over() == ([], ["a handover being written"])
        await pilot.press("q")
        await pilot.pause(0.1)
        notes = [str(n.message) for n in app._notifications]
        assert exits == [] and any("A handover being written still ends first (this tab waits for it), but what "
                                   "it would open next does not open." in note for note in notes)
        gate.set()
        await app.workers.wait_for_complete()
        assert ready == ["written"] and app.screen.handing_over() == ([], [])


def test_the_quit_note_names_both_kinds():
    assert quit_note(["api's handover, queued for when it finishes"], []) == (
        "q again quits, and loses api's handover, queued for when it finishes.")
    assert quit_note([], ["tabs being opened", "members starting in the background"]).startswith(
        "q again quits. Tabs being opened and members starting in the background still end first (this tab "
        "waits for them)")


async def test_a_queued_handover_of_a_member_closed_meanwhile_is_dropped(fake_home, shop, monkeypatch):
    handed = []
    monkeypatch.setattr(SessionPicker, "hand_over", lambda self, session, how: handed.append(session.session_id))
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        app.queued["one"] = "self"
        app.apply_live_updates({"one": replace(app.sessions["one"], exit=Exit.CLEAN)})
        await pilot.pause(0.1)
        assert handed == [] and "one" not in app.queued
        assert any("not handed over from here" in str(n.message) for n in app._notifications)


async def test_tabs_from_the_monitor_go_to_the_dashboards_window(fake_home, shop, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, window=None: opened.append(window) or [])
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        found = []
        monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: found.append(title) or True)
        app.open_sessions([teams.launch(shop, shop.members[0])])  # looked for off the screen's thread
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: False)
        app.open_sessions([teams.launch(shop, shop.members[1])])
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
    assert found == ["tabmancer"]  # the dashboard's tab, brought to the front first
    assert opened == ["0", "new"]  # its window; with no dashboard open, a new one



async def test_a_monitor_told_the_dashboards_window_opens_there_by_name(fake_home, shop, monkeypatch):
    opened, found = [], []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, window=None: opened.append(window) or [])
    monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: found.append(title) or True)
    app = SessionPicker(days=14, watch="shop", tabs_window="tabmancer")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        app.open_sessions([teams.launch(shop, shop.members[0])])
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
    assert opened == ["tabmancer"] and found == []  # no tab brought to the front first


@pytest.mark.parametrize(("tabs_window", "monitor", "goes"),
                         [("tabmancer", True, "tabmancer"), ("", False, "0"), ("", True, "tabmancer")])
async def test_what_opens_unasked_goes_to_the_dashboards_window(fake_home, monkeypatch, tabs_window, monitor, goes):
    """A session whose limit lifted opens in the dashboard's own window by name, never the one used
    last; with no name, the one used last unless a team's monitor is open (it may be that one): then
    a window named for it. The window you were in gets the keys back. A key press opens as before."""
    opened, found, given, looked = [], [], [], []
    monkeypatch.setattr(app_module.focus, "tab_open", lambda start, timeout=5: looked.append(start) or monitor)
    monkeypatch.setattr(app_module, "terminal_kind", lambda: "windows-terminal")
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, window=None: opened.append(window) or [])
    monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: found.append(title) or True)
    monkeypatch.setattr(app_module.focus, "foreground", lambda: 4242)
    monkeypatch.setattr(app_module.focus, "give_back", lambda hwnd, wait=3.0, timeout=20: given.append(hwnd) or True)
    app = SessionPicker(days=14, tabs_window=tabs_window)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        stopped = replace(app.sessions["one"], exit=Exit.CLEAN)
        app.continue_these([stopped], unasked=True)
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        app.continue_these([stopped])  # c, now: a key press
        await pilot.pause(0.1)
    assert opened == [goes, None]
    assert found == [] and given == [4242]  # unasked: never the dashboard's tab to the front
    assert looked == ([] if tabs_window else ["tabmancer · "])  # only looked for, with no name


async def test_a_monitor_never_brings_the_dashboard_up_for_what_opens_unasked(fake_home, shop, monkeypatch):
    opened, found = [], []
    monkeypatch.setattr(app_module, "terminal_kind", lambda: "windows-terminal")
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, window=None: opened.append(window) or [])
    monkeypatch.setattr(app_module.focus, "focus_tab", lambda title: found.append(title) or True)
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        app.open_unasked([teams.launch(shop, shop.members[0])])  # a queued handover's fresh session
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
    assert opened == ["new"] and found == []


async def test_a_queued_handovers_fresh_session_opens_unasked(fake_home, monkeypatch):
    from tabmancer import handover

    unasked, asked = [], []
    monkeypatch.setattr(handover, "fresh", lambda session, written: Launch("fresh", ".", Mode.NEW, ""))
    monkeypatch.setattr(SessionPicker, "open_unasked", lambda self, launches: unasked.append(len(launches)))
    monkeypatch.setattr(SessionPicker, "open_sessions", lambda self, launches, window=None: asked.append(len(launches)))
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        session = app.sessions["one"]
        app.unasked.add(session.session_id)  # as apply_live_updates marks a queued one
        app.handover_ready(session, "written")
        app.handover_ready(session, "written")  # h on one at rest: as before
    assert unasked == [1] and asked == [1] and not app.unasked


def test_the_dashboards_own_launcher_names_its_window(monkeypatch):
    from tabmancer import shortcuts

    monkeypatch.setattr(shortcuts, "_windows_terminal", lambda: "wt.exe")
    monkeypatch.setattr(shortcuts.shutil, "which", lambda name: {"tabmancer": "tabmancer.exe"}.get(name))
    assert shortcuts.picker_argv([]) == ["wt.exe", "-w", "tabmancer", "new-tab", "--title", "tabmancer",
                                         "tabmancer.exe", "--tabs-window", "tabmancer"]
    workspace = shortcuts.picker_argv(["open", "pcb"], title="pcb (Claude)")
    assert workspace[:3] == ["wt.exe", "-w", "new"] and "--tabs-window" not in workspace
    monkeypatch.setattr(teams, "_tabmancer_cmd", lambda: ("tabmancer",))
    line = teamwatch.wt_command("shop", "", None, tabs_window="tabmancer")
    assert line[-6:] == ["tabmancer", "--tabs-window", "tabmancer", "team", "watch", "shop"]


async def test_the_dashboard_still_opens_tabs_as_before(fake_home, monkeypatch):
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.append(len(launches)) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        assert app.acts
        app.open_sessions([app.launch_for(app.sessions["one"], {})])
    assert opened == [1]


async def test_m_opens_a_monitor_once_and_the_second_press_waits(fake_home, shop, monkeypatch):
    asked = []
    monkeypatch.setattr(teamwatch, "open_monitor", lambda name, folder="", tabs_window="": asked.append(name) or "opens")
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        app.action_monitor("shop")
        app.action_monitor("shop")  # a second M while the window is on its way
        app.action_monitor("docs")
        await pilot.pause(0.3)
        app.action_monitor("")  # on a row of no team: said, nothing opened
        await pilot.pause(0.1)
    assert sorted(asked) == ["docs", "shop"]



async def test_a_slow_monitor_window_is_not_opened_twice(fake_home, shop, monkeypatch):
    """Windows Terminal starting cold takes longer than the few seconds M waits: still one window."""
    import threading

    asked, gate = [], threading.Event()
    monkeypatch.setattr(teamwatch, "open_monitor", lambda name, folder="", tabs_window="": asked.append(name) or gate.wait(5) and "")
    monkeypatch.setattr(app_module, "MONITOR_AGAIN", 0.0)
    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        app.action_monitor("shop")
        await pilot.pause(0.3)
        app.action_monitor("shop")  # long after the wait, the first still opening
        assert asked == ["shop"]
        gate.set()
        await app.workers.wait_for_complete()
        app.action_monitor("shop")  # done: M again brings it up (focus_tab, inside open_monitor)
        await app.workers.wait_for_complete()
    assert asked == ["shop", "shop"]



async def test_m_in_the_monitor_does_nothing(fake_home, shop, monkeypatch):
    asked = []
    monkeypatch.setattr(teamwatch, "open_monitor", lambda name, folder="", tabs_window="": asked.append(name) or "")
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        assert app.screen.check_action("monitor", ()) is False
        await pilot.press("M")
        await pilot.pause(0.2)
    assert asked == []


async def test_a_new_team_picked_at_the_start_says_where_and_asks_again(fake_home, shop):
    from tabmancer.teamview import TeamsDialog

    app = SessionPicker(days=14, watch="")
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if isinstance(app.screen, TeamsDialog):
                break
        await pilot.press("n")  # a new team: not made here
        await pilot.pause(0.2)
        assert isinstance(app.screen, TeamsDialog) and app.is_running
        assert any("made in the dashboard" in str(n.message) for n in app._notifications)
        await pilot.press("enter")  # the first team: its monitor
        await settle(pilot, app)
        assert isinstance(app.screen, TeamMonitor) and app.screen.team.name == "shop"


def test_the_monitors_window_runs_tabmancer_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(teams, "_tabmancer_cmd", lambda: ("tabmancer",))
    line = teamwatch.wt_command("shop;now", str(tmp_path), (-1920, 0))
    assert line[:3] == ["wt.exe", "-w", "tabmancer-watch-shop-now"]
    assert line[3:5] == ["--pos", "-1880,40"] and "--maximized" in line
    assert line[line.index("--title") + 1] == r"tabmancer · shop\;now"
    assert line[-4:] == ["tabmancer", "team", "watch", r"shop\;now"]  # no shell around it: q closes the tab
    assert not any(word in line for word in ("pwsh.exe", "powershell.exe", "-Command", "--"))
    assert "--suppressApplicationTitle" not in line  # T retitles the tab, and M finds it by the new name
    assert "--pos" not in teamwatch.wt_command("shop", "", None)


def test_d_with_no_dashboard_opens_it_in_a_window_of_its_own(monkeypatch):
    started = []
    monkeypatch.setattr(teamwatch, "terminal_kind", lambda: "windows-terminal")
    monkeypatch.setattr(teamwatch.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(teams, "_tabmancer_cmd", lambda: ("tabmancer",))
    assert teamwatch.open_dashboard("tabmancer") == "The dashboard opens in a new tab."
    assert started[0][:3] == ["wt.exe", "-w", "tabmancer"]  # named ("0" would be the monitor's own window)
    assert started[0][-3:] == ["tabmancer", "--tabs-window", "tabmancer"]  # and the dashboard told the name
    monkeypatch.setattr(teamwatch.focus, "focus_tab", lambda title: True)
    assert teamwatch.open_dashboard("tabmancer") == "" and len(started) == 1


def test_team_watch_picks_the_team(monkeypatch, capsys, shop):
    assert cli.watched_team("SHOP") == ("shop", "")
    assert cli.watched_team("nope")[1].startswith("There is no team nope")
    monkeypatch.setattr(teams, "live_names", lambda: {shop.session_name(shop.members[0]): {}})
    assert cli.watched_team("") == ("shop", "")  # the one team with members open
    monkeypatch.setattr(teams, "live_names", dict)
    assert cli.watched_team("") == ("", "")  # none or several: the monitor asks with the list
    teams.save({})
    assert cli.team_watch("", 14) == 1
    assert "no team yet" in capsys.readouterr().err



def test_the_monitor_leaves_claude_update_to_the_dashboard(monkeypatch, shop):
    """A tab opened from the monitor never waits for (or races) the dashboard's claude update."""
    claudeupdate.set_enabled(True)
    ran = []
    monkeypatch.setattr(claudeupdate, "run", lambda claude=None, timeout=0: ran.append("update"))

    class Picker:
        def __init__(self, days, watch=None, tabs_window=""):
            assert watch == "shop"

        def run(self):
            return None

    monkeypatch.setattr(app_module, "SessionPicker", Picker)
    monkeypatch.setattr("tabmancer.sixel.probe", lambda: None)
    assert claudeupdate.due()
    assert cli.team_watch("shop", 14) == 0
    assert not claudeupdate.due() and claudeupdate.before_opening() is None and ran == []



def test_the_monitor_takes_the_dashboards_fresh_reading(fake_home):
    now = datetime(2026, 11, 20, 12, 0)
    assert usage.latest(now=now) is None  # nothing recorded yet
    usage.record([usage.Limit("session", 40, datetime(2026, 11, 20, 14, 0), "2pm")], now=now - timedelta(minutes=4))
    (limit,) = usage.latest(now=now)
    assert (limit.label, limit.percent, limit.resets) == ("session", 40, datetime(2026, 11, 20, 14, 0))
    assert usage.latest(now=now + timedelta(minutes=12)) is None  # too old: read again


async def test_a_monitor_reads_neither_usage_nor_other_folders_twice(fake_home, shop, monkeypatch, tmp_path):
    read, folders = [], []
    usage.record([usage.Limit("session", 40, None, "")])
    monkeypatch.setattr(usage, "read_all", lambda: read.append(1) or [])
    monkeypatch.setattr(app_module.gitstate, "read_all", lambda found: folders.append(list(found)) or ({}, set()))
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        app.refresh_usage()
        app.refresh_git()
        await app.workers.wait_for_complete()
        assert read == [] and [lim.percent for lim in app.limits] == [40]
    outside = [s.cwd for s in app.sessions.values() if s.cwd and not teams.in_team_folder(shop, s.cwd)]
    assert outside and folders and not any(f in outside for found in folders for f in found)


def test_the_quota_beside_the_name():
    limit = usage.Limit("session", 91, None, "")
    now = datetime(2026, 11, 20, 12, 0)
    chain = [{"name": "Argon", "left": 0.0, "back": datetime(2026, 11, 25, 10, 2)},
             {"name": "Hydrogen", "left": 31.0, "back": None}, {"name": "Flash", "left": None, "back": None}]
    shown = quota_line([limit], {"read_at": now - timedelta(minutes=5), "chain": chain}, True, now).plain
    assert shown == "Claude session 91%   agy Argon out till Wed 10:02 · Hydrogen 31% · Flash ?"
    assert quota_line([limit], {"read_at": now - timedelta(minutes=5), "chain": chain}, False, now).plain == \
        "Claude session 91%"  # no agy member: no agy
    assert quota_line([], {"read_at": now - agy.STALE - timedelta(minutes=1), "chain": chain}, True, now).plain == \
        "agy quota read 09:59"
    assert quota_line([], {"read_at": None, "chain": []}, True, now).plain == "agy quota not read"


def test_the_picked_lines_are_cut_with_an_ellipsis(shop):
    said = teams.Message(sender="api", receiver="ui", text="a long message " * 20, at=datetime(2026, 11, 20, 9, 5))
    state = teams.TeamState(shop, {}, [said])
    view = VM.team_view(state, {}, {}, bus=({}, {}))
    lines = picked_lines(view, "api", state, {}, 60).plain.split("\n")
    assert len(lines) == 2 and all(len(line) <= 60 for line in lines)
    assert lines[1].startswith("last 09:05") and lines[1].endswith("…")
    everyone = picked_lines(view, None, state, {}, 60).plain
    assert everyone.startswith("everyone")


def test_the_keys_fit_two_rows_at_120():
    shown = keys_line(116)
    assert isinstance(shown, Text) and len(shown.plain.split("\n")) == 2


def test_the_question_mark_lists_every_key_of_the_monitor():
    from tabmancer.monitorview import MONITOR_KEYS

    listed = {action for _, rows in MONITOR_KEYS for _, action, _ in rows if action}
    bound = {b.action for b in [*TeamMonitor.BINDINGS, *TeamMonitor.__mro__[1].BINDINGS]}
    assert bound - listed <= {"close", "pick(-1)", "pick(1)", "monitor", "keys"}  # ← → and ? are listed by key


async def test_j_starts_at_the_first_who_needs_you_and_again_when_that_changes(fake_home, shop):
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        monitor = app.screen
        view = monitor.view
        names = [m.name for m in monitor.graph.order]

        def waiting(*who):
            monitor.view = replace(view, members=[replace(m, needs_you=m.name in who) for m in view.members])

        def picked():
            return monitor.graph.selected_member().name

        waiting("api", "ui")
        for expected in ("api", "ui", "api"):
            await pilot.press("j")
            assert picked() == expected
        waiting("ui", "lead")  # another set: from its first again, in the team's order
        await pilot.press("j")
        assert picked() == min(("ui", "lead"), key=names.index)


def test_a_member_on_the_bus_says_whether_it_listens(shop):
    from tabmancer.monitorview import _facts

    member = VM.MemberView("ui", False, "the pages", "s1", "idle", "idle", "done", cli="agy",
                           bus={"runner": False, "listening": True, "since": datetime(2026, 11, 20, 14, 28)})
    assert "bus: listening since 14:28" in [words for words, _ in _facts(member)]
    member.bus = {"runner": True, "listening": False, "since": None}
    assert "bus: runner alive" in [words for words, _ in _facts(member)]
    member.bus = {"runner": False, "listening": False, "since": None}
    assert "bus: not listening" in [words for words, _ in _facts(member)]
    member.state = "not-started"  # nothing to say of one not started
    assert not any(words.startswith("bus") for words, _ in _facts(member))


def test_the_wide_table_marks_the_picked_member(shop):
    from rich.console import Console

    from tabmancer.monitorview import members_table

    view = VM.team_view(teams.TeamState(shop, {}, []), {}, {}, bus=({}, {}))
    console = Console(width=100, record=True)
    console.print(members_table(view, {}, "api"))
    rows = console.export_text().splitlines()
    assert any(row.lstrip().startswith("▶ api") for row in rows)
    assert not any("▶" in row for row in rows if "api" not in row)


async def test_m_is_in_the_footer_at_120_and_it_still_takes_two_rows(fake_home):
    from textual._context import visible_screen_stack

    from tabmancer.widgets import KeysFooter

    app = SessionPicker(days=14)
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if app.sessions:
                break
        await pilot.pause(0.3)
        assert app.query_one(KeysFooter).size.height == 2
        visible_screen_stack.set([])
        screen = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        assert "M Monitor" in screen and "q Quit" in screen
        assert app.check_action("monitor", ()) is None  # a session of no team: dimmed, still there
