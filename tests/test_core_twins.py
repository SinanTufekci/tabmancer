"""What the terminal dashboard's tests reached in the core and nothing else did, tested on the core alone (so it
stays tested once the terminal dashboard is archived): the fronts' timers and the store's readings on one clock,
the token chart's days, the background jobs in their order, the sessions on disk for the clean-up, words the
forms and dialogs say, doctor's word on Bit, a team of peers' line-up, a long message to a closed member, today's
claude update before a session opens, agy's quota read too long ago, and how long p's notes stay."""

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tabmancer import (
    agy,
    bgjobs,
    claudeupdate,
    clis,
    diskclean,
    doctor,
    engine,
    picks,
    prompting,
    store,
    teamforms,
    teams,
    teamwords,
    usage,
)
from tabmancer.config import write_json
from tabmancer.sessions import Session, Status

# --- the fronts' timers and the store's readings ------------------------------------------------------------


def test_the_schedule_runs_each_reading_at_the_stores_seconds():
    """One clock for both: what a front runs on a timer (engine.SCHEDULE) is read by the store as often."""
    every = {each.name: each for each in engine.SCHEDULE}
    assert len(every) == len(engine.SCHEDULE)
    assert {name for name, each in every.items() if each.acting} == {"check_updates", "continue_due", "tell_wrapped"}
    assert [every[name].seconds for name in ("refresh_git", "show_accounts", "refresh_tokens", "refresh_usage",
                                             "tick_actor")] == [store.GIT_SECONDS, store.ACCOUNTS_SECONDS,
                                                                store.TOKENS_SECONDS, store.USAGE_SECONDS,
                                                                store.ACTOR_SECONDS]
    assert every["tell_wrapped"].first == engine.WRAPPED_AFTER
    assert [each.name for each in engine.SCHEDULE if each.first is not None] == ["tell_wrapped"]


def test_the_store_reads_each_thing_when_it_is_due(monkeypatch):
    """Polled every second: who is signed in every ACCOUNTS_SECONDS, git and tokens every GIT_SECONDS and
    TOKENS_SECONDS, the plan limits every USAGE_SECONDS; all at the first poll."""
    now = [1000.0]
    reading = store.DashboardStore(clock=lambda: now[0])
    read: list[str] = []
    for name in ("read_listed", "read_teams", "read_live", "read_others", "read_tokens", "read_git", "read_limits",
                 "read_accounts", "read_acting"):
        monkeypatch.setattr(reading, name, lambda *args, name=name, **kwargs: read.append(name))

    def at(second: float) -> set[str]:
        read.clear()
        now[0] = 1000.0 + second
        reading.poll()
        return set(read) - {"read_live", "read_teams", "read_listed", "read_others"}

    assert at(0) == {"read_tokens", "read_git", "read_limits", "read_accounts", "read_acting"}
    assert at(store.ACCOUNTS_SECONDS - 0.5) == set()
    assert at(store.ACCOUNTS_SECONDS) == {"read_accounts"}
    assert at(store.GIT_SECONDS) == {"read_accounts", "read_acting", "read_git", "read_tokens"}
    assert at(store.USAGE_SECONDS) >= {"read_limits"}


def test_tokens_are_counted_for_the_charts_days_at_least(monkeypatch):
    asked: list[float] = []

    class Report:
        def per_session(self, whole_life=False):
            return {}

    monkeypatch.setattr(store.tokens, "collect", lambda days: asked.append(days) or Report())
    store.DashboardStore(days=3).read_tokens()
    store.DashboardStore(days=30).read_tokens()
    assert asked == [store.CHART_DAYS, 30] and store.CHART_DAYS == 14


# --- B: the background jobs ---------------------------------------------------------------------------------


