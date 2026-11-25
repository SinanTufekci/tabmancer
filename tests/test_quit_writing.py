"""The terminal's quit keys while a handover is written (minutes in life; a fake write of a second here, in the
real headless run()): the first press says so, the second waits for it, so its fresh session opens, and then
the dashboard quits. A team's handover dialog shown meanwhile stays, to be answered."""

from __future__ import annotations

import time

from tabmancer import app as app_module
from tabmancer import handover, handoverwords, teams
from tabmancer.sessions import Exit, Session, Status

WRITE = 1.0


def test_the_words():
    assert handoverwords.writing_on_quit(["shop"], "q").words == (
        "The handover of shop is being written: q again waits for it, then quits.")
    assert handoverwords.writing_on_quit(["shop", "team web"], "Esc").words.startswith(
        "The handovers of shop, team web are being written: Esc again waits for them")


def run(fake_home, monkeypatch, start, presses: int) -> list[str]:
    """The dashboard, headless: start(app) once it is up, then q pressed `presses` times; what happened, in
    order. It is stopped a while after the write if it has not quit by then."""
    done: list[str] = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches, *a: done.append(f"opened {launches[0].title}") or [])
    monkeypatch.setattr(app_module.SessionPicker, "notify", lambda self, message, **kw: done.append(f"said {message}"))

    def written(*args):
        time.sleep(WRITE)
        done.append("written")
        return "## the handover"

    monkeypatch.setattr(handover, "write_itself", written)
    monkeypatch.setattr(handover, "close", lambda session, open_ones, **kw: done.append(f"closed {session.session_id}"))
    monkeypatch.setattr(handover, "work_left", lambda team, members: "")
    monkeypatch.setattr(handover, "write_team", lambda lead, team, left: (written(), "api: a"))
    monkeypatch.setattr(app_module, "close_tabs", lambda names: ("closed", "information"))

    class Picker(app_module.SessionPicker):
        def on_ready(self) -> None:
            self.set_timer(0.3, self.go)

        def go(self) -> None:
            start(self)
            for at in range(presses):
                self.set_timer(0.2 + 0.2 * at, self.press_q)
            self.set_timer(WRITE + 2, self.stop)

        async def press_q(self) -> None:
            done.append("q")
            await self.run_action("quit")

        def stop(self) -> None:
            done.append(f"not quit: {type(self.screen).__name__} on top")
            super().exit(None)

        def exit(self, *args, **kwargs) -> None:
            done.append("quit")
            super().exit(*args, **kwargs)

    Picker(days=14).run(headless=True)
    return done


def opened(fake_home) -> Session:
    return Session(session_id="s1", path=fake_home / "s1.jsonl", cwd=str(fake_home), title="the work",
                   status=Status.RUNNING, exit=Exit.RUNNING, live_state="idle")


def test_q_twice_while_it_writes_its_own_waits_for_it_and_its_fresh_session(fake_home, monkeypatch):
    done = run(fake_home, monkeypatch, lambda app: app.hand_over(opened(fake_home), "self"), presses=2)
    said = [line for line in done if line.startswith("said ")]
    assert "is being written: q again waits for it, then quits." in said[0]
    assert said[1].startswith("said Finishing the handover of")
    order = [line for line in done if line in ("q", "written", "quit") or line.startswith(("opened", "closed"))]
    assert order[:4] == ["closed s1", "q", "q", "written"]  # its old session closed before writing, as h does
    assert order[4].startswith("opened ") and order[4].endswith(": handed over") and order[5:] == ["quit"]


def test_q_once_leaves_it_open_and_the_fresh_session_opens(fake_home, monkeypatch):
    done = run(fake_home, monkeypatch, lambda app: app.hand_over(opened(fake_home), "self"), presses=1)
    assert "quit" not in done and any(line.startswith("opened ") for line in done)
    assert done[-1] == "not quit: Screen on top"


def test_a_teams_handover_dialog_shown_meanwhile_stays_to_be_answered(fake_home, monkeypatch):
    def start(app):
        team = teams.new_team("shop", str(fake_home), "g", teams.parse_members("lead: steer; api: a", lead_ok=True))
        lead = Session(session_id="L", path=fake_home / "L.jsonl", cwd=str(fake_home), title="lead",
                       status=Status.RUNNING, exit=Exit.RUNNING, live_state="idle")
        app.hand_over_team(team, {"lead": lead, "api": None})

    done = run(fake_home, monkeypatch, start, presses=2)
    assert "quit" not in done and done[-1] == "not quit: TeamHandoverDialog on top"
    assert handoverwords.team_written_on_quit("shop").words in [line[len("said "):] for line in done]
