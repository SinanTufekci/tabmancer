"""The one-team store (G1a): one team as the dashboard reads it, without a screen, for the desktop
monitor. It reads the team's own sessions only, as often as the dashboard does, and keeps nothing a
dashboard keeps (no limits recorded, no agy run, no actor lock). Made-up data only."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from test_team_tree import peer, send, shop, transcript

from tabmancer import accounts, actor, agy, gitstate, marks, sessions, shutdowns, store, teams, tokens, usage
from tabmancer import viewmodel as VM
from tabmancer.config import config_dir
from tabmancer.sessions import Exit, Session
from tabmancer.store import DashboardStore, Snapshot, TeamStore


class Clock:
    """The store's clock, moved on by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def quiet(monkeypatch):
    """No git, no plan limits asked: a test that wants them says so."""
    monkeypatch.setattr(gitstate, "read_all", lambda folders: ({}, set()))
    monkeypatch.setattr(usage, "read_all", list)


@pytest.fixture(autouse=True)
def no_shutdown_log(monkeypatch):
    """Never the system's own log of shutdowns (wevtutil, journalctl, last): a test that wants one says so."""
    monkeypatch.setattr(shutdowns, "recent_shutdowns", lambda limit=30: [])


def members(snap: Snapshot) -> dict:
    return {m.name: m for m in snap.view.members}


