"""tabmancer's own folder, and taking over the one saved under its old name."""

import json
import os
from pathlib import Path

import pytest

from tabmancer import config


def test_the_folder_of_the_old_name_is_taken_over_once(tmp_path, monkeypatch):
    monkeypatch.delenv("TABMANCER_CONFIG_DIR")
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # elsewhere
    old = tmp_path / "ccrestore"
    old.mkdir()
    (old / "workspaces.json").write_text(json.dumps({"PCB work": []}), encoding="utf-8")

    assert config.config_dir() == tmp_path / "tabmancer"
    assert config.read_json("workspaces.json") == {"PCB work": []}
    assert not old.exists()

    old.mkdir()  # an old folder that turns up later never replaces the new one
    config.write_json("config.json", {"theme": "x"})
    assert config.read_json("config.json") == {"theme": "x"} and old.exists()


def test_the_newest_old_name_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("TABMANCER_CONFIG_DIR")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for old, theme in (("ccrestore", "oldest"), ("ccharbor", "older"), ("antyard", "newest")):
        (tmp_path / old).mkdir()
        (tmp_path / old / "config.json").write_text(json.dumps({"theme": theme}), encoding="utf-8")
    assert config.read_json("config.json") == {"theme": "newest"}  # antyard's, the one in use
    assert not (tmp_path / "antyard").exists() and (tmp_path / "ccharbor").exists()
    assert (tmp_path / "tabmancer" / "config.json").exists()


def test_a_read_and_a_write_that_collide_wait_it_out(monkeypatch):
    # Windows: a file being replaced can't be opened, one open can't be replaced. A read that gave
    # up read as no file, and the load-then-save after it wrote teams.json back with one team.
    config.write_json("teams.json", {"teams": ["a", "b"]})
    monkeypatch.setattr(config, "BUSY_WAIT", 0)
    real_read, real_replace = Path.read_text, os.replace
    busy = {"read": 2, "replace": 2}

    def read_text(self, *args, **kwargs):
        if busy["read"]:
            busy["read"] -= 1
            raise PermissionError(13, "in use")
        return real_read(self, *args, **kwargs)

    def replace(src, dst):
        if busy["replace"]:
            busy["replace"] -= 1
            raise PermissionError(5, "in use")
        real_replace(src, dst)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(config.os, "replace", replace)
    assert config.read_json("teams.json") == {"teams": ["a", "b"]}
    config.write_json("teams.json", {"teams": ["a", "b", "c"]})
    assert config.read_json("teams.json") == {"teams": ["a", "b", "c"]}
    assert not list(config.config_dir().glob("*.tmp"))
    assert config.read_json("missing.json") is None  # no file is still no file, at once


def test_a_lasting_refusal_is_not_waited_out():
    # The wait above is for another process: a folder by that name, or a read-only file, never
    # gives way, and every load and save of it stalled for a second.
    import stat
    import sys
    import time

    import pytest

    folder = config.config_dir() / "teams.json"
    folder.mkdir(parents=True)
    began = time.monotonic()
    assert config.read_json("teams.json") is None
    with pytest.raises(OSError):
        config.write_json("teams.json", {"teams": []})
    if sys.platform == "win32":  # elsewhere a read-only file is replaced all the same
        locked = config.config_dir() / "marks.json"
        config.write_json("marks.json", {"hidden": []})
        locked.chmod(stat.S_IREAD)
        try:
            with pytest.raises(PermissionError):
                config.write_json("marks.json", {"hidden": ["a"]})
            assert config.read_json("marks.json") == {"hidden": []}
        finally:
            locked.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert time.monotonic() - began < 0.5
    assert not list(config.config_dir().glob("*.tmp"))


def test_a_save_that_fails_otherwise_leaves_no_half_file(monkeypatch):
    # Linux and macOS refuse to replace a folder with IsADirectoryError, no PermissionError: the
    # temporary file stayed behind.
    import pytest

    def replace(src, dst):
        raise IsADirectoryError(21, "Is a directory")

    monkeypatch.setattr(config.os, "replace", replace)
    with pytest.raises(IsADirectoryError):
        config.write_json("teams.json", {"teams": []})
    assert not list(config.config_dir().glob("*.tmp"))


