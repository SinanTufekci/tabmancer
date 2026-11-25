"""What the dashboard says when sessions change, without a screen (alerts.py): two looks a poll apart
come to events, each with its note, Bit's part, the bell and the toast; and that no UI comes with
them. The dashboard's own tests (test_handover, test_toast, test_claude_update ...) show the same on
the screen."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import alerts, claudeupdate, typein
from tabmancer.actions import Note, Told
from tabmancer.sessions import Exit, Session, Status

BIG = 900_000
LATER = datetime.now() + timedelta(hours=3)


def s(sid, state="busy", cwd="C:/work/shop", **kw) -> Session:
    kw.setdefault("exit", Exit.RUNNING)
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=cwd, title=f"Title {sid}",
                   live_state=state, status=kw.pop("status", Status.RUNNING), **kw)


def test_alerts_load_no_ui():
    code = ("import sys, tabmancer.alerts; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_a_working_session_that_turns():
    done, = alerts.session_events(s("a"), s("a", "idle"))
    assert (done.kind, done.session, done.bell, done.toast) == (alerts.DONE, "a", True, True)
    assert done.note == Told("shop: waiting for you", seconds=10)
    assert done.bit == alerts.Bit("celebrate", "shop")
    asks, = alerts.session_events(s("b", status=Status.BACKGROUND), s("b", "blocked", status=Status.BACKGROUND))
    assert asks.kind == alerts.NEEDS_YOU and asks.bit.how == "alert"
    assert asks.note.words.endswith("(Title b). It runs in the background: Enter on its row attaches to it.")
    # Only a turn from working says anything: idle to idle, a first look, a closed one.
    assert alerts.session_events(s("a", "idle"), s("a", "blocked")) == []
    assert alerts.session_events(None, s("a", "idle")) == []
    assert alerts.session_events(s("a"), s("a", "idle", exit=Exit.CLEAN)) == []


def test_a_limit_goes_to_the_toast_and_waits_for_bit():
    limit, = alerts.session_events(s("a"), s("a", "idle", resets=LATER))  # not "done": stopped
    assert (limit.kind, limit.toast, limit.note, limit.bit, limit.bell) == (alerts.LIMIT, True, None, None, False)


def test_a_fallback_is_said_once_with_who():
    fell = "Opus 5.5 stopped a reply; Sonnet 5.5 answered"
    event, = alerts.session_events(s("a"), s("a", fell_back=fell), who="arcade: sound")
    assert event.kind == alerts.FELL_BACK and event.bell and not event.toast
    assert event.note.title == "Model fell back" and event.note.severity == "warning" and event.note.seconds == 15
    assert event.note.words.startswith(f"arcade: sound: {fell}. It goes on with that model")
    assert event.bit == alerts.Bit("alert", "shop")
    assert alerts.session_events(s("a", fell_back=fell), s("a", fell_back=fell)) == []
    assert alerts.session_events(None, s("a", fell_back=fell))[0].note.words.startswith("shop: ")


def test_crowded_once_one_or_many():
    cap = 300_000
    one = alerts.crowded_event([s("a", context=BIG, started=datetime(2026, 1, 1, 10))], lambda _: cap, cap, set(),
                               now=datetime(2026, 1, 1, 12, 5))
    assert one.kind == alerts.CROWDED and one.once_keys == ("a",) and one.note.title == "Crowded session"
    assert one.note.words.startswith("shop has been going for 2h 05m and its context is 900k, 100% of its cap")
    assert one.bit == alerts.Bit("react", "shop: context 900k", "crowded")
    many = alerts.crowded_event([s("a", context=BIG), s("b", cwd="C:/work/two", context=BIG)], lambda _: cap, cap, set())
    assert many.once_keys == ("a", "b") and many.bit is None
    assert many.note.words.startswith("2 open sessions have crowded contexts (shop, two).")
    assert alerts.crowded_event([s("a", context=BIG)], lambda _: cap, cap, {"a"}) is None
    assert alerts.crowded_event([s("a", context=1000)], lambda _: cap, cap, set()) is None


def test_events_crowded_first_then_the_poll_order_but_skipped():
    before = {"a": s("a"), "b": s("b")}
    updates = {"b": s("b", "idle"), "a": s("a", "permission", context=900_000)}
    said = alerts.events(before, updates, set(), lambda _: 300_000, 300_000, who_of=lambda x: "who")
    assert [(e.kind, e.session) for e in said] == [(alerts.CROWDED, "a"), (alerts.DONE, "b"), (alerts.NEEDS_YOU, "a")]
    skipped = alerts.events(before, updates, {"a"}, lambda _: 300_000, 300_000, skip={"b"})
    assert [(e.kind, e.session) for e in skipped] == [(alerts.NEEDS_YOU, "a")]


def test_the_toast_words():
    assert alerts.toast_words([s("a", "idle"), s("b", "blocked", cwd="C:/work/blog")], lambda _: "") == (
        "2 sessions want you back", "shop (waiting for you), blog (needs you)")
    assert alerts.toast_words([s("a", "permission")], lambda _: "") == ("shop: needs your OK", "Title a")
    asked = []
    title, body = alerts.toast_words([s("a", "idle", resets=LATER)], lambda when: asked.append(when) or "other: free")
    assert title == "shop stopped at the usage limit" and asked == [LATER]
    assert body.startswith("It lifts at ") and body.endswith("c in the dashboard continues it then.\nother: free")
    _, past = alerts.toast_words([s("a", "idle", resets=datetime.now() - timedelta(minutes=1))], lambda _: "")
    assert past == "It lifts now. c in the dashboard continues it then."


def test_stopped_and_signed_in_are_bits():
    stopped = alerts.stopped_event([s("a", "idle", resets=LATER), s("b", "idle", resets=LATER)], lambda _: "")
    assert stopped.kind == alerts.STOPPED and stopped.once_keys == ("a", "b") and stopped.note is None
    assert stopped.bit.how == "say" and stopped.bit.words.startswith("shop: limit until ") and stopped.bit.seconds == 10
    again = alerts.stopped_event([s("a", "idle", signed_in_again=True)], lambda _: "")
    assert again.bit == alerts.Bit("react", "shop: signed in again. c continues now", "lifted", seconds=10)
    assert alerts.signed_in_event(0, "me").bit == alerts.Bit("say", "Signed in as me", tone="accent", seconds=6)
    assert alerts.signed_in_event(2, "me").bit == alerts.Bit(
        "react", "Signed in as me: 2 stopped at a limit can go on. c continues them", "lifted", seconds=10)


def test_not_up():
    sessions = {"x": s("x", cwd="C:/work/lost", exit=Exit.CLEAN), "y": s("y")}
    event = alerts.not_up_event({"x", "y"}, set(), sessions)
    assert event.kind == alerts.NOT_UP and event.note == Told("lost did not come up: its tab says why.", "warning", 15)
    assert alerts.not_up_event({"x"}, {"x"}, sessions) is None


class Said:
    def __init__(self, sender, receiver, text):
        self.sender, self.receiver, self.text = sender, receiver, text


class State:
    def __init__(self, *said):
        self.said = list(said)

    def between_members(self):
        return [m for m in self.said if m.sender != "you"]


def test_team_messages_fly_after_the_first_look():
    go, built = Said("lead", "api", "build it"), Said("api", "lead", "done")
    first, seen = alerts.team_events({"shop": State(go)}, {})
    assert first == [] and seen == {"shop": {("lead", "api", "build it")}}
    later, seen = alerts.team_events({"shop": State(go, built, Said("you", "lead", "x"))}, seen)
    assert [(e.kind, e.team, e.message) for e in later] == [(alerts.TEAM_MESSAGE, "shop", built)]
    assert ("you", "lead", "x") in seen["shop"]


def test_deliveries_and_typing_a_note_in(monkeypatch):
    notes = {"gone": ("ui", "first"), "ready": ("api", "second"), "busy": ("x", "later")}
    sessions = {"ready": s("ready", "idle"), "busy": s("busy")}
    closed, ready = alerts.deliveries(notes, sessions)
    assert closed.sid == "gone" and closed.closed == Told(
        "ui closed before its message went in: the message is on the clipboard.", "warning", 12, clipboard="first")
    assert ready.sid == "ready" and ready.closed is None and ready.session is sessions["ready"]
    typed = []
    monkeypatch.setattr(typein, "type_into", lambda sid, text: typed.append((sid, text)) or True)
    assert alerts.type_note(sessions["ready"], "api", "second") == Told("Sent to api.", seconds=5)
    monkeypatch.setattr(typein, "type_into", lambda sid, text: False)
    failed = alerts.type_note(sessions["ready"], "api", "second")
    assert failed.clipboard == "second" and failed.go_to is sessions["ready"] and failed.severity == "warning"
    assert typed == [("ready", "second")]


def test_claude_update(monkeypatch):
    monkeypatch.setattr(claudeupdate, "due", lambda *a, **k: True)
    assert alerts.update_due(False, ["agy", "claude"])
    assert not alerts.update_due(True, ["claude"]) and not alerts.update_due(False, ["agy", "codex"])
    changed = claudeupdate.Outcome(True, "2.1.1", "2.1.2")
    assert alerts.update_notes(changed, ["--advisor"], False) == [
        Note("Claude Code updated: 2.1.1 → 2.1.2.", "information", 10),
        Note("Claude Code 2.1.2 no longer lists what tabmancer runs it with: --advisor. tabmancer doctor says more.",
             "error", 30)]
    same = claudeupdate.Outcome(True, "2.1.2", "2.1.2")
    assert alerts.update_notes(same, None, False) == []
    assert alerts.update_notes(same, None, True)[0].words == "Claude Code is up to date (2.1.2)."
    failed, = alerts.update_notes(claudeupdate.Outcome(False, "2.1.1", "", "network"), None, False)
    assert (failed.severity, failed.seconds) == ("warning", 20)
    assert alerts.updating_note("the session opens") == Note(
        "Claude Code is updating: the session opens once it is done.", seconds=8)


def test_run_claude_update_checks_the_flags_only_when_it_changed(monkeypatch):
    import tabmancer.doctor

    monkeypatch.setattr(alerts.shutil, "which", lambda name: "C:/bin/claude.exe")
    monkeypatch.setattr(tabmancer.doctor, "missing_from_cli", lambda path: ["--advisor"])
    monkeypatch.setattr(claudeupdate, "run", lambda: claudeupdate.Outcome(True, "1", "2"))
    assert alerts.run_claude_update()[1] == ["--advisor"]
    monkeypatch.setattr(claudeupdate, "run", lambda: claudeupdate.Outcome(True, "2", "2"))
    assert alerts.run_claude_update()[1] is None
