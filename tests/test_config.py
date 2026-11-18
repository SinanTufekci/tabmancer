"""tabmancer's own folder, and taking over the one saved under its old name."""

import json
import os
from pathlib import Path

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
