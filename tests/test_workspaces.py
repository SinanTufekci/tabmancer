from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tabmancer import workspaces as W
from tabmancer.launch import Mode
from tabmancer.sessions import Exit, Session, Status


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def folders(tmp_path):
    made = {}
    for name in ("pcb", "web", "empty"):
        made[name] = tmp_path / name
        made[name].mkdir()
    return made


def session(sid, cwd, minutes_ago, status=Status.DONE):
    s = Session(session_id=sid, path=Path(sid), cwd=str(cwd), title=f"title {sid}",
                last_active=datetime.now() - timedelta(minutes=minutes_ago))
    s.status = status
    s.exit = Exit.RUNNING if status is Status.RUNNING else Exit.CLEAN
    return s


def test_round_trip_and_case_insensitive_find(folders):
    ws = W.Workspace("PCB work", [W.Entry(str(folders["pcb"])), W.Entry(str(folders["web"]), W.NEW)])
    W.save({ws.name: ws})
    loaded = W.load()
    assert loaded["PCB work"] == ws
    assert W.find(loaded, "pcb WORK") == ws
    assert W.find(loaded, "missing") is None


def test_clean_name_strips_characters_that_break_shortcuts():
    assert W.clean_name(' a/b:c;d"e ') == "a-b-c-d-e"


def test_from_sessions_keeps_folders_and_new_rows(folders):
    a, b = session("a", folders["pcb"], 1), session("b", folders["web"], 2)
    ws = W.from_sessions("x", [a, b], {"b": Mode.NEW})
    assert [(e.cwd, e.mode) for e in ws.entries] == [(str(folders["pcb"]), W.CONTINUE),
                                                     (str(folders["web"]), W.NEW)]


def test_resolve_continues_latest_sessions_per_folder(folders):
    sessions = [  # newest first
        session("pcb-open", folders["pcb"], 1, Status.RUNNING),
        session("pcb-2", folders["pcb"], 5),
        session("pcb-1", folders["pcb"], 50),
        session("web-1", folders["web"], 10),
    ]
    ws = W.Workspace("w", [W.Entry(str(folders["pcb"])), W.Entry(str(folders["pcb"])),
                           W.Entry(str(folders["web"])), W.Entry(str(folders["web"]), W.NEW)])
    launches, notes = W.resolve(ws, sessions)
    # pcb wants two: one is already open, so only the next newest is resumed.
    assert [(launch.mode, launch.session_id) for launch in launches] == [
        (Mode.RESUME, "pcb-2"), (Mode.RESUME, "web-1"), (Mode.NEW, "")]
    assert notes == []


def test_resolve_skips_open_folders_and_missing_ones(folders, tmp_path):
    sessions = [session("web-open", folders["web"], 1, Status.RUNNING)]
    ws = W.Workspace("w", [W.Entry(str(folders["web"])), W.Entry(str(tmp_path / "gone"))])
    launches, notes = W.resolve(ws, sessions)
    assert launches == []
    assert notes == ["web: already open", f"gone: folder not found ({tmp_path / 'gone'})"]


def test_resolve_falls_back_to_continue_or_new(folders):
    # Nothing recent known for the folder: let claude --continue find an older session.
    launches, _ = W.resolve(W.Workspace("w", [W.Entry(str(folders["empty"]))]), [])
    assert [launch.mode for launch in launches] == [Mode.CONTINUE]
    # A session there is open: --continue could pick it, so start fresh instead.
    open_one = session("e", folders["empty"], 1, Status.RUNNING)
    ws = W.Workspace("w", [W.Entry(str(folders["empty"])), W.Entry(str(folders["empty"]))])
    launches, _ = W.resolve(ws, [open_one])
    assert [launch.mode for launch in launches] == [Mode.NEW]