def test_a_poll_shows_the_team_as_the_dashboard_does(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    snap = TeamStore("shop").poll()

    assert snap is not None and snap.view.name == "shop" and snap.view.lead == "lead"
    by = members(snap)
    assert by["lead"].session == "lead-1" and by["lead"].needs_you  # open, its turn over: yours
    assert by["api"].session == "api-1" and by["api"].tone == "working" and not by["api"].needs_you
    assert by["ui"].session == "ui-1" and not by["ui"].needs_you  # closed
    assert snap.view.open == 2
    assert [(m.sender, m.receiver) for m in snap.state.between_members()] == [("lead", "api"), ("api", "lead")]
    assert {w["from"] for w in snap.view.wires} == {"lead", "api"}


def test_it_keeps_the_team_folder_only(fake_home, tmp_path, monkeypatch, quiet):
    """The open session "one" in another folder is read once, then left alone till a full poll."""
    shop(fake_home, tmp_path, monkeypatch)
    kept = TeamStore("shop")
    kept.poll()
    assert set(kept.sessions) == {"lead-1", "api-1", "ui-1"}
    assert "one" in kept.elsewhere


def test_a_member_other_than_the_lead_done_for_now_rests(fake_home, tmp_path, monkeypatch, quiet):
    """Idle, it waits for its lead, not for you; the lead idle waits for you."""
    shop(fake_home, tmp_path, monkeypatch)
    live = [{"sessionId": "lead-1", "kind": "interactive", "status": "idle"},
            {"sessionId": "api-1", "kind": "interactive", "status": "idle"}]
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    by = members(TeamStore("shop").poll())
    assert (by["api"].state, by["api"].tone, by["api"].needs_you) == ("idle", "done", False)  # as the list says it
    assert (by["lead"].state, by["lead"].needs_you) == ("waiting", True)


def test_peers_have_no_resting_member(fake_home, tmp_path, monkeypatch, quiet):
    """Nobody leads: each member idle waits for you."""
    made = shop(fake_home, tmp_path, monkeypatch)
    peers = teams.new_team("shop", made.cwd, made.goal, made.members, peers=True)
    peers.created = made.created
    teams.save({"shop": peers})
    live = [{"sessionId": "api-1", "kind": "interactive", "status": "idle"}]
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    by = members(TeamStore("shop").poll())
    assert by["api"].state == "waiting" and by["api"].needs_you


def test_a_member_that_closed_is_seen_closed_at_the_next_full_poll(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    kept = TeamStore("shop")
    assert kept.poll().view.open == 2
    live = [{"sessionId": "lead-1", "kind": "interactive", "status": "idle"}]  # api closed its tab
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    for _ in range(store.FULL_EVERY - 1):  # quick polls read the pid files: none here, so nothing changes
        assert kept.poll().view.open == 2
    snap = kept.poll()  # the full one
    assert snap.view.open == 1 and members(snap)["api"].tone != "working"


def test_a_message_said_since_shows_at_the_next_poll(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    kept = TeamStore("shop")
    kept.poll()
    folder = tmp_path / "shop"
    transcript(fake_home, folder, "lead-1", "shop-lead", [send("shop-api", "make the endpoint", 9),
                                                         peer("shop-api", "endpoint done", 2),
                                                         send("shop-ui", "draw the cart", 1)])
    snaps = [kept.poll() for _ in range(store.FULL_EVERY)]
    snap = snaps[-1]
    assert ("lead", "ui") in [(m.sender, m.receiver) for m in snap.state.said]
    assert members(snap)["ui"].last is not None and members(snap)["ui"].last.text == "draw the cart"


def test_no_team_no_snapshot(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    kept = TeamStore("shop")
    assert kept.poll() is not None
    teams.save({})
    assert kept.poll() is None
    assert TeamStore("nobody").poll() is None


def test_it_reads_tokens_git_and_limits_when_due_only(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    calls = {"tokens": 0, "git": 0, "limits": 0}

    def collect(days=14):
        calls["tokens"] += 1
        return tokens.Report(first=datetime.now().date(), days=14)

    def read_all(folders):
        calls["git"] += 1
        return {}, set()

    def limits():
        calls["limits"] += 1
        return []

    monkeypatch.setattr(tokens, "collect", collect)
    monkeypatch.setattr(gitstate, "read_all", read_all)
    monkeypatch.setattr(usage, "read_all", limits)
    clock = Clock()
    kept = TeamStore("shop", clock=clock)
    for _ in range(5):
        kept.poll()
        clock.now += 2
    assert calls == {"tokens": 1, "git": 1, "limits": 1}
    clock.now += store.TOKENS_SECONDS
    kept.poll()
    assert calls == {"tokens": 2, "git": 2, "limits": 1}
    clock.now += store.USAGE_SECONDS
    kept.poll()
    assert calls["limits"] == 2


def test_git_is_read_at_once_for_a_member_in_a_new_folder(fake_home, tmp_path, monkeypatch):
    shop(fake_home, tmp_path, monkeypatch)
    asked: list[list[str]] = []

    def read_all(folders):
        asked.append(list(folders))
        return {sessions.folder_key(f): gitstate.GitState(branch="main", changed=2, ahead=1) for f in folders}, set()

    monkeypatch.setattr(gitstate, "read_all", read_all)
    monkeypatch.setattr(usage, "read_all", list)
    clock = Clock()
    kept = TeamStore("shop", clock=clock)
    snap = kept.poll()
    assert members(snap)["lead"].git == {"branch": "main", "changed": 2, "ahead": 1}
    kept.poll()
    assert len(asked) == 1  # nothing new, and a minute is not up
    worktree = tmp_path / "shop" / ".claude" / "worktrees" / "ui"
    worktree.mkdir(parents=True)
    transcript(fake_home, worktree, "ui-2", "shop-ui", [])
    live = [{"sessionId": "lead-1", "kind": "interactive", "status": "idle"},
            {"sessionId": "ui-2", "kind": "interactive", "status": "busy"}]
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: live)
    snap = [kept.poll() for _ in range(store.FULL_EVERY)][-1]
    assert members(snap)["ui"].session == "ui-2"
    assert len(asked) == 2 and str(worktree) in asked[-1]
    assert members(snap)["ui"].git == {"branch": "main", "changed": 2, "ahead": 1}


def test_limits_are_the_dashboards_reading_and_never_recorded(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    usage.record([usage.Limit("session", 42, None, ""), usage.Limit("week", 18, None, "")])  # the dashboard's, a moment ago

    def no_record(*args, **kwargs):
        raise AssertionError("a watcher never records the plan limits")

    def no_ask():
        raise AssertionError("a fresh reading of the dashboard's is not asked again")

    monkeypatch.setattr(usage, "record", no_record)
    monkeypatch.setattr(usage, "read_all", no_ask)
    snap = TeamStore("shop").poll()
    assert [(limit.label, limit.percent) for limit in snap.limits] == [("session", 42), ("week", 18)]


def test_without_a_fresh_reading_it_asks_unless_told_not_to(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(usage, "read_all", lambda: [usage.Limit("session", 7, None, "")])
    assert [limit.percent for limit in TeamStore("shop").poll().limits] == [7]
    assert TeamStore("shop", ask_limits=False).poll().limits == []


def test_it_never_claims_the_actor_lock_nor_runs_agy(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)

    def no(*args, **kwargs):
        raise AssertionError("not from a watcher")

    monkeypatch.setattr(actor, "claim", no)
    monkeypatch.setattr(agy, "_run_usage", no)
    monkeypatch.setattr(agy, "refresh_quota", no)
    clock = Clock()
    kept = TeamStore("shop", clock=clock)
    for _ in range(store.FULL_EVERY + 1):
        kept.poll()
        clock.now += store.USAGE_SECONDS


def test_an_agy_member_carries_the_chain_from_the_saved_quota(fake_home, tmp_path, monkeypatch, quiet):
    made = shop(fake_home, tmp_path, monkeypatch)
    made.members += teams.parse_members("docs: the guide @agy")
    teams.save({"shop": made})
    monkeypatch.setattr(agy, "installed", lambda: True)
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    agy._save_quota({"read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "rows": [["Gemini Models", "Weekly Limit Remaining", "72%", soon]], "out": {}, "unreadable": ""})
    snap = TeamStore("shop").poll()

    docs = members(snap)["docs"]
    neon, hydrogen = agy.DEFAULT_CHAIN
    assert docs.cli == "agy" and docs.model == neon and docs.quota["left"] == 72
    assert [standing["model"] for standing in snap.agy_quota["chain"]] == [neon, hydrogen]
    assert snap.agy_quota["pick"] == neon


def test_an_agy_session_takes_its_live_state_from_agy(monkeypatch):
    """agy has no pid files: what it does now comes from its own db, as the dashboard's poll asks it."""
    asleep = Session(session_id="a1", path=None, cwd="C:/work/shop", title="shop-docs", exit=Exit.CLEAN, cli="agy")
    claude = Session(session_id="c1", path=None, cwd="C:/work/shop", title="shop-api", exit=Exit.CLEAN)
    monkeypatch.setattr(agy, "live_state", lambda sid, now=None: "busy" if sid == "a1" else "")
    fresh = store.fresh_sessions({"a1": asleep, "c1": claude}, {}, full=False)
    assert set(fresh) == {"a1"}  # only what changed
    assert (fresh["a1"].exit, fresh["a1"].live_state) == (Exit.RUNNING, "busy")


def test_a_team_without_agy_has_no_agy_quota(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    monkeypatch.setattr(agy, "installed", lambda: True)
    assert TeamStore("shop").poll().agy_quota == {}


def test_team_hours_counts_the_teams_sessions_today_only():
    now = datetime(2026, 11, 23, 15, 30)

    def at(hour: int, day: int = 23) -> int:
        return int(datetime(2026, 11, day, hour, 10).timestamp())

    def used(count: int) -> tokens.Tokens:
        return tokens.Tokens(input=count)

    report = tokens.Report(first=now.date(), days=1, events=[
        tokens.Event("lead-1", at(22, day=22), "m", used(500), [], []),  # yesterday
        tokens.Event("lead-1", at(9), "m", used(100), [], []),
        tokens.Event("other", at(10), "m", used(900), [], []),  # in no team
        tokens.Event("api-1", at(14), "m", used(30), [], []),
        tokens.Event("lead-1", at(14), "m", used(20), [], []),
    ])
    hours = store.team_hours(report, {"lead-1", "api-1"}, now=now)
    assert len(hours) == 16 and hours[9] == 100 and hours[10] == 0 and hours[14] == 50 and sum(hours) == 150


def test_the_store_loads_no_ui():
    """A front end of any kind builds on it: neither Textual, Rich nor Qt comes with it."""
    code = ("import sys, tabmancer.store; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


async def test_the_store_and_the_terminal_monitor_show_the_same_team(fake_home, tmp_path, monkeypatch, quiet):
    """The seam: what the desktop monitor will draw is what `tabmancer team watch` draws."""
    import time as clock

    from tabmancer.app import SessionPicker
    from tabmancer.monitorview import TeamMonitor

    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14, watch="shop")
    async with app.run_test(size=(200, 50)) as pilot:
        end = clock.monotonic() + 20
        while clock.monotonic() < end:
            await pilot.pause(0.05)
            screen = app.screen
            if isinstance(screen, TeamMonitor) and screen.view is not None and screen.view.open == 2:
                break
        assert isinstance(app.screen, TeamMonitor) and app.screen.view is not None
        tui = app.screen.view
    assert tui.open == 2  # the live poll landed: not two empty views alike
    ours = TeamStore("shop").poll().view

    def facts(view):
        return [(m.name, m.session, m.state, m.label, m.tone, m.needs_you, m.cli, m.model, m.last and m.last.text)
                for m in view.members]

    assert facts(ours) == facts(tui)
    assert (ours.open, ours.wires, [m.text for m in ours.latest]) == (tui.open, tui.wires, [m.text for m in tui.latest])


# --- the whole dashboard (DashboardStore) ------------------------------------------------------


def test_a_full_poll_keeps_the_background_sessions_for_the_quick_ones(monkeypatch):
    """The edge of the dashboard's live read (app.read_live): a full poll asks `claude agents --json`
    and keeps its background sessions; a quick one reads the pid files, blind to those, and carries
    the kept ones over, their record winning over a pid file of the same session."""
    agents = [{"sessionId": "fg", "kind": "interactive", "status": "busy"},
              {"sessionId": "bg", "kind": "background", "status": "idle", "pid": 7}]
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: agents)
    monkeypatch.setattr(store, "_registry_sessions", lambda: [{"sessionId": "fg", "pid": 3}, {"sessionId": "bg", "pid": 9}])
    live, kept = store.live_entries(True, {})
    assert set(live) == {"fg", "bg"} and kept == {"bg": agents[1]}
    live, again = store.live_entries(False, kept)
    assert again is kept
    assert live == {"fg": {"sessionId": "fg", "pid": 3}, "bg": agents[1]}


async def test_the_dashboards_live_read_tells_of_idle_background_sessions_once(fake_home, monkeypatch):
    """What stayed in app.read_live around the store's read: the background sessions are kept before
    the transcripts are read, and Bit says once that some sit idle."""
    from tabmancer import app as app_module
    from tabmancer.app import SessionPicker

    idle = {"sessionId": "bg", "kind": "background", "status": "idle", "pid": 7}
    monkeypatch.setattr(sessions, "_agents_json", lambda include_finished=False: [idle])
    said: list[str] = []
    monkeypatch.setattr(app_module.Mascot, "react", lambda self, mood, words, *args, **kwargs: said.append(words))
    app = SessionPicker(days=14)
    async with app.run_test() as pilot:
        for _ in range(100):
            await pilot.pause(0.05)
            if app.full_polls:
                break
        assert app.background_live == {"bg": idle}
        app.poll_live(full=True)
        for _ in range(100):
            await pilot.pause(0.05)
            if app.full_polls > 1:
                break
        assert app.full_polls > 1
    assert said == ["1 background session sits idle. B tidies up."]


def comparable(view) -> dict:
    """A view as JSON, its sessions by id; the moment it was built left out."""
    data = VM.to_json(view)
    data.pop("at")
    data["sessions"] = sorted(data["sessions"], key=lambda s: s["id"])
    return data


async def test_the_dashboard_store_and_the_terminal_dashboard_show_the_same(fake_home, tmp_path, monkeypatch, quiet):
    """The seam: what the desktop main window will draw is what the terminal dashboard holds."""
    import time as clock

    from test_team_tree import ready

    from tabmancer.app import SessionPicker

    shop(fake_home, tmp_path, monkeypatch)
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await ready(pilot, app)
        end = clock.monotonic() + 20
        while clock.monotonic() < end and not (app.token_report is not None and app.full_polls):
            await pilot.pause(0.05)
        app.queued["one"] = "self"  # h on a busy one: the store reads it from the queue file
        app.save_queued()
        tui = comparable(app.view())
    assert {"lead-1", "api-1", "ui-1", "one", "two"} <= {s["id"] for s in tui["sessions"]}
    assert next(s for s in tui["sessions"] if s["id"] == "one")["queued"] == "self"
    assert tui["teams"] and tui["cards"]["open"]  # the live poll landed: not two empty views alike
    snap = DashboardStore().poll()
    assert comparable(snap.view) == tui
    assert set(snap.sessions) == {s["id"] for s in tui["sessions"]} and set(snap.team_states) == {"shop"}


def test_the_dashboard_store_only_watches(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)

    def no(*args, **kwargs):
        raise AssertionError("not from a watcher")

    for owner, name in ((actor, "claim"), (actor, "first_to"), (actor, "release"), (usage, "record"),
                        (accounts, "remember"), (agy, "_run_usage"), (agy, "refresh_quota"), (marks.Marks, "save")):
        monkeypatch.setattr(owner, name, no)
    clock = Clock()
    dash = DashboardStore(clock=clock)
    for _ in range(store.FULL_EVERY + 1):
        snap = dash.poll()
        clock.now += store.USAGE_SECONDS
    assert snap.acting is None  # no dashboard runs here
    assert {"lead-1", "api-1", "one"} <= {s.session_id for s in snap.sessions.values() if s.exit is Exit.RUNNING}


def test_the_dashboard_store_shows_who_acts_and_the_marks_of_any_dashboard(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    clock = Clock()
    dash = DashboardStore(clock=clock)
    assert dash.poll().pinned == set()
    marks.Marks(hidden={"two"}, pinned={"one"}).save()  # pinned and hidden in the terminal dashboard
    held = {"pid": 4242, "started": "then", "command": "tabmancer"}
    monkeypatch.setattr(actor, "holder", lambda: held)
    monkeypatch.setattr(actor, "running", lambda record: record is held)
    clock.now += store.ACTOR_SECONDS
    snap = dash.poll()
    assert (snap.pinned, snap.hidden, snap.acting) == ({"one"}, {"two"}, held)
    by_id = {s.id: s for s in snap.view.sessions}
    assert by_id["one"].pinned and by_id["two"].hidden
    (config_dir() / marks.FILE).write_text('{"pinned": ["one", "tw', encoding="utf-8")  # half a file
    assert (dash.poll().pinned, dash.poll().hidden) == ({"one"}, {"two"})  # unreadable now: the last ones stay


def test_a_reading_with_new_marks_is_what_the_next_poll_shows(fake_home, tmp_path, monkeypatch, quiet):
    """A window that has just pinned or hidden one shows it at once (DashSnapshot.with_marks), as its next
    poll will: the same view, built by the same code."""
    shop(fake_home, tmp_path, monkeypatch)
    dash = DashboardStore(clock=Clock())
    before = dash.poll()
    now = marks.Marks(hidden={"two"}, pinned={"one"})
    now.save()
    marked, polled = before.with_marks(now), dash.poll()
    assert (marked.pinned, marked.hidden) == (polled.pinned, polled.hidden) == ({"one"}, {"two"})
    assert comparable(marked.view) == comparable(polled.view) != comparable(before.view)
    assert before.pinned == set()  # the reading it came from is left as it was


def test_the_dashboard_store_reads_as_often_as_the_dashboard(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    clock = Clock()
    dash = DashboardStore(clock=clock)
    read: list[str] = []

    def counted(name: str):
        then = getattr(dash, name)
        return lambda: read.append(name[5:]) or then()

    for name in ("read_others", "read_tokens", "read_git", "read_limits", "read_accounts", "read_acting"):
        monkeypatch.setattr(dash, name, counted(name))
    full: list[int] = []
    asked = sessions.live_sessions
    monkeypatch.setattr(store, "live_sessions", lambda: full.append(1) or asked())

    def poll_at(moment: float) -> list[str]:
        clock.now, read[:] = moment, []
        dash.poll()
        return list(read)

    assert poll_at(1000) == ["tokens", "git", "limits", "accounts", "acting"]  # Codex's and agy's came with the list
    assert poll_at(1002) == []
    assert poll_at(1005) == ["accounts"]
    assert poll_at(1010) == ["accounts", "acting"]
    for moment in range(1012, 1022, 2):
        poll_at(moment)
    assert len(full) == 2 and dash.polls == store.FULL_EVERY + 1  # the first poll and the ninth ask the CLI
    assert poll_at(1060) == ["others", "tokens", "git", "accounts", "acting"]
    assert poll_at(1300) == ["others", "tokens", "git", "limits", "accounts", "acting"]


def test_the_dashboard_store_asks_git_again_soon_once_when_it_begins_failing(fake_home, tmp_path, monkeypatch, quiet):
    shop(fake_home, tmp_path, monkeypatch)
    asked: list[float] = []
    clock = Clock()
    monkeypatch.setattr(gitstate, "read_all", lambda folders: asked.append(clock.now) or ({}, {"refuses"}))
    dash = DashboardStore(clock=clock)
    for _ in range(40):
        dash.poll()
        clock.now += 2
    assert asked == [1000, 1010, 1070]  # once more after 10 s, then each minute: never a chain


def test_the_dashboard_store_keeps_the_dashboards_timings():
    from tabmancer import app as app_module

    assert store.CHART_DAYS == app_module.CHART_DAYS
    assert (store.FULL_EVERY, store.USAGE_SECONDS, store.TOKENS_SECONDS, store.GIT_SECONDS, store.ACCOUNTS_SECONDS,
            store.ACTOR_SECONDS) == (app_module.FULL_POLL_EVERY, app_module.USAGE_SECONDS, app_module.TOKENS_SECONDS,
                                     app_module.GIT_SECONDS, app_module.ACCOUNTS_SECONDS, app_module.ACTOR_SECONDS)


def test_the_dashboard_store_reads_which_looked_open_at_the_last_shutdown_with_the_list(fake_home, monkeypatch, quiet):
    down = datetime(2026, 11, 24, 23, 0)
    asked = []

    def open_at_shutdown(listed, times):
        asked.append((sorted(s.session_id for s in listed), times))
        return {"two"}

    monkeypatch.setattr(shutdowns, "recent_shutdowns", lambda limit=30: [down])
    monkeypatch.setattr(shutdowns, "open_at_shutdown", open_at_shutdown)
    dash = DashboardStore(clock=Clock())
    assert dash.poll().open_at_shutdown == {"two"} == dash.poll().open_at_shutdown  # the window ticks them at its start
    assert asked == [(["one", "two"], [down])]  # once, with the list as the dashboard reads it at its start
    monkeypatch.setattr(store, "find_sessions", lambda *args, **kwargs: [])
    monkeypatch.setattr(shutdowns, "recent_shutdowns", lambda limit=30: pytest.fail("nothing listed: no log read"))
    assert DashboardStore(clock=Clock()).poll().open_at_shutdown == set()