def test_a_lock_is_one_process_at_a_time_and_given_back(monkeypatch):
    """config.locked: the read-then-write of one file, one process at a time. A held lock is waited for
    BUSY_TRIES times, then Busy; it is given back after the change, and after a change that failed."""
    waits = []
    monkeypatch.setattr(config.time, "sleep", waits.append)
    path = config.config_dir() / "queue.json.lock"
    with config.locked("queue.json"):
        assert path.exists()
        try:
            with config.locked("queue.json"):
                raise AssertionError("a second holder")
        except config.Busy:
            pass
        assert waits == [config.BUSY_WAIT] * (config.BUSY_TRIES - 1)
    assert not path.exists()
    try:
        with config.locked("queue.json"):
            raise ValueError("the change failed")
    except ValueError:
        pass
    assert not path.exists()
    with config.locked("queue.json"), config.locked("other.json"):  # one lock per file
        pass


def test_a_lock_left_by_a_process_that_died_is_taken_over(monkeypatch):
    monkeypatch.setattr(config.time, "sleep", lambda seconds: None)
    path = config.config_dir() / "queue.json.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    old = path.stat().st_mtime - config.LOCK_STALE_SECONDS - 5
    os.utime(path, (old, old))
    with config.locked("queue.json"):
        assert path.stat().st_mtime > old  # this process's now
    assert not path.exists()
    assert issubclass(config.Busy, OSError)  # callers that catch OSError catch it too


@pytest.mark.parametrize("fresh", [True, False])
def test_a_holder_waiting_inside_its_lock_is_never_taken_over(monkeypatch, fresh):
    """A live holder whose read and write wait out a held file (a scanner), on a machine so loaded that each
    BUSY_WAIT takes 0.2 s, holds the lock far past LOCK_STALE_SECONDS: its waits keep the lock fresh, so
    nobody takes it over. Without that (fresh=False, the check that the test can tell), it would be."""
    clock = [1_000_000.0]
    monkeypatch.setattr(config.time, "time", lambda: clock[0])
    monkeypatch.setattr(config.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 0.2))
    if not fresh:
        monkeypatch.setattr(config, "_freshen", lambda: None)
    path = config.config_dir() / "queue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    real_read, real_replace = Path.read_text, os.replace

    def held_read(self, *args, **kwargs):
        if self == path:
            raise PermissionError(13, "held by a scanner")
        return real_read(self, *args, **kwargs)

    def held_replace(src, dst):
        if Path(dst) == path:
            raise PermissionError(13, "held by a scanner")
        return real_replace(src, dst)

    with config.locked("queue.json"):
        lock = config.config_dir() / "queue.json.lock"
        os.utime(lock, (clock[0], clock[0]))  # made now, on the test's clock
        started = clock[0]
        with monkeypatch.context() as scanned:
            scanned.setattr(config.Path, "read_text", held_read)
            scanned.setattr(config.os, "replace", held_replace)
            assert config.read_json("queue.json") is None
            with pytest.raises(PermissionError):
                config.write_json("queue.json", {"a": 1})
        assert clock[0] - started > 2 * config.LOCK_STALE_SECONDS  # held long past the stale age
        if fresh:
            with pytest.raises(config.Busy), config.locked("queue.json", tries=1):
                pass
        else:
            with config.locked("queue.json", tries=1):  # taken over as left by a process that died
                pass


def test_a_ui_waits_for_a_held_lock_only_a_moment(monkeypatch):
    waits = []
    monkeypatch.setattr(config.time, "sleep", waits.append)
    with config.locked("queue.json"), pytest.raises(config.Busy), config.locked("queue.json", config.UI_TRIES):
        pass
    assert waits == [config.BUSY_WAIT] * (config.UI_TRIES - 1)
    assert sum(waits) < 0.1 < config.LOCK_STALE_SECONDS  # ~75 ms: not a freeze
