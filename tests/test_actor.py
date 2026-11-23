"""actor.py: one dashboard does what happens by itself; a lock left behind is taken over only when its
process is checked gone by pid, start time and command line."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from tabmancer import actor
from tabmancer.config import config_dir

HOLD = ("import os, sys; from tabmancer import actor; print(actor.claim(), os.getpid(), flush=True); "
        "sys.stdin.readline(); actor.release()")


def holding(env=None) -> tuple[subprocess.Popen, int]:
    """A second Python process holding the lock, and its own pid: a venv's python.exe on Windows is a
    launcher that runs the interpreter as a child of its own, so Popen's pid is not the holder's."""
    child = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True, env=env)
    took, pid = child.stdout.readline().split()
    assert took == "True"
    return child, int(pid)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(actor, "_mine", None)
    monkeypatch.setattr(actor, "_registered", True)  # no atexit hook from a test
    yield


def lock() -> dict:
    return json.loads((config_dir() / actor.LOCK).read_text(encoding="utf-8"))


def write(record: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / actor.LOCK).write_text(json.dumps(record), encoding="utf-8")


def test_the_first_takes_it_and_names_itself_three_ways():
    assert actor.claim() and actor.holds()
    record = lock()
    assert record["pid"] == os.getpid() and record["started"] and "since" in record
    assert record["command"] == actor.me()["command"]
    assert actor.claim()  # ours already
    actor.release()
    assert not (config_dir() / actor.LOCK).exists() and not actor.holds()


def test_a_running_holder_keeps_it_and_a_second_process_waits():
    """Two real processes: the second sees the first running and does not take the lock."""
    first, pid = holding()
    try:
        assert lock()["pid"] == pid
        assert not actor.claim() and not actor.holds()
        assert actor.running(lock())
    finally:
        first.communicate("\n", timeout=30)
    assert actor.claim()  # it let go as it closed: the next one takes it


def test_a_holder_that_died_is_taken_over():
    """Killed (no release): its pid is gone, so the next one takes the lock over."""
    first, pid = holding()
    try:
        assert actor.process(pid) is not None and "actor.claim()" in actor.process(pid)[1]  # our own child
        os.kill(pid, 9)  # TerminateProcess on Windows: no release
        for _ in range(100):
            if actor.process(pid) is None:
                break
            time.sleep(0.05)
        left = lock()
        assert left["pid"] == pid and not actor.running(left)
        assert actor.claim() and lock()["pid"] == os.getpid()
    finally:
        first.kill()
        first.wait(30)


@pytest.mark.parametrize("change", ["started", "command"])
def test_a_pid_given_to_another_process_is_not_the_holder(monkeypatch, change):
    """Windows reuses pids: the same pid with another start time or command line is someone else."""
    other = {"pid": 4242, "started": "100", "command": "tabmancer.exe"}
    write(other)
    now = {"started": "100", "command": "tabmancer.exe", change: "something else"}
    monkeypatch.setattr(actor, "process", lambda pid: (now["started"], now["command"]) if pid == 4242
                        else ("1", "me"))
    assert not actor.running(other)
    assert actor.claim() and lock()["pid"] == os.getpid()


def test_the_same_pid_start_and_command_is_the_holder(monkeypatch):
    write({"pid": 4242, "started": "100", "command": "tabmancer.exe"})
    monkeypatch.setattr(actor, "process", lambda pid: ("100", "tabmancer.exe") if pid == 4242 else ("1", "me"))
    assert not actor.claim()


def test_a_process_that_cannot_be_read_is_not_the_holder(monkeypatch):
    write({"pid": 4242, "started": "100", "command": "tabmancer.exe"})
    monkeypatch.setattr(actor, "process", lambda pid: None if pid == 4242 else ("1", "me"))
    assert actor.claim()


def test_a_lock_being_written_is_left_alone_and_garbage_is_not():
    path = config_dir() / actor.LOCK
    config_dir().mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert not actor.claim()  # empty and new: another is writing it this moment
    old = time.time() - 60
    os.utime(path, (old, old))
    assert actor.claim()  # empty and a minute old: left half-written


def test_one_taker_at_a_time(monkeypatch):
    write({"pid": 4242, "started": "100", "command": "gone"})
    monkeypatch.setattr(actor, "process", lambda pid: None if pid == 4242 else ("1", "me"))
    (config_dir() / actor.TAKING).write_text("", encoding="utf-8")
    assert not actor.claim()  # another is taking it over right now
    old = time.time() - 60
    os.utime(config_dir() / actor.TAKING, (old, old))
    assert actor.claim()  # its taker died taking over
    assert not (config_dir() / actor.TAKING).exists()


def test_a_taker_that_was_beaten_to_it_steps_back(monkeypatch):
    gone = {"pid": 4242, "started": "100", "command": "gone"}
    winner = {"pid": 5151, "started": "200", "command": "tabmancer.exe"}
    write(gone)
    seen = iter([gone, winner])  # judged gone at first; read again under the mutex, another's
    monkeypatch.setattr(actor, "_read", lambda path: next(seen))
    monkeypatch.setattr(actor, "process", lambda pid: None if pid == 4242 else ("1", "me"))
    assert not actor.claim()
    assert json.loads((config_dir() / actor.LOCK).read_text(encoding="utf-8")) == gone  # not written over


def test_release_leaves_anothers_lock():
    other = {"pid": 4242, "started": "100", "command": "tabmancer.exe"}
    write(other)
    actor.release()
    assert lock() == other


def test_first_to_once_within_the_time():
    assert actor.first_to("handover-abc")
    assert not actor.first_to("handover-abc")  # the second dashboard: already done
    assert actor.first_to("handover-def")
    path = config_dir() / actor.CLAIMS / "handover-abc"
    old = time.time() - actor.CLAIM_SECONDS - 5
    os.utime(path, (old, old))
    assert actor.first_to("handover-abc")  # long after: a new one


def test_first_to_keeps_the_name_inside_its_folder():
    assert actor.first_to("../../escape")
    assert not (config_dir().parent / "escape").exists()
    assert len(list((config_dir() / actor.CLAIMS).iterdir())) == 1


def test_a_holder_whose_command_line_cannot_be_read_still_counts(monkeypatch):
    """An elevated holder's command line may not be readable: its pid and start time decide then."""
    record = {"pid": 4242, "started": "100", "command": "tabmancer.exe"}
    monkeypatch.setattr(actor, "process", lambda pid: ("100", ""))
    assert actor.running(record)
    assert actor.running({**record, "command": ""})
    monkeypatch.setattr(actor, "process", lambda pid: ("999", ""))
    assert not actor.running(record)  # the start time always counts


