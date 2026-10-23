"""tabmancer's own folder, and taking over the one saved under its old name."""

import json

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
