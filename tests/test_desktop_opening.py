"""What the window's Enter, j and m keep between presses (desktop/opening.py): how each session opens,
the ones opening, and which never came up; and the UI-free steps they take (actions.next_mode,
actions.next_waiting). The window's own tests (test_desktop_main) press the keys. Made-up data only.
Skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

from PySide6.QtWidgets import QApplication

from tabmancer import actions, openings
from tabmancer.desktop import opening
from tabmancer.launch import Launch, Mode
from tabmancer.sessions import Exit, Session, Status

NOW = datetime(2026, 11, 25, 14, 0)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["tabmancer-tests"])


def made(sid, project="", status=Status.DONE, exit=Exit.CLEAN, live="", cli="claude") -> Session:
    return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd=f"C:/work/{project or sid}",
                   title=f"the {sid}", status=status, exit=exit, live_state=live, cli=cli,
                   last_active=NOW - timedelta(minutes=5))


def resumed(sid) -> Launch:
    return Launch(title=f"{sid}: the {sid}", cwd=f"C:/work/{sid}", mode=Mode.RESUME, session_id=sid)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_m_goes_round_how_a_closed_one_opens_and_leaves_an_open_one(qapp):
    opener = opening.Opener()
    done, cut = made("d1"), made("c1", status=Status.CUT_OFF)
    assert opener.mode(done) is Mode.RESUME and opener.mode(cut) is Mode.AUTO  # cut off: it carries on
    seen = []
    for _ in range(4):
        assert opener.cycle(done)
        seen.append(opener.mode(done))
    assert seen == [Mode.AUTO, Mode.FORK, Mode.NEW, Mode.RESUME]
    agy = made("a1", cli="agy")  # agy forks no session: m goes past it
    assert opener.cycle(agy) and opener.cycle(agy) and opener.mode(agy) is Mode.NEW
    running = made("r1", status=Status.RUNNING, exit=Exit.RUNNING, live="busy")
    assert not opener.cycle(running) and "r1" not in opener.modes


def test_one_opened_that_never_shows_open_is_said_once_a_while_later(qapp):
    clock = Clock()
    opener = opening.Opener(clock=clock)
    note = opener.took(actions.Opened([resumed("up"), resumed("gone")], [], []))
    assert note == actions.Note("Opened 2 sessions in new tabs.", "information", 4)
    kept = opener.openings  # the terminal dashboard's one copy (openings.Openings), on the window's clock
    assert isinstance(kept, openings.Openings) and kept.recent() == {"up", "gone"}
    now = {"up": made("up", status=Status.RUNNING, exit=Exit.RUNNING, live="busy"), "gone": made("gone")}
    clock.now += 10
    assert opener.seen(now) is None and kept.came_up == {"up"}
    now["up"] = made("up")  # closed again since: it did come up
    clock.now += opening.CAME_UP_SECONDS
    assert opener.seen(now) == actions.Note("gone did not come up: its tab says why.", "warning", 10)
    assert opener.seen(now) is None and kept.checks == []  # said once
    assert opening.ALREADY == actions.Note(openings.ALREADY.words, "warning", 4)  # its words, said for 4 s here


def test_what_an_enter_came_to_is_one_line_for_bit():
    a, b = resumed("a"), resumed("b")
    assert opening.opened_note(actions.Opened([], [], [], "Could not open the tab: no wt")) == \
        actions.Note("Could not open the tab: no wt", "error", 15)
    assert opening.opened_note(actions.Opened([], [], ["claude --resume a"])) == \
        actions.Note(opening.NO_TERMINAL, "warning", 15)
    assert opening.opened_note(actions.Opened([a], [b], [])) == actions.Note(
        "Opened a: the a in a new tab. Open already, so not opened again: b: the b.", "warning", 10)
    assert opening.opened_note(actions.Opened([], [], [])) is None


def test_j_goes_round_the_ones_that_need_you_then_the_ones_waiting():
    asks = made("x", "zeta", Status.RUNNING, Exit.RUNNING, "permission")
    blocked = made("y", "alpha", Status.RUNNING, Exit.RUNNING, "blocked")
    idle = made("z", "beta", Status.RUNNING, Exit.RUNNING, "idle")
    busy = made("w", "gamma", Status.RUNNING, Exit.RUNNING, "busy")
    closed = made("v", "delta")
    every = [asks, blocked, idle, busy, closed]
    turn = actions.next_waiting(every)
    assert (turn.session, turn.group) == (blocked, "needs")  # by project: alpha first
    turn = actions.next_waiting(every, turn.group, turn.turn)
    assert turn.session is asks
    turn = actions.next_waiting(every, turn.group, turn.turn)
    assert turn.session is blocked  # round again
    waiting = [idle, busy, closed]  # nobody needs you now: the ones waiting, from the top
    turn = actions.next_waiting(waiting, turn.group, turn.turn)
    assert (turn.session, turn.group, turn.turn) == (idle, "waiting", 1)
    nobody = actions.next_waiting([busy, closed], turn.group, turn.turn)
    assert nobody.session is None and (nobody.group, nobody.turn) == ("waiting", 1)


def test_m_on_a_fork_less_cli_and_an_odd_mode():
    assert actions.next_mode(made("a", cli="agy"), Mode.AUTO) is Mode.NEW
    assert actions.next_mode(made("c"), Mode.ATTACH) is Mode.RESUME  # one m never offers: back to the start