def test_a_lock_held_open_a_moment_is_waited_for(monkeypatch):
    write({"pid": 4242, "started": "100", "command": "x"})
    path_type = type(config_dir())
    real = path_type.read_text
    refused = []

    def read_text(self, *args, **kwargs):
        if self.name == actor.LOCK and len(refused) < 2:
            refused.append(1)
            raise PermissionError("in use")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", read_text)
    assert actor.holder() == {"pid": 4242, "started": "100", "command": "x"} and len(refused) == 2


def test_a_stale_takeover_mutex_is_replaced_and_only_its_own_removed(monkeypatch):
    write({"pid": 4242, "started": "100", "command": "gone"})
    monkeypatch.setattr(actor, "process", lambda pid: None if pid == 4242 else ("1", "me"))
    taking = config_dir() / actor.TAKING
    taking.write_text("9999 a sleeper", encoding="utf-8")
    old = time.time() - 60
    os.utime(taking, (old, old))
    assert actor.claim() and not taking.exists()  # replaced with ours, then ours removed

    # A taker beaten to the stale mutex: it neither takes the lock nor removes the other's mutex.
    write({"pid": 4242, "started": "100", "command": "gone"})
    taking.write_text("9999 a sleeper", encoding="utf-8")
    os.utime(taking, (old, old))
    real_replace = os.replace

    def replace(src, dst):
        real_replace(src, dst)
        if str(dst).endswith(actor.TAKING):
            taking.write_text("5151 quicker", encoding="utf-8")  # another taker, just after us

    monkeypatch.setattr(actor.os, "replace", replace)
    assert not actor.claim()
    assert taking.read_text(encoding="utf-8") == "5151 quicker"
    assert lock()["command"] == "gone"


def test_the_lock_is_put_in_place_whole(monkeypatch):
    """Written in full before it shows under its name: a reader never meets it half-written."""
    seen = []
    real_link = os.link

    def link(src, dst):
        seen.append(json.loads(pathlib.Path(src).read_text(encoding="utf-8")))
        real_link(src, dst)

    monkeypatch.setattr(actor.os, "link", link)
    assert actor.claim()
    assert seen and seen[0]["pid"] == os.getpid()
    assert sorted(p.name for p in config_dir().iterdir()) == [actor.LOCK]  # no temporary file left


def test_where_links_cannot_be_made_it_still_works(monkeypatch):
    def no_link(src, dst):
        raise OSError("FAT")

    monkeypatch.setattr(actor.os, "link", no_link)
    assert actor.claim() and lock()["pid"] == os.getpid()
    assert sorted(p.name for p in config_dir().iterdir()) == [actor.LOCK]


def test_let_go_gives_a_claim_back():
    assert actor.first_to("handover-x") and not actor.first_to("handover-x")
    actor.let_go("handover-x")
    assert actor.first_to("handover-x")


def test_temporary_files_left_behind_are_swept():
    config_dir().mkdir(parents=True, exist_ok=True)
    old = config_dir() / f"{actor.LOCK}.4242.deadbeef.tmp"
    young = config_dir() / f"{actor.LOCK}.5151.cafebabe.tmp"  # another one writing it this moment
    for path in (old, young):
        path.write_text("{}", encoding="utf-8")
    then = time.time() - actor.LEFT_TMP - 5
    os.utime(old, (then, then))
    assert actor.claim()
    assert not old.exists() and young.exists()


def test_a_process_that_cannot_read_its_own_start_time_does_not_take_it(monkeypatch):
    """Others would judge such a lock gone at once and take it over: two would act."""
    monkeypatch.setattr(actor, "process", lambda pid: None)
    assert actor.nameless()
    assert not actor.claim()
    assert not (config_dir() / actor.LOCK).exists()