def test_the_background_jobs_needing_you_first_then_working_idle_stopped(monkeypatch):
    def job(sid, pid, state="", status="", started=None):
        return {"kind": "background", "id": f"bg-{sid}", "sessionId": sid, "name": sid, "cwd": "C:/work/shop",
                "pid": pid, "state": state, "status": status, "startedAt": started}

    noon = datetime(2026, 11, 27, 12, 0).timestamp() * 1000
    entries = [job("gone", None, "done", started=noon), job("busy", 7, "working", "busy", started=noon),
               job("idle-old", 8, "working", "idle", started=noon - 60_000), job("idle-new", 9, "", "idle", started=noon),
               job("asks", 10, "blocked", started="not a time"), {"kind": "interactive", "id": "tab-1"},
               {"kind": "background"}]
    monkeypatch.setattr(bgjobs.sessions, "_agents_json", lambda include_finished=False: entries)
    found = bgjobs.jobs()
    assert [one.session_id for one in found] == ["asks", "busy", "idle-new", "idle-old", "gone"]
    assert [bgjobs.kind(one) for one in found] == ["needs", "working", "idle", "idle", "stopped"]
    assert found[0].started is None and found[1].started == datetime(2026, 11, 27, 12, 0) and found[1].id == "bg-busy"
    assert found[2].state == "idle"  # a working job whose turn is over is idle
    monkeypatch.setattr(bgjobs.sessions, "_agents_json", lambda include_finished=False: None)
    assert bgjobs.jobs() is None  # no claude CLI


# --- the clean-up: every session on disk --------------------------------------------------------------------


