"""B's words and steps (backgroundwords.py), one copy for the terminal dashboard and the desktop window: the
heading and rows, attaching, x, d and c each on a second press, and what running the commands came to."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tabmancer import backgroundwords as BW
from tabmancer import bgjobs
from tabmancer.bgjobs import Job
from tabmancer.launch import Mode
from tabmancer.palette import MUTED


def job(state="idle", running=True, **changes) -> Job:
    made = Job(id="bg-1", session_id="s1234567890", name="Nightly tests", cwd="C:/work/shop", state=state,
               running=running, started=datetime.now() - timedelta(minutes=5))
    return replace(made, **changes)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_heading_and_a_row_say_what_the_b_screen_says():
    idle, working = job(), job("working", id="bg-2")
    assert BW.heading([idle, working]) == ["Background sessions", "2", "1 idle or stopped"]
    assert BW.heading([working]) == ["Background sessions", "1"]
    assert BW.row(idle) == [bgjobs.LABELS["idle"], ("shop", ""), ("Nightly tests", ""), ("5m ago", MUTED),
                            ("bg-1", MUTED)]
    assert BW.row(job(name="", running=False))[:3] == [bgjobs.LABELS["stopped"], ("shop", ""), ("s1234567", "")]
    launch = BW.attach_launch(idle)
    assert (launch.mode, launch.session_id, launch.cwd, launch.title) == (Mode.ATTACH, "bg-1", "C:/work/shop",
                                                                          "shop: Nightly tests")


def test_a_second_press_soon_on_the_same_one_confirms():
    clock = Clock()
    confirm = BW.Confirm(clock)
    assert not confirm.confirmed("stop", "bg-1") and confirm.armed == ("stop", "bg-1")
    clock.now += BW.CONFIRM_SECONDS - 0.5
    assert confirm.confirmed("stop", "bg-1") and confirm.armed is None
    assert not confirm.confirmed("stop", "bg-1")
    clock.now += BW.CONFIRM_SECONDS  # too late: armed again
    assert not confirm.confirmed("stop", "bg-1")
    assert not confirm.confirmed("remove", "bg-1")  # another key: armed for that one
    assert not confirm.confirmed("remove", "bg-2")
    assert confirm.disarm() and not confirm.disarm() and confirm.armed is None


def test_x_stops_an_idle_one_at_once_and_a_busy_one_on_a_second_press():
    confirm = BW.Confirm(Clock())
    assert BW.stop(None, confirm) == BW.Asked()
    assert BW.stop(job(running=False), confirm) == BW.Asked(words="bg-1 is not running.")
    idle = job()
    assert BW.stop(idle, confirm) == BW.Asked([("stop", idle)])
    needs = job("blocked")
    first = BW.stop(needs, confirm)
    assert first.warning and first.commands == []
    assert first.words == "shop is waiting for you: press x again to stop it. Its conversation is kept."
    assert BW.stop(needs, confirm) == BW.Asked([("stop", needs)])
    assert BW.stop(job("working"), confirm).words.startswith("shop is working: press x again")


def test_d_removes_on_a_second_press_and_c_clears_the_idle_and_stopped_ones():
    confirm = BW.Confirm(Clock())
    running, stopped = job(), job(running=False, id="bg-2")
    asked = BW.remove(running, confirm)
    assert asked.warning and asked.words == ("Press d again to stop and remove bg-1. Its transcript stays, so the "
                                             "session can still be resumed from the list.")
    assert BW.remove(running, confirm) == BW.Asked([("remove", running)])
    assert BW.remove(stopped, confirm).words.startswith("Press d again to remove bg-2.")
    working = job("working", id="bg-3")
    assert BW.clean_up([working], confirm) == BW.Asked(words=BW.NOTHING_TO_CLEAN)
    asked = BW.clean_up([running, working, stopped], confirm)
    assert asked.warning and asked.words == ("Press c again to remove 2 idle or stopped background sessions. Their "
                                             "transcripts stay.")
    assert BW.clean_up([running, working, stopped], confirm).commands == [("remove", running), ("remove", stopped)]


@pytest.fixture
def cli(monkeypatch):
    """What the commands would have run: `claude stop` and `claude rm`, each id; one fails."""
    ran: list[tuple[str, str]] = []

    def stop(job_id):
        ran.append(("stop", job_id))
        return "stopped"

    def remove(job_id):
        if job_id == "bg-bad":
            raise OSError("it has unpushed work")
        ran.append(("rm", job_id))
        return "removed"

    monkeypatch.setattr(bgjobs, "stop", stop)
    monkeypatch.setattr(bgjobs, "remove", remove)
    monkeypatch.setattr(bgjobs, "jobs", list)  # the list read again: empty
    return ran


def test_running_the_commands_stops_a_running_one_before_it_is_removed(cli):
    running, stopped = job(), job(running=False, id="bg-2")
    ran = BW.run([("remove", running)])
    assert cli == [("stop", "bg-1"), ("rm", "bg-1")] and ran.found == [] and ran.words == "Removed shop."
    assert BW.run([("stop", running)]).words == "Stopped shop."
    cli.clear()
    ran = BW.run([("remove", running), ("remove", stopped), ("remove", job(running=False, id="bg-bad"))])
    assert cli == [("stop", "bg-1"), ("rm", "bg-1"), ("rm", "bg-2")] and ran.done == 2
    assert ran.words == "Removed 2 of 3. bg-bad: it has unpushed work"
    assert BW.run([("remove", running), ("remove", stopped)]).words == "Removed 2 background sessions."
