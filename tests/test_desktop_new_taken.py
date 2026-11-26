"""T in the window with a team of that name made meanwhile, by another window or `tabmancer team`, while the
dialog was open: yours is not made (teams.make), that is said, the other team is kept as it was and nothing
opens; the names in use are read with a short wait (UI_TRIES), never ~1 s on the UI thread. Offscreen over the
made-up world of `tabmancer --demo`; skipped without the desktop extra (pip install "tabmancer[desktop]")."""

import importlib
import os

import pytest

if os.environ.get("TABMANCER_NEEDS_QT"):  # the CI job with the extra: Qt missing fails it, never skips
    importlib.import_module("PySide6")
else:
    pytest.importorskip("PySide6", reason="the desktop extra is not installed")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window on the screen, on any runner

import test_desktop_new as new
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_desktop_new import CTRL, by_key, made_up_lab, said

from tabmancer import config, teams
from tabmancer.desktop import look
from tabmancer.desktop.newteam import NewTeamDialog

qapp, world, window = new.qapp, new.world, new.window  # its fixtures, used here as they are there


def test_a_team_of_that_name_made_meanwhile_is_kept_and_yours_is_not_made(qapp, window, world, tmp_path,
                                                                          monkeypatch):
    here = made_up_lab(tmp_path)
    shown = window()
    loads: list[int | None] = []
    real_load = teams.load
    monkeypatch.setattr(teams, "load", lambda tries=None: loads.append(tries) or real_load(tries))
    shown.show_teams()
    qapp.processEvents()
    asked = by_key(qapp, shown, "T", NewTeamDialog)
    assert config.UI_TRIES in loads  # the names in use, read on the UI thread
    asked.name.setText("made-up-team")
    asked.folder.setText(str(here))
    asked.goal.setPlainText("Add a made-up checkout with tests")
    other = teams.new_team("made-up-team", str(here), "Another window's made-up goal", teams.parse_members("docs: z"))
    teams.put(other)  # made meanwhile, elsewhere
    QTest.keyClick(asked.name, Qt.Key.Key_S, CTRL)
    qapp.processEvents()
    assert said(shown) == ("made-up-team was not made: a team named made-up-team was made meanwhile. "
                           "Give yours another name.")
    assert look.AMBER in shown.bit_line.styleSheet() and teams.load()["made-up-team"] == other
    assert not world.tabs and shown.new_team_shown is None