def test_the_clean_up_lists_every_session_on_disk_biggest_first(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    folder = home / "projects" / "C--work-shop"
    folder.mkdir(parents=True)
    record = {"type": "user", "sessionId": "big", "cwd": "C:\\work\\shop", "timestamp": "2026-11-20T10:00:00Z",
              "message": {"role": "user", "content": "Fix the login page"}}
    (folder / "big.jsonl").write_text(json.dumps(record) + "\n" + "x" * 4000 + "\n", encoding="utf-8")
    (folder / "empty.jsonl").write_text("", encoding="utf-8")
    sidecar = folder / "big"
    sidecar.mkdir()
    (sidecar / "notes.txt").write_text("y" * 1000, encoding="utf-8")
    monkeypatch.setattr(diskclean, "live_sessions", dict)
    items = diskclean.collect()
    assert [item.session.session_id for item in items] == ["big", "empty"]
    assert items[0].size >= 5000 and items[1].size == 0
    assert items[1].session.title == "(no prompt: nothing to resume)"
    assert not any(item.is_open for item in items)


# --- what the forms and dialogs say -------------------------------------------------------------------------


def test_the_context_rows_help_says_what_each_cap_means():
    for cap in teamforms.CAPS:
        said = teamforms.cap_help(cap)
        assert said.startswith("←/→ the context cap every member starts under. ") and said.endswith(".")
        assert picks.cap_words(cap) in said
    assert teamforms.cap_help("500k").endswith("500k: " + picks.OWN_CAP + ".")


def test_the_cli_dialog_says_which_clis_are_listed_now():
    assert clis.listing_words(["claude", "codex"]) == "Listing sessions from Claude Code, Codex."
    assert clis.listing_words(["agy"]) == "Listing sessions from Antigravity."


def test_doctor_says_how_bit_is_drawn_without_a_terminal_to_ask(monkeypatch):
    monkeypatch.setenv("TABMANCER_SIXEL", "0")
    assert doctor.bit_check() == ("ok", "Bit is drawn in block characters: TABMANCER_SIXEL=0.")
    monkeypatch.delenv("TABMANCER_SIXEL")
    assert "not run in a terminal" in doctor.bit_check()[1]  # pytest's stdout is no terminal
    write_json("config.json", {"mascot": False})  # hidden, from before b chose among three
    assert doctor.bit_check() == ("ok", "Bit is away (b in the dashboard brings it back).")


# --- a team: peers, and a message to a closed member ------------------------------------------------------


def test_a_team_of_peers_line_up_has_no_lead_line():
    form = teamforms.TeamForm(taken=set(), saved={})
    (name,) = teams.PEER_TEMPLATES
    form.template = form.lineups.index(name)
    text = form.template_text(name, "lead: plans the work @opus")
    assert text == teams.PEER_TEMPLATES[name] and form.peers(text) and form.shown_lineup(text) == name
    assert [member.name for member in teams.parse_members(text)] == ["first", "second"]
    form.template = 0  # a ready-made one with a lead: its line kept as it was in the box
    assert form.template_text(form.picked(), "lead: plans the work @opus").startswith("lead: plans the work @opus\n")


def test_a_long_message_to_a_closed_member_opens_with_its_start(tmp_path):
    words = " ".join(f"word{n}" for n in range(3000))  # some 26 000 characters: past a tab's command line
    more = " (the rest follows: the user pastes it)"
    cut = teams.cut(words, teams.MESSAGE_IN_PROMPT, more)
    assert cut.endswith("…" + more) and len(cut) <= teams.MESSAGE_IN_PROMPT + 1 + len(more)
    assert words.startswith(cut.removesuffix("…" + more))  # its start, ending at a word
    assert teams.cut("Fix  the\nlogin page", teams.MESSAGE_IN_PROMPT, more) == "Fix the login page"  # short: whole
    team = teams.new_team("shop", str(tmp_path), "Goal", teams.parse_members("api: x"))
    assert teams.launch(team, team.members[-1], prompt=cut).prompt == cut


# --- today's claude update, before a session opens -------------------------------------------------------


def test_claude_update_runs_once_before_a_session_opens_and_never_when_left_to_the_dashboard(monkeypatch):
    claudeupdate.set_enabled(True)
    ran: list[int] = []

    def run(claude=None, timeout=0):
        ran.append(1)
        monkeypatch.setattr(claudeupdate, "_tried", True)
        return claudeupdate.Outcome(True, "2.1.0", "2.1.1")

    monkeypatch.setattr(claudeupdate, "run", run)
    said: list[str] = []
    outcome = claudeupdate.before_opening(said.append)
    assert outcome is not None and outcome.changed and ran == [1]
    assert said == ["Updating Claude Code first (claude update)…", "Claude Code updated: 2.1.0 → 2.1.1."]
    assert claudeupdate.before_opening(said.append) is None and ran == [1]  # once in a process
    monkeypatch.setattr(claudeupdate, "_tried", False)
    monkeypatch.setattr(claudeupdate, "_elsewhere", True)  # a monitor: the dashboard's to run
    assert claudeupdate.before_opening() is None and ran == [1]
    monkeypatch.setattr(claudeupdate, "_elsewhere", False)
    claudeupdate.set_enabled(False)
    assert claudeupdate.before_opening() is None and ran == [1]


# --- agy's quota, read too long ago ----------------------------------------------------------------------


def test_agys_quota_read_longer_ago_than_stale_says_when_not_how_much():
    limit = usage.Limit("session", 91, None, "")
    now = datetime(2026, 11, 20, 12, 0)
    chain = [{"name": "Neon", "left": 0.0, "back": datetime(2026, 11, 25, 10, 2)},
             {"name": "Hydrogen", "left": 31.0, "back": None}, {"name": "Flash", "left": None, "back": None}]

    def said(limits, read_at, with_agy=True) -> str:
        return "".join(words for words, _ in teamwords.quota_parts(limits, {"read_at": read_at, "chain": chain},
                                                                   with_agy, now))

    assert said([limit], now - timedelta(minutes=5)) == \
        "Claude session 91%   agy Neon out till Wed 10:02 · Hydrogen 31% · Flash ?"
    assert said([limit], now - timedelta(minutes=5), with_agy=False) == "Claude session 91%"  # no agy member
    assert said([], now - agy.STALE).startswith("agy Neon")  # just within
    assert said([], now - agy.STALE - timedelta(minutes=1)) == "agy quota read 09:59"
    assert said([], None) == "agy quota not read" and agy.STALE == timedelta(hours=2)


# --- how long p's notes stay ------------------------------------------------------------------------------


@pytest.fixture
def closed(tmp_path, monkeypatch):
    monkeypatch.setattr(prompting.teams, "member_of", lambda session, saved=None: None)
    return Session(session_id="s1", path=None, cwd=str(tmp_path / "shop"), title="Fix it", status=Status.DONE)


def test_ps_notes_stay_long_enough_to_read(closed):
    def send(session, how, live=(), start=None):
        def started(launch):
            if isinstance(start, OSError):
                raise start
            return "Started bg-1."

        return prompting.send(session, how, "Go on", lambda: set(live), lambda launches: [], started, lambda note: None)

    assert send(closed, prompting.TAB, live={"s1"}).note.seconds == prompting.ELSEWHERE_SECONDS  # open elsewhere
    assert send(replace(closed, cli="codex"), prompting.BACKGROUND).note.seconds == prompting.ELSEWHERE_SECONDS
    assert send(closed, prompting.TAB).note.seconds is None  # opened: the usual while
    assert send(closed, prompting.BACKGROUND).note.seconds == prompting.STARTED_SECONDS
    failed = send(closed, prompting.BACKGROUND, start=OSError("The claude CLI is not on PATH."))
    assert failed.outcome == "failed" and failed.note.seconds == prompting.STARTED_SECONDS
    assert prompting.ELSEWHERE_SECONDS >= prompting.STARTED_SECONDS > 0
