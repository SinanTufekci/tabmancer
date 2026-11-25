"""What the dashboard says of one session with no UI (sessionwords.py): its Model and Opens-as columns,
what Enter does, how it came out, its status and what was said, and the facts beside the preview, as
(words, style) parts. test_dashboard shows them as the terminal draws them; test_desktop_extra proves
the module loads with neither Textual nor Qt."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import agy, gitstate, sessionwords, teams, teamwords
from tabmancer import viewmodel as VM
from tabmancer.launch import Launch, Mode
from tabmancer.palette import AMBER, CYAN, GREEN, MUTED, TEXT
from tabmancer.sessions import Exit, Session, Status
from tabmancer.tokens import Tokens

ACCENT = "#d97757"


def s(sid="s1", exit=Exit.CLEAN, status=Status.DONE, **kw) -> Session:
    kw.setdefault("last_active", datetime.now() - timedelta(minutes=5))
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd="C:/work/shop", title="t",
                   exit=exit, status=status, **kw)


def words(parts) -> str:
    return "".join(w for w, _ in parts)


def never() -> Launch:
    raise AssertionError("asked how it opens")


def test_the_model_column():
    assert sessionwords.model_parts(s(model=""), "") == []
    assert sessionwords.model_parts(s(model="<synthetic>"), "high") == []
    closed = sessionwords.model_parts(s(model="claude-opus-5-5"), "medium")
    assert closed == [("opus 5.5", MUTED), ("·med", MUTED)]
    fell = sessionwords.model_parts(s(exit=Exit.RUNNING, status=Status.RUNNING, model="claude-opus-5-5",
                                      fell_back="Opus 5.5 → Sonnet 5.5"), "xhigh")
    assert fell == [("opus 5.5", AMBER), ("↓", f"bold {AMBER}"), ("·xhigh", MUTED)]
    agy_model = sessionwords.model_parts(s(cli="agy", model="gemini-4-hydrogen-high"), "high")
    assert words(agy_model) == "Hydrogen (High)"  # its level is in its name: no effort after it


def test_how_it_opens():
    closed = s()
    assert sessionwords.open_as(closed, Mode.RESUME, armed=None, queued=False, accent=ACCENT) == [("", "")]
    assert sessionwords.open_as(closed, Mode.RESUME, armed=None, queued=False, accent=ACCENT, plain=True) == \
        [("resume", MUTED)]
    assert sessionwords.open_as(closed, Mode.AUTO, armed=None, queued=False, accent=ACCENT) == \
        [("▶ auto", f"bold {ACCENT}")]
    assert sessionwords.open_as(closed, Mode.AUTO, armed=None, queued=True, accent=ACCENT) == \
        [("▶ hand over", f"bold {ACCENT}")]
    lifts = datetime.now() + timedelta(hours=1)
    assert words(sessionwords.open_as(closed, Mode.AUTO, armed=lifts, queued=True, accent=ACCENT)) == \
        f"▶ at {VM.lifts(lifts)}"
    running = s(exit=Exit.RUNNING, status=Status.RUNNING)
    assert sessionwords.open_as(running, Mode.FORK, armed=None, queued=False, accent=ACCENT, plain=True) == [("", "")]


def test_what_enter_does():
    one = s("a")
    assert sessionwords.enter_words(one, never, {"a"}) == "opens the 1 ticked session, each in a tab"
    assert sessionwords.enter_words(one, never, {"b", "c"}).endswith("(not this one: Space ticks it)")
    assert sessionwords.enter_words(s(status=Status.BACKGROUND), never, set()) == "attaches to it in a new tab"
    assert sessionwords.enter_words(s(exit=Exit.RUNNING, status=Status.RUNNING), never, set()).startswith("goes to")
    auto = Launch(title="shop: api", cwd="C:/work/shop", mode=Mode.AUTO, session_id="a", cli="agy",
                  options=("--x",))
    said = sessionwords.enter_words(s(cli="agy"), lambda: auto, set())
    assert said == "resumes it in a new tab and tells Antigravity to carry on, as api of the team shop · m changes how"


def test_how_it_came_out():
    heading, body = sessionwords.outcome(s(last_reply="All  done\nnow."), None)
    assert heading[0][1].startswith("bold ") and body == "All done now."
    heading, body = sessionwords.outcome(s(status=Status.CUT_OFF, first_prompt="go"), "a summary")
    assert body == "Last asked: go"
    heading, _ = sessionwords.outcome(s(exit=Exit.RUNNING, status=Status.RUNNING, live_state="busy"), None)
    assert heading[0] == ("Open now · working", f"bold {GREEN}")


def test_the_status_and_a_limit(monkeypatch):
    parts, means = sessionwords.status_row(s(cli="codex"))
    assert "Codex" in means and "Claude" not in means
    now = datetime(2026, 11, 25, 14, 0, 0)  # one clock for the row and the words it is held to

    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(VM, "datetime", Pinned)
    stopped = s(status=Status.LIMIT, resets=now + timedelta(hours=1))
    parts, means = sessionwords.status_row(stopped)
    assert words(parts).startswith("◐ limit · ") and means == VM.limit_help(stopped)


def test_what_was_said():
    rows = sessionwords.said_rows(s(first_prompt="hi", last_prompt="more", last_reply="ok", files=["a.py"]),
                                  messages=[("1 in · 0 out", TEXT)], found=("the cart page", 4, 8),
                                  summary={"summary": "old", "size": 3}, accent=ACCENT)
    assert [label for label, _ in rows] == ["Messages", "Found", "Summary", "First prompt", "Last prompt", "Claude",
                                           "Files edited"]
    assert rows[1][1] == [("the ", ""), ("cart", f"bold reverse {ACCENT}"), (" page", "")]
    assert rows[2][1][1][1] == "dim"  # written before the latest activity
    asking = sessionwords.said_rows(s(), summarizing=True, summary_model="haiku")
    assert asking[0] == ("Summary", [("asking Claude (haiku)…", "dim italic")])


def test_the_facts():
    agy_out = s(cli="agy", status=Status.CUT_OFF, model="gemini-4-neon-high")

    class Out(Launch):
        @property
        def argv(self):
            raise agy.NoModel("every agy model is out")

    rows = sessionwords.fact_rows(
        agy_out, member=("shop", "api"), effort="", git=gitstate.GitState("main", 2, 0), tokens=Tokens(output=5),
        cap=0, default_cap=0, launch=lambda: Out(title="shop: api", cwd="C:/work/shop", mode=Mode.AUTO,
                                                 session_id="s1", cli="agy", options=("--x",)),
        mode=Mode.AUTO, armed=None, queued=False, accent=ACCENT)
    found = {label: value for label, value in rows if label}
    assert found["Team"] == [("shop", f"bold {ACCENT}"), (" · api", TEXT)]
    assert found["CLI"] == [("Antigravity", CYAN)] and found["Git"] == [("2 changed · no upstream", AMBER)]
    assert found["Opens as"] == [("▶ auto", f"bold {ACCENT}")]
    assert ("", [("every agy model is out: nothing opens it until the quota is back", AMBER)]) in rows
    assert rows[-1] == ("", [("as shop: api, with its role and the team's messages let in", MUTED)])
    running = s(exit=Exit.RUNNING, status=Status.RUNNING, model="claude-opus-5-5", context=50_000,
                started=datetime.now() - timedelta(hours=1))
    rows = sessionwords.fact_rows(running, member=None, effort="high", git=None, tokens=None, cap=0, default_cap=0,
                                  launch=never, mode=Mode.RESUME, armed=None, queued=False, accent=ACCENT)
    found = {label: value for label, value in rows if label}
    assert found["Model"] == [("Opus 5.5", TEXT), (" · high effort", MUTED)]
    assert words(found["Context"]).startswith("50k of ") and found["Going for"] == "1h 00m"
    assert "Opens as" not in found


def test_the_context_fill():
    assert sessionwords.fill_parts(s()) == [("context size not known yet", MUTED)]
    capped = sessionwords.fill_parts(s(context=240_000, started=datetime.now() - timedelta(hours=1)), cap=300_000)
    assert words(capped).startswith("240k of a 300k cap (80%) · ran for")


def test_effort_and_a_members_messages():
    assert VM.effort_of(s(effort="high"), member=False) == "high"
    msg = teams.Message(datetime.now() - timedelta(minutes=2), "lead", "api", "lead: build it", delivered=True)
    assert teamwords.message_parts(msg, "api", {"lead": ACCENT}) == \
        [("← ", MUTED), ("lead", f"bold {ACCENT}"), (": ", MUTED), ("build it", TEXT)]
    line = sessionwords.messages([msg], "api", {"lead": ACCENT})
    assert words(line) == "1 in · 0 out  ·  2m ago  ← lead: build it"
