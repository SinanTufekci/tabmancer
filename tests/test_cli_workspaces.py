"""tabmancer workspaces --delete NAME: "Deleted" only once workspaces.drop deleted it; refused (another
tabmancer changing workspaces.json past the full wait, or the file unreadable), it says nothing was deleted
and exits 1, the file left as it is."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tabmancer import cli, config, workspaces, workspacewords


@pytest.fixture
def saved(tmp_path) -> list[workspaces.Workspace]:
    web = workspaces.Workspace("web", [workspaces.Entry(str(tmp_path))])
    pcb = workspaces.Workspace("PCB work", [workspaces.Entry(str(tmp_path), workspaces.NEW)])
    assert workspaces.keep(web) and workspaces.keep(pcb)
    return [web, pcb]


def test_delete_says_deleted_and_keeps_the_others(saved, capsys):
    cli.manage_workspaces("WEB")  # found whatever its case
    assert capsys.readouterr().out == workspacewords.deleted_note("web").words + "\n"
    assert workspaces.load() == {"PCB work": saved[1]}


def test_a_refused_delete_says_nothing_was_deleted_and_exits_1(saved, capsys, monkeypatch):
    path = config.config_dir() / workspaces.FILE
    before = path.read_bytes()
    waits: list[float] = []
    monkeypatch.setattr(config, "time", SimpleNamespace(time=time.time, sleep=waits.append))  # config's clock only
    with config.locked(workspaces.FILE), pytest.raises(SystemExit) as refused:
        cli.manage_workspaces("web")
    assert refused.value.code == 1
    assert capsys.readouterr().out == workspacewords.not_kept_note("web", deleting=True).words + "\n"
    assert waits == [config.BUSY_WAIT] * (config.BUSY_TRIES - 1)  # the command line waits the full wait
    assert path.read_bytes() == before
    path.write_text('{"workspaces": [', encoding="utf-8")  # cut off mid-write by something else
    with pytest.raises(SystemExit):
        cli.manage_workspaces("web")  # unreadable: no workspace named so, and nothing written
    assert path.read_text(encoding="utf-8") == '{"workspaces": ['
